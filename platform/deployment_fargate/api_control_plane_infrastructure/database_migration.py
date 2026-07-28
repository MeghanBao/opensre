"""One-shot control-plane Postgres schema migration infrastructure."""

from __future__ import annotations

from aws_cdk import CustomResource, Duration, Fn, RemovalPolicy, custom_resources
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from platform.deployment_fargate.api_control_plane_infrastructure.lambda_bundle import (
    bundled_lambda_code,
)

_MIGRATION_HANDLER = (
    "platform.deployment_fargate.api_control_plane_infrastructure.migration_runtime.lambda_handler"
)


class ControlPlaneDatabaseMigration(Construct):
    """Apply the idempotent schema before the HTTP API becomes available."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        database_url_secret_arn: str,
        resource_prefix: str,
        lambda_code: lambda_.Code | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=Fn.join("", ["/aws/lambda/", resource_prefix, "-db-migration"]),
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )
        execution_role = iam.Role(
            self,
            "ExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[database_url_secret_arn],
            )
        )
        migration_function = lambda_.Function(
            self,
            "Function",
            function_name=Fn.join("", [resource_prefix, "-db-migration"]),
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler=_MIGRATION_HANDLER,
            code=lambda_code or bundled_lambda_code(),
            role=execution_role,
            memory_size=256,
            timeout=Duration.minutes(2),
            environment={"OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN": database_url_secret_arn},
        )
        migration_function.node.add_dependency(log_group)
        migration_version = migration_function.current_version
        provider = custom_resources.Provider(
            self,
            "Provider",
            on_event_handler=migration_function,
        )
        self.resource = CustomResource(
            self,
            "Schema",
            service_token=provider.service_token,
            properties={
                "DatabaseUrlSecretArn": database_url_secret_arn,
                "MigrationFunctionVersion": migration_version.version,
            },
        )
