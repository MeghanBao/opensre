# `platform/deployment_fargate/`

Multi-tenant Fargate deployment for OpenSRE: control-plane Lambda, public API
forwarder, shared fleet CDK, and the EC2 Telegram gateway lifecycle helpers that
still live beside this package.

EC2 AWS SDK primitives live in [`../deployment_ec2/`](../deployment_ec2/).

**Scope: Telegram only for EC2.** Slack is deployed and operated separately, not from
this repo. The EC2 path here never ships `SLACK_*` variables: Socket Mode is
single-consumer, so a second gateway holding the same tokens would split events.

## Three deployment entities

Each entity owns its code under a dedicated package. Shared fleet IaC lives in
`fargate_fleet_infrastructure/` (one CDK app per entity; add sibling `*_infrastructure/`
folders as other entities gain IaC).

| Entity | Path | Purpose |
| --- | --- | --- |
| **Control plane** | [`api_control_plane/`](api_control_plane/) | Lambda lifecycle provisioning (IAM-protected `/v1/organizations/.../gateway` routes), tenant reconciliation, AWS adapters, image build contracts, and verification harnesses. |
| **Public API** | [`api_public_forwarder/`](api_public_forwarder/) | Bearer-authorizer-backed `/v1/runs` routes extracted from the Lambda handler. |
| **Shared fleet (IaC)** | [`fargate_fleet_infrastructure/`](fargate_fleet_infrastructure/) | Python CDK stack for ECS cluster, gateway security group, log group, and execution role. |
| **Gateway runtime** | [`../../gateway/`](../../gateway/) | Tenant Gateway process (Fargate task or legacy EC2/systemd). |

Shared HTTP helpers for both Lambda handlers live in [`http_lambda.py`](http_lambda.py).

The control-plane Lambda entry point is
`platform.deployment_fargate.api_control_plane.api.bootstrap.lambda_handler`.

## Fargate fleet (CDK)

| Command | What it does |
| --- | --- |
| `make cdk-synth` | Synthesize shared Fargate fleet CloudFormation template |
| `make cdk-deploy` | Deploy ECS cluster, gateway SG, log group, execution role |
| `make cdk-destroy` | Tear down shared fleet stack |
| `make cdk-verify` | Run synth-level CDK tests (no AWS credentials) |

See [fargate_fleet_infrastructure/README.md](fargate_fleet_infrastructure/README.md) and [`.env.fargate-fleet.example`](../../.env.fargate-fleet.example).

## EC2 gateway deploy

| Path | Purpose |
| --- | --- |
| [`../deployment_ec2/`](../deployment_ec2/) | EC2 gateway AWS SDK primitives (`client`, `config`, EC2/IAM, SSM). |
| [`gateway/`](gateway/) | AMI + systemd deployment path for the Telegram gateway. See [gateway/README.md](gateway/README.md). |
| [`utils/`](utils/) | Shared helpers: EC2 deploy env validation (`prep_ec2_deployment`), health polling, existing-infrastructure validation. |

The Slack backend (web API + Slack gateway) is **not** in this repo — it is
deployed and operated separately.

## EC2 deploy commands

Run from the **repo root**. Requires `make install` first.

| Command | What it does |
| --- | --- |
| `make bake-gateway` | Launch temp EC2, install OpenSRE, snapshot AMI, save AMI id locally |
| `make deploy-gateway` | Destroy any prior stack, launch EC2 from saved AMI, write env, start service |
| `make destroy-gateway` | Terminate instance, delete IAM profile/role; AMI kept by default |
| `make deploy-gateway-direct` | Install inline on a fresh EC2 instance via SSM (no pre-baked AMI) |
| `make destroy-gateway-direct` | Tear down a direct-deploy stack |

Equivalent Python entrypoints:

```bash
uv run python -m platform.deployment_fargate.gateway.lifecycle bake-ami
uv run python -m platform.deployment_fargate.gateway.lifecycle deploy
uv run python -m platform.deployment_fargate.gateway.lifecycle destroy
```

### Prerequisites

1. **AWS credentials** — static keys or role via the default boto3 chain (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, or `AWS_ROLE_ARN`).
2. **Permissions** — EC2, IAM, and SSM for the deploy account/region.
3. **Region** — hardcoded to `us-east-1` in [`aws/config.py`](aws/config.py).

### Environment

Gateway deploy commands validate required variables **before** cleanup or provisioning and print
any missing keys (with `MISSING:` / `WARN:` labels).

Copy [`.env.deploy.example`](../../.env.deploy.example) to `.env` in the repo root (or export vars):

| Variable | Required | Used by |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Yes (or role) | Provisioning |
| `TELEGRAM_BOT_TOKEN` | Yes | Gateway service |
| `TELEGRAM_ALLOWED_USERS` | Recommended | Gateway pairing gate |
| `LLM_PROVIDER` + API key | Yes | Gateway service |
| `EC2_KEY_NAME` | No | Optional SSH debug key pair |

`SLACK_*` variables are ignored by the EC2 deploy (warning at validation) —
Slack is deployed and operated separately, not from this repo.

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
