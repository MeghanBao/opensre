"""POST start method and lifecycle operation for a tenant Fargate container."""

from __future__ import annotations

from typing import Any

from platform.deployment_fargate.api_control_plane.methods.lifecycle_context import (
    LifecycleContext,
)
from platform.deployment_fargate.api_control_plane.methods.lifecycle_support import (
    assert_can_activate,
    require_live_deployment,
    set_desired_count,
)
from platform.deployment_fargate.api_control_plane.utils.models import TenantDeployment
from platform.deployment_fargate.api_control_plane.utils.ports import LifecycleService
from platform.deployment_fargate.utils.http_lambda import jsonable, response


def method_post_start_fargate_container(
    _event: dict[str, Any],
    lifecycle: LifecycleService,
    organization_id: str,
) -> dict[str, Any]:
    """Start one tenant Fargate container."""
    deployment = lifecycle.start_gateway(organization_id)
    return response(200, {"deployment": jsonable(deployment)})


def post_start_fargate_container(
    context: LifecycleContext,
    organization_id: str,
) -> TenantDeployment:
    """Set desired count to one for a previously stopped deployment."""
    deployment = require_live_deployment(context, organization_id)
    assert_can_activate(context, deployment)
    return set_desired_count(context, deployment, desired_count=1)
