# `platform/deployment_fargate/`

Multi-tenant Fargate deployment for OpenSRE: control-plane Lambda, public API
forwarder, and shared fleet CDK.

EC2 AWS SDK primitives and the Telegram gateway AMI/systemd lifecycle live in
[`../deployment_ec2/`](../deployment_ec2/)
([`telegram_gateway/`](../deployment_ec2/telegram_gateway/)).

## Three deployment entities

Each entity owns its code under a dedicated package. Shared fleet IaC lives in
`fargate_fleet_infrastructure/` (one CDK app per entity; add sibling `*_infrastructure/`
folders as other entities gain IaC).

| Entity | Path | Purpose |
| --- | --- | --- |
| **Control plane** | [`api_control_plane/`](api_control_plane/) | Lambda lifecycle provisioning (IAM-protected `/v1/organizations/.../gateway` routes), tenant lifecycle, AWS adapters, and image build contracts. |
| **Public API** | [`api_public_forwarder/`](api_public_forwarder/) | Bearer-authorizer-backed `/v1/runs` routes extracted from the Lambda handler. |
| **Shared fleet (IaC)** | [`fargate_fleet_infrastructure/`](fargate_fleet_infrastructure/) | Python CDK stack for ECS cluster, gateway security group, log group, and execution role. |
| **Gateway runtime** | [`../../gateway/`](../../gateway/) | Tenant Gateway process (Fargate task or legacy EC2/systemd). |

Shared HTTP helpers for both Lambda handlers live in [`utils/http_lambda.py`](utils/http_lambda.py).

The control-plane Lambda entry point is
`platform.deployment_fargate.api_control_plane.runtime.lambda_handler`.

## Fargate fleet (CDK)

| Command | What it does |
| --- | --- |
| `make cdk-synth` | Synthesize shared Fargate fleet CloudFormation template |
| `make cdk-deploy` | Deploy ECS cluster, gateway SG, log group, execution role |
| `make cdk-destroy` | Tear down shared fleet stack |
| `make cdk-verify` | Run synth-level CDK tests (no AWS credentials) |

See [fargate_fleet_infrastructure/README.md](fargate_fleet_infrastructure/README.md) and [`.env.fargate-fleet.example`](../../.env.fargate-fleet.example).

## Related: EC2 Telegram gateway

The AMI + systemd Telegram gateway deploy path lives in
[`../deployment_ec2/telegram_gateway/`](../deployment_ec2/telegram_gateway/).
Makefile targets: `make bake-gateway`, `make deploy-gateway`, `make destroy-gateway`.

Shared helpers still in this package:

| Path | Purpose |
| --- | --- |
| [`utils/`](utils/) | Shared helpers: EC2 deploy env validation (`prep_ec2_deployment`), health polling, existing-infrastructure validation. |

The Slack backend (web API + Slack gateway) is **not** in this repo — it is
deployed and operated separately.

For gateway env vars and deploy prerequisites, see
[telegram_gateway/README.md](../deployment_ec2/telegram_gateway/README.md).

### E2E test infrastructure (separate from gateway deploy)

These Makefile targets provision **test-case** AWS stacks for the e2e suite, not the OpenSRE runtime:

| Command | Stack |
| --- | --- |
| `make deploy-lambda` / `make destroy-lambda` | Lambda test fixture |
| `make deploy-prefect` / `make destroy-prefect` | Prefect ECS Fargate fixture |
| `make deploy-flink` / `make destroy-flink` | Flink ECS fixture |

## Cloud-OpsBench AWS infrastructure

The Terraform module for running Cloud-OpsBench on AWS Fargate lives with the
benchmark code at
[`tests/benchmarks/cloudopsbench/infra/`](../../tests/benchmarks/cloudopsbench/infra/).
The one-time Terraform state bootstrap script lives at
[`tests/benchmarks/cloudopsbench/infra/scripts/bootstrap-bench-state.sh`](../../tests/benchmarks/cloudopsbench/infra/scripts/bootstrap-bench-state.sh).
See that directory's [README](../../tests/benchmarks/cloudopsbench/infra/README.md)
and the benchmark runner guide at
[`tests/benchmarks/cloudopsbench/README.md`](../../tests/benchmarks/cloudopsbench/README.md).
