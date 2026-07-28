"""Lambda composition root for the public run API and bearer authorizer."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from platform.deployment_fargate.api_control_plane.db.db_client import ControlPlaneDbClient
from platform.deployment_fargate.api_control_plane.utils.ports import (
    AgentRunRepository,
    TenantApiCredentialRepository,
)
from platform.deployment_fargate.api_public_forwarder.authorizer import (
    AwsSecretsManagerReader,
    BearerAuthorizer,
)
from platform.deployment_fargate.api_public_forwarder.handler import PublicApiHandler
from platform.deployment_fargate.utils.http_lambda import header, is_authorizer_event, response

_DATABASE_URL_SECRET_ARN_ENV = "OPENSRE_CONTROL_PLANE_DATABASE_URL_SECRET_ARN"


class PublicForwarderRepository(
    AgentRunRepository,
    TenantApiCredentialRepository,
    Protocol,
):
    """Repository capabilities required by the public forwarder Lambda."""


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Required production runtime configuration."""

    database_url: str
    aws_region: str
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
        return cls(
            database_url=database_url,
            aws_region=region,
            database_url_secret_arn=database_url_secret_arn or None,
        )


class LambdaApp:
    """Composition root for public `/v1/runs` routes and the REQUEST authorizer."""

    def __init__(
        self,
        *,
        public_api: PublicApiHandler,
        bearer_authorizer: BearerAuthorizer,
    ) -> None:
        self._public_api = public_api
        self._bearer_authorizer = bearer_authorizer

    def handle(self, event: dict[str, Any], context: object = None) -> dict[str, Any]:
        if is_authorizer_event(event):
            return self.authorize_run_route(event)
        path = event.get("rawPath") or event.get("path")
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
    store: PublicForwarderRepository | None = None,
    secrets_client: Any | None = None,
) -> LambdaApp:
    """Build the production application using injected values or real SDK clients."""

    resolved = config or RuntimeConfig.from_environment()
    if secrets_client is None:
        import boto3

        secrets_client = boto3.client("secretsmanager", region_name=resolved.aws_region)
    database_url = resolved.database_url or _secret_string(
        secrets_client,
        resolved.database_url_secret_arn,
    )
    if not os.getenv("DATABASE_URL", "").strip():
        os.environ["DATABASE_URL"] = database_url
    repository = store or ControlPlaneDbClient(
        database_url,
        initialize_schema=False,
    )
    return LambdaApp(
        public_api=PublicApiHandler(runs=repository),
        bearer_authorizer=BearerAuthorizer(
            repository,
            AwsSecretsManagerReader(secrets_client),
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


def _secret_string(secrets_client: Any, secret_arn: str | None) -> str:
    if not secret_arn:
        raise RuntimeError("Database secret ARN is required")
    secret = secrets_client.get_secret_value(SecretId=secret_arn).get("SecretString")
    if not isinstance(secret, str) or not secret:
        raise RuntimeError("Database secret must contain SecretString")
    return secret
