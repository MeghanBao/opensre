"""Lambda composition root and entrypoint for the control-plane runtime.

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

from platform.deployment_fargate.api_control_plane.db.db_client import ControlPlaneDbClient
from platform.deployment_fargate.api_control_plane.handler import ControlPlaneApi
from platform.deployment_fargate.api_control_plane.utils.ports import (
    AgentRunRepository,
    LifecycleService,
    TenantApiCredentialRepository,
)
from platform.deployment_fargate.api_public_forwarder.authorizer import (
    AwsSecretsManagerReader,
    BearerAuthorizer,
)
from platform.deployment_fargate.api_public_forwarder.handler import PublicApiHandler
from platform.deployment_fargate.utils.http_lambda import header, is_authorizer_event, response

_DEFAULT_LIFECYCLE_FACTORY = (
    "platform.deployment_fargate.api_control_plane.methods.fargate_container_service:"
    "create_fargate_container_lifecycle_from_environment"
)


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


def build_runtime_api(
    config: RuntimeConfig | None = None,
    *,
    lifecycle: LifecycleService | None = None,
    store: ControlPlaneRepository | None = None,
    secrets_client: Any | None = None,
) -> LambdaApp:
    """Build the production application using injected values or real SDK clients."""

    resolved = config or RuntimeConfig.from_environment()
    repository = store or ControlPlaneDbClient(
        resolved.database_url,
        initialize_schema=False,
    )
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
