"""POST method and lifecycle operation for rotating a tenant API credential."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from platform.deployment_fargate.api_control_plane.methods.lifecycle_context import (
    LifecycleContext,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_errors import (
    LifecycleNotFoundError,
    LifecycleOperationError,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_support import (
    key_id,
    require_live_deployment,
    tags,
)
from platform.deployment_fargate.api_control_plane.utils.models import RotatedApiCredential
from platform.deployment_fargate.api_control_plane.utils.ports import LifecycleService
from platform.deployment_fargate.utils.http_lambda import response


def method_post_rotate_api_credential(
    _event: dict[str, Any],
    lifecycle: LifecycleService,
    organization_id: str,
) -> dict[str, Any]:
    """Rotate one tenant API credential."""
    result = lifecycle.rotate_api_credential(organization_id)
    return response(
        200,
        {
            "key_id": result.key_id,
            "api_credential": result.api_credential,
        },
    )


def post_rotate_api_credential(
    context: LifecycleContext,
    organization_id: str,
) -> RotatedApiCredential:
    """Rotate a tenant bearer and return its plaintext exactly once."""
    require_live_deployment(context, organization_id)
    credential_key_id = key_id(organization_id)
    metadata = context.repository.fetch_tenant_api_credential_by_key_id(credential_key_id)
    if metadata is None:
        raise LifecycleNotFoundError
    try:
        rotated = context.secrets.rotate_public_api_credential(
            secret_id=metadata.secret_arn,
            key_id=credential_key_id,
        )
        now = context.clock()
        context.repository.upsert_tenant_api_credential(
            replace(
                metadata,
                secret_arn=rotated.secret_arn,
                enabled=True,
                updated_at=now,
                rotated_at=now,
            )
        )
        context.secrets.ensure_secret_tags(
            rotated.secret_arn,
            tags(organization_id, enabled=True),
        )
        return RotatedApiCredential(
            key_id=rotated.key_id,
            api_credential=rotated.bearer_token,
        )
    except Exception:
        context.logger.exception(
            "Tenant public API credential rotation failed",
            extra={"organization_id": organization_id},
        )
        raise LifecycleOperationError(code="API_CREDENTIAL_ROTATION_FAILED") from None
