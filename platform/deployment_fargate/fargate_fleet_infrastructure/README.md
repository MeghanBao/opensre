# Shared Fargate fleet (Python CDK)

This CDK app owns the **stable shared foundation** for the multi-tenant Gateway
control plane. The sibling
[`api_control_plane_infrastructure/`](../api_control_plane_infrastructure/) and
[`api_public_forwarder_infrastructure/`](../api_public_forwarder_infrastructure/)
apps import its outputs. Per-organization ECS services, task definitions, IAM
task roles, Secrets Manager records, and S3 Files access points remain in the
Python tenant lifecycle under [`../api_control_plane/`](../api_control_plane/).

## What this stack creates

| Resource | Purpose |
| --- | --- |
| ECS Fargate cluster | Logical namespace for all tenant Gateway services |
| Gateway task security group | Outbound-only; no inbound rules |
| S3 Files mount-target security group | Accepts NFS only from Gateway tasks |
| CloudWatch log group | Shared `awslogs` destination for Gateway containers |
| ECS task execution role | Image pull + log write (managed execution policy) |

## What you must supply (parameters)

These resources are **not** created by this stack — pass explicit identifiers at deploy time:

| Parameter | Maps to env var |
| --- | --- |
| `VpcId` | (network placement only) |
| `PublicSubnetIds` | `OPENSRE_FARGATE_SUBNET_IDS` |
| `S3FileSystemId` | `OPENSRE_S3_FILESYSTEM_ID` |
| `S3FileSystemArn` | `OPENSRE_S3_FILESYSTEM_ARN` |
| `GatewayImage` | `OPENSRE_GATEWAY_IMAGE` |
| `CredentialsApiUrl` | `OPENSRE_CREDENTIALS_API_URL` |
| `ResourcePrefix` | `OPENSRE_FARGATE_RESOURCE_PREFIX` (optional, default `opensre`) |

Stack outputs echo the values needed by
[`TenantFleetConfig`](../api_control_plane/utils/get_fleet_config.py).
The control-plane lifecycle ensures the existing filesystem has one mount target
in every configured public subnet using the mount-target security group.

## Prerequisites

1. **Install project deps with CDK extra** (from repo root):

   ```bash
   make install
   uv sync --extra cdk
   ```

2. **Install AWS CDK CLI** (once per machine, **v2.1133.0 or newer** to match `aws-cdk-lib`):

   ```bash
   npm install -g aws-cdk@2
   cdk --version
   ```

3. **Bootstrap the target account/region** (once per account/region):

   ```bash
   cd platform/deployment_fargate/fargate_fleet_infrastructure
   cdk bootstrap aws://ACCOUNT_ID/REGION
   ```

4. **AWS credentials** with permissions for ECS, EC2 (security groups), IAM, and CloudWatch Logs.

## Commands

From repo root:

```bash
make cdk-synth       # synthesize fleet and control-plane templates
make cdk-deploy-fleet FLEET_CDK_ARGS='--parameters ...'
make cdk-deploy      # deploy fleet, then control plane
make cdk-destroy     # destroy control plane, then fleet
make cdk-verify      # run synth-level tests (no AWS credentials required)
```

Example deploy with parameters:

```bash
cd platform/deployment_fargate/fargate_fleet_infrastructure
uv run --extra cdk cdk deploy OpensreFargateFleet \
  --parameters VpcId=vpc-abc123 \
  --parameters PublicSubnetIds=subnet-a,subnet-b \
  --parameters S3FileSystemId=fs-123 \
  --parameters S3FileSystemArn=arn:aws:s3files:us-east-1:123456789012:file-system/fs-123 \
  --parameters GatewayImage=123456789012.dkr.ecr.us-east-1.amazonaws.com/opensre@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --parameters CredentialsApiUrl=https://credentials.example.com
```

The control-plane and public-forwarder stacks import these outputs directly; no
manual environment copy is required. See
[`api_control_plane_infrastructure/README.md`](../api_control_plane_infrastructure/README.md)
and
[`api_public_forwarder_infrastructure/README.md`](../api_public_forwarder_infrastructure/README.md)
for their parameters.
