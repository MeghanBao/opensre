"""Fail-closed startup hydration of tenant integration credentials."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from config.constants.credentials import (
    CREDENTIALS_API_URL_ENV,
    CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV,
    FLEET_ORGANIZATION_ID_ENV,
    INTEGRATIONS_SECRET_ARN_ENV,
    INTEGRATIONS_STORE_PATH_ENV,
)
from integrations.credentials_api import (
    CredentialsApiClient,
    CredentialsApiError,
    IntegrationStoreV2,
    hydrate_integration_store,
)
from integrations.store import replace_integrations

logger = logging.getLogger(__name__)

_AWSCURRENT = "AWSCURRENT"


class SecretsManagerClient(Protocol):
    """Narrow boto3 Secrets Manager surface used at Gateway startup."""

    def get_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        """Return a secret version (callers pass VersionStage when required)."""


@dataclass(frozen=True, slots=True)
class GatewayBootstrap:
    """Decrypted bootstrap values held in memory for this process only."""

    credentials_api_token: str | None = None
    database_url: str | None = None
    integrations_hydrated: bool = False


@dataclass(frozen=True, slots=True)
class CredentialHydrationConfig:
    """Non-secret references required to hydrate one tenant."""

    organization_id: str
    bootstrap_secret_arn: str
    credentials_api_url: str | None = None
    integrations_secret_arn: str | None = None
    integrations_store_path: str | None = None

    @classmethod
    def from_environment(cls) -> CredentialHydrationConfig | None:
        """Return ``None`` when disabled, and reject partial configuration."""
        organization_id = os.getenv(FLEET_ORGANIZATION_ID_ENV, "").strip()
        bootstrap_secret_arn = os.getenv(CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV, "").strip()
        credentials_api_url = os.getenv(CREDENTIALS_API_URL_ENV, "").strip()
        integrations_secret_arn = os.getenv(INTEGRATIONS_SECRET_ARN_ENV, "").strip()
        integrations_store_path = os.getenv(INTEGRATIONS_STORE_PATH_ENV, "").strip()

        if (
            not organization_id
            and not bootstrap_secret_arn
            and not credentials_api_url
            and not integrations_secret_arn
        ):
            return None
        if not organization_id or not bootstrap_secret_arn:
            raise ValueError("Credential hydration configuration is incomplete")
        if credentials_api_url and not credentials_api_url.lower().startswith("https://"):
            raise ValueError("Credentials API URL must use HTTPS")
        if integrations_secret_arn and not integrations_store_path:
            raise ValueError("Integrations secret hydration requires a private store path")
        return cls(
            organization_id=organization_id,
            bootstrap_secret_arn=bootstrap_secret_arn,
            credentials_api_url=credentials_api_url or None,
            integrations_secret_arn=integrations_secret_arn or None,
            integrations_store_path=integrations_store_path or None,
        )


def _parse_bootstrap_secret(secret_string: str) -> GatewayBootstrap:
    """Accept a legacy raw token or a secret-safe JSON bootstrap bundle."""
    if not secret_string:
        raise ValueError("Bootstrap secret is empty")
    try:
        value = json.loads(secret_string)
    except json.JSONDecodeError:
        return GatewayBootstrap(credentials_api_token=secret_string)
    if not isinstance(value, dict):
        raise ValueError("Bootstrap secret has an invalid shape")
    token = value.get("credentials_api_token")
    database_url = value.get("database_url")
    if token is not None and (not isinstance(token, str) or not token):
        raise ValueError("Bootstrap secret has an invalid shape")
    if database_url is not None and (not isinstance(database_url, str) or not database_url):
        raise ValueError("Bootstrap secret has an invalid shape")
    if token is None and database_url is None:
        raise ValueError("Bootstrap secret has an invalid shape")
    return GatewayBootstrap(credentials_api_token=token, database_url=database_url)


def _materialize_ephemeral_store(store_path: str, integrations: list[dict[str, Any]]) -> None:
    """Write the validated store under a private ephemeral path with tight modes.

    The ECS task already sets ``OPENSRE_INTEGRATIONS_STORE_PATH``; this helper
    does not mutate process environment so tests stay isolated.
    """
    from integrations import store as integrations_store

    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    previous = integrations_store.STORE_PATH
    integrations_store.STORE_PATH = path
    try:
        replace_integrations(integrations)
    finally:
        integrations_store.STORE_PATH = previous
    with contextlib.suppress(OSError):
        path.chmod(0o600)


class GatewayCredentialHydrator:
    """Fetch allowed secrets, then materialize the validated local v2 store."""

    def __init__(
        self,
        *,
        config: CredentialHydrationConfig,
        secrets_client: SecretsManagerClient,
    ) -> None:
        self._config = config
        self._secrets_client = secrets_client

    @classmethod
    def from_environment(cls) -> GatewayCredentialHydrator | None:
        """Compose the production hydrator from task-role AWS credentials."""
        config = CredentialHydrationConfig.from_environment()
        if config is None:
            return None
        import boto3

        return cls(config=config, secrets_client=boto3.client("secretsmanager"))

    def hydrate(self) -> GatewayBootstrap:
        """Hydrate credentials atomically before any runtime component starts."""
        response = self._secrets_client.get_secret_value(
            SecretId=self._config.bootstrap_secret_arn,
            VersionStage=_AWSCURRENT,
        )
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise ValueError("Bootstrap secret has no string value")
        bootstrap = _parse_bootstrap_secret(secret_string)

        if self._config.integrations_secret_arn is not None:
            self._hydrate_from_integrations_secret()
            return replace(bootstrap, integrations_hydrated=True)

        if self._config.credentials_api_url is None:
            return bootstrap
        if bootstrap.credentials_api_token is None:
            raise ValueError("Bootstrap secret has no credentials API token")
        logger.warning(
            "Hydrating integrations via legacy credentials API; "
            "prefer OPENSRE_INTEGRATIONS_SECRET_ARN"
        )
        with CredentialsApiClient(
            base_url=self._config.credentials_api_url,
            bootstrap_credential=bootstrap.credentials_api_token,
        ) as client:
            hydrate_integration_store(
                client=client,
                organization_id=self._config.organization_id,
            )
        return replace(bootstrap, integrations_hydrated=True)

    def _hydrate_from_integrations_secret(self) -> None:
        assert self._config.integrations_secret_arn is not None
        assert self._config.integrations_store_path is not None
        response = self._secrets_client.get_secret_value(
            SecretId=self._config.integrations_secret_arn,
            VersionStage=_AWSCURRENT,
        )
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str) or not secret_string:
            raise ValueError("Integrations secret has no string value")
        try:
            payload = json.loads(secret_string)
            validated = IntegrationStoreV2.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, CredentialsApiError):
            raise ValueError("Integrations secret has an invalid shape") from None
        store_data = validated.as_store_data()
        integrations = store_data["integrations"]
        if not isinstance(integrations, list):
            raise ValueError("Integrations secret has an invalid shape")
        _materialize_ephemeral_store(self._config.integrations_store_path, integrations)


__all__ = [
    "CredentialHydrationConfig",
    "GatewayBootstrap",
    "GatewayCredentialHydrator",
    "SecretsManagerClient",
]
