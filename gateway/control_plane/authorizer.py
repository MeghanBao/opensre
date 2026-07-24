"""Authorization boundaries for the Fargate control-plane Lambda."""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Any, Protocol

from gateway.control_plane.contracts import (
    TenantApiCredential,
    TenantApiCredentialRepository,
)

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

        credential = self._credentials.get_api_credential(match.group("key_id"))
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


def iam_principal_is_allowed(
    event: dict[str, Any],
    allowed_role_arns: frozenset[str],
) -> bool:
    """Validate API Gateway's authenticated IAM principal against a role allowlist."""

    if not allowed_role_arns:
        return False
    principal_arn = _iam_principal_arn(event)
    if principal_arn is None:
        return False
    return any(
        hmac.compare_digest(principal_arn, allowed) or _is_session_for_role(principal_arn, allowed)
        for allowed in allowed_role_arns
    )


def _iam_principal_arn(event: dict[str, Any]) -> str | None:
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        return None
    authorizer = request_context.get("authorizer")
    if isinstance(authorizer, dict):
        iam = authorizer.get("iam")
        if isinstance(iam, dict) and isinstance(iam.get("userArn"), str):
            return str(iam["userArn"])
    identity = request_context.get("identity")
    if isinstance(identity, dict) and isinstance(identity.get("userArn"), str):
        return str(identity["userArn"])
    return None


def _is_session_for_role(principal_arn: str, role_arn: str) -> bool:
    role_prefix = "arn:aws:iam::"
    if not role_arn.startswith(role_prefix):
        return False
    account_and_resource = role_arn.removeprefix(role_prefix)
    account_id, separator, resource = account_and_resource.partition(":")
    if separator != ":" or not resource.startswith("role/"):
        return False
    role_path_and_name = resource.removeprefix("role/")
    role_name = role_path_and_name.rsplit("/", 1)[-1]
    session_prefix = f"arn:aws:sts::{account_id}:assumed-role/{role_name}/"
    path_preserving_prefix = f"arn:aws:sts::{account_id}:assumed-role/{role_path_and_name}/"
    return principal_arn.startswith((session_prefix, path_preserving_prefix))
