"""Least-privilege IAM policies for tenant S3 Files mounts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from platform.deployment.fargate.aws_types import IamClient, aws_iam_tags

S3_FILES_CLIENT_ACTIONS = ("s3files:ClientMount", "s3files:ClientWrite")
S3_FILES_ACCESS_POINT_CONDITION = "s3files:AccessPointArn"


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
        ],
    }


def build_file_system_isolation_policy(
    *,
    file_system_arn: str,
    tenant_bindings: tuple[TenantMountBinding, ...],
) -> dict[str, Any]:
    """Deny access-point-free and cross-tenant mounts at the filesystem boundary.

    IAM allows are additive. The resource policy therefore supplies explicit
    denies so accidentally broad identity permissions cannot bypass isolation.
    """
    statements: list[dict[str, Any]] = [
        {
            "Sid": "DenyMountWithoutAccessPoint",
            "Effect": "Deny",
            "Principal": "*",
            "Action": list(S3_FILES_CLIENT_ACTIONS),
            "Resource": file_system_arn,
            "Condition": {"Null": {S3_FILES_ACCESS_POINT_CONDITION: "true"}},
        }
    ]
    for index, binding in enumerate(tenant_bindings):
        statements.append(
            {
                "Sid": f"DenyTenantCrossAccessPoint{index + 1}",
                "Effect": "Deny",
                "Principal": {"AWS": binding.task_role_arn},
                "Action": list(S3_FILES_CLIENT_ACTIONS),
                "Resource": file_system_arn,
                "Condition": {
                    "ArnNotEquals": {
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
                Tags=aws_iam_tags(tags),
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
