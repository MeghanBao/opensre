"""Validate AWS resources that the MVP intentionally reuses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

AwsResponse = dict[str, Any]


class AwsResourceConfigurationError(RuntimeError):
    """Raised when existing AWS resources violate an MVP invariant."""


class Ec2Client(Protocol):
    """Subset of EC2 used for existing-network validation."""

    def describe_vpcs(self, **kwargs: Any) -> AwsResponse: ...

    def describe_subnets(self, **kwargs: Any) -> AwsResponse: ...


class EcrClient(Protocol):
    """Subset of ECR used for existing-repository validation."""

    def describe_repositories(self, **kwargs: Any) -> AwsResponse: ...


class S3Client(Protocol):
    """Subset of S3 used to validate the shared backing bucket."""

    def head_bucket(self, **kwargs: Any) -> AwsResponse: ...

    def get_bucket_encryption(self, **kwargs: Any) -> AwsResponse: ...

    def get_public_access_block(self, **kwargs: Any) -> AwsResponse: ...


def first_required(
    values: Sequence[Mapping[str, Any]],
    *,
    resource: str,
) -> Mapping[str, Any]:
    """Return exactly one matching AWS resource."""
    if len(values) != 1:
        raise AwsResourceConfigurationError(
            f"Expected exactly one {resource}; discovered {len(values)}"
        )
    return values[0]


@dataclass(frozen=True)
class ExistingAwsInfrastructure:
    """Validated identifiers for shared, pre-existing infrastructure."""

    vpc_id: str
    subnet_ids: tuple[str, ...]
    ecr_repository_arn: str
    ecr_repository_uri: str
    s3_bucket_arn: str
    s3_kms_key_id: str


class ExistingInfrastructureValidator:
    """Validate existing networking, ECR, and encrypted S3 resources."""

    def __init__(self, ec2: Ec2Client, ecr: EcrClient, s3: S3Client) -> None:
        self._ec2 = ec2
        self._ecr = ecr
        self._s3 = s3

    def validate(
        self,
        *,
        vpc_id: str,
        subnet_ids: tuple[str, ...],
        ecr_repository_name: str,
        s3_bucket_name: str,
    ) -> ExistingAwsInfrastructure:
        """Validate explicit existing resources; never select a default implicitly."""
        vpc = first_required(
            self._items(self._ec2.describe_vpcs(VpcIds=[vpc_id]), "Vpcs"),
            resource=f"VPC {vpc_id}",
        )
        if vpc.get("State") != "available":
            raise AwsResourceConfigurationError(f"VPC {vpc_id} is not available")

        if not subnet_ids:
            raise AwsResourceConfigurationError("At least one explicit subnet is required")
        subnet_response = self._ec2.describe_subnets(SubnetIds=list(subnet_ids))
        subnets = self._items(subnet_response, "Subnets")
        found_subnets = {str(subnet.get("SubnetId")): subnet for subnet in subnets}
        missing = set(subnet_ids) - found_subnets.keys()
        if missing:
            raise AwsResourceConfigurationError(
                f"Subnets were not found: {', '.join(sorted(missing))}"
            )
        for subnet_id in subnet_ids:
            subnet = found_subnets[subnet_id]
            if subnet.get("VpcId") != vpc_id:
                raise AwsResourceConfigurationError(
                    f"Subnet {subnet_id} does not belong to VPC {vpc_id}"
                )
            if subnet.get("State") != "available":
                raise AwsResourceConfigurationError(f"Subnet {subnet_id} is not available")

        repository = first_required(
            self._items(
                self._ecr.describe_repositories(repositoryNames=[ecr_repository_name]),
                "repositories",
            ),
            resource=f"ECR repository {ecr_repository_name}",
        )
        repository_arn = self._required_string(repository, "repositoryArn")
        repository_uri = self._required_string(repository, "repositoryUri")

        self._s3.head_bucket(Bucket=s3_bucket_name)
        kms_key_id = self._validated_kms_key(s3_bucket_name)
        self._validate_public_access_block(s3_bucket_name)

        return ExistingAwsInfrastructure(
            vpc_id=vpc_id,
            subnet_ids=subnet_ids,
            ecr_repository_arn=repository_arn,
            ecr_repository_uri=repository_uri,
            s3_bucket_arn=f"arn:aws:s3:::{s3_bucket_name}",
            s3_kms_key_id=kms_key_id,
        )

    def _validated_kms_key(self, bucket: str) -> str:
        response = self._s3.get_bucket_encryption(Bucket=bucket)
        configuration = response.get("ServerSideEncryptionConfiguration", {})
        rules = configuration.get("Rules", []) if isinstance(configuration, dict) else []
        for raw_rule in rules:
            if not isinstance(raw_rule, dict):
                continue
            default = raw_rule.get("ApplyServerSideEncryptionByDefault", {})
            if (
                isinstance(default, dict)
                and default.get("SSEAlgorithm") == "aws:kms"
                and isinstance(default.get("KMSMasterKeyID"), str)
            ):
                return str(default["KMSMasterKeyID"])
        raise AwsResourceConfigurationError(
            f"S3 bucket {bucket} must use default SSE-KMS encryption"
        )

    def _validate_public_access_block(self, bucket: str) -> None:
        response = self._s3.get_public_access_block(Bucket=bucket)
        config = response.get("PublicAccessBlockConfiguration")
        required = (
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        )
        if not isinstance(config, dict) or not all(config.get(key) is True for key in required):
            raise AwsResourceConfigurationError(
                f"S3 bucket {bucket} must block every form of public access"
            )

    @staticmethod
    def _items(response: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = response.get(key)
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _required_string(value: dict[str, Any] | Any, key: str) -> str:
        item = value.get(key) if isinstance(value, dict) else None
        if not isinstance(item, str) or not item:
            raise AwsResourceConfigurationError(f"AWS response is missing {key}")
        return item
