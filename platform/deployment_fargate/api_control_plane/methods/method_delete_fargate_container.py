"""DELETE method and lifecycle operation for a tenant Fargate container."""

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
    record_failure,
    service_name,
    service_spec,
    tags,
    validate_organization_id,
)
from platform.deployment_fargate.api_control_plane.utils.models import (
    DeploymentActualState,
    DeploymentDesiredState,
    TenantDeployment,
)
from platform.deployment_fargate.api_control_plane.utils.ports import LifecycleService
from platform.deployment_fargate.utils.http_lambda import jsonable, response


def method_delete_fargate_container(
    _event: dict[str, Any],
    lifecycle: LifecycleService,
    organization_id: str,
) -> dict[str, Any]:
    """Delete one tenant Fargate container."""
    deployment = lifecycle.delete_gateway(organization_id)
    return response(200, {"deployment": jsonable(deployment)})


def delete_fargate_container(
    context: LifecycleContext,
    organization_id: str,
) -> TenantDeployment:
    """Delete ECS compute while preserving the tenant access point and filesystem."""
    validate_organization_id(organization_id)
    deployment = context.repository.fetch_tenant_deployment_by_organization_id(organization_id)
    if deployment is None:
        raise LifecycleNotFoundError
    if deployment.desired_state is DeploymentDesiredState.DELETED:
        return deployment

    deleting = replace(
        deployment,
        desired_state=DeploymentDesiredState.DELETED,
        actual_state=DeploymentActualState.PROVISIONING,
        updated_at=context.clock(),
        last_error_code=None,
    )
    context.repository.upsert_tenant_deployment(deleting)
    try:
        container_service_name = service_name(context, organization_id)
        service = context.ecs.describe_service(
            cluster=context.config.cluster_arn,
            service_name=container_service_name,
        )
        if service is not None and service.desired_count != 0:
            context.ecs.update_service(
                service_spec(
                    context,
                    organization_id=organization_id,
                    task_definition_arn=service.task_definition_arn,
                    desired_count=0,
                )
            )
        context.ecs.delete_service(
            cluster=context.config.cluster_arn,
            service_name=container_service_name,
        )
        if deployment.task_definition_arn:
            context.ecs.deregister_task_definition(deployment.task_definition_arn)

        context.repository.disable_active_tenant_api_credentials_for_organization(organization_id)
        _disable_credential_secrets(context, organization_id, deployment)
        return context.repository.upsert_tenant_deployment(
            replace(
                deleting,
                actual_state=DeploymentActualState.DELETED,
                service_arn=None,
                task_definition_arn=None,
                updated_at=context.clock(),
            )
        )
    except Exception:
        record_failure(context, deleting, code="GATEWAY_DELETE_FAILED")
        raise LifecycleOperationError(code="GATEWAY_DELETE_FAILED") from None


def _disable_credential_secrets(
    context: LifecycleContext,
    organization_id: str,
    deployment: TenantDeployment,
) -> None:
    metadata = context.repository.fetch_tenant_api_credential_by_key_id(key_id(organization_id))
    if metadata is not None and context.secrets.secret_exists(metadata.secret_arn):
        context.secrets.ensure_secret_tags(
            metadata.secret_arn,
            tags(organization_id, enabled=False),
        )
        context.secrets.disable_secret_access(metadata.secret_arn)
    if deployment.bootstrap_secret_arn and context.secrets.secret_exists(
        deployment.bootstrap_secret_arn
    ):
        context.secrets.ensure_secret_tags(
            deployment.bootstrap_secret_arn,
            tags(organization_id, enabled=False),
        )
        context.secrets.disable_secret_access(deployment.bootstrap_secret_arn)
