"""Shared primitives used by multiple Fargate container lifecycle methods."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace

from platform.deployment_fargate.api_control_plane.aws.ecs import (
    FargateServiceSpec,
    FargateServiceState,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_context import (
    LifecycleContext,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_errors import (
    LifecycleCapacityError,
    LifecycleError,
    LifecycleNotFoundError,
    LifecycleOperationError,
    LifecycleValidationError,
)
from platform.deployment_fargate.api_control_plane.utils.models import (
    DeploymentActualState,
    DeploymentDesiredState,
    TenantDeployment,
)

_ORGANIZATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


def validate_organization_id(organization_id: str) -> None:
    if not _ORGANIZATION_ID.fullmatch(organization_id):
        raise LifecycleValidationError


def require_live_deployment(
    context: LifecycleContext,
    organization_id: str,
) -> TenantDeployment:
    validate_organization_id(organization_id)
    deployment = context.repository.fetch_tenant_deployment_by_organization_id(organization_id)
    if deployment is None or deployment.desired_state is DeploymentDesiredState.DELETED:
        raise LifecycleNotFoundError
    return deployment


def assert_can_activate(
    context: LifecycleContext,
    deployment: TenantDeployment | None,
) -> None:
    if deployment is not None and deployment.desired_state is DeploymentDesiredState.RUNNING:
        return
    if context.config.max_active_organizations <= 0:
        raise LifecycleValidationError
    active_count = context.repository.count_deployments_with_running_desired_state(
        exclude_organization_id=(deployment.organization_id if deployment is not None else None)
    )
    if active_count >= context.config.max_active_organizations:
        raise LifecycleCapacityError


def set_desired_count(
    context: LifecycleContext,
    deployment: TenantDeployment,
    *,
    desired_count: int,
) -> TenantDeployment:
    if deployment.task_definition_arn is None:
        raise LifecycleOperationError(code="GATEWAY_TASK_DEFINITION_NOT_FOUND")
    desired_state = (
        DeploymentDesiredState.RUNNING if desired_count == 1 else DeploymentDesiredState.STOPPED
    )
    pending = replace(
        deployment,
        desired_state=desired_state,
        actual_state=DeploymentActualState.PROVISIONING,
        updated_at=context.clock(),
        last_error_code=None,
    )
    context.repository.upsert_tenant_deployment(pending)
    try:
        service = context.ecs.describe_service(
            cluster=context.config.cluster_arn,
            service_name=service_name(context, deployment.organization_id),
        )
        if service is None:
            raise LifecycleOperationError(code="GATEWAY_SERVICE_NOT_FOUND")
        if service.desired_count != desired_count:
            context.ecs.update_service(
                service_spec(
                    context,
                    organization_id=deployment.organization_id,
                    task_definition_arn=deployment.task_definition_arn,
                    desired_count=desired_count,
                )
            )
            observed_state = DeploymentActualState.PROVISIONING
        else:
            observed_state = actual_state(service)
        return context.repository.upsert_tenant_deployment(
            replace(
                pending,
                actual_state=observed_state,
                updated_at=context.clock(),
            )
        )
    except LifecycleError:
        raise
    except Exception:
        record_failure(context, pending, code="GATEWAY_STATE_CHANGE_FAILED")
        raise LifecycleOperationError(code="GATEWAY_STATE_CHANGE_FAILED") from None


def record_failure(
    context: LifecycleContext,
    deployment: TenantDeployment,
    *,
    code: str,
) -> None:
    context.logger.exception(
        "Tenant Gateway lifecycle operation failed",
        extra={
            "organization_id": deployment.organization_id,
            "error_code": code,
        },
    )
    failed = replace(
        deployment,
        actual_state=DeploymentActualState.FAILED,
        last_error_code=code,
        updated_at=context.clock(),
    )
    try:
        context.repository.upsert_tenant_deployment(failed)
    except Exception:
        context.logger.exception(
            "Failed to persist generic lifecycle failure state",
            extra={
                "organization_id": deployment.organization_id,
                "error_code": code,
            },
        )


def service_spec(
    context: LifecycleContext,
    *,
    organization_id: str,
    task_definition_arn: str,
    desired_count: int,
) -> FargateServiceSpec:
    return FargateServiceSpec(
        cluster=context.config.cluster_arn,
        service_name=service_name(context, organization_id),
        task_definition_arn=task_definition_arn,
        subnet_ids=context.config.subnet_ids,
        security_group_ids=context.config.security_group_ids,
        desired_count=desired_count,
    )


def actual_state(service: FargateServiceState) -> DeploymentActualState:
    if service.status == "INACTIVE":
        return DeploymentActualState.FAILED
    if service.desired_count == 0 and service.running_count == 0:
        return DeploymentActualState.STOPPED
    if (
        service.desired_count > 0
        and service.running_count >= service.desired_count
        and service.pending_count == 0
    ):
        return DeploymentActualState.RUNNING
    return DeploymentActualState.PROVISIONING


def tags(
    organization_id: str,
    *,
    enabled: bool,
) -> dict[str, str]:
    return {
        "managed-by": "opensre",
        "organization-id": organization_id,
        "enabled": str(enabled).lower(),
    }


def task_family(context: LifecycleContext, organization_id: str) -> str:
    return f"{context.config.resource_prefix}-{organization_id}-gateway"


def service_name(context: LifecycleContext, organization_id: str) -> str:
    return f"{context.config.resource_prefix}-{organization_id}-gateway"


def task_role_name(context: LifecycleContext, organization_id: str) -> str:
    return f"{context.config.resource_prefix}-{organization_id}-gateway-task"


def bootstrap_secret_name(context: LifecycleContext, organization_id: str) -> str:
    return f"{context.config.resource_prefix}/tenants/{organization_id}/credentials-api-bootstrap"


def public_secret_name(context: LifecycleContext, organization_id: str) -> str:
    return f"{context.config.resource_prefix}/tenants/{organization_id}/public-api"


def key_id(organization_id: str) -> str:
    return hashlib.sha256(f"public-api:{organization_id}".encode()).hexdigest()[:24]


def client_token(resource: str, organization_id: str) -> str:
    return hashlib.sha256(f"{resource}:{organization_id}:v1".encode()).hexdigest()
