"""Small typed boundaries around the boto3 clients used by Fargate deployment."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

AwsResponse = dict[str, Any]


class Ec2Client(Protocol):
    """Subset of EC2 used for existing-network discovery."""

    def describe_vpcs(self, **kwargs: Any) -> AwsResponse: ...

    def describe_subnets(self, **kwargs: Any) -> AwsResponse: ...


class EcrClient(Protocol):
    """Subset of ECR used for existing-repository discovery."""

    def describe_repositories(self, **kwargs: Any) -> AwsResponse: ...


class S3Client(Protocol):
    """Subset of S3 used to validate the shared backing bucket."""

    def head_bucket(self, **kwargs: Any) -> AwsResponse: ...

    def get_bucket_encryption(self, **kwargs: Any) -> AwsResponse: ...

    def get_public_access_block(self, **kwargs: Any) -> AwsResponse: ...


class S3FilesClient(Protocol):
    """S3 Files control-plane subset.

    Keeping this boundary local makes the newly released service straightforward
    to fake in unit tests and avoids coupling domain code to generated SDK types.
    """

    def create_file_system(self, **kwargs: Any) -> AwsResponse: ...

    def list_file_systems(self, **kwargs: Any) -> AwsResponse: ...

    def create_mount_target(self, **kwargs: Any) -> AwsResponse: ...

    def list_mount_targets(self, **kwargs: Any) -> AwsResponse: ...

    def create_access_point(self, **kwargs: Any) -> AwsResponse: ...

    def list_access_points(self, **kwargs: Any) -> AwsResponse: ...

    def put_file_system_policy(self, **kwargs: Any) -> AwsResponse: ...


class IamClient(Protocol):
    """Subset of IAM used for tenant task roles."""

    def create_role(self, **kwargs: Any) -> AwsResponse: ...

    def get_role(self, **kwargs: Any) -> AwsResponse: ...

    def put_role_policy(self, **kwargs: Any) -> AwsResponse: ...


class SecretsManagerClient(Protocol):
    """Subset of Secrets Manager used by tenant credential records."""

    def create_secret(self, **kwargs: Any) -> AwsResponse: ...

    def describe_secret(self, **kwargs: Any) -> AwsResponse: ...

    def put_secret_value(self, **kwargs: Any) -> AwsResponse: ...

    def tag_resource(self, **kwargs: Any) -> AwsResponse: ...


class EcsClient(Protocol):
    """Subset of ECS used for task definitions and services."""

    def register_task_definition(self, **kwargs: Any) -> AwsResponse: ...

    def create_service(self, **kwargs: Any) -> AwsResponse: ...

    def update_service(self, **kwargs: Any) -> AwsResponse: ...

    def describe_services(self, **kwargs: Any) -> AwsResponse: ...


def aws_tags(tags: Mapping[str, str]) -> list[dict[str, str]]:
    """Convert stable application tags to AWS's common key/value shape."""
    return [{"key": key, "value": value} for key, value in sorted(tags.items())]


def aws_iam_tags(tags: Mapping[str, str]) -> list[dict[str, str]]:
    """Convert stable application tags to IAM's title-cased tag shape."""
    return [{"Key": key, "Value": value} for key, value in sorted(tags.items())]


def first_required(
    values: Sequence[Mapping[str, Any]],
    *,
    resource: str,
) -> Mapping[str, Any]:
    """Return exactly one discovered AWS resource."""
    if len(values) != 1:
        raise AwsResourceConfigurationError(
            f"Expected exactly one {resource}; discovered {len(values)}"
        )
    return values[0]


class AwsResourceConfigurationError(RuntimeError):
    """Raised when existing AWS resources violate an MVP invariant."""
