"""Lifecycle boundary shared by the Lambda API and Fargate reconciler."""

from __future__ import annotations

from typing import Protocol

from gateway.control_plane.contracts import SizeProfile, TenantDeployment
from platform.deployment.fargate.lifecycle_models import (
    ProvisionGatewayResult,
    RotatedApiCredential,
)


class LifecycleService(Protocol):
    """Tenant Gateway lifecycle operations exposed by the control-plane API."""

    def provision_gateway(
        self,
        organization_id: str,
        size_profile: SizeProfile,
    ) -> ProvisionGatewayResult:
        """Reconcile every long-lived resource for a tenant."""

    def get_gateway(self, organization_id: str) -> TenantDeployment | None:
        """Return the tenant's current desired and observed state."""

    def start_gateway(self, organization_id: str) -> TenantDeployment:
        """Set desired count to one and return the updated deployment."""

    def stop_gateway(self, organization_id: str) -> TenantDeployment:
        """Set desired count to zero while preserving durable storage."""

    def delete_gateway(self, organization_id: str) -> TenantDeployment:
        """Delete compute and disable credentials while preserving storage."""

    def rotate_api_credential(self, organization_id: str) -> RotatedApiCredential:
        """Disable the old public credential and return a replacement once."""
