"""AWS CDK stack: Cognito + DynamoDB + Lambda (ARM) + HTTP API + S3 + CloudFront.

Cost design for <~$10/mo at low traffic:
- HTTP API (not REST) – lower $ per million requests
- Lambda arm64 (Graviton) – ~20% cheaper
- DynamoDB on-demand single table
- S3 + CloudFront for HTTPS custom domain (dev: melon-dev.com)
- Cognito free tier (50k MAU)
- No NAT, no VPC, no RDS

Custom domain (optional via context frontendDomain):
  cdk deploy -c env=dev -c frontendDomain=melon-dev.com
Creates Route53 hosted zone + ACM (us-east-1) + CloudFront.
Point the registrar NS records at the hosted zone nameservers once.
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_authorizers as apigw_auth
from aws_cdk import aws_apigatewayv2_integrations as apigw_integrations
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from constructs import Construct

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"


class StemStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str = "dev",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_name = env_name
        is_prod = env_name == "prod"

        # Custom domain for the SPA (HTTPS via CloudFront). Dev default: melon-dev.com
        frontend_domain = (
            self.node.try_get_context("frontendDomain")
            or ("melon-dev.com" if env_name == "dev" else None)
            or ""
        ).strip().lower() or None

        removal = RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY
        table = self._create_table(removal)
        user_pool, user_pool_client, admin_group = self._create_cognito(removal)
        frontend_bucket = self._create_frontend_bucket(removal)
        fn = self._create_api_lambda(table, user_pool)
        http_api = self._create_http_api(fn, user_pool, user_pool_client)

        # S3 website URL (HTTP) — kept as fallback; primary is custom domain when set
        s3_website_url = f"http://{frontend_bucket.bucket_website_url}"

        distribution = None
        frontend_url = s3_website_url
        if frontend_domain:
            distribution, frontend_url = self._create_frontend_cdn(
                frontend_bucket,
                domain_name=frontend_domain,
                removal=removal,
            )

        s3deploy.BucketDeployment(
            self,
            "FrontendDeploy",
            sources=[s3deploy.Source.asset(str(FRONTEND_DIR))],
            destination_bucket=frontend_bucket,
            distribution=distribution,
            distribution_paths=["/*"] if distribution else None,
            memory_limit=256,
        )

        CfnOutput(self, "HttpApiUrl", value=http_api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "FrontendBucketName", value=frontend_bucket.bucket_name)
        CfnOutput(self, "FrontendS3WebsiteUrl", value=s3_website_url)
        CfnOutput(self, "FrontendUrl", value=frontend_url)
        if frontend_domain:
            CfnOutput(self, "FrontendDomain", value=frontend_domain)
        if distribution is not None:
            CfnOutput(
                self,
                "CloudFrontDomainName",
                value=distribution.distribution_domain_name,
            )
        CfnOutput(self, "AdminGroupName", value=admin_group.group_name or "admin")
        CfnOutput(self, "Region", value=self.region)
        CfnOutput(
            self,
            "GitRepository",
            value="https://github.com/beawizard/stem-study.git",
            description="Development repository (unchanged)",
        )

    def _create_table(self, removal: RemovalPolicy) -> dynamodb.Table:
        table = dynamodb.Table(
            self,
            "StemTable",
            table_name=f"stem-study-{self.env_name}",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=self.env_name == "prod",
            ),
        )
        table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(name="GSI1PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI1SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        return table

    def _create_cognito(
        self, removal: RemovalPolicy
    ) -> tuple[cognito.UserPool, cognito.UserPoolClient, cognito.CfnUserPoolGroup]:
        pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=f"stem-study-{self.env_name}",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,  # matches admin password strength
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=removal,
        )

        client = pool.add_client(
            "WebClient",
            user_pool_client_name=f"stem-web-{self.env_name}",
            auth_flows=cognito.AuthFlow(
                user_srp=True,
                user_password=True,
            ),
            generate_secret=False,
            prevent_user_existence_errors=True,
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(30),
        )

        admin_group = cognito.CfnUserPoolGroup(
            self,
            "AdminGroup",
            user_pool_id=pool.user_pool_id,
            group_name="admin",
            description="Administrators – manage subjects, levels, payments",
            precedence=1,
        )
        return pool, client, admin_group

    def _create_frontend_bucket(self, removal: RemovalPolicy) -> s3.Bucket:
        # Public website endpoint kept as HTTP fallback; CloudFront is primary for HTTPS.
        return s3.Bucket(
            self,
            "FrontendBucket",
            website_index_document="index.html",
            website_error_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
            removal_policy=removal,
            auto_delete_objects=removal == RemovalPolicy.DESTROY,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

    def _create_frontend_cdn(
        self,
        bucket: s3.Bucket,
        *,
        domain_name: str,
        removal: RemovalPolicy,
    ) -> tuple[cloudfront.Distribution, str]:
        """HTTPS custom domain: Route53 zone + ACM (us-east-1) + CloudFront.

        After first deploy, set the domain registrar nameservers to the hosted zone NS
        outputs so ACM can validate and https://{domain} resolves.
        """
        zone = route53.PublicHostedZone(
            self,
            "FrontendHostedZone",
            zone_name=domain_name,
            comment=f"MElon Basic Education frontend ({self.env_name})",
        )

        # CloudFront requires the certificate in us-east-1.
        # DnsValidatedCertificate is the supported cross-region pattern for this use case.
        certificate = acm.DnsValidatedCertificate(
            self,
            "FrontendCertificate",
            domain_name=domain_name,
            subject_alternative_names=[f"www.{domain_name}"],
            hosted_zone=zone,
            region="us-east-1",
        )

        # Use S3 REST origin with OAI so we do not require the public website endpoint.
        # Keep public website on for the HTTP fallback URL during migration.
        origin_access_identity = cloudfront.OriginAccessIdentity(
            self,
            "FrontendOAI",
            comment=f"OAI for {domain_name} ({self.env_name})",
        )
        bucket.grant_read(origin_access_identity)

        distribution = cloudfront.Distribution(
            self,
            "FrontendDistribution",
            comment=f"stem-study-{self.env_name} frontend",
            default_root_object="index.html",
            domain_names=[domain_name, f"www.{domain_name}"],
            certificate=certificate,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_identity(
                    bucket,
                    origin_access_identity=origin_access_identity,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                compress=True,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            error_responses=[
                # SPA: client-side routes fall back to index.html
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(5),
                ),
            ],
        )

        route53.ARecord(
            self,
            "FrontendAliasA",
            zone=zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )
        route53.AaaaRecord(
            self,
            "FrontendAliasAAAA",
            zone=zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )
        route53.ARecord(
            self,
            "FrontendWwwAliasA",
            zone=zone,
            record_name=f"www.{domain_name}",
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )
        route53.AaaaRecord(
            self,
            "FrontendWwwAliasAAAA",
            zone=zone,
            record_name=f"www.{domain_name}",
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )

        CfnOutput(
            self,
            "HostedZoneId",
            value=zone.hosted_zone_id,
        )
        CfnOutput(
            self,
            "HostedZoneNameServers",
            value=cdk_join_ns(zone),
            description=(
                f"Set these NS records at the domain registrar for {domain_name} "
                "(currently IONOS). ACM validation and HTTPS need this."
            ),
        )

        # Keep removal intent on zone/distribution via stack removal policy (dev DESTROY)
        del removal  # zone/distribution follow stack; retained only when stack is retained
        return distribution, f"https://{domain_name}"

    def _create_api_lambda(
        self,
        table: dynamodb.Table,
        user_pool: cognito.UserPool,
    ) -> lambda_.Function:
        # Prefer pre-built package (scripts/package_lambda.sh) so deploy works without Docker.
        package_dir = BACKEND_DIR / "lambda_package"
        asset_path = package_dir if package_dir.is_dir() else BACKEND_DIR
        fn = lambda_.Function(
            self,
            "ApiFunction",
            function_name=f"stem-study-api-{self.env_name}",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="app.handler.handler",
            code=lambda_.Code.from_asset(str(asset_path)),
            # Assessment can touch many level META rows; 512MB + 90s is still low-cost.
            memory_size=512,
            timeout=Duration.seconds(90),
            environment={
                "TABLE_NAME": table.table_name,
                "USER_POOL_ID": user_pool.user_pool_id,
                "POWERTOOLS_SERVICE_NAME": "stem-study",
                "LOG_LEVEL": "INFO",
                # Optional: set via stack context or after deploy
                #   cdk deploy -c adminNotifyEmail=you@example.com
                "ADMIN_NOTIFY_EMAIL": self.node.try_get_context("adminNotifyEmail") or "",
                "SES_FROM_EMAIL": self.node.try_get_context("sesFromEmail") or "",
            },
            description="STEM Study API (ARM64 / Graviton)",
        )

        # Least privilege: read/write only this table (includes GSI ARNs)
        table.grant_read_write_data(fn)
        # Best-effort school-request emails (identity must be verified in SES)
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=["*"],
            )
        )
        return fn

    def _create_http_api(
        self,
        fn: lambda_.Function,
        user_pool: cognito.UserPool,
        client: cognito.UserPoolClient,
    ) -> apigwv2.HttpApi:
        """
        Use a small set of routes to avoid Lambda resource-policy size limit (20KB):
          GET  /health            – public
          GET  /schools           – public (sign-up school combobox)
          POST /schools/requests  – public (learner school request)
          /{proxy+}               – Cognito JWT
        """
        authorizer = apigw_auth.HttpJwtAuthorizer(
            "CognitoAuthorizer",
            jwt_issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}",
            jwt_audience=[client.user_pool_client_id],
        )

        http_api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name=f"stem-study-{self.env_name}",
            description="STEM Study HTTP API",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_headers=["Content-Type", "Authorization"],
                allow_methods=[
                    apigwv2.CorsHttpMethod.GET,
                    apigwv2.CorsHttpMethod.POST,
                    apigwv2.CorsHttpMethod.PUT,
                    apigwv2.CorsHttpMethod.PATCH,
                    apigwv2.CorsHttpMethod.DELETE,
                    apigwv2.CorsHttpMethod.OPTIONS,
                ],
                allow_origins=["*"],
                max_age=Duration.days(1),
            ),
            create_default_stage=True,
        )

        integration = apigw_integrations.HttpLambdaIntegration("ApiIntegration", fn)

        # Public health check
        http_api.add_routes(
            path="/health",
            methods=[apigwv2.HttpMethod.GET],
            integration=integration,
        )

        # Public school catalog for Sign up (no JWT)
        http_api.add_routes(
            path="/schools",
            methods=[apigwv2.HttpMethod.GET],
            integration=integration,
        )

        # Public: learner requests a school not yet listed (sign-up modal)
        http_api.add_routes(
            path="/schools/requests",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
        )

        # Protected catch-all — do NOT use ANY (that captures OPTIONS and forces JWT
        # on CORS preflight, which browsers send without Authorization → NetworkError).
        # CORS OPTIONS is handled by cors_preflight above without an authorizer.
        http_api.add_routes(
            path="/{proxy+}",
            methods=[
                apigwv2.HttpMethod.GET,
                apigwv2.HttpMethod.POST,
                apigwv2.HttpMethod.PUT,
                apigwv2.HttpMethod.PATCH,
                apigwv2.HttpMethod.DELETE,
            ],
            integration=integration,
            authorizer=authorizer,
        )

        return http_api


def cdk_join_ns(zone: route53.PublicHostedZone) -> str:
    """Join hosted zone name servers for a single CfnOutput string."""
    # hosted_zone_name_servers is a token list; Fn.join at synth time
    from aws_cdk import Fn

    return Fn.join(", ", zone.hosted_zone_name_servers or [])
