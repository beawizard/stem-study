"""AWS CDK stack: Cognito + DynamoDB + Lambda (ARM) + HTTP API + S3.

Cost design for <~$10/mo at low traffic:
- HTTP API (not REST)
- Lambda arm64 (Graviton)
- DynamoDB on-demand single table
- Dev: S3 static website (HTTP)
- Prod: S3 + CloudFront + ACM for https://stem.melon.com (Price Class 100)
- Cognito free tier
- No NAT, VPC, RDS, or ALB
"""

from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Fn,
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

        # Prod default domain; override with -c frontendDomain=...
        # Empty string disables custom domain.
        domain_ctx = self.node.try_get_context("frontendDomain")
        if domain_ctx is None and is_prod:
            frontend_domain = "stem.melon.com"
        else:
            frontend_domain = (domain_ctx or "").strip().lower() or None

        removal = RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY
        table = self._create_table(removal)
        user_pool, user_pool_client, admin_group = self._create_cognito(removal)
        frontend_bucket = self._create_frontend_bucket(removal, is_prod=is_prod)
        fn = self._create_api_lambda(table, user_pool)
        http_api = self._create_http_api(fn, user_pool, user_pool_client)

        s3_website_url = f"http://{frontend_bucket.bucket_website_url}"
        distribution = None
        frontend_url = s3_website_url

        if frontend_domain:
            distribution, frontend_url = self._create_frontend_cdn(
                frontend_bucket,
                domain_name=frontend_domain,
                is_prod=is_prod,
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
            CfnOutput(
                self,
                "CloudFrontDistributionId",
                value=distribution.distribution_id,
            )
        CfnOutput(self, "AdminGroupName", value=admin_group.group_name or "admin")
        CfnOutput(self, "Region", value=self.region)
        CfnOutput(self, "EnvName", value=env_name)

    def _create_table(self, removal: RemovalPolicy) -> dynamodb.Table:
        table = dynamodb.Table(
            self,
            "StemTable",
            table_name=f"stem-study-{self.env_name}",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=removal,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=self.env_name == "prod",
            ),
        )
        table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="GSI1PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI1SK", type=dynamodb.AttributeType.STRING
            ),
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
                nickname=cognito.StandardAttribute(required=False, mutable=True),
                fullname=cognito.StandardAttribute(required=False, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
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

    def _create_frontend_bucket(
        self, removal: RemovalPolicy, *, is_prod: bool
    ) -> s3.Bucket:
        # Public website kept for HTTP fallback / OAI origin compatibility.
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
            auto_delete_objects=removal == RemovalPolicy.DESTROY and not is_prod,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

    def _create_frontend_cdn(
        self,
        bucket: s3.Bucket,
        *,
        domain_name: str,
        is_prod: bool,
    ) -> tuple[cloudfront.Distribution, str]:
        """HTTPS custom domain via CloudFront + ACM (us-east-1) + Route53 zone.

        For stem.melon.com, create a public hosted zone and delegate NS from the
        parent melon.com DNS (Kakao/IONOS/etc.), OR copy the zone NS / ACM CNAMEs
        as documented in docs/PRODUCTION.md.
        """
        zone = route53.PublicHostedZone(
            self,
            "FrontendHostedZone",
            zone_name=domain_name,
            comment=f"MElon STEM frontend ({self.env_name})",
        )

        certificate = acm.DnsValidatedCertificate(
            self,
            "FrontendCertificate",
            domain_name=domain_name,
            hosted_zone=zone,
            region="us-east-1",
        )

        oai = cloudfront.OriginAccessIdentity(
            self,
            "FrontendOAI",
            comment=f"OAI {domain_name} ({self.env_name})",
        )
        bucket.grant_read(oai)

        distribution = cloudfront.Distribution(
            self,
            "FrontendDistribution",
            comment=f"stem-study-{self.env_name}",
            default_root_object="index.html",
            domain_names=[domain_name],
            certificate=certificate,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            # Cost: cheapest edge set (US/EU/Israel) — fine for APAC via mid-tier latency
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_identity(
                    bucket,
                    origin_access_identity=oai,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                compress=True,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            error_responses=[
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

        CfnOutput(self, "HostedZoneId", value=zone.hosted_zone_id)
        CfnOutput(
            self,
            "HostedZoneNameServers",
            value=Fn.join(", ", zone.hosted_zone_name_servers or []),
            description=(
                f"Delegate DNS for {domain_name} at the parent registrar "
                f"(melon.com → NS for label 'stem') to these AWS nameservers."
            ),
        )
        _ = is_prod
        return distribution, f"https://{domain_name}"

    def _create_api_lambda(
        self,
        table: dynamodb.Table,
        user_pool: cognito.UserPool,
    ) -> lambda_.Function:
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
            memory_size=512,
            timeout=Duration.seconds(90),
            environment={
                "TABLE_NAME": table.table_name,
                "USER_POOL_ID": user_pool.user_pool_id,
                "POWERTOOLS_SERVICE_NAME": "stem-study",
                "LOG_LEVEL": "INFO",
                "ENV_NAME": self.env_name,
                "ADMIN_NOTIFY_EMAIL": self.node.try_get_context("adminNotifyEmail")
                or "",
                "SES_FROM_EMAIL": self.node.try_get_context("sesFromEmail") or "",
            },
            description=f"STEM Study API ({self.env_name}, ARM64)",
        )

        table.grant_read_write_data(fn)
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
        authorizer = apigw_auth.HttpJwtAuthorizer(
            "CognitoAuthorizer",
            jwt_issuer=(
                f"https://cognito-idp.{self.region}.amazonaws.com/"
                f"{user_pool.user_pool_id}"
            ),
            jwt_audience=[client.user_pool_client_id],
        )

        http_api = apigwv2.HttpApi(
            self,
            "HttpApi",
            api_name=f"stem-study-{self.env_name}",
            description=f"STEM Study HTTP API ({self.env_name})",
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

        http_api.add_routes(
            path="/health",
            methods=[apigwv2.HttpMethod.GET],
            integration=integration,
        )
        http_api.add_routes(
            path="/schools",
            methods=[apigwv2.HttpMethod.GET],
            integration=integration,
        )
        http_api.add_routes(
            path="/schools/requests",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
        )
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
