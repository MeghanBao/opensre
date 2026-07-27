"""Typed records and repository ports for the tenant control plane."""

from platform.deployment_fargate.api_control_plane.contracts.contracts import (
    AgentRun,
    AgentRunRepository,
    AgentRunSource,
    AgentRunStatus,
    DeploymentActualState,
    DeploymentDesiredState,
    DeploymentRepository,
    SizeProfile,
    TenantApiCredential,
    TenantApiCredentialRepository,
    TenantDeployment,
)
from platform.deployment_fargate.api_control_plane.contracts.lifecycle_service import LifecycleService

__all__ = [
    "AgentRun",
    "AgentRunRepository",
    "AgentRunSource",
    "AgentRunStatus",
    "DeploymentActualState",
    "DeploymentDesiredState",
    "DeploymentRepository",
    "LifecycleService",
    "SizeProfile",
    "TenantApiCredential",
    "TenantApiCredentialRepository",
    "TenantDeployment",
]
