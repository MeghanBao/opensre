"""Control-plane Lambda, logs, environment, and execution role."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from aws_cdk import Duration, Fn, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from platform.deployment_fargate.api_control_plane_infrastructure.lambda_bundle import (
    bundled_lambda_code,
)
from platform.deployment_fargate.api_control_plane_infrastructure.permissions import (
    grant_lifecycle_permissions,
)

_LAMBDA_HANDLER = "platform.deployment_fargate.api_control_plane.runtime.lambda_handler"


class ControlPlaneFunction(Construct):
    """Build the Lambda used by IAM-protected lifecycle routes."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        database_url_secret_arn: str,
        lifecycle_role_arns: Sequence[str],
        fleet: Mapping[str, str],
        lambda_code: lambda_.Code | None = None,
    ) -> None:
        super().__init__(scope, construct_id)
        stack = Stack.of(self)
        resource_prefix = fleet["OPENSRE_FARGATE_RESOURCE_PREFIX"]

        log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=Fn.join("", ["/aws/lambda/", resource_prefix, "-control-plane"]),
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )
        execution_role = iam.Role(
            self,
            "ExecutionRole",
            role_name=Fn.join("", [resource_prefix, "-control-plane"]),
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        grant_lifecycle_permissions(stack, execution_role, fleet)
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[database_url_secret_arn],
            )
        )

        environment = {
            **fleet,
            "OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN": database_url_secret_arn,
            "OPENSRE_CONTROL_PLANE_LIFECYCLE_ROLE_ARNS": Fn.join(
                ",",
                lifecycle_role_arns,
            ),
        }
        self.function = lambda_.Function(
            self,
            "Function",
            function_name=Fn.join("", [resource_prefix, "-control-plane"]),
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler=_LAMBDA_HANDLER,
            code=lambda_code or bundled_lambda_code(),
            role=execution_role,
            memory_size=512,
            timeout=Duration.seconds(29),
            environment=environment,
        )
        self.function.node.add_dependency(log_group)
