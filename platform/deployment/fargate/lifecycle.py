"""Idempotent orchestration for one Fargate Gateway service per organization."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, cast

from gateway.control_plane.contracts import (
    DeploymentActualState,
    DeploymentDesiredState,
    DeploymentRepository,
    SizeProfile,
    TenantApiCredential,
    TenantApiCredentialRepository,
    TenantDeployment,
)
from gateway.control_plane.postgres_store import PostgresControlPlaneStore
from platform.deployment.aws.client import get_boto3_client
from platform.deployment.fargate.aws_ecs import (
    FargateServiceSpec,
    FargateServiceState,
    GatewayTaskDefinitionSpec,
    TenantEcsAdapter,
)
from platform.deployment.fargate.aws_iam import TenantIamAdapter
from platform.deployment.fargate.aws_s3_files import S3FilesAdapter
from platform.deployment.fargate.aws_secrets import (
    TenantPublicApiCredential,
    TenantSecretsAdapter,
)
from platform.deployment.fargate.aws_types import (
    EcsClient,
    IamClient,
    S3FilesClient,
    SecretsManagerClient,
)
from platform.deployment.fargate.lifecycle_errors import (
    LifecycleCapacityError,
    LifecycleError,
    LifecycleNotFoundError,
    LifecycleOperationError,
    LifecycleValidationError,
)
from platform.deployment.fargate.lifecycle_models import (
    FargateLifecycleConfig,
    ProvisionGatewayResult,
    RotatedApiCredential,
)

_ORGANIZATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


class LifecycleRepository(
    DeploymentRepository,
    TenantApiCredentialRepository,
    Protocol,
):
    """Combined persistence boundary required by lifecycle reconciliation."""


class FargateLifecycle:
    """Reconcile tenant isolation resources and independently controlled compute."""

    def __init__(
        self,
        *,
        config: FargateLifecycleConfig,
        repository: LifecycleRepository,
        s3_files: S3FilesAdapter,
        iam: TenantIamAdapter,
        secrets: TenantSecretsAdapter,
        ecs: TenantEcsAdapter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._s3_files = s3_files
        self._iam = iam
        self._secrets = secrets
        self._ecs = ecs
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)

    def provision_gateway(
        self,
        organization_id: str,
        size_profile: SizeProfile = SizeProfile.SMALL,
    ) -> ProvisionGatewayResult:
        """Create or repair one tenant deployment without duplicating stable resources."""
        self._validate_organization_id(organization_id)
        now = self._clock()
        existing = self._repository.get_deployment(organization_id)
        self._assert_can_activate(existing)
        pending = TenantDeployment(
            organization_id=organization_id,
            desired_state=DeploymentDesiredState.RUNNING,
            actual_state=DeploymentActualState.PROVISIONING,
            size_profile=size_profile,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            cluster_arn=self._config.cluster_arn,
            service_arn=existing.service_arn if existing else None,
            task_definition_arn=existing.task_definition_arn if existing else None,
            task_role_arn=existing.task_role_arn if existing else None,
            s3_filesystem_arn=self._config.file_system_arn,
            s3_access_point_arn=existing.s3_access_point_arn if existing else None,
            bootstrap_secret_arn=existing.bootstrap_secret_arn if existing else None,
        )
        self._repository.save_deployment(pending)
        failure_state = pending

        try:
            bootstrap_secret_name = self._bootstrap_secret_name(organization_id)
            self._secrets.enable_secret_access(bootstrap_secret_name)
            access_point = self._s3_files.create_tenant_access_point(
                file_system_id=self._config.file_system_id,
                organization_id=organization_id,
                uid=self._config.tenant_uid,
                gid=self._config.tenant_gid,
                client_token=self._client_token("access-point", organization_id),
                tags=self._tags(organization_id, enabled=True),
            )
            failure_state = replace(
                failure_state,
                s3_access_point_arn=access_point.access_point_arn,
            )
            bootstrap_secret_arn = self._secrets.describe_secret_arn(bootstrap_secret_name)
            failure_state = replace(
                failure_state,
                bootstrap_secret_arn=bootstrap_secret_arn,
            )
            self._secrets.ensure_secret_tags(
                bootstrap_secret_arn,
                self._tags(organization_id, enabled=True),
            )
            task_role_arn = self._iam.ensure_task_role(
                role_name=self._task_role_name(organization_id),
                file_system_arn=self._config.file_system_arn,
                access_point_arn=access_point.access_point_arn,
                bootstrap_secret_arn=bootstrap_secret_arn,
                tags=self._tags(organization_id, enabled=True),
            )
            failure_state = replace(failure_state, task_role_arn=task_role_arn)
            task_definition_arn = self._ensure_task_definition(
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
            service_spec = self._service_spec(
                organization_id=organization_id,
                task_definition_arn=task_definition_arn,
                desired_count=1,
            )
            service_state = self._ecs.describe_service(
                cluster=self._config.cluster_arn,
                service_name=self._service_name(organization_id),
            )
            if service_state is None:
                service_arn = self._ecs.create_service(service_spec)
                actual_state = DeploymentActualState.PROVISIONING
            else:
                service_arn = service_state.service_arn
                if (
                    service_state.task_definition_arn != task_definition_arn
                    or service_state.desired_count != 1
                ):
                    self._ecs.update_service(service_spec)
                    actual_state = DeploymentActualState.PROVISIONING
                else:
                    actual_state = self._actual_state(service_state)

            deployment = self._repository.save_deployment(
                replace(
                    pending,
                    actual_state=actual_state,
                    updated_at=self._clock(),
                    service_arn=service_arn,
                    task_definition_arn=task_definition_arn,
                    task_role_arn=task_role_arn,
                    s3_access_point_arn=access_point.access_point_arn,
                    bootstrap_secret_arn=bootstrap_secret_arn,
                    last_error_code=None,
                )
            )
            failure_state = deployment
            credential = self._ensure_public_api_credential(organization_id)
            return ProvisionGatewayResult(
                deployment=deployment,
                api_credential=credential.bearer_token if credential else None,
            )
        except LifecycleError:
            raise
        except Exception:
            self._record_failure(failure_state, code="GATEWAY_RECONCILE_FAILED")
            raise LifecycleOperationError(code="GATEWAY_RECONCILE_FAILED") from None

    def get_gateway(self, organization_id: str) -> TenantDeployment | None:
        """Return persisted desired state updated with the latest ECS observation."""
        self._validate_organization_id(organization_id)
        deployment = self._repository.get_deployment(organization_id)
        if deployment is None or deployment.desired_state is DeploymentDesiredState.DELETED:
            return deployment
        if deployment.service_arn is None:
            return self._repository.save_deployment(
                replace(
                    deployment,
                    actual_state=DeploymentActualState.FAILED,
                    last_error_code="GATEWAY_SERVICE_NOT_FOUND",
                    updated_at=self._clock(),
                )
            )
        try:
            service = self._ecs.describe_service(
                cluster=self._config.cluster_arn,
                service_name=self._service_name(organization_id),
            )
            if service is None:
                return self._repository.save_deployment(
                    replace(
                        deployment,
                        actual_state=DeploymentActualState.FAILED,
                        last_error_code="GATEWAY_SERVICE_NOT_FOUND",
                        updated_at=self._clock(),
                    )
                )
            return self._repository.save_deployment(
                replace(
                    deployment,
                    actual_state=self._actual_state(service),
                    service_arn=service.service_arn,
                    task_definition_arn=service.task_definition_arn,
                    last_error_code=None,
                    updated_at=self._clock(),
                )
            )
        except Exception:
            self._record_failure(deployment, code="GATEWAY_STATUS_FAILED")
            raise LifecycleOperationError(code="GATEWAY_STATUS_FAILED") from None

    def status(self, organization_id: str) -> TenantDeployment | None:
        """Alias used by non-HTTP lifecycle callers."""
        return self.get_gateway(organization_id)

    def start_gateway(self, organization_id: str) -> TenantDeployment:
        """Set desired count to one for a previously stopped deployment."""
        deployment = self._require_live_deployment(organization_id)
        self._assert_can_activate(deployment)
        return self._set_desired_count(deployment, desired_count=1)

    def stop_gateway(self, organization_id: str) -> TenantDeployment:
        """Set desired count to zero while retaining all tenant state and credentials."""
        deployment = self._require_live_deployment(organization_id)
        return self._set_desired_count(deployment, desired_count=0)

    def delete_gateway(self, organization_id: str) -> TenantDeployment:
        """Delete ECS compute while preserving the tenant access point and filesystem."""
        self._validate_organization_id(organization_id)
        deployment = self._repository.get_deployment(organization_id)
        if deployment is None:
            raise LifecycleNotFoundError
        if deployment.desired_state is DeploymentDesiredState.DELETED:
            return deployment

        deleting = replace(
            deployment,
            desired_state=DeploymentDesiredState.DELETED,
            actual_state=DeploymentActualState.PROVISIONING,
            updated_at=self._clock(),
            last_error_code=None,
        )
        self._repository.save_deployment(deleting)
        try:
            service_name = self._service_name(organization_id)
            service = self._ecs.describe_service(
                cluster=self._config.cluster_arn,
                service_name=service_name,
            )
            if service is not None and service.desired_count != 0:
                self._ecs.update_service(
                    self._service_spec(
                        organization_id=organization_id,
                        task_definition_arn=service.task_definition_arn,
                        desired_count=0,
                    )
                )
            self._ecs.delete_service(
                cluster=self._config.cluster_arn,
                service_name=service_name,
            )
            if deployment.task_definition_arn:
                self._ecs.deregister_task_definition(deployment.task_definition_arn)

            self._repository.disable_api_credentials(organization_id)
            self._tag_credential_secrets_disabled(organization_id, deployment)
            return self._repository.save_deployment(
                replace(
                    deleting,
                    actual_state=DeploymentActualState.DELETED,
                    service_arn=None,
                    task_definition_arn=None,
                    updated_at=self._clock(),
                )
            )
        except Exception:
            self._record_failure(deleting, code="GATEWAY_DELETE_FAILED")
            raise LifecycleOperationError(code="GATEWAY_DELETE_FAILED") from None

    def rotate_api_credential(self, organization_id: str) -> RotatedApiCredential:
        """Rotate a tenant bearer and return its plaintext exactly once."""
        self._require_live_deployment(organization_id)
        key_id = self._key_id(organization_id)
        metadata = self._repository.get_api_credential(key_id)
        if metadata is None:
            raise LifecycleNotFoundError
        try:
            rotated = self._secrets.rotate_public_api_credential(
                secret_id=metadata.secret_arn,
                key_id=key_id,
            )
            now = self._clock()
            self._repository.save_api_credential(
                replace(
                    metadata,
                    secret_arn=rotated.secret_arn,
                    enabled=True,
                    updated_at=now,
                    rotated_at=now,
                )
            )
            self._secrets.ensure_secret_tags(
                rotated.secret_arn,
                self._tags(organization_id, enabled=True),
            )
            return RotatedApiCredential(
                key_id=rotated.key_id,
                api_credential=rotated.bearer_token,
            )
        except Exception:
            self._logger.exception(
                "Tenant public API credential rotation failed",
                extra={"organization_id": organization_id},
            )
            raise LifecycleOperationError(code="API_CREDENTIAL_ROTATION_FAILED") from None

    def _set_desired_count(
        self,
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
            updated_at=self._clock(),
            last_error_code=None,
        )
        self._repository.save_deployment(pending)
        try:
            service = self._ecs.describe_service(
                cluster=self._config.cluster_arn,
                service_name=self._service_name(deployment.organization_id),
            )
            if service is None:
                raise LifecycleOperationError(code="GATEWAY_SERVICE_NOT_FOUND")
            if service.desired_count != desired_count:
                self._ecs.update_service(
                    self._service_spec(
                        organization_id=deployment.organization_id,
                        task_definition_arn=deployment.task_definition_arn,
                        desired_count=desired_count,
                    )
                )
                actual_state = DeploymentActualState.PROVISIONING
            else:
                actual_state = self._actual_state(service)
            return self._repository.save_deployment(
                replace(
                    pending,
                    actual_state=actual_state,
                    updated_at=self._clock(),
                )
            )
        except LifecycleError:
            raise
        except Exception:
            self._record_failure(pending, code="GATEWAY_STATE_CHANGE_FAILED")
            raise LifecycleOperationError(code="GATEWAY_STATE_CHANGE_FAILED") from None

    def _ensure_task_definition(
        self,
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
            and existing.s3_filesystem_arn == self._config.file_system_arn
            and existing.s3_access_point_arn == access_point_arn
            and existing.bootstrap_secret_arn == bootstrap_secret_arn
            and self._ecs.task_definition_exists(existing.task_definition_arn)
        ):
            return existing.task_definition_arn
        return self._ecs.register_gateway_task_definition(
            GatewayTaskDefinitionSpec(
                family=self._task_family(organization_id),
                organization_id=organization_id,
                image=self._config.gateway_image,
                size_profile=size_profile.value,
                task_role_arn=task_role_arn,
                execution_role_arn=self._config.execution_role_arn,
                file_system_arn=self._config.file_system_arn,
                access_point_arn=access_point_arn,
                bootstrap_secret_arn=bootstrap_secret_arn,
                credentials_api_url=self._config.credentials_api_url,
                log_group=self._config.log_group,
                log_region=self._config.aws_region,
                tags=self._tags(organization_id, enabled=True),
            )
        )

    def _ensure_public_api_credential(
        self,
        organization_id: str,
    ) -> TenantPublicApiCredential | None:
        key_id = self._key_id(organization_id)
        metadata = self._repository.get_api_credential(key_id)
        secret_name = self._public_secret_name(organization_id)
        secret_exists = self._secrets.secret_exists(
            metadata.secret_arn if metadata else secret_name
        )
        generated: TenantPublicApiCredential | None = None
        if not secret_exists:
            generated = self._secrets.create_public_api_credential(
                secret_name=secret_name,
                key_id=key_id,
                tags=self._tags(organization_id, enabled=True),
            )
            secret_arn = generated.secret_arn
        elif metadata is None or not metadata.enabled:
            self._secrets.enable_secret_access(metadata.secret_arn if metadata else secret_name)
            generated = self._secrets.rotate_public_api_credential(
                secret_id=metadata.secret_arn if metadata else secret_name,
                key_id=key_id,
            )
            secret_arn = generated.secret_arn
        else:
            self._secrets.enable_secret_access(metadata.secret_arn)
            secret_arn = self._secrets.describe_secret_arn(metadata.secret_arn)
            self._secrets.ensure_secret_tags(
                secret_arn,
                self._tags(organization_id, enabled=True),
            )

        now = self._clock()
        self._repository.save_api_credential(
            TenantApiCredential(
                key_id=key_id,
                organization_id=organization_id,
                secret_arn=secret_arn,
                enabled=True,
                created_at=metadata.created_at if metadata else now,
                updated_at=now,
                rotated_at=now
                if generated and metadata
                else (metadata.rotated_at if metadata else None),
            )
        )
        return generated

    def _tag_credential_secrets_disabled(
        self,
        organization_id: str,
        deployment: TenantDeployment,
    ) -> None:
        metadata = self._repository.get_api_credential(self._key_id(organization_id))
        if metadata is not None and self._secrets.secret_exists(metadata.secret_arn):
            self._secrets.ensure_secret_tags(
                metadata.secret_arn,
                self._tags(organization_id, enabled=False),
            )
            self._secrets.disable_secret_access(metadata.secret_arn)
        if deployment.bootstrap_secret_arn and self._secrets.secret_exists(
            deployment.bootstrap_secret_arn
        ):
            self._secrets.ensure_secret_tags(
                deployment.bootstrap_secret_arn,
                self._tags(organization_id, enabled=False),
            )
            self._secrets.disable_secret_access(deployment.bootstrap_secret_arn)

    def _require_live_deployment(self, organization_id: str) -> TenantDeployment:
        self._validate_organization_id(organization_id)
        deployment = self._repository.get_deployment(organization_id)
        if deployment is None or deployment.desired_state is DeploymentDesiredState.DELETED:
            raise LifecycleNotFoundError
        return deployment

    def _assert_can_activate(self, deployment: TenantDeployment | None) -> None:
        if deployment is not None and deployment.desired_state is DeploymentDesiredState.RUNNING:
            return
        if self._config.max_active_organizations <= 0:
            raise LifecycleValidationError
        active_count = self._repository.count_running_deployments(
            exclude_organization_id=(deployment.organization_id if deployment is not None else None)
        )
        if active_count >= self._config.max_active_organizations:
            raise LifecycleCapacityError

    def _record_failure(self, deployment: TenantDeployment, *, code: str) -> None:
        self._logger.exception(
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
            updated_at=self._clock(),
        )
        try:
            self._repository.save_deployment(failed)
        except Exception:
            self._logger.exception(
                "Failed to persist generic lifecycle failure state",
                extra={
                    "organization_id": deployment.organization_id,
                    "error_code": code,
                },
            )

    def _service_spec(
        self,
        *,
        organization_id: str,
        task_definition_arn: str,
        desired_count: int,
    ) -> FargateServiceSpec:
        return FargateServiceSpec(
            cluster=self._config.cluster_arn,
            service_name=self._service_name(organization_id),
            task_definition_arn=task_definition_arn,
            subnet_ids=self._config.subnet_ids,
            security_group_ids=self._config.security_group_ids,
            desired_count=desired_count,
        )

    @staticmethod
    def _actual_state(service: FargateServiceState) -> DeploymentActualState:
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

    def _tags(self, organization_id: str, *, enabled: bool) -> dict[str, str]:
        return {
            "managed-by": "opensre",
            "organization-id": organization_id,
            "enabled": str(enabled).lower(),
        }

    def _task_family(self, organization_id: str) -> str:
        return f"{self._config.resource_prefix}-{organization_id}-gateway"

    def _service_name(self, organization_id: str) -> str:
        return f"{self._config.resource_prefix}-{organization_id}-gateway"

    def _task_role_name(self, organization_id: str) -> str:
        return f"{self._config.resource_prefix}-{organization_id}-gateway-task"

    def _bootstrap_secret_name(self, organization_id: str) -> str:
        return f"{self._config.resource_prefix}/tenants/{organization_id}/credentials-api-bootstrap"

    def _public_secret_name(self, organization_id: str) -> str:
        return f"{self._config.resource_prefix}/tenants/{organization_id}/public-api"

    @staticmethod
    def _key_id(organization_id: str) -> str:
        return hashlib.sha256(f"public-api:{organization_id}".encode()).hexdigest()[:24]

    @staticmethod
    def _client_token(resource: str, organization_id: str) -> str:
        return hashlib.sha256(f"{resource}:{organization_id}:v1".encode()).hexdigest()

    @staticmethod
    def _validate_organization_id(organization_id: str) -> None:
        if not _ORGANIZATION_ID.fullmatch(organization_id):
            raise LifecycleValidationError


def create_lifecycle_service_from_environment() -> FargateLifecycle:
    """Compose the production lifecycle using boto3's standard credential chain."""
    config = FargateLifecycleConfig.from_environment()
    repository = PostgresControlPlaneStore(_required_database_url())
    return FargateLifecycle(
        config=config,
        repository=repository,
        s3_files=S3FilesAdapter(
            cast(S3FilesClient, get_boto3_client("s3files", config.aws_region))
        ),
        iam=TenantIamAdapter(cast(IamClient, get_boto3_client("iam", config.aws_region))),
        secrets=TenantSecretsAdapter(
            cast(
                SecretsManagerClient,
                get_boto3_client("secretsmanager", config.aws_region),
            )
        ),
        ecs=TenantEcsAdapter(cast(EcsClient, get_boto3_client("ecs", config.aws_region))),
    )


def _required_database_url() -> str:
    import os

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    return database_url
