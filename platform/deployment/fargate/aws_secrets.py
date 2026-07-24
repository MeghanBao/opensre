"""Secrets Manager operations for tenant bootstrap and public API credentials."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from platform.deployment.fargate.aws_types import SecretsManagerClient, aws_iam_tags

_DISABLED_POLICY_SID = "OpenSreTenantCredentialDisabled"


@dataclass(frozen=True)
class TenantPublicApiCredential:
    """One newly generated bearer credential; plaintext is returned only once."""

    key_id: str
    secret_arn: str
    bearer_token: str


class TenantSecretsAdapter:
    """Store and rotate tenant credentials without placing values in task metadata."""

    def __init__(
        self,
        client: SecretsManagerClient,
        *,
        token_factory: Callable[[int], str] = secrets.token_urlsafe,
    ) -> None:
        self._client = client
        self._token_factory = token_factory

    def require_bootstrap_secret(self, secret_id: str) -> str:
        """Resolve an existing bootstrap secret to an ARN without reading its value."""
        response = self._client.describe_secret(SecretId=secret_id)
        return self._required_string(response, "ARN")

    def create_public_api_credential(
        self,
        *,
        secret_name: str,
        key_id: str,
        tags: dict[str, str],
    ) -> TenantPublicApiCredential:
        """Generate and persist a public API bearer secret."""
        bearer_token = self._new_bearer_token(key_id)
        response = self._client.create_secret(
            Name=secret_name,
            Description="OpenSRE tenant public API bearer credential",
            SecretString=bearer_token,
            Tags=aws_iam_tags(tags),
        )
        return TenantPublicApiCredential(
            key_id=key_id,
            secret_arn=self._required_string(response, "ARN"),
            bearer_token=bearer_token,
        )

    def rotate_public_api_credential(
        self,
        *,
        secret_id: str,
        key_id: str,
    ) -> TenantPublicApiCredential:
        """Write a new bearer version and return it once to the lifecycle caller."""
        bearer_token = self._new_bearer_token(key_id)
        response = self._client.put_secret_value(
            SecretId=secret_id,
            SecretString=bearer_token,
        )
        secret_arn = response.get("ARN")
        if not isinstance(secret_arn, str) or not secret_arn:
            secret_arn = self.require_bootstrap_secret(secret_id)
        return TenantPublicApiCredential(
            key_id=key_id,
            secret_arn=secret_arn,
            bearer_token=bearer_token,
        )

    def ensure_secret_tags(self, secret_arn: str, tags: dict[str, str]) -> None:
        """Update non-secret ownership tags."""
        self._client.tag_resource(SecretId=secret_arn, Tags=aws_iam_tags(tags))

    def describe_secret_arn(self, secret_id: str) -> str:
        """Resolve any managed secret identifier to its ARN without reading it."""
        response = self._client.describe_secret(SecretId=secret_id)
        return self._required_string(response, "ARN")

    def disable_secret_access(self, secret_id: str) -> None:
        """Add an explicit read deny while preserving unrelated resource policy."""
        policy = self._resource_policy(secret_id)
        statements = self._statements_without_disabled_deny(policy)
        statements.append(
            {
                "Sid": _DISABLED_POLICY_SID,
                "Effect": "Deny",
                "Principal": {"AWS": "*"},
                "Action": "secretsmanager:GetSecretValue",
                "Resource": "*",
            }
        )
        policy["Statement"] = statements
        self._client.put_resource_policy(
            SecretId=secret_id,
            ResourcePolicy=json.dumps(policy, sort_keys=True),
            BlockPublicPolicy=True,
        )

    def enable_secret_access(self, secret_id: str) -> None:
        """Remove only the lifecycle-managed deny from a tenant secret."""
        policy = self._resource_policy(secret_id)
        statements = self._statements_without_disabled_deny(policy)
        if statements:
            policy["Statement"] = statements
            self._client.put_resource_policy(
                SecretId=secret_id,
                ResourcePolicy=json.dumps(policy, sort_keys=True),
                BlockPublicPolicy=True,
            )
        elif policy.get("Statement"):
            self._client.delete_resource_policy(SecretId=secret_id)

    def secret_exists(self, secret_id: str) -> bool:
        """Return whether a secret exists, without fetching its contents."""
        try:
            self._client.describe_secret(SecretId=secret_id)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return False
            raise
        return True

    def _new_bearer_token(self, key_id: str) -> str:
        return f"osre_{key_id}.{self._token_factory(32)}"

    def _resource_policy(self, secret_id: str) -> dict[str, Any]:
        response = self._client.get_resource_policy(SecretId=secret_id)
        serialized = response.get("ResourcePolicy")
        if serialized is None:
            return {"Version": "2012-10-17", "Statement": []}
        if not isinstance(serialized, str):
            raise RuntimeError("Secrets Manager response included an invalid resource policy")
        parsed = json.loads(serialized)
        if not isinstance(parsed, dict):
            raise RuntimeError("Secrets Manager response included an invalid resource policy")
        return parsed

    @staticmethod
    def _statements_without_disabled_deny(
        policy: dict[str, Any],
    ) -> list[dict[str, Any]]:
        raw_statements = policy.get("Statement", [])
        if not isinstance(raw_statements, list):
            raise RuntimeError("Secrets Manager resource policy has invalid statements")
        statements: list[dict[str, Any]] = []
        for statement in raw_statements:
            if not isinstance(statement, dict):
                raise RuntimeError("Secrets Manager resource policy has invalid statements")
            if statement.get("Sid") != _DISABLED_POLICY_SID:
                statements.append(statement)
        return statements

    @staticmethod
    def _required_string(response: dict[str, Any], key: str) -> str:
        value = response.get(key)
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"Secrets Manager response did not include {key}")
        return value
