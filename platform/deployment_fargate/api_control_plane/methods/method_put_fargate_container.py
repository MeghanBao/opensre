"""PUT method and lifecycle operation for a tenant Fargate container."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from platform.deployment_fargate.api_control_plane.aws.ecs import GatewayTaskDefinitionSpec
from platform.deployment_fargate.api_control_plane.aws.secrets_manager import (
    TenantPublicApiCredential,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_context import (
    LifecycleContext,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_errors import (
    LifecycleError,
    LifecycleOperationError,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_support import (
    actual_state,
    assert_can_activate,
    bootstrap_secret_name,
    client_token,
    key_id,
    public_secret_name,
    record_failure,
    service_name,
    service_spec,
    tags,
    task_family,
    task_role_name,
    validate_organization_id,
)
from platform.deployment_fargate.api_control_plane.utils.models import (
    DeploymentActualState,
    DeploymentDesiredState,
    ProvisionGatewayResult,
    SizeProfile,
    TenantApiCredential,
    TenantDeployment,
)
from platform.deployment_fargate.api_control_plane.utils.ports import LifecycleService
from platform.deployment_fargate.utils.http_lambda import (
    ClientRequestError,
    json_body,
    jsonable,
    response,
)


def method_put_fargate_container(
    event: dict[str, Any],
    lifecycle: LifecycleService,
    organization_id: str,
) -> dict[str, Any]:
    """Provision a tenant Fargate container from an HTTP request."""
    payload = json_body(event)
    unexpected = set(payload) - {"size_profile"}
    if unexpected:
        raise ClientRequestError(400, "invalid_request")

    raw_profile = payload.get("size_profile", SizeProfile.SMALL.value)
    try:
        size_profile = SizeProfile(raw_profile)
    except (TypeError, ValueError):
        raise ClientRequestError(400, "invalid_size_profile") from None

    result = lifecycle.provision_gateway(organization_id, size_profile)
    response_payload = {"deployment": jsonable(result.deployment)}
    if result.api_credential is not None:
        response_payload["api_credential"] = result.api_credential
    return response(200, response_payload)


def put_fargate_container(
    context: LifecycleContext,
    organization_id: str,
    size_profile: SizeProfile = SizeProfile.SMALL,
) -> ProvisionGatewayResult:
    """Create or repair one tenant deployment without duplicating stable resources."""
    validate_organization_id(organization_id)
    now = context.clock()
    existing = context.repository.fetch_tenant_deployment_by_organization_id(organization_id)
    assert_can_activate(context, existing)
    pending = TenantDeployment(
        organization_id=organization_id,
        desired_state=DeploymentDesiredState.RUNNING,
        actual_state=DeploymentActualState.PROVISIONING,
        size_profile=size_profile,
        created_at=existing.created_at if existing else now,
        updated_at=now,
        cluster_arn=context.config.cluster_arn,
        service_arn=existing.service_arn if existing else None,
        task_definition_arn=existing.task_definition_arn if existing else None,
        task_role_arn=existing.task_role_arn if existing else None,
        s3_filesystem_arn=context.config.file_system_arn,
        s3_access_point_arn=existing.s3_access_point_arn if existing else None,
        bootstrap_secret_arn=existing.bootstrap_secret_arn if existing else None,
    )
    context.repository.upsert_tenant_deployment(pending)
    failure_state = pending

    try:
        secret_name = bootstrap_secret_name(context, organization_id)
        context.secrets.enable_secret_access(secret_name)
        access_point = context.s3_files.create_tenant_access_point(
            file_system_id=context.config.file_system_id,
            organization_id=organization_id,
            uid=context.config.tenant_uid,
            gid=context.config.tenant_gid,
            client_token=client_token("access-point", organization_id),
            tags=tags(organization_id, enabled=True),
        )
        failure_state = replace(
            failure_state,
            s3_access_point_arn=access_point.access_point_arn,
        )
        bootstrap_secret_arn = context.secrets.describe_secret_arn(secret_name)
        failure_state = replace(
            failure_state,
            bootstrap_secret_arn=bootstrap_secret_arn,
        )
        context.secrets.ensure_secret_tags(
            bootstrap_secret_arn,
            tags(organization_id, enabled=True),
        )
        task_role_arn = context.iam.ensure_task_role(
            role_name=task_role_name(context, organization_id),
            file_system_arn=context.config.file_system_arn,
            access_point_arn=access_point.access_point_arn,
            bootstrap_secret_arn=bootstrap_secret_arn,
            tags=tags(organization_id, enabled=True),
        )
        failure_state = replace(failure_state, task_role_arn=task_role_arn)
        task_definition_arn = _ensure_task_definition(
            context,
            organization_id=organization_id,
            size_profile=size_profile,
            existing=existing,
            access_point_arn=access_point.access_point_arn,
            bootstrap_secret_arn=bootstrap_secret_arn,
            task_role_arn=task_role_arn,
        )
        failure_state = replace(
            failure_state,
            task_definition_arn=task_definition_arn,
        )
        desired_service = service_spec(
            context,
            organization_id=organization_id,
            task_definition_arn=task_definition_arn,
            desired_count=1,
        )
        service_state = context.ecs.describe_service(
            cluster=context.config.cluster_arn,
            service_name=service_name(context, organization_id),
        )
        if service_state is None:
            service_arn = context.ecs.create_service(desired_service)
            observed_state = DeploymentActualState.PROVISIONING
        else:
            service_arn = service_state.service_arn
            if (
                service_state.task_definition_arn != task_definition_arn
                or service_state.desired_count != 1
            ):
                context.ecs.update_service(desired_service)
                observed_state = DeploymentActualState.PROVISIONING
            else:
                observed_state = actual_state(service_state)

        deployment = context.repository.upsert_tenant_deployment(
            replace(
                pending,
                actual_state=observed_state,
                updated_at=context.clock(),
                service_arn=service_arn,
                task_definition_arn=task_definition_arn,
                task_role_arn=task_role_arn,
                s3_access_point_arn=access_point.access_point_arn,
                bootstrap_secret_arn=bootstrap_secret_arn,
                last_error_code=None,
            )
        )
        failure_state = deployment
        credential = _ensure_public_api_credential(context, organization_id)
        return ProvisionGatewayResult(
            deployment=deployment,
            api_credential=credential.bearer_token if credential else None,
        )
    except LifecycleError:
        raise
    except Exception:
        record_failure(context, failure_state, code="GATEWAY_RECONCILE_FAILED")
        raise LifecycleOperationError(code="GATEWAY_RECONCILE_FAILED") from None


def _ensure_task_definition(
    context: LifecycleContext,
    *,
    organization_id: str,
    size_profile: SizeProfile,
    existing: TenantDeployment | None,
    access_point_arn: str,
    bootstrap_secret_arn: str,
    task_role_arn: str,
) -> str:
    if (
        existing is not None
        and existing.task_definition_arn is not None
        and existing.size_profile is size_profile
        and existing.task_role_arn == task_role_arn
        and existing.s3_filesystem_arn == context.config.file_system_arn
        and existing.s3_access_point_arn == access_point_arn
        and existing.bootstrap_secret_arn == bootstrap_secret_arn
        and context.ecs.task_definition_uses_image(
            existing.task_definition_arn,
            context.config.gateway_image,
        )
    ):
        return existing.task_definition_arn
    return context.ecs.register_gateway_task_definition(
        GatewayTaskDefinitionSpec(
            family=task_family(context, organization_id),
            organization_id=organization_id,
            image=context.config.gateway_image,
            size_profile=size_profile.value,
            task_role_arn=task_role_arn,
            execution_role_arn=context.config.execution_role_arn,
            file_system_arn=context.config.file_system_arn,
            access_point_arn=access_point_arn,
            bootstrap_secret_arn=bootstrap_secret_arn,
            credentials_api_url=context.config.credentials_api_url,
            log_group=context.config.log_group,
            log_region=context.config.aws_region,
            tags=tags(organization_id, enabled=True),
        )
    )


def _ensure_public_api_credential(
    context: LifecycleContext,
    organization_id: str,
) -> TenantPublicApiCredential | None:
    credential_key_id = key_id(organization_id)
    metadata = context.repository.fetch_tenant_api_credential_by_key_id(credential_key_id)
    secret_name = public_secret_name(context, organization_id)
    secret_exists = context.secrets.secret_exists(metadata.secret_arn if metadata else secret_name)
    generated: TenantPublicApiCredential | None = None
    if not secret_exists:
        generated = context.secrets.create_public_api_credential(
            secret_name=secret_name,
            key_id=credential_key_id,
            tags=tags(organization_id, enabled=True),
        )
        secret_arn = generated.secret_arn
    elif metadata is None or not metadata.enabled:
        context.secrets.enable_secret_access(metadata.secret_arn if metadata else secret_name)
        generated = context.secrets.rotate_public_api_credential(
            secret_id=metadata.secret_arn if metadata else secret_name,
            key_id=credential_key_id,
        )
        secret_arn = generated.secret_arn
    else:
        context.secrets.enable_secret_access(metadata.secret_arn)
        secret_arn = context.secrets.describe_secret_arn(metadata.secret_arn)
        context.secrets.ensure_secret_tags(
            secret_arn,
            tags(organization_id, enabled=True),
        )

    now = context.clock()
    context.repository.upsert_tenant_api_credential(
        TenantApiCredential(
            key_id=credential_key_id,
            organization_id=organization_id,
            secret_arn=secret_arn,
            enabled=True,
            created_at=metadata.created_at if metadata else now,
            updated_at=now,
            rotated_at=(
                now if generated and metadata else (metadata.rotated_at if metadata else None)
            ),
        )
    )
    return generated
