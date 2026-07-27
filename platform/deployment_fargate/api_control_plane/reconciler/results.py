"""Typed outputs for tenant Gateway lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass

from platform.deployment_fargate.api_control_plane.contracts.contracts import TenantDeployment


@dataclass(frozen=True, slots=True)
class ProvisionGatewayResult:
    """Provisioned state plus a bearer returned only when newly generated."""

    deployment: TenantDeployment
    api_credential: str | None


@dataclass(frozen=True, slots=True)
class RotatedApiCredential:
    """A rotated public bearer returned to the authorized caller exactly once."""

    key_id: str
    api_credential: str
