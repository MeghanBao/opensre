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
from typing import Any, cast

from platform.deployment_fargate.api_control_plane.handler import ControlPlaneApi
from platform.deployment_fargate.api_control_plane.utils.ports import LifecycleService
from platform.deployment_fargate.utils.http_lambda import response

_DEFAULT_LIFECYCLE_FACTORY = (
    "platform.deployment_fargate.api_control_plane.methods.fargate_container_service:"
    "create_fargate_container_lifecycle_from_environment"
)
_DATABASE_URL_SECRET_ARN_ENV = "OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Required production runtime configuration."""

    database_url: str
    lifecycle_role_arns: frozenset[str]
    aws_region: str
    lifecycle_factory: str = _DEFAULT_LIFECYCLE_FACTORY
    database_url_secret_arn: str | None = None

    @classmethod
    def from_environment(cls) -> RuntimeConfig:
        database_url = os.getenv("DATABASE_URL", "").strip()
        database_url_secret_arn = os.getenv(_DATABASE_URL_SECRET_ARN_ENV, "").strip()
        if not database_url and not database_url_secret_arn:
            raise RuntimeError(f"DATABASE_URL or {_DATABASE_URL_SECRET_ARN_ENV} is required")
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
            database_url_secret_arn=database_url_secret_arn or None,
        )


class LambdaApp:
    """Thin composition root routing lifecycle API events."""

    def __init__(self, *, control_plane: ControlPlaneApi) -> None:
        self._control_plane = control_plane

    def handle(self, event: dict[str, Any], context: object = None) -> dict[str, Any]:
        path = event.get("rawPath") or event.get("path")
        if isinstance(path, str) and path.startswith("/v1/organizations/"):
            return self._control_plane.handle(event, context)
        return response(404, {"error": "not_found"})


def build_runtime_api(
    config: RuntimeConfig | None = None,
    *,
    lifecycle: LifecycleService | None = None,
) -> LambdaApp:
    """Build the production application using injected values or real SDK clients."""

    resolved = config or RuntimeConfig.from_environment()
    if resolved.database_url_secret_arn and not os.getenv("DATABASE_URL", "").strip():
        import boto3

        secrets_client = boto3.client("secretsmanager", region_name=resolved.aws_region)
        os.environ["DATABASE_URL"] = _secret_string(
            secrets_client,
            resolved.database_url_secret_arn,
        )
    lifecycle_service = lifecycle or _load_lifecycle_factory(resolved.lifecycle_factory)()
    return LambdaApp(
        control_plane=ControlPlaneApi(
            lifecycle=lifecycle_service,
            allowed_lifecycle_role_arns=resolved.lifecycle_role_arns,
        ),
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


def _secret_string(secrets_client: Any, secret_arn: str | None) -> str:
    if not secret_arn:
        raise RuntimeError("Database secret ARN is required")
    secret = secrets_client.get_secret_value(SecretId=secret_arn).get("SecretString")
    if not isinstance(secret, str) or not secret:
        raise RuntimeError("Database secret must contain SecretString")
    return secret
