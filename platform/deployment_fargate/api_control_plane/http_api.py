"""API Gateway HTTP API bootstrap around the control-plane Lambda."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

_LIFECYCLE_ROUTES = (
    ("PUT", "/v1/organizations/{organization_id}/gateway"),
    ("GET", "/v1/organizations/{organization_id}/gateway"),
    ("POST", "/v1/organizations/{organization_id}/gateway/start"),
    ("POST", "/v1/organizations/{organization_id}/gateway/stop"),
    ("DELETE", "/v1/organizations/{organization_id}/gateway"),
    ("POST", "/v1/organizations/{organization_id}/api-credential/rotate"),
)
_RUN_ROUTES = (
    ("POST", "/v1/runs"),
    ("GET", "/v1/runs/{run_id}"),
)


class ApiGatewayV2Client(Protocol):
    """API Gateway v2 methods used by the HTTP API bootstrap."""

    def create_api(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_integration(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_authorizer(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_route(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_stage(self, **kwargs: Any) -> dict[str, Any]: ...


class LambdaClient(Protocol):
    """Lambda method used to grant API Gateway invoke access."""

    def add_permission(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class HttpApiBootstrapConfig:
    """Inputs required to create the HTTP API around an existing Lambda."""

    api_name: str
    function_name: str
    function_arn: str
    region: str
    account_id: str
    tags: dict[str, str]


@dataclass(frozen=True, slots=True)
class HttpApiBootstrapResult:
    api_id: str
    integration_id: str
    authorizer_id: str


class ControlPlaneHttpApiBootstrap:
    """Compact SDK bootstrap for one HTTP API and Lambda integration."""

    def __init__(
        self,
        api_gateway: ApiGatewayV2Client,
        lambda_client: LambdaClient,
    ) -> None:
        self._api_gateway = api_gateway
        self._lambda = lambda_client

    def create(self, config: HttpApiBootstrapConfig) -> HttpApiBootstrapResult:
        """Create the API, integration, authorizer, routes, stage, and permission."""

        api_response = self._api_gateway.create_api(**http_api_request(config))
        api_id = _required_response_id(api_response, "ApiId")

        integration_response = self._api_gateway.create_integration(
            **lambda_integration_request(api_id, config.function_arn)
        )
        integration_id = _required_response_id(integration_response, "IntegrationId")

        authorizer_response = self._api_gateway.create_authorizer(
            **lambda_authorizer_request(
                api_id=api_id,
                function_arn=config.function_arn,
                region=config.region,
            )
        )
        authorizer_id = _required_response_id(authorizer_response, "AuthorizerId")

        for request in route_requests(api_id, integration_id, authorizer_id):
            self._api_gateway.create_route(**request)
        self._api_gateway.create_stage(
            ApiId=api_id,
            StageName="$default",
            AutoDeploy=True,
            Tags=config.tags,
        )
        self._lambda.add_permission(**lambda_permission_request(api_id=api_id, config=config))
        self._lambda.add_permission(
            **lambda_authorizer_permission_request(
                api_id=api_id,
                authorizer_id=authorizer_id,
                config=config,
            )
        )
        return HttpApiBootstrapResult(
            api_id=api_id,
            integration_id=integration_id,
            authorizer_id=authorizer_id,
        )


def http_api_request(config: HttpApiBootstrapConfig) -> dict[str, Any]:
    return {
        "Name": config.api_name,
        "ProtocolType": "HTTP",
        "DisableExecuteApiEndpoint": False,
        "Tags": config.tags,
    }


def lambda_integration_request(api_id: str, function_arn: str) -> dict[str, Any]:
    return {
        "ApiId": api_id,
        "IntegrationType": "AWS_PROXY",
        "IntegrationUri": function_arn,
        "PayloadFormatVersion": "2.0",
        "TimeoutInMillis": 29_000,
    }


def lambda_authorizer_request(
    *,
    api_id: str,
    function_arn: str,
    region: str,
) -> dict[str, Any]:
    authorizer_uri = (
        f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{function_arn}/invocations"
    )
    return {
        "ApiId": api_id,
        "Name": "opensre-tenant-bearer",
        "AuthorizerType": "REQUEST",
        "AuthorizerUri": authorizer_uri,
        "AuthorizerPayloadFormatVersion": "2.0",
        "EnableSimpleResponses": True,
        "AuthorizerResultTtlInSeconds": 0,
        "IdentitySource": ["$request.header.Authorization"],
    }


def route_requests(
    api_id: str,
    integration_id: str,
    authorizer_id: str,
) -> tuple[dict[str, Any], ...]:
    target = f"integrations/{integration_id}"
    lifecycle = tuple(
        {
            "ApiId": api_id,
            "RouteKey": f"{method} {path}",
            "Target": target,
            "AuthorizationType": "AWS_IAM",
        }
        for method, path in _LIFECYCLE_ROUTES
    )
    runs = tuple(
        {
            "ApiId": api_id,
            "RouteKey": f"{method} {path}",
            "Target": target,
            "AuthorizationType": "CUSTOM",
            "AuthorizerId": authorizer_id,
        }
        for method, path in _RUN_ROUTES
    )
    return lifecycle + runs


def lambda_permission_request(
    *,
    api_id: str,
    config: HttpApiBootstrapConfig,
) -> dict[str, Any]:
    return {
        "FunctionName": config.function_name,
        "StatementId": f"opensre-http-api-{api_id}",
        "Action": "lambda:InvokeFunction",
        "Principal": "apigateway.amazonaws.com",
        "SourceArn": (f"arn:aws:execute-api:{config.region}:{config.account_id}:{api_id}/*/*"),
    }


def lambda_authorizer_permission_request(
    *,
    api_id: str,
    authorizer_id: str,
    config: HttpApiBootstrapConfig,
) -> dict[str, Any]:
    return {
        "FunctionName": config.function_name,
        "StatementId": f"opensre-http-authorizer-{api_id}",
        "Action": "lambda:InvokeFunction",
        "Principal": "apigateway.amazonaws.com",
        "SourceArn": (
            f"arn:aws:execute-api:{config.region}:{config.account_id}:"
            f"{api_id}/authorizers/{authorizer_id}"
        ),
    }


def build_boto3_http_api_bootstrap(region: str) -> ControlPlaneHttpApiBootstrap:
    """Use the standard boto3 chain locally and workload identity in AWS."""

    if not region.strip():
        raise ValueError("AWS region is required")
    import boto3

    return ControlPlaneHttpApiBootstrap(
        cast(ApiGatewayV2Client, boto3.client("apigatewayv2", region_name=region)),
        cast(LambdaClient, boto3.client("lambda", region_name=region)),
    )


def _required_response_id(response: dict[str, Any], key: str) -> str:
    value = response.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"AWS response did not contain {key}")
    return value
