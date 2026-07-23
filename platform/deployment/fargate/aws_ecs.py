"""ECS task-definition and service adapters for tenant Gateways."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from platform.deployment.fargate.aws_types import EcsClient, aws_tags

_IMMUTABLE_IMAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}$")
_ALLOWED_ENVIRONMENT = frozenset(
    {
        "HOME",
        "OPENSRE_WORKSPACE",
        "ORGANIZATION_ID",
        "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN",
        "OPENSRE_CREDENTIALS_API_URL",
    }
)
_PROFILE_RESOURCES = {
    "SMALL": ("512", "1024"),
    "MEDIUM": ("1024", "2048"),
    "LARGE": ("2048", "4096"),
}


@dataclass(frozen=True)
class GatewayTaskDefinitionSpec:
    """All tenant-specific inputs for an immutable Gateway task definition."""

    family: str
    organization_id: str
    image: str
    size_profile: str
    task_role_arn: str
    execution_role_arn: str
    file_system_arn: str
    access_point_arn: str
    bootstrap_secret_arn: str
    credentials_api_url: str
    log_group: str
    log_region: str
    tags: dict[str, str]


@dataclass(frozen=True)
class FargateServiceSpec:
    """Network and desired-state inputs for one tenant ECS service."""

    cluster: str
    service_name: str
    task_definition_arn: str
    subnet_ids: tuple[str, ...]
    security_group_ids: tuple[str, ...]
    desired_count: int = 1


def validate_immutable_image(image: str) -> None:
    """Reject mutable image tags and malformed digest references."""
    if not _IMMUTABLE_IMAGE.fullmatch(image):
        raise ValueError("Gateway image must be an immutable ECR sha256 digest reference")


class TenantEcsAdapter:
    """Build valid S3 Files task definitions and control ECS services."""

    def __init__(self, ecs: EcsClient) -> None:
        self._ecs = ecs

    def register_gateway_task_definition(self, spec: GatewayTaskDefinitionSpec) -> str:
        """Register a secret-safe Fargate task definition."""
        validate_immutable_image(spec.image)
        try:
            cpu, memory = _PROFILE_RESOURCES[spec.size_profile]
        except KeyError as error:
            raise ValueError(f"Unsupported size profile: {spec.size_profile}") from error

        environment = {
            "HOME": "/workspace/home",
            "OPENSRE_WORKSPACE": "/workspace/files",
            "ORGANIZATION_ID": spec.organization_id,
            "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN": spec.bootstrap_secret_arn,
            "OPENSRE_CREDENTIALS_API_URL": spec.credentials_api_url,
        }
        if set(environment) != _ALLOWED_ENVIRONMENT:
            raise AssertionError("Task environment contains an unsupported variable")

        response = self._ecs.register_task_definition(
            family=spec.family,
            requiresCompatibilities=["FARGATE"],
            networkMode="awsvpc",
            cpu=cpu,
            memory=memory,
            taskRoleArn=spec.task_role_arn,
            executionRoleArn=spec.execution_role_arn,
            runtimePlatform={
                "cpuArchitecture": "X86_64",
                "operatingSystemFamily": "LINUX",
            },
            volumes=[
                {
                    "name": "workspace",
                    "s3filesVolumeConfiguration": {
                        "fileSystemArn": spec.file_system_arn,
                        "rootDirectory": "/",
                        "transitEncryptionPort": 2049,
                        "accessPointArn": spec.access_point_arn,
                    },
                }
            ],
            containerDefinitions=[
                {
                    "name": "gateway",
                    "image": spec.image,
                    "essential": True,
                    "environment": [
                        {"name": key, "value": value} for key, value in sorted(environment.items())
                    ],
                    "mountPoints": [
                        {
                            "sourceVolume": "workspace",
                            "containerPath": "/workspace",
                            "readOnly": False,
                        }
                    ],
                    "logConfiguration": {
                        "logDriver": "awslogs",
                        "options": {
                            "awslogs-group": spec.log_group,
                            "awslogs-region": spec.log_region,
                            "awslogs-stream-prefix": "gateway",
                        },
                    },
                }
            ],
            tags=aws_tags(spec.tags),
        )
        task_definition = response.get("taskDefinition")
        arn = (
            task_definition.get("taskDefinitionArn") if isinstance(task_definition, dict) else None
        )
        if not isinstance(arn, str) or not arn:
            raise RuntimeError("ECS response did not include the task definition ARN")
        return arn

    def create_service(self, spec: FargateServiceSpec) -> str:
        """Create a one-task service in explicit existing subnets."""
        response = self._ecs.create_service(
            cluster=spec.cluster,
            serviceName=spec.service_name,
            taskDefinition=spec.task_definition_arn,
            desiredCount=spec.desired_count,
            launchType="FARGATE",
            platformVersion="LATEST",
            enableECSManagedTags=True,
            propagateTags="TASK_DEFINITION",
            networkConfiguration=self._network_configuration(spec),
        )
        service = response.get("service")
        arn = service.get("serviceArn") if isinstance(service, dict) else None
        if not isinstance(arn, str) or not arn:
            raise RuntimeError("ECS response did not include the service ARN")
        return arn

    def update_service(self, spec: FargateServiceSpec) -> None:
        """Update task revision, desired count, and network placement."""
        self._ecs.update_service(
            cluster=spec.cluster,
            service=spec.service_name,
            taskDefinition=spec.task_definition_arn,
            desiredCount=spec.desired_count,
            forceNewDeployment=True,
            networkConfiguration=self._network_configuration(spec),
        )

    @staticmethod
    def _network_configuration(spec: FargateServiceSpec) -> dict[str, Any]:
        if not spec.subnet_ids or not spec.security_group_ids:
            raise ValueError("Fargate service requires explicit subnets and security groups")
        return {
            "awsvpcConfiguration": {
                "subnets": list(spec.subnet_ids),
                "securityGroups": list(spec.security_group_ids),
                "assignPublicIp": "DISABLED",
            }
        }
