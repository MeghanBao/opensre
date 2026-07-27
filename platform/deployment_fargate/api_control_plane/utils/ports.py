"""Persistence and lifecycle boundaries for the Fargate control plane."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol

from platform.deployment_fargate.api_control_plane.utils.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStatus,
    ProvisionGatewayResult,
    RotatedApiCredential,
    SizeProfile,
    TenantApiCredential,
    TenantDeployment,
)


class DeploymentRepository(Protocol):
    def upsert_tenant_deployment(self, deployment: TenantDeployment) -> TenantDeployment: ...

    def fetch_tenant_deployment_by_organization_id(
        self,
        organization_id: str,
    ) -> TenantDeployment | None: ...

    def count_deployments_with_running_desired_state(
        self,
        *,
        exclude_organization_id: str | None = None,
    ) -> int: ...


class AgentRunRepository(Protocol):
    def enqueue_agent_run(
        self,
        *,
        organization_id: str,
        source: AgentRunSource,
        prompt: str,
        source_event_id: str | None = None,
    ) -> AgentRun: ...

    def fetch_agent_run_by_id(self, run_id: str) -> AgentRun | None: ...

    def claim_oldest_available_agent_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None: ...

    def extend_owned_agent_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool: ...

    def finalize_owned_agent_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool: ...


class TenantApiCredentialRepository(Protocol):
    def upsert_tenant_api_credential(
        self,
        credential: TenantApiCredential,
    ) -> TenantApiCredential: ...

    def fetch_tenant_api_credential_by_key_id(
        self,
        key_id: str,
    ) -> TenantApiCredential | None: ...

    def disable_active_tenant_api_credentials_for_organization(
        self,
        organization_id: str,
    ) -> int: ...


class LifecycleService(Protocol):
    def provision_gateway(
        self,
        organization_id: str,
        size_profile: SizeProfile,
    ) -> ProvisionGatewayResult: ...

    def get_gateway(self, organization_id: str) -> TenantDeployment | None: ...

    def start_gateway(self, organization_id: str) -> TenantDeployment: ...

    def stop_gateway(self, organization_id: str) -> TenantDeployment: ...

    def delete_gateway(self, organization_id: str) -> TenantDeployment: ...

    def rotate_api_credential(self, organization_id: str) -> RotatedApiCredential: ...
