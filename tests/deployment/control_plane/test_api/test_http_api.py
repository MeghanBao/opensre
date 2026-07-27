"""HTTP API bootstrap tests for the control-plane Lambda."""

from __future__ import annotations

from typing import Any

import pytest

from platform.deployment_fargate.api_control_plane.http_api import (
    ControlPlaneHttpApiBootstrap,
    HttpApiBootstrapConfig,
    build_boto3_http_api_bootstrap,
    lambda_authorizer_permission_request,
    lambda_authorizer_request,
    lambda_permission_request,
    route_requests,
)


class _ApiGateway:
    def __init__(self) -> None:
        self.api_requests: list[dict[str, Any]] = []
        self.integration_requests: list[dict[str, Any]] = []
        self.authorizer_requests: list[dict[str, Any]] = []
        self.route_requests: list[dict[str, Any]] = []
        self.stage_requests: list[dict[str, Any]] = []

    def create_api(self, **kwargs: Any) -> dict[str, Any]:
        self.api_requests.append(kwargs)
        return {"ApiId": "api123"}

    def create_integration(self, **kwargs: Any) -> dict[str, Any]:
        self.integration_requests.append(kwargs)
        return {"IntegrationId": "integration123"}

    def create_authorizer(self, **kwargs: Any) -> dict[str, Any]:
        self.authorizer_requests.append(kwargs)
        return {"AuthorizerId": "authorizer123"}

    def create_route(self, **kwargs: Any) -> dict[str, Any]:
        self.route_requests.append(kwargs)
        return {}

    def create_stage(self, **kwargs: Any) -> dict[str, Any]:
        self.stage_requests.append(kwargs)
        return {}


class _Lambda:
    def __init__(self) -> None:
        self.permissions: list[dict[str, Any]] = []

    def add_permission(self, **kwargs: Any) -> dict[str, Any]:
        self.permissions.append(kwargs)
        return {}


def _config() -> HttpApiBootstrapConfig:
    return HttpApiBootstrapConfig(
        api_name="opensre-control-plane",
        function_name="opensre-control-plane",
        function_arn=("arn:aws:lambda:eu-west-1:123456789012:function:opensre-control-plane"),
        region="eu-west-1",
        account_id="123456789012",
        tags={"opensre:managed": "true"},
    )


def test_route_builders_apply_iam_and_bearer_authorization_without_cache() -> None:
    routes = route_requests("api123", "integration123", "authorizer123")

    assert len(routes) == 8
    assert all(
        route["AuthorizationType"] == "AWS_IAM"
        for route in routes
        if "/organizations/" in route["RouteKey"]
    )
    assert all(
        route["AuthorizationType"] == "CUSTOM" and route["AuthorizerId"] == "authorizer123"
        for route in routes
        if "/v1/runs" in route["RouteKey"]
    )

    authorizer = lambda_authorizer_request(
        api_id="api123",
        function_arn=_config().function_arn,
        region="eu-west-1",
    )
    assert authorizer["AuthorizerResultTtlInSeconds"] == 0
    assert authorizer["IdentitySource"] == ["$request.header.Authorization"]
    assert authorizer["EnableSimpleResponses"] is True


def test_sdk_bootstrap_creates_lambda_integration_routes_stage_and_permission() -> None:
    api_gateway = _ApiGateway()
    lambda_client = _Lambda()

    result = ControlPlaneHttpApiBootstrap(api_gateway, lambda_client).create(_config())

    assert result.api_id == "api123"
    assert len(api_gateway.route_requests) == 8
    assert api_gateway.integration_requests == [
        {
            "ApiId": "api123",
            "IntegrationType": "AWS_PROXY",
            "IntegrationUri": _config().function_arn,
            "PayloadFormatVersion": "2.0",
            "TimeoutInMillis": 29_000,
        }
    ]
    assert api_gateway.stage_requests == [
        {
            "ApiId": "api123",
            "StageName": "$default",
            "AutoDeploy": True,
            "Tags": {"opensre:managed": "true"},
        }
    ]
    assert lambda_client.permissions == [
        lambda_permission_request(api_id="api123", config=_config()),
        lambda_authorizer_permission_request(
            api_id="api123",
            authorizer_id="authorizer123",
            config=_config(),
        ),
    ]
    assert "AWS_ACCESS_KEY_ID" not in repr(api_gateway.api_requests)


def test_boto3_bootstrap_uses_default_credential_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import boto3

    clients: list[tuple[str, str]] = []

    def client(service: str, *, region_name: str) -> object:
        clients.append((service, region_name))
        return object()

    monkeypatch.setattr(boto3, "client", client)

    bootstrap = build_boto3_http_api_bootstrap("eu-west-1")

    assert isinstance(bootstrap, ControlPlaneHttpApiBootstrap)
    assert clients == [("apigatewayv2", "eu-west-1"), ("lambda", "eu-west-1")]
