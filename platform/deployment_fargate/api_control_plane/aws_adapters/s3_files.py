"""Amazon S3 Files filesystem, mount-target, and tenant access-point adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from platform.deployment_fargate.api_control_plane.aws_adapters.iam import (
    TenantMountBinding,
    build_file_system_isolation_policy,
)
from platform.deployment_fargate.api_control_plane.aws_adapters.types import S3FilesClient, aws_tags


@dataclass(frozen=True)
class S3FilesFileSystem:
    """Identifiers for the shared S3 Files filesystem."""

    file_system_id: str
    file_system_arn: str


@dataclass(frozen=True)
class S3FilesAccessPoint:
    """Identifiers for one tenant-rooted access point."""

    access_point_id: str
    access_point_arn: str


class S3FilesAdapter:
    """Thin, injectable control-plane adapter for the new S3 Files service."""

    def __init__(self, client: S3FilesClient) -> None:
        self._client = client

    def create_file_system(
        self,
        *,
        bucket_arn: str,
        prefix: str,
        kms_key_id: str,
        service_role_arn: str,
        client_token: str,
        tags: dict[str, str],
    ) -> S3FilesFileSystem:
        """Create the shared KMS-encrypted filesystem over an existing bucket."""
        response = self._client.create_file_system(
            bucket=bucket_arn,
            prefix=prefix,
            clientToken=client_token,
            kmsKeyId=kms_key_id,
            roleArn=service_role_arn,
            tags=aws_tags(tags),
        )
        return self._file_system_from_response(response)

    def create_mount_target(
        self,
        *,
        file_system_id: str,
        subnet_id: str,
        security_group_ids: tuple[str, ...],
    ) -> str:
        """Create a mount target in an explicitly selected existing subnet."""
        response = self._client.create_mount_target(
            fileSystemId=file_system_id,
            subnetId=subnet_id,
            securityGroups=list(security_group_ids),
        )
        return self._required_string(response, "mountTargetId")

    def create_tenant_access_point(
        self,
        *,
        file_system_id: str,
        organization_id: str,
        uid: int,
        gid: int,
        client_token: str,
        tags: dict[str, str],
    ) -> S3FilesAccessPoint:
        """Create an access point enforcing tenant root and POSIX identity."""
        response = self._client.create_access_point(
            fileSystemId=file_system_id,
            clientToken=client_token,
            posixUser={"uid": uid, "gid": gid},
            rootDirectory={
                "path": f"/organizations/{organization_id}",
                "creationPermissions": {
                    "ownerUid": uid,
                    "ownerGid": gid,
                    "permissions": "0700",
                },
            },
            tags=aws_tags(tags),
        )
        return S3FilesAccessPoint(
            access_point_id=self._required_string(response, "accessPointId"),
            access_point_arn=self._required_string(response, "accessPointArn"),
        )

    def put_isolation_policy(
        self,
        *,
        file_system_id: str,
        file_system_arn: str,
        tenant_bindings: tuple[TenantMountBinding, ...],
    ) -> None:
        """Apply explicit filesystem denies for missing and foreign access points."""
        policy = build_file_system_isolation_policy(
            file_system_arn=file_system_arn,
            tenant_bindings=tenant_bindings,
        )
        self._client.put_file_system_policy(
            fileSystemId=file_system_id,
            policy=json.dumps(policy, sort_keys=True),
        )

    @staticmethod
    def _file_system_from_response(response: dict[str, Any]) -> S3FilesFileSystem:
        return S3FilesFileSystem(
            file_system_id=S3FilesAdapter._required_string(response, "fileSystemId"),
            file_system_arn=S3FilesAdapter._required_string(response, "fileSystemArn"),
        )

    @staticmethod
    def _required_string(response: dict[str, Any], key: str) -> str:
        value = response.get(key)
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"S3 Files response did not include {key}")
        return value
