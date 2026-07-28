"""Least-privilege IAM grants for runtime tenant reconciliation."""

from __future__ import annotations

from collections.abc import Mapping

from aws_cdk import Fn, Stack
from aws_cdk import aws_iam as iam


def grant_lifecycle_permissions(
    scope: Stack,
    role: iam.Role,
    fleet: Mapping[str, str],
) -> None:
    """Allow the Lambda to reconcile only its tenant resource namespace."""
    resource_prefix = fleet["OPENSRE_FARGATE_RESOURCE_PREFIX"]
    tenant_role_arn = Fn.join(
        "",
        [
            "arn:",
            scope.partition,
            ":iam::",
            scope.account,
            ":role/",
            resource_prefix,
            "-*-gateway-task",
        ],
    )
    tenant_secret_arn_prefix = Fn.join(
        "",
        [
            "arn:",
            scope.partition,
            ":secretsmanager:",
            scope.region,
            ":",
            scope.account,
            ":secret:",
            resource_prefix,
            "/tenants/*",
        ],
    )
    public_secret_arn = Fn.join("", [tenant_secret_arn_prefix, "/public-api-*"])
    bootstrap_secret_arn = Fn.join(
        "",
        [tenant_secret_arn_prefix, "/credentials-api-bootstrap-*"],
    )
    tenant_service_arn = Fn.join(
        "",
        [
            "arn:",
            scope.partition,
            ":ecs:",
            scope.region,
            ":",
            scope.account,
            ":service/",
            resource_prefix,
            "-gateways/",
            resource_prefix,
            "-*-gateway",
        ],
    )
    tenant_task_definition_arn = Fn.join(
        "",
        [
            "arn:",
            scope.partition,
            ":ecs:",
            scope.region,
            ":",
            scope.account,
            ":task-definition/",
            resource_prefix,
            "-*-gateway:*",
        ],
    )
    file_system_arn = fleet["OPENSRE_S3_FILESYSTEM_ARN"]

    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "ecs:CreateService",
                "ecs:DeleteService",
                "ecs:DescribeServices",
                "ecs:UpdateService",
            ],
            resources=[tenant_service_arn],
        )
    )
    # Task-definition APIs do not support resource-level IAM; AWS requires "*".
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "ecs:DeregisterTaskDefinition",
                "ecs:DescribeTaskDefinition",
                "ecs:RegisterTaskDefinition",
            ],
            resources=["*"],
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["ecs:TagResource"],
            resources=[tenant_task_definition_arn],
            conditions={
                "StringEquals": {
                    "aws:RequestTag/managed-by": "opensre",
                    "ecs:CreateAction": "RegisterTaskDefinition",
                }
            },
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "iam:CreateRole",
                "iam:GetRole",
                "iam:PutRolePolicy",
                "iam:TagRole",
            ],
            resources=[tenant_role_arn],
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=[
                tenant_role_arn,
                fleet["OPENSRE_ECS_EXECUTION_ROLE_ARN"],
            ],
            conditions={"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}},
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "s3files:CreateAccessPoint",
                "s3files:CreateMountTarget",
                "s3files:ListAccessPoints",
                "s3files:ListMountTargets",
                "s3files:PutFileSystemPolicy",
                "s3files:TagResource",
            ],
            resources=[
                file_system_arn,
                Fn.join("", [file_system_arn, "/access-point/*"]),
            ],
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "secretsmanager:CreateSecret",
                "secretsmanager:GetSecretValue",
                "secretsmanager:PutSecretValue",
            ],
            resources=[public_secret_arn],
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "secretsmanager:DeleteResourcePolicy",
                "secretsmanager:DescribeSecret",
                "secretsmanager:GetResourcePolicy",
                "secretsmanager:PutResourcePolicy",
                "secretsmanager:TagResource",
            ],
            resources=[public_secret_arn, bootstrap_secret_arn],
        )
    )
