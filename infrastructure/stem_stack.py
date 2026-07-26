"""AWS CDK stack: Cognito + DynamoDB + Lambda (ARM) + HTTP API + S3.

Cost design for <~$10/mo at low traffic:
- HTTP API (not REST) – lower $ per million requests
- Lambda arm64 (Graviton) – ~20% cheaper
- DynamoDB on-demand single table
- S3 static website (no CloudFront required for MVP)
- Cognito free tier (50k MAU)
- No NAT, no VPC, no RDS
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
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
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

        removal = RemovalPolicy.RETAIN if is_prod else RemovalPolicy.DESTROY
        table = self._create_table(removal)
        user_pool, user_pool_client, admin_group = self._create_cognito(removal)
        frontend_bucket = self._create_frontend_bucket(removal)
        fn = self._create_api_lambda(table, user_pool)
        http_api = self._create_http_api(fn, user_pool, user_pool_client)

        s3deploy.BucketDeployment(
            self,
            "FrontendDeploy",
            sources=[s3deploy.Source.asset(str(FRONTEND_DIR))],
            destination_bucket=frontend_bucket,
            memory_limit=256,
        )

        CfnOutput(self, "HttpApiUrl", value=http_api.api_endpoint)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "FrontendBucketName", value=frontend_bucket.bucket_name)
        CfnOutput(
            self,
            "FrontendUrl",
            value=f"http://{frontend_bucket.bucket_website_url}",
        )
        CfnOutput(self, "AdminGroupName", value=admin_group.group_name or "admin")
        CfnOutput(self, "Region", value=self.region)

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
