"""Authorization boundaries for the public API Lambda routes."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Any, Protocol

from platform.deployment_fargate.api_control_plane.utils.models import TenantApiCredential
from platform.deployment_fargate.api_control_plane.utils.ports import TenantApiCredentialRepository

_BEARER_PATTERN = re.compile(
    r"^osre_(?P<key_id>[A-Za-z0-9_-]{8,64})\.(?P<secret>[A-Za-z0-9_-]{32,256})$"
)


class SecretReader(Protocol):
    """Minimal Secrets Manager read boundary."""

    def get_secret(self, secret_arn: str) -> str:
        """Return the exact plaintext stored at ``secret_arn``."""


class AwsSecretsManagerReader:
    """Secrets Manager adapter that never logs returned values."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get_secret(self, secret_arn: str) -> str:
        response = self._client.get_secret_value(SecretId=secret_arn)
        value = response.get("SecretString")
        if not isinstance(value, str) or not value:
            raise ValueError("Public API credential secret must contain SecretString")
        return value


@dataclass(frozen=True, slots=True)
class AuthorizedTenant:
    """Trusted tenant identity derived from a valid public bearer credential."""

    organization_id: str
    key_id: str


class BearerAuthorizer:
    """Resolve opaque public bearer credentials without storing them in Neon."""

    def __init__(
        self,
        credentials: TenantApiCredentialRepository,
        secrets: SecretReader,
    ) -> None:
        self._credentials = credentials
        self._secrets = secrets

    def authorize(self, authorization_header: str | None) -> AuthorizedTenant | None:
        token = _bearer_token(authorization_header)
        if token is None:
            return None
        match = _BEARER_PATTERN.fullmatch(token)
        if match is None:
            return None

        credential = self._credentials.fetch_tenant_api_credential_by_key_id(match.group("key_id"))
        if not _credential_can_authorize(credential):
            return None

        assert credential is not None
        try:
            expected_token = self._secrets.get_secret(credential.secret_arn)
        except Exception:  # noqa: BLE001 - auth failures are deliberately indistinguishable
            return None
        if not hmac.compare_digest(token, expected_token):
            return None
        return AuthorizedTenant(
            organization_id=credential.organization_id,
            key_id=credential.key_id,
        )


def _bearer_token(authorization_header: str | None) -> str | None:
    if authorization_header is None:
        return None
    scheme, separator, token = authorization_header.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    if token != token.strip() or " " in token:
        return None
    return token


def _credential_can_authorize(credential: TenantApiCredential | None) -> bool:
    return credential is not None and credential.enabled
