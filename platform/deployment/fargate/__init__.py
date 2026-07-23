"""AWS Fargate deployment primitives for the multi-tenant Gateway."""

from platform.deployment.fargate.aws_discovery import (
    ExistingAwsInfrastructure,
    ExistingInfrastructureDiscovery,
)
from platform.deployment.fargate.aws_ecs import (
    FargateServiceSpec,
    GatewayTaskDefinitionSpec,
    TenantEcsAdapter,
)
from platform.deployment.fargate.aws_iam import TenantIamAdapter, TenantMountBinding
from platform.deployment.fargate.aws_s3_files import S3FilesAdapter
from platform.deployment.fargate.aws_secrets import TenantSecretsAdapter

__all__ = [
    "ExistingAwsInfrastructure",
    "ExistingInfrastructureDiscovery",
    "FargateServiceSpec",
    "GatewayTaskDefinitionSpec",
    "S3FilesAdapter",
    "TenantEcsAdapter",
    "TenantIamAdapter",
    "TenantMountBinding",
    "TenantSecretsAdapter",
]
