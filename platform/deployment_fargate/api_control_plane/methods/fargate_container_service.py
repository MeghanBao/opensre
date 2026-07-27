"""Composition service for the individual Fargate container methods."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from platform.deployment_fargate.api_control_plane.aws.boto3_client_factory import (
    create_boto3_client,
)
from platform.deployment_fargate.api_control_plane.aws.ecs import (
    EcsClient,
    TenantEcsAdapter,
)
from platform.deployment_fargate.api_control_plane.aws.iam import IamClient, TenantIamAdapter
from platform.deployment_fargate.api_control_plane.aws.s3_files import (
    S3FilesAdapter,
    S3FilesClient,
)
from platform.deployment_fargate.api_control_plane.aws.secrets_manager import (
    SecretsManagerClient,
    TenantSecretsManagerAdapter,
)
from platform.deployment_fargate.api_control_plane.db.db_client import ControlPlaneDbClient
from platform.deployment_fargate.api_control_plane.methods.lifecycle_context import (
    LifecycleContext,
    LifecycleRepository,
)
from platform.deployment_fargate.api_control_plane.methods.method_delete_fargate_container import (
    delete_fargate_container,
)
from platform.deployment_fargate.api_control_plane.methods.method_get_fargate_container import (
    get_fargate_container,
)
from platform.deployment_fargate.api_control_plane.methods.method_post_rotate_api_credential import (
    post_rotate_api_credential,
)
from platform.deployment_fargate.api_control_plane.methods.method_post_start_fargate_container import (
    post_start_fargate_container,
)
from platform.deployment_fargate.api_control_plane.methods.method_post_stop_fargate_container import (
    post_stop_fargate_container,
)
from platform.deployment_fargate.api_control_plane.methods.method_put_fargate_container import (
    put_fargate_container,
)
from platform.deployment_fargate.api_control_plane.utils.get_fleet_config import (
    TenantFleetConfig,
)
from platform.deployment_fargate.api_control_plane.utils.models import (
    ProvisionGatewayResult,
    RotatedApiCredential,
    SizeProfile,
    TenantDeployment,
)


class FargateContainerLifecycle:
    """Compose independently implemented lifecycle methods behind one protocol."""

    def __init__(
        self,
        *,
        config: TenantFleetConfig,
        repository: LifecycleRepository,
        s3_files: S3FilesAdapter,
        iam: TenantIamAdapter,
        secrets: TenantSecretsManagerAdapter,
        ecs: TenantEcsAdapter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        logger: logging.Logger | None = None,
    ) -> None:
        self._context = LifecycleContext(
            config=config,
            repository=repository,
            s3_files=s3_files,
            iam=iam,
            secrets=secrets,
            ecs=ecs,
            clock=clock,
            logger=logger or logging.getLogger(__name__),
        )

    def provision_gateway(
        self,
        organization_id: str,
        size_profile: SizeProfile = SizeProfile.SMALL,
    ) -> ProvisionGatewayResult:
        return put_fargate_container(self._context, organization_id, size_profile)

    def get_gateway(self, organization_id: str) -> TenantDeployment | None:
        return get_fargate_container(self._context, organization_id)

    def start_gateway(self, organization_id: str) -> TenantDeployment:
        return post_start_fargate_container(self._context, organization_id)

    def stop_gateway(self, organization_id: str) -> TenantDeployment:
        return post_stop_fargate_container(self._context, organization_id)

    def delete_gateway(self, organization_id: str) -> TenantDeployment:
        return delete_fargate_container(self._context, organization_id)

    def rotate_api_credential(self, organization_id: str) -> RotatedApiCredential:
        return post_rotate_api_credential(self._context, organization_id)


def create_fargate_container_lifecycle_from_environment() -> FargateContainerLifecycle:
    """Compose production lifecycle methods using boto3's credential chain."""
    config = TenantFleetConfig.from_environment()
    repository = ControlPlaneDbClient(
        _required_database_url(),
        initialize_schema=False,
    )
    return FargateContainerLifecycle(
        config=config,
        repository=repository,
        s3_files=S3FilesAdapter(
            cast(S3FilesClient, create_boto3_client("s3files", config.aws_region))
        ),
        iam=TenantIamAdapter(cast(IamClient, create_boto3_client("iam", config.aws_region))),
        secrets=TenantSecretsManagerAdapter(
            cast(
                SecretsManagerClient,
                create_boto3_client("secretsmanager", config.aws_region),
            )
        ),
        ecs=TenantEcsAdapter(cast(EcsClient, create_boto3_client("ecs", config.aws_region))),
    )


def _required_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    return database_url
