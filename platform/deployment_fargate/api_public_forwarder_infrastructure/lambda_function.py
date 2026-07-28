"""Public-forwarder Lambda, logs, environment, and execution role."""

from __future__ import annotations

from collections.abc import Mapping

from aws_cdk import Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from platform.deployment_fargate.api_public_forwarder_infrastructure.lambda_bundle import (
    bundled_lambda_code,
)
from platform.deployment_fargate.api_public_forwarder_infrastructure.permissions import (
    grant_public_forwarder_permissions,
)

_LAMBDA_HANDLER = "platform.deployment_fargate.api_public_forwarder.runtime.lambda_handler"


class PublicForwarderFunction(Construct):
    """Build the Lambda used by public `/v1/runs` routes and the bearer authorizer."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        database_url_secret_arn: str,
        fleet: Mapping[str, str],
        lambda_code: lambda_.Code | None = None,
    ) -> None:
        super().__init__(scope, construct_id)
        stack = Stack.of(self)
        resource_prefix = fleet["OPENSRE_FARGATE_RESOURCE_PREFIX"]

        log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=Fn.join("", ["/aws/lambda/", resource_prefix, "-public-forwarder"]),
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )
        execution_role = iam.Role(
            self,
            "ExecutionRole",
            role_name=Fn.join("", [resource_prefix, "-public-forwarder"]),
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        grant_public_forwarder_permissions(
            stack,
            execution_role,
            fleet,
            database_url_secret_arn=database_url_secret_arn,
        )

        self.function = lambda_.Function(
            self,
            "Function",
            function_name=Fn.join("", [resource_prefix, "-public-forwarder"]),
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler=_LAMBDA_HANDLER,
            code=lambda_code or bundled_lambda_code(),
            role=execution_role,
            memory_size=512,
            timeout=Duration.seconds(29),
            environment={
                "OPENSRE_FARGATE_RESOURCE_PREFIX": resource_prefix,
                "OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN": database_url_secret_arn,
            },
        )
        self.function.node.add_dependency(log_group)
