"""Pure validation helpers for tenant-isolated ECS S3 Files definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from platform.deployment.fargate.images import require_immutable_ecr_image

WORKSPACE_VOLUME_NAME = "tenant-workspace"
WORKSPACE_CONTAINER_PATH = "/workspace"
EXPECTED_ENVIRONMENT = {
    "HOME": "/workspace/home",
    "OPENSRE_WORKSPACE": "/workspace/files",
}
_NON_SECRET_CREDENTIAL_ENVIRONMENT = {"OPENSRE_CREDENTIALS_API_URL"}
_SECRET_ENVIRONMENT_MARKERS = (
    "API_KEY",
    "ACCESS_KEY",
    "AUTHORIZATION",
    "CREDENTIAL",
    "DATABASE_URL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)
_DISALLOWED_CLIENT_ACTIONS = {
    "s3files:ClientRootAccess",
    "s3:GetObject",
    "s3:GetObjectVersion",
    "s3:PutObject",
}


def s3files_volume(
    *,
    file_system_arn: str,
    access_point_arn: str,
) -> dict[str, Any]:
    """Build the access-point-rooted volume fragment supported by ECS."""

    if not file_system_arn.strip() or not access_point_arn.strip():
        raise ValueError("S3 Files filesystem and access point ARNs are required")
    if not access_point_arn.startswith(f"{file_system_arn}/access-point/"):
        raise ValueError("access point ARN must belong to the configured filesystem")
    return {
        "name": WORKSPACE_VOLUME_NAME,
        "s3filesVolumeConfiguration": {
            "fileSystemArn": file_system_arn,
            "rootDirectory": "/",
            "accessPointArn": access_point_arn,
        },
    }


def validate_task_definition(
    task_definition: Mapping[str, Any],
    *,
    file_system_arn: str,
    access_point_arn: str,
    container_name: str,
) -> None:
    """Reject a task definition that weakens the tenant mount/image contract."""

    if not task_definition.get("taskRoleArn"):
        raise ValueError("S3 Files task definition requires a task IAM role")
    if task_definition.get("networkMode") != "awsvpc":
        raise ValueError("Fargate task definition must use awsvpc")
    compatibilities = task_definition.get("requiresCompatibilities", [])
    if "FARGATE" not in compatibilities:
        raise ValueError("task definition must require Fargate")

    expected_volume = s3files_volume(
        file_system_arn=file_system_arn,
        access_point_arn=access_point_arn,
    )
    volumes = task_definition.get("volumes", [])
    if expected_volume not in volumes:
        raise ValueError("task definition must mount only the tenant S3 Files access point")

    containers = task_definition.get("containerDefinitions", [])
    container = next(
        (item for item in containers if item.get("name") == container_name),
        None,
    )
    if container is None:
        raise ValueError("expected container is missing")
    require_immutable_ecr_image(str(container.get("image", "")))
    if container.get("privileged", False):
        raise ValueError("tenant container cannot be privileged")

    mount_points = container.get("mountPoints", [])
    expected_mount = {
        "sourceVolume": WORKSPACE_VOLUME_NAME,
        "containerPath": WORKSPACE_CONTAINER_PATH,
        "readOnly": False,
    }
    if expected_mount not in mount_points:
        raise ValueError("tenant S3 Files access point must be writable at /workspace")

    environment = {
        str(item.get("name")): str(item.get("value")) for item in container.get("environment", [])
    }
    for name, value in EXPECTED_ENVIRONMENT.items():
        if environment.get(name) != value:
            raise ValueError(f"container environment must set {name}")
    for name in environment:
        if name not in _NON_SECRET_CREDENTIAL_ENVIRONMENT and any(
            marker in name.upper() for marker in _SECRET_ENVIRONMENT_MARKERS
        ):
            raise ValueError("secret-like values cannot be embedded in container environment")


def validate_tenant_mount_policy(
    policy: Mapping[str, Any],
    *,
    access_point_arn: str,
) -> None:
    """Reject direct S3 access, root access, or unscoped S3 Files client grants."""

    statements = policy.get("Statement")
    if not isinstance(statements, Sequence) or isinstance(statements, (str, bytes)):
        raise ValueError("IAM policy must contain statements")

    saw_scoped_mount = False
    for statement in statements:
        if not isinstance(statement, Mapping):
            raise ValueError("IAM policy statement must be an object")
        actions = _actions(statement.get("Action"))
        if actions & _DISALLOWED_CLIENT_ACTIONS:
            raise ValueError("tenant task policy grants root or direct S3 object access")
        if not actions & {"s3files:ClientMount", "s3files:ClientWrite"}:
            continue
        condition = statement.get("Condition", {})
        equals = condition.get("StringEquals", {}) if isinstance(condition, Mapping) else {}
        if (
            statement.get("Effect") == "Allow"
            and isinstance(equals, Mapping)
            and equals.get("s3files:AccessPointArn") == access_point_arn
        ):
            saw_scoped_mount = True
            continue
        raise ValueError("S3 Files client grants must be scoped to the tenant access point")
    if not saw_scoped_mount:
        raise ValueError("tenant task policy must grant scoped mount access")


def _actions(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, Sequence):
        return {str(item) for item in value}
    return set()
