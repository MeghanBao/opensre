"""GET method and lifecycle operation for a tenant Fargate container."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from platform.deployment_fargate.api_control_plane.methods.lifecycle_context import (
    LifecycleContext,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_errors import (
    LifecycleOperationError,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_support import (
    actual_state,
    record_failure,
    service_name,
    validate_organization_id,
)
from platform.deployment_fargate.api_control_plane.utils.models import (
    DeploymentActualState,
    DeploymentDesiredState,
    TenantDeployment,
)
from platform.deployment_fargate.api_control_plane.utils.ports import LifecycleService
from platform.deployment_fargate.utils.http_lambda import jsonable, response


def method_get_fargate_container(
    _event: dict[str, Any],
    lifecycle: LifecycleService,
    organization_id: str,
) -> dict[str, Any]:
    """Return one tenant Fargate container or a not-found response."""
    deployment = lifecycle.get_gateway(organization_id)
    if deployment is None:
        return response(404, {"error": "not_found"})
    return response(200, {"deployment": jsonable(deployment)})


def get_fargate_container(
    context: LifecycleContext,
    organization_id: str,
) -> TenantDeployment | None:
    """Return persisted desired state updated with the latest ECS observation."""
    validate_organization_id(organization_id)
    deployment = context.repository.fetch_tenant_deployment_by_organization_id(organization_id)
    if deployment is None or deployment.desired_state is DeploymentDesiredState.DELETED:
        return deployment
    if deployment.service_arn is None:
        return context.repository.upsert_tenant_deployment(
            replace(
                deployment,
                actual_state=DeploymentActualState.FAILED,
                last_error_code="GATEWAY_SERVICE_NOT_FOUND",
                updated_at=context.clock(),
            )
        )
    try:
        service = context.ecs.describe_service(
            cluster=context.config.cluster_arn,
            service_name=service_name(context, organization_id),
        )
        if service is None:
            return context.repository.upsert_tenant_deployment(
                replace(
                    deployment,
                    actual_state=DeploymentActualState.FAILED,
                    last_error_code="GATEWAY_SERVICE_NOT_FOUND",
                    updated_at=context.clock(),
                )
            )
        return context.repository.upsert_tenant_deployment(
            replace(
                deployment,
                actual_state=actual_state(service),
                service_arn=service.service_arn,
                task_definition_arn=service.task_definition_arn,
                last_error_code=None,
                updated_at=context.clock(),
            )
        )
    except Exception:
        record_failure(context, deployment, code="GATEWAY_STATUS_FAILED")
        raise LifecycleOperationError(code="GATEWAY_STATUS_FAILED") from None
