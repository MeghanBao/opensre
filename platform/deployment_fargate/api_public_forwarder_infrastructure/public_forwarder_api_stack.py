"""CDK stack hosting the public run API Lambda and HTTP API."""

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

from platform.deployment_fargate.api_public_forwarder_infrastructure.http_api import (
    PublicForwarderHttpApi,
)
from platform.deployment_fargate.api_public_forwarder_infrastructure.lambda_function import (
    PublicForwarderFunction,
)
from platform.deployment_fargate.api_public_forwarder_infrastructure.monitoring import (
    PublicForwarderMonitoring,
)
from platform.deployment_fargate.infrastructure_exports import (
    PUBLIC_FORWARDER_FLEET_ENVIRONMENT,
    fleet_export_name,
)


class PublicForwarderApiStack(Stack):
    """Deploy the bearer-authorizer public `/v1/runs` API."""

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

        fleet = {
            env_name: Fn.import_value(fleet_export_name(env_name))
            for env_name in PUBLIC_FORWARDER_FLEET_ENVIRONMENT
        }
        resource_prefix = fleet["OPENSRE_FARGATE_RESOURCE_PREFIX"]

        public_function = PublicForwarderFunction(
            self,
            "PublicForwarderFunction",
            database_url_secret_arn=database_url_secret_arn.value_as_string,
            fleet=fleet,
            lambda_code=lambda_code,
        )
        function = public_function.function

        http_api = PublicForwarderHttpApi(
            self,
            "PublicForwarderHttpApi",
            function=function,
            resource_prefix=resource_prefix,
        )
        PublicForwarderMonitoring(
            self,
            "Monitoring",
            function=function,
            api_id=http_api.api_id,
        )

        CfnOutput(
            self,
            "PublicForwarderApiEndpoint",
            value=http_api.api_endpoint,
            description="Base URL for the public bearer-authenticated run API",
        )
        CfnOutput(
            self,
            "PublicForwarderFunctionArn",
            value=function.function_arn,
        )
