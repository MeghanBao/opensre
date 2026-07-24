"""AWS Fargate deployment primitives for the multi-tenant Gateway."""

from platform.deployment.fargate.aws_discovery import (
    ExistingAwsInfrastructure,
    ExistingInfrastructureDiscovery,
)
from platform.deployment.fargate.aws_ecs import (
    FargateServiceSpec,
    FargateServiceState,
    GatewayTaskDefinitionSpec,
    TenantEcsAdapter,
)
from platform.deployment.fargate.aws_iam import TenantIamAdapter, TenantMountBinding
from platform.deployment.fargate.aws_s3_files import S3FilesAdapter
from platform.deployment.fargate.aws_secrets import TenantSecretsAdapter
from platform.deployment.fargate.lifecycle import (
    FargateLifecycle,
    create_lifecycle_service_from_environment,
)
from platform.deployment.fargate.lifecycle_errors import (
    LifecycleCapacityError,
    LifecycleError,
    LifecycleNotFoundError,
    LifecycleOperationError,
    LifecycleValidationError,
)
from platform.deployment.fargate.lifecycle_models import (
    FargateLifecycleConfig,
    ProvisionGatewayResult,
    RotatedApiCredential,
)

__all__ = [
    "ExistingAwsInfrastructure",
    "ExistingInfrastructureDiscovery",
    "FargateServiceSpec",
    "FargateServiceState",
    "FargateLifecycle",
    "FargateLifecycleConfig",
    "GatewayTaskDefinitionSpec",
    "LifecycleError",
    "LifecycleCapacityError",
    "LifecycleNotFoundError",
    "LifecycleOperationError",
    "LifecycleValidationError",
    "ProvisionGatewayResult",
    "RotatedApiCredential",
    "create_lifecycle_service_from_environment",
    "S3FilesAdapter",
    "TenantEcsAdapter",
    "TenantIamAdapter",
    "TenantMountBinding",
    "TenantSecretsAdapter",
]
