"""CloudFormation export names shared by Fargate infrastructure stacks."""

from __future__ import annotations

CONTROL_PLANE_FLEET_ENVIRONMENT = (
    "OPENSRE_FARGATE_CLUSTER_ARN",
    "OPENSRE_ECS_EXECUTION_ROLE_ARN",
    "OPENSRE_GATEWAY_LOG_GROUP",
    "OPENSRE_FARGATE_SUBNET_IDS",
    "OPENSRE_FARGATE_SECURITY_GROUP_IDS",
    "OPENSRE_S3_FILES_MOUNT_SECURITY_GROUP_IDS",
    "OPENSRE_S3_FILESYSTEM_ID",
    "OPENSRE_S3_FILESYSTEM_ARN",
    "OPENSRE_GATEWAY_IMAGE",
    "OPENSRE_CREDENTIALS_API_URL",
    "OPENSRE_FARGATE_RESOURCE_PREFIX",
)

PUBLIC_FORWARDER_FLEET_ENVIRONMENT = ("OPENSRE_FARGATE_RESOURCE_PREFIX",)


def fleet_export_name(env_name: str) -> str:
    """Build the export name for a control-plane fleet environment variable."""
    return f"opensre-fleet:{env_name.replace('_', '-')}"
