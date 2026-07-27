"""IAM authorization helpers for control-plane lifecycle routes."""

from __future__ import annotations

import hmac
from typing import Any


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
