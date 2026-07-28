"""Amazon S3 Files filesystem, mount-target, and tenant access-point adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import ClientError

from platform.deployment_fargate.api_control_plane.aws.iam import (
    TenantMountBinding,
    build_file_system_isolation_policy,
)
from platform.deployment_fargate.api_control_plane.aws.tags import resource_tags


class S3FilesClient(Protocol):
    def create_file_system(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_file_systems(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_mount_target(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_mount_targets(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_access_point(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_access_points(self, **kwargs: Any) -> dict[str, Any]: ...

    def put_file_system_policy(self, **kwargs: Any) -> dict[str, Any]: ...


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
            tags=resource_tags(tags),
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

    def ensure_mount_targets(
        self,
        *,
        file_system_id: str,
        subnet_ids: tuple[str, ...],
        security_group_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Ensure one shared filesystem mount target exists in every task subnet."""
        if not subnet_ids or not security_group_ids:
            raise ValueError("S3 Files mount targets require subnets and security groups")
        existing = self._mount_targets_by_subnet(file_system_id)
        mount_target_ids: list[str] = []
        for subnet_id in subnet_ids:
            mount_target_id = existing.get(subnet_id)
            if mount_target_id is None:
                try:
                    mount_target_id = self.create_mount_target(
                        file_system_id=file_system_id,
                        subnet_id=subnet_id,
                        security_group_ids=security_group_ids,
                    )
                except ClientError as error:
                    if error.response.get("Error", {}).get("Code") != "ConflictException":
                        raise
                    mount_target_id = self._mount_targets_by_subnet(file_system_id).get(subnet_id)
                    if mount_target_id is None:
                        raise
            mount_target_ids.append(mount_target_id)
        return tuple(mount_target_ids)

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
        root_directory = {
            "path": f"/organizations/{organization_id}",
            "creationPermissions": {
                "ownerUid": uid,
                "ownerGid": gid,
                "permissions": "0700",
            },
        }
        try:
            response = self._client.create_access_point(
                fileSystemId=file_system_id,
                clientToken=client_token,
                posixUser={"uid": uid, "gid": gid},
                rootDirectory=root_directory,
                tags=resource_tags(tags),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConflictException":
                raise
            existing = self._find_tenant_access_point(
                file_system_id=file_system_id,
                root_directory=root_directory,
                uid=uid,
                gid=gid,
            )
            if existing is None:
                raise
            return existing
        return S3FilesAccessPoint(
            access_point_id=self._required_string(response, "accessPointId"),
            access_point_arn=self._required_string(response, "accessPointArn"),
        )

    def _find_tenant_access_point(
        self,
        *,
        file_system_id: str,
        root_directory: dict[str, Any],
        uid: int,
        gid: int,
    ) -> S3FilesAccessPoint | None:
        next_token: str | None = None
        while True:
            request: dict[str, Any] = {"fileSystemId": file_system_id}
            if next_token is not None:
                request["nextToken"] = next_token
            response = self._client.list_access_points(**request)
            access_points = response.get("accessPoints")
            if not isinstance(access_points, list):
                return None
            for access_point in access_points:
                if (
                    isinstance(access_point, dict)
                    and access_point.get("rootDirectory") == root_directory
                    and access_point.get("posixUser") == {"uid": uid, "gid": gid}
                    and access_point.get("status") != "deleted"
                ):
                    return S3FilesAccessPoint(
                        access_point_id=self._required_string(
                            access_point,
                            "accessPointId",
                        ),
                        access_point_arn=self._required_string(
                            access_point,
                            "accessPointArn",
                        ),
                    )
            token = response.get("nextToken")
            if not isinstance(token, str) or not token:
                return None
            next_token = token

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

    def _mount_targets_by_subnet(self, file_system_id: str) -> dict[str, str]:
        mount_targets: dict[str, str] = {}
        next_token: str | None = None
        while True:
            request: dict[str, Any] = {"fileSystemId": file_system_id}
            if next_token is not None:
                request["nextToken"] = next_token
            response = self._client.list_mount_targets(**request)
            raw_targets = response.get("mountTargets")
            if not isinstance(raw_targets, list):
                return mount_targets
            for target in raw_targets:
                if not isinstance(target, dict) or target.get("status") == "deleted":
                    continue
                subnet_id = target.get("subnetId")
                mount_target_id = target.get("mountTargetId")
                if isinstance(subnet_id, str) and isinstance(mount_target_id, str):
                    mount_targets[subnet_id] = mount_target_id
            token = response.get("nextToken")
            if not isinstance(token, str) or not token:
                return mount_targets
            next_token = token

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
