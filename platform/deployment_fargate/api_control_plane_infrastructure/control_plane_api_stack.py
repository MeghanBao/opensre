"""CDK stack hosting the control-plane lifecycle API Lambda."""

from __future__ import annotations

from typing import Any

from aws_cdk import (
    CfnOutput,
    CfnParameter,
    Fn,
    Stack,
)
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

from platform.deployment_fargate.api_control_plane_infrastructure.database_migration import (
    ControlPlaneDatabaseMigration,
)
from platform.deployment_fargate.api_control_plane_infrastructure.http_api import (
    ControlPlaneHttpApi,
)
from platform.deployment_fargate.api_control_plane_infrastructure.lambda_function import (
    ControlPlaneFunction,
)
from platform.deployment_fargate.api_control_plane_infrastructure.monitoring import (
    ControlPlaneMonitoring,
)
from platform.deployment_fargate.infrastructure_exports import (
    CONTROL_PLANE_FLEET_ENVIRONMENT,
    fleet_export_name,
)


class ControlPlaneApiStack(Stack):
    """Deploy the IAM-protected lifecycle API and schema migration."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        lambda_code: lambda_.Code | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        database_url_secret_arn = CfnParameter(
            self,
            "DatabaseUrlSecretArn",
            type="String",
            no_echo=True,
            allowed_pattern=(r"arn:[^:]+:secretsmanager:[^:]+:[0-9]{12}:secret:.+"),
            constraint_description="Must be a complete Secrets Manager secret ARN",
            description="Secrets Manager ARN whose SecretString is the Postgres DATABASE_URL",
        )
        lifecycle_role_arns = CfnParameter(
            self,
            "LifecycleRoleArns",
            type="CommaDelimitedList",
            description="IAM role ARNs allowed to call lifecycle routes",
        )

        fleet = {
            env_name: Fn.import_value(fleet_export_name(env_name))
            for env_name in CONTROL_PLANE_FLEET_ENVIRONMENT
        }
        resource_prefix = fleet["OPENSRE_FARGATE_RESOURCE_PREFIX"]

        database_migration = ControlPlaneDatabaseMigration(
            self,
            "DatabaseMigration",
            database_url_secret_arn=database_url_secret_arn.value_as_string,
            resource_prefix=resource_prefix,
            lambda_code=lambda_code,
        )
        control_plane_function = ControlPlaneFunction(
            self,
            "ControlPlaneFunction",
            database_url_secret_arn=database_url_secret_arn.value_as_string,
            lifecycle_role_arns=lifecycle_role_arns.value_as_list,
            fleet=fleet,
            lambda_code=lambda_code,
        )
        function = control_plane_function.function
        function.node.add_dependency(database_migration.resource)

        http_api = ControlPlaneHttpApi(
            self,
            "ControlPlaneHttpApi",
            function=function,
            resource_prefix=resource_prefix,
        )
        http_api.node.add_dependency(database_migration.resource)
        ControlPlaneMonitoring(
            self,
            "Monitoring",
            function=function,
            api_id=http_api.api_id,
        )

        CfnOutput(
            self,
            "ControlPlaneApiEndpoint",
            value=http_api.api_endpoint,
            description="Base URL for IAM-protected lifecycle APIs",
        )
        CfnOutput(
            self,
            "ControlPlaneFunctionArn",
            value=function.function_arn,
        )
