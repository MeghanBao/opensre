"""Unit tests for dated ECS gateway service naming."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from platform.deployment_multi_tenant.lambda_control_plane.aws.ecs import FargateServiceState
from platform.deployment_multi_tenant.lambda_control_plane.methods.lifecycle_context import (
    LifecycleContext,
)
from platform.deployment_multi_tenant.lambda_control_plane.methods.lifecycle_support import (
    find_gateway_service,
    legacy_service_name,
    service_day_prefix,
    service_name,
    service_name_from_arn,
)
from platform.deployment_multi_tenant.lambda_control_plane.utils.get_fleet_config import (
    TenantFleetConfig,
)


def _context(*, clock: datetime) -> LifecycleContext:
    config = TenantFleetConfig(
        cluster_arn="arn:aws:ecs:eu-west-2:123456789012:cluster/opensre",
        file_system_id="fs-123",
        file_system_arn="arn:aws:s3files:eu-west-2:123456789012:file-system/fs-123",
        gateway_image="123456789012.dkr.ecr.eu-west-2.amazonaws.com/opensre@sha256:" + ("a" * 64),
        execution_role_arn="arn:aws:iam::123456789012:role/ecs-execution",
        credentials_api_url="",
        log_group="/opensre/organization-agents/dev",
        aws_region="eu-west-2",
        subnet_ids=("subnet-a",),
        security_group_ids=("sg-gateway",),
        mount_security_group_ids=("sg-s3files-mount",),
        resource_prefix="opensre",
    )
    return LifecycleContext(
        config=config,
        repository=MagicMock(),
        s3_files=MagicMock(),
        iam=MagicMock(),
        secrets=MagicMock(),
        ecs=MagicMock(),
        clock=lambda: clock,
        logger=MagicMock(),
    )


def test_service_name_uses_yymmdd_utc_prefix() -> None:
    created = datetime(2026, 7, 29, 15, 30, tzinfo=UTC)
    context = _context(clock=created)

    assert service_day_prefix(created) == "260729"
    assert (
        service_name(
            context,
            "org_3GSi5hEUnKuUKXxfIHHyHSZ5pUP",
            created_at=created,
        )
        == "260729-opensre-org_3GSi5hEUnKuUKXxfIHHyHSZ5pUP-gateway"
    )
    assert (
        legacy_service_name(context, "org_3GSi5hEUnKuUKXxfIHHyHSZ5pUP")
        == "opensre-org_3GSi5hEUnKuUKXxfIHHyHSZ5pUP-gateway"
    )


def test_service_name_from_arn_extracts_final_segment() -> None:
    assert (
        service_name_from_arn(
            "arn:aws:ecs:us-east-1:123456789012:service/"
            "opensre-organization-agents-dev/"
            "260729-opensre-org_abc-gateway"
        )
        == "260729-opensre-org_abc-gateway"
    )


def test_find_gateway_service_falls_back_to_legacy_name() -> None:
    created = datetime(2026, 7, 29, tzinfo=UTC)
    context = _context(clock=created)
    legacy_state = FargateServiceState(
        service_arn=(
            "arn:aws:ecs:us-east-1:123456789012:service/"
            "opensre-organization-agents-dev/"
            "opensre-org_abc-gateway"
        ),
        task_definition_arn="arn:aws:ecs:us-east-1:123456789012:task-definition/x:1",
        desired_count=1,
        running_count=1,
        pending_count=0,
        status="ACTIVE",
    )

    def _describe(*, cluster: str, service_name: str) -> FargateServiceState | None:
        del cluster
        if service_name == "opensre-org_abc-gateway":
            return legacy_state
        return None

    context.ecs.describe_service.side_effect = _describe

    found = find_gateway_service(
        context,
        organization_id="org_abc",
        created_at=created,
    )
    assert found is legacy_state
