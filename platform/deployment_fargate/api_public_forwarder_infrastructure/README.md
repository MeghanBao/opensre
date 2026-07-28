# Public forwarder API infrastructure

This CDK app deploys one Python 3.12 Lambda for the bearer-authenticated
`/v1/runs` API and REQUEST authorizer. It imports the shared resource prefix
from `../fargate_fleet_infrastructure/`.

It creates the runtime Lambda, execution role, retained Lambda and API access
logs, HTTP API, authorizer, two routes, default stage, invocation permissions,
and baseline CloudWatch alarms. Schema migration stays in
`../api_control_plane_infrastructure/` — deploy that stack first so the
`agent_runs` tables exist before this API receives traffic.

## Parameters

- `DatabaseUrlSecretArn`: Secrets Manager ARN whose `SecretString` is the full
  Postgres `DATABASE_URL`. Use AWS-managed Secrets Manager encryption (or grant
  the Lambda role `kms:Decrypt` on any customer-managed key separately).

The fleet and control-plane stacks must be deployed first. The Lambda receives
only the database secret ARN and resolves its value at runtime. Its execution
role can read that secret and tenant `public-api-*` secrets under the fleet
resource prefix.

## Prerequisites

- Docker, for the Python 3.12 x86_64 Lambda bundle.
- AWS CDK CLI 2.1133.0 or newer and a bootstrapped target account/region.
- The database secret described above.
- Control-plane stack access logs configure the account-level API Gateway
  CloudWatch role; deploy control plane before relying on public API access logs.

From the repository root:

```bash
make cdk-deploy-public-forwarder PUBLIC_FORWARDER_CDK_ARGS='
  --parameters DatabaseUrlSecretArn=arn:aws:secretsmanager:us-east-1:123456789012:secret:opensre/database'
```

The deploy bundles the required `platform/` source and an x86_64
`psycopg2-binary` wheel in the Lambda ZIP by using Docker through CDK.
