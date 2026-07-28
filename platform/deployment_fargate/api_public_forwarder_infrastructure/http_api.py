"""HTTP API, bearer authorizer, and public run route wiring."""

from __future__ import annotations

from aws_cdk import Fn, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigatewayv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

_RUN_ROUTES = (
    ("POST", "/v1/runs"),
    ("GET", "/v1/runs/{run_id}"),
)


class PublicForwarderHttpApi(Construct):
    """Expose bearer-protected run routes through the public forwarder Lambda."""

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
            name=Fn.join("", [resource_prefix, "-public-forwarder"]),
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
        authorizer = apigatewayv2.CfnAuthorizer(
            self,
            "TenantBearerAuthorizer",
            api_id=api.ref,
            name="opensre-tenant-bearer",
            authorizer_type="REQUEST",
            authorizer_uri=Fn.join(
                "",
                [
                    "arn:",
                    stack.partition,
                    ":apigateway:",
                    stack.region,
                    ":lambda:path/2015-03-31/functions/",
                    function.function_arn,
                    "/invocations",
                ],
            ),
            authorizer_payload_format_version="2.0",
            authorizer_result_ttl_in_seconds=0,
            enable_simple_responses=True,
            identity_source=["$request.header.Authorization"],
        )
        target = Fn.join("", ["integrations/", integration.ref])
        for index, (method, path) in enumerate(_RUN_ROUTES, start=1):
            apigatewayv2.CfnRoute(
                self,
                f"RunRoute{index}",
                api_id=api.ref,
                route_key=f"{method} {path}",
                target=target,
                authorization_type="CUSTOM",
                authorizer_id=authorizer.ref,
            )
        access_log_group = logs.LogGroup(
            self,
            "AccessLogGroup",
            log_group_name=Fn.join(
                "",
                ["/aws/apigateway/", resource_prefix, "-public-forwarder"],
            ),
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )
        apigatewayv2.CfnStage(
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

        function.add_permission(
            "AllowHttpApiInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=_execute_api_arn(stack, api.ref, "/*/*"),
        )
        function.add_permission(
            "AllowAuthorizerInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=_execute_api_arn(
                stack,
                api.ref,
                Fn.join("", ["/authorizers/", authorizer.ref]),
            ),
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
