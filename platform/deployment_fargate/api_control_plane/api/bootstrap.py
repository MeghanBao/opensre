"""Runtime and infrastructure bootstrap for the deployment Lambda.

No AWS or database call happens at module import. boto3 uses its standard
credential provider chain locally and the Lambda execution role in AWS.
"""

from __future__ import annotations

import importlib
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from platform.deployment_fargate.api_control_plane.api.handler import ControlPlaneApi
from platform.deployment_fargate.api_control_plane.contracts.contracts import (
    AgentRunRepository,
    TenantApiCredentialRepository,
)
from platform.deployment_fargate.api_control_plane.contracts.lifecycle_service import LifecycleService
from platform.deployment_fargate.api_control_plane.store.postgres_store import PostgresControlPlaneStore
from platform.deployment_fargate.http_lambda import header, is_authorizer_event, response
from platform.deployment_fargate.public_api.authorizer import AwsSecretsManagerReader, BearerAuthorizer
from platform.deployment_fargate.public_api.handler import PublicApiHandler

_DEFAULT_LIFECYCLE_FACTORY = (
    "platform.deployment_fargate.api_control_plane.reconciler.reconcile:create_reconciler_from_environment"
)
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
    """API Gateway v2 methods used by the deployment bootstrap."""

    def create_api(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_integration(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_authorizer(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_route(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_stage(self, **kwargs: Any) -> dict[str, Any]: ...


class LambdaClient(Protocol):
    """Lambda method used to grant API Gateway invoke access."""

    def add_permission(self, **kwargs: Any) -> dict[str, Any]: ...


class ControlPlaneRepository(
    AgentRunRepository,
    TenantApiCredentialRepository,
    Protocol,
):
    """Combined repository capabilities required by the Lambda."""


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Required production runtime configuration."""

    database_url: str
    lifecycle_role_arns: frozenset[str]
    aws_region: str
    lifecycle_factory: str = _DEFAULT_LIFECYCLE_FACTORY

    @classmethod
    def from_environment(cls) -> RuntimeConfig:
        database_url = _required_environment("DATABASE_URL")
        region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "")).strip()
        if not region:
            raise RuntimeError("AWS_REGION is required")
        raw_roles = _required_environment("OPENSRE_CONTROL_PLANE_LIFECYCLE_ROLE_ARNS")
        role_arns = frozenset(role.strip() for role in raw_roles.split(",") if role.strip())
        if not role_arns or any(":role/" not in role for role in role_arns):
            raise RuntimeError("At least one valid lifecycle IAM role ARN is required")
        lifecycle_factory = os.getenv(
            "OPENSRE_CONTROL_PLANE_LIFECYCLE_FACTORY",
            _DEFAULT_LIFECYCLE_FACTORY,
        ).strip()
        if not lifecycle_factory:
            raise RuntimeError("Lifecycle factory is required")
        return cls(
            database_url=database_url,
            lifecycle_role_arns=role_arns,
            aws_region=region,
            lifecycle_factory=lifecycle_factory,
        )


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


class LambdaApp:
    """Thin composition root routing lifecycle, public API, and authorizer events."""

    def __init__(
        self,
        *,
        control_plane: ControlPlaneApi,
        public_api: PublicApiHandler,
        bearer_authorizer: BearerAuthorizer,
    ) -> None:
        self._control_plane = control_plane
        self._public_api = public_api
        self._bearer_authorizer = bearer_authorizer

    def handle(self, event: dict[str, Any], context: object = None) -> dict[str, Any]:
        if is_authorizer_event(event):
            return self.authorize_run_route(event)
        path = event.get("rawPath") or event.get("path")
        if isinstance(path, str) and path.startswith("/v1/organizations/"):
            return self._control_plane.handle(event, context)
        if isinstance(path, str) and (path == "/v1/runs" or path.startswith("/v1/runs/")):
            return self._public_api.handle(event, context)
        return response(404, {"error": "not_found"})

    def authorize_run_route(self, event: dict[str, Any]) -> dict[str, Any]:
        authorization = header(event, "authorization")
        tenant = self._bearer_authorizer.authorize(authorization)
        if tenant is None:
            return {"isAuthorized": False}
        return {
            "isAuthorized": True,
            "context": {
                "organization_id": tenant.organization_id,
                "key_id": tenant.key_id,
            },
        }


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


def build_runtime_api(
    config: RuntimeConfig | None = None,
    *,
    lifecycle: LifecycleService | None = None,
    store: ControlPlaneRepository | None = None,
    secrets_client: Any | None = None,
) -> LambdaApp:
    """Build the production application using injected values or real SDK clients."""

    resolved = config or RuntimeConfig.from_environment()
    repository = store or PostgresControlPlaneStore(resolved.database_url)
    if secrets_client is None:
        import boto3

        secrets_client = boto3.client("secretsmanager", region_name=resolved.aws_region)
    lifecycle_service = lifecycle or _load_lifecycle_factory(resolved.lifecycle_factory)()
    bearer_authorizer = BearerAuthorizer(
        repository,
        AwsSecretsManagerReader(secrets_client),
    )
    return LambdaApp(
        control_plane=ControlPlaneApi(
            lifecycle=lifecycle_service,
            allowed_lifecycle_role_arns=resolved.lifecycle_role_arns,
        ),
        public_api=PublicApiHandler(runs=repository),
        bearer_authorizer=bearer_authorizer,
    )


_runtime_api: LambdaApp | None = None
_runtime_lock = threading.Lock()


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """AWS Lambda entry point, initialized lazily on the first invocation."""

    global _runtime_api
    with _runtime_lock:
        if _runtime_api is None:
            _runtime_api = build_runtime_api()
    return _runtime_api.handle(event, context)


def _load_lifecycle_factory(path: str) -> Callable[[], LifecycleService]:
    module_name, separator, attribute_name = path.partition(":")
    if separator != ":" or not module_name or not attribute_name:
        raise RuntimeError("Lifecycle factory must use module:function syntax")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute_name, None)
    if not callable(factory):
        raise RuntimeError("Lifecycle factory is not callable")
    return cast(Callable[[], LifecycleService], factory)


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _required_response_id(response: dict[str, Any], key: str) -> str:
    value = response.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"AWS response did not contain {key}")
    return value
