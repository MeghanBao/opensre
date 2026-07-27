"""Shared dependencies for Fargate container lifecycle methods."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from platform.deployment_fargate.api_control_plane.aws.ecs import TenantEcsAdapter
from platform.deployment_fargate.api_control_plane.aws.iam import TenantIamAdapter
from platform.deployment_fargate.api_control_plane.aws.s3_files import S3FilesAdapter
from platform.deployment_fargate.api_control_plane.aws.secrets_manager import (
    TenantSecretsManagerAdapter,
)
from platform.deployment_fargate.api_control_plane.utils.get_fleet_config import (
    TenantFleetConfig,
)
from platform.deployment_fargate.api_control_plane.utils.ports import (
    DeploymentRepository,
    TenantApiCredentialRepository,
)


class LifecycleRepository(
    DeploymentRepository,
    TenantApiCredentialRepository,
    Protocol,
):
    """Persistence capabilities shared by lifecycle methods."""


@dataclass(frozen=True, slots=True)
class LifecycleContext:
    """Dependencies shared by individual Fargate container methods."""

    config: TenantFleetConfig
    repository: LifecycleRepository
    s3_files: S3FilesAdapter
    iam: TenantIamAdapter
    secrets: TenantSecretsManagerAdapter
    ecs: TenantEcsAdapter
    clock: Callable[[], datetime]
    logger: logging.Logger
