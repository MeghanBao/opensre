# Control-plane API infrastructure

This CDK app deploys one Python 3.12 Lambda for the IAM-protected lifecycle API
and a migration custom resource that applies the Postgres schema. It imports the
shared fleet exports from `../fargate_fleet_infrastructure/`.

It creates the runtime Lambda, migration Lambda/custom resource, execution
roles, retained Lambda and API access logs, HTTP API, six IAM routes, default
stage, invocation permissions, account-level API Gateway CloudWatch logs role,
and baseline CloudWatch alarms. Public `/v1/runs` routes live in
`../api_public_forwarder_infrastructure/`. Per-organization ECS services, task
definitions, task roles, access points, and credentials remain runtime-managed
by `../api_control_plane/`.

## Parameters

- `DatabaseUrlSecretArn`: Secrets Manager ARN whose `SecretString` is the full
  Postgres `DATABASE_URL`. Use AWS-managed Secrets Manager encryption (or grant
  the Lambda role `kms:Decrypt` on any customer-managed key separately).
- `LifecycleRoleArns`: comma-separated IAM role ARNs allowed to invoke lifecycle
  routes. Those callers also need `execute-api:Invoke`.

The fleet stack must be deployed first. The stack applies the idempotent
database schema before exposing the API. Each tenant's credentials bootstrap
secret must already exist before that tenant is provisioned.
The two Lambdas receive only the database secret ARN and resolve its value at
runtime. Their execution roles can read only that secret.

## Prerequisites

- Docker, for the Python 3.12 x86_64 Lambda bundles.
- AWS CDK CLI 2.1133.0 or newer and a bootstrapped target account/region.
- The database secret and lifecycle caller roles described above.

From the repository root:

```bash
make cdk-deploy-control-plane CONTROL_PLANE_CDK_ARGS='
  --parameters DatabaseUrlSecretArn=arn:aws:secretsmanager:us-east-1:123456789012:secret:opensre/database
  --parameters LifecycleRoleArns=arn:aws:iam::123456789012:role/opensre-saas'
```

The deploy bundles the required `platform/` source and an x86_64
`psycopg2-binary` wheel in the Lambda ZIP by using Docker through CDK.
