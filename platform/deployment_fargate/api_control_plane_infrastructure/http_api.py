"""HTTP API and IAM-protected lifecycle route wiring."""

from __future__ import annotations

from aws_cdk import Fn, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_apigatewayv2 as apigatewayv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

_LIFECYCLE_ROUTES = (
    ("PUT", "/v1/organizations/{organization_id}/gateway"),
    ("GET", "/v1/organizations/{organization_id}/gateway"),
    ("POST", "/v1/organizations/{organization_id}/gateway/start"),
    ("POST", "/v1/organizations/{organization_id}/gateway/stop"),
    ("DELETE", "/v1/organizations/{organization_id}/gateway"),
    ("POST", "/v1/organizations/{organization_id}/api-credential/rotate"),
)


class ControlPlaneHttpApi(Construct):
    """Expose lifecycle routes through the control-plane Lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        function: lambda_.Function,
        resource_prefix: str,
    ) -> None:
        super().__init__(scope, construct_id)
        stack = Stack.of(self)

        api = apigatewayv2.CfnApi(
            self,
            "Api",
            name=Fn.join("", [resource_prefix, "-control-plane"]),
            protocol_type="HTTP",
            disable_execute_api_endpoint=False,
        )
        integration = apigatewayv2.CfnIntegration(
            self,
            "Integration",
            api_id=api.ref,
            integration_type="AWS_PROXY",
            integration_uri=function.function_arn,
            payload_format_version="2.0",
            timeout_in_millis=29_000,
        )
        target = Fn.join("", ["integrations/", integration.ref])
        for index, (method, path) in enumerate(_LIFECYCLE_ROUTES, start=1):
            apigatewayv2.CfnRoute(
                self,
                f"LifecycleRoute{index}",
                api_id=api.ref,
                route_key=f"{method} {path}",
                target=target,
                authorization_type="AWS_IAM",
            )
        access_log_group = logs.LogGroup(
            self,
            "AccessLogGroup",
            log_group_name=Fn.join("", ["/aws/apigateway/", resource_prefix, "-control-plane"]),
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )
        api_gateway_logs_role = iam.Role(
            self,
            "CloudWatchLogsRole",
            assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonAPIGatewayPushToCloudWatchLogs"
                )
            ],
        )
        api_gateway_account = apigateway.CfnAccount(
            self,
            "ApiGatewayAccount",
            cloud_watch_role_arn=api_gateway_logs_role.role_arn,
        )
        stage = apigatewayv2.CfnStage(
            self,
            "DefaultStage",
            api_id=api.ref,
            stage_name="$default",
            auto_deploy=True,
            access_log_settings=apigatewayv2.CfnStage.AccessLogSettingsProperty(
                destination_arn=access_log_group.log_group_arn,
                format=(
                    '{"requestId":"$context.requestId","routeKey":"$context.routeKey",'
                    '"status":"$context.status","responseLength":"$context.responseLength",'
                    '"integrationError":"$context.integrationErrorMessage"}'
                ),
            ),
        )
        stage.add_resource_dependency(api_gateway_account)

        function.add_permission(
            "AllowHttpApiInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=_execute_api_arn(stack, api.ref, "/*/*"),
        )

        self.api_endpoint = api.attr_api_endpoint
        self.api_id = api.ref


def _execute_api_arn(stack: Stack, api_id: str, suffix: str) -> str:
    return Fn.join(
        "",
        [
            "arn:",
            stack.partition,
            ":execute-api:",
            stack.region,
            ":",
            stack.account,
            ":",
            api_id,
            suffix,
        ],
    )
