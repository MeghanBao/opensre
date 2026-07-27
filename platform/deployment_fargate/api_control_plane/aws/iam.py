"""Least-privilege IAM policies for tenant S3 Files mounts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import ClientError

from platform.deployment_fargate.api_control_plane.aws.tags import iam_tags

S3_FILES_CLIENT_ACTIONS = ("s3files:ClientMount", "s3files:ClientWrite")
S3_FILES_ACCESS_POINT_CONDITION = "s3files:AccessPointArn"


class IamClient(Protocol):
    def create_role(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_role(self, **kwargs: Any) -> dict[str, Any]: ...

    def put_role_policy(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TenantMountBinding:
    """A task role's sole allowed S3 Files access point."""

    task_role_arn: str
    access_point_arn: str


def build_tenant_task_policy(
    *,
    file_system_arn: str,
    access_point_arn: str,
    bootstrap_secret_arn: str,
) -> dict[str, Any]:
    """Build the complete tenant task-role policy.

    It deliberately omits ``ClientRootAccess`` and direct S3 object actions.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "MountOnlyTenantAccessPoint",
                "Effect": "Allow",
                "Action": list(S3_FILES_CLIENT_ACTIONS),
                "Resource": file_system_arn,
                "Condition": {
                    "ArnEquals": {
                        S3_FILES_ACCESS_POINT_CONDITION: access_point_arn,
                    }
                },
            },
            {
                "Sid": "ReadOnlyCredentialsBootstrapSecret",
                "Effect": "Allow",
                "Action": "secretsmanager:GetSecretValue",
                "Resource": bootstrap_secret_arn,
            },
            {
                "Sid": "InvokeBedrockModels",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            },
        ],
    }


def build_file_system_isolation_policy(
    *,
    file_system_arn: str,
    tenant_bindings: tuple[TenantMountBinding, ...],
) -> dict[str, Any]:
    """Allow each tenant access point and deny every other mount for that role.

    S3 Files validates principals when the policy is installed, so every
    statement names a concrete task role. The allow/deny pair follows the AWS
    access-point isolation pattern and the ``StringNotEquals`` deny also covers
    a direct mount that omits the access-point condition key.
    """
    statements: list[dict[str, Any]] = []
    for index, binding in enumerate(tenant_bindings):
        statements.append(
            {
                "Sid": f"AllowTenantAccessPoint{index + 1}",
                "Effect": "Allow",
                "Principal": {"AWS": binding.task_role_arn},
                "Action": list(S3_FILES_CLIENT_ACTIONS),
                "Resource": file_system_arn,
                "Condition": {
                    "StringEquals": {
                        S3_FILES_ACCESS_POINT_CONDITION: binding.access_point_arn,
                    }
                },
            }
        )
        statements.append(
            {
                "Sid": f"DenyTenantCrossAccessPoint{index + 1}",
                "Effect": "Deny",
                "Principal": {"AWS": binding.task_role_arn},
                "Action": "s3files:Client*",
                "Resource": file_system_arn,
                "Condition": {
                    "StringNotEquals": {
                        S3_FILES_ACCESS_POINT_CONDITION: binding.access_point_arn,
                    }
                },
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


class TenantIamAdapter:
    """Create and constrain one ECS task role per organization."""

    def __init__(self, iam: IamClient) -> None:
        self._iam = iam

    def ensure_task_role(
        self,
        *,
        role_name: str,
        file_system_arn: str,
        access_point_arn: str,
        bootstrap_secret_arn: str,
        tags: dict[str, str],
    ) -> str:
        """Return a tenant role ARN and replace its single managed inline policy."""
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        try:
            response = self._iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(assume_role_policy, sort_keys=True),
                Description="OpenSRE tenant Gateway ECS task role",
                Tags=iam_tags(tags),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "EntityAlreadyExists":
                raise
            response = self._iam.get_role(RoleName=role_name)

        role = response.get("Role")
        role_arn = role.get("Arn") if isinstance(role, dict) else None
        if not isinstance(role_arn, str) or not role_arn:
            raise RuntimeError("IAM response did not include the task role ARN")

        task_policy = build_tenant_task_policy(
            file_system_arn=file_system_arn,
            access_point_arn=access_point_arn,
            bootstrap_secret_arn=bootstrap_secret_arn,
        )
        self._iam.put_role_policy(
            RoleName=role_name,
            PolicyName="OpenSreTenantGateway",
            PolicyDocument=json.dumps(task_policy, sort_keys=True),
        )
        return role_arn
