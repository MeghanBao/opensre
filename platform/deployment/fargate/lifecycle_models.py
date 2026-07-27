"""Typed inputs and outputs for tenant Gateway lifecycle operations."""

from __future__ import annotations

import os
from dataclasses import dataclass

from gateway.control_plane.contracts import TenantDeployment


@dataclass(frozen=True, slots=True)
class FargateLifecycleConfig:
    """Shared, non-secret deployment configuration for all tenant Gateways."""

    cluster_arn: str
    file_system_id: str
    file_system_arn: str
    gateway_image: str
    execution_role_arn: str
    credentials_api_url: str
    log_group: str
    aws_region: str
    subnet_ids: tuple[str, ...]
    security_group_ids: tuple[str, ...]
    tenant_uid: int = 10001
    tenant_gid: int = 10001
    resource_prefix: str = "opensre"
    max_active_organizations: int = 10

    @classmethod
    def from_environment(cls) -> FargateLifecycleConfig:
        """Load explicit production identifiers without demo-value fallbacks."""
        region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "")).strip()
        if not region:
            raise RuntimeError("AWS_REGION is required")
        return cls(
            cluster_arn=_required_environment("OPENSRE_FARGATE_CLUSTER_ARN"),
            file_system_id=_required_environment("OPENSRE_S3_FILESYSTEM_ID"),
            file_system_arn=_required_environment("OPENSRE_S3_FILESYSTEM_ARN"),
            gateway_image=_required_environment("OPENSRE_GATEWAY_IMAGE"),
            execution_role_arn=_required_environment("OPENSRE_ECS_EXECUTION_ROLE_ARN"),
            credentials_api_url=os.getenv("OPENSRE_CREDENTIALS_API_URL", "").strip(),
            log_group=_required_environment("OPENSRE_GATEWAY_LOG_GROUP"),
            aws_region=region,
            subnet_ids=_required_csv_environment("OPENSRE_FARGATE_SUBNET_IDS"),
            security_group_ids=_required_csv_environment("OPENSRE_FARGATE_SECURITY_GROUP_IDS"),
            tenant_uid=_positive_integer_environment("OPENSRE_TENANT_UID", 10001),
            tenant_gid=_positive_integer_environment("OPENSRE_TENANT_GID", 10001),
            resource_prefix=os.getenv("OPENSRE_FARGATE_RESOURCE_PREFIX", "opensre").strip(),
            max_active_organizations=_positive_integer_environment(
                "OPENSRE_MAX_ACTIVE_ORGANIZATIONS",
                10,
            ),
        )


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


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _required_csv_environment(name: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in _required_environment(name).split(","))
    if not values or any(not value for value in values):
        raise RuntimeError(f"{name} must contain non-empty values")
    return values


def _positive_integer_environment(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    try:
        value = default if raw_value is None else int(raw_value)
    except ValueError:
        raise RuntimeError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer")
    return value
