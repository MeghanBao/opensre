"""Typed records and repository ports for the Fargate control plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


class SizeProfile(StrEnum):
    """Supported Fargate task sizes."""

    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


class DeploymentDesiredState(StrEnum):
    """Operator-requested state for a tenant Gateway."""

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    DELETED = "DELETED"


class DeploymentActualState(StrEnum):
    """Last observed state of a tenant Gateway."""

    PROVISIONING = "PROVISIONING"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    DELETED = "DELETED"
    FAILED = "FAILED"


class AgentRunStatus(StrEnum):
    """Lifecycle state of a remotely submitted agent run."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AgentRunSource(StrEnum):
    """Supported sources for durable Gateway work."""

    API = "API"
    SLACK = "SLACK"
    TELEGRAM = "TELEGRAM"
    SCHEDULER = "SCHEDULER"


@dataclass(frozen=True, slots=True)
class TenantDeployment:
    """Non-secret desired and observed state for one tenant deployment."""

    organization_id: str
    desired_state: DeploymentDesiredState
    actual_state: DeploymentActualState
    size_profile: SizeProfile
    created_at: datetime
    updated_at: datetime
    cluster_arn: str | None = None
    service_arn: str | None = None
    task_definition_arn: str | None = None
    task_role_arn: str | None = None
    s3_filesystem_arn: str | None = None
    s3_access_point_arn: str | None = None
    bootstrap_secret_arn: str | None = None
    last_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRun:
    """A durable, organization-scoped unit of Gateway work."""

    id: str
    organization_id: str
    source: AgentRunSource
    prompt: str
    status: AgentRunStatus
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    source_event_id: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    claimed_by: str | None = None
    lease_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TenantApiCredential:
    """Metadata mapping for a public bearer credential.

    Only the opaque key identifier and the Secrets Manager ARN are persisted.
    The bearer secret itself must never be stored in this record.
    """

    key_id: str
    organization_id: str
    secret_arn: str
    enabled: bool
    created_at: datetime
    updated_at: datetime
    rotated_at: datetime | None = None


class DeploymentRepository(Protocol):
    """Persistence port for tenant deployment state."""

    def save_deployment(self, deployment: TenantDeployment) -> TenantDeployment:
        """Insert or replace the tenant's current deployment state."""

    def get_deployment(self, organization_id: str) -> TenantDeployment | None:
        """Return the tenant deployment, if it exists."""


class AgentRunRepository(Protocol):
    """Persistence port for durable, leased agent work."""

    def enqueue_run(
        self,
        *,
        organization_id: str,
        source: AgentRunSource,
        prompt: str,
        source_event_id: str | None = None,
    ) -> AgentRun:
        """Create a queued run, or return the existing source-event duplicate."""

    def get_run(self, run_id: str) -> AgentRun | None:
        """Return a run by identifier."""

    def claim_next_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None:
        """Claim queued or lease-expired work scoped to one organization."""

    def renew_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool:
        """Extend a live lease owned by ``worker_id``."""

    def finish_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        """Finish work when the caller still owns its lease."""


class TenantApiCredentialRepository(Protocol):
    """Persistence port for public API credential metadata."""

    def save_api_credential(self, credential: TenantApiCredential) -> TenantApiCredential:
        """Insert or update non-secret credential metadata."""

    def get_api_credential(self, key_id: str) -> TenantApiCredential | None:
        """Resolve an opaque key identifier to its tenant metadata."""

    def disable_api_credentials(self, organization_id: str) -> int:
        """Disable every public credential for an organization."""
