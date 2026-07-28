"""Least-privilege IAM grants for public-run authorization and enqueue."""

from __future__ import annotations

from collections.abc import Mapping

from aws_cdk import Fn, Stack
from aws_cdk import aws_iam as iam


def grant_public_forwarder_permissions(
    scope: Stack,
    role: iam.Role,
    fleet: Mapping[str, str],
    *,
    database_url_secret_arn: str,
) -> None:
    """Allow the Lambda to read the database URL and tenant public API secrets."""
    resource_prefix = fleet["OPENSRE_FARGATE_RESOURCE_PREFIX"]
    public_secret_arn = Fn.join(
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
            "/tenants/*/public-api-*",
        ],
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[database_url_secret_arn, public_secret_arn],
        )
    )
