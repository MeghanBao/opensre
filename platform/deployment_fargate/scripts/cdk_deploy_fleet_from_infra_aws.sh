#!/usr/bin/env bash
# Deploy OpensreFargateFleet using S3 Files identifiers from opensre-infra-aws.
#
# One-time (infra Terraform backend):
#   cd platform/deployment_fargate/opensre-infra/stacks/shared
#   terraform init -input=false
#
# Deploy (from repo root):
#   ./platform/deployment_fargate/scripts/cdk_deploy_fleet_from_infra_aws.sh \
#     --environment dev \
#     --parameters VpcId=vpc-... \
#     --parameters PublicSubnetIds=subnet-a,subnet-b \
#     --parameters GatewayImage=... \
#     --parameters CredentialsApiUrl=...
#
# Defaults OPENSRE_INFRA_AWS_DIR to the opensre-infra git submodule.
# Override with --infra-dir or OPENSRE_INFRA_AWS_DIR.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FARGATE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${FARGATE_DIR}/../.." && pwd)"
DEFAULT_INFRA_DIR="${FARGATE_DIR}/opensre-infra"

INFRA_DIR="${OPENSRE_INFRA_AWS_DIR:-${DEFAULT_INFRA_DIR}}"
ENVIRONMENT="${OPENSRE_INFRA_AWS_ENVIRONMENT:-}"
EXTRA_CDK_ARGS=()
PRINT_JSON=0
INIT_TERRAFORM=0

usage() {
  cat <<'EOF'
Usage:
  cdk_deploy_fleet_from_infra_aws.sh --environment ENV [options] [--parameters KEY=VALUE ...]

Options:
  --environment ENV     Shared-stack memories key (dev|prod). Required.
  --infra-dir PATH      opensre-infra-aws checkout
                        (default: platform/deployment_fargate/opensre-infra)
  --init                Run `terraform init -input=false` in stacks/shared first
  --print-json          Resolve and print memories JSON; do not deploy
  --parameters KEY=VALUE Extra CDK fleet parameters (repeatable)
  -h, --help            Show this help

Environment:
  OPENSRE_INFRA_AWS_DIR           Override default submodule path
  OPENSRE_INFRA_AWS_ENVIRONMENT   Same as --environment
  FLEET_CDK_ARGS                  Extra CDK tokens (space-separated), same as Make
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment)
      ENVIRONMENT="${2:-}"
      shift 2
      ;;
    --infra-dir)
      INFRA_DIR="${2:-}"
      shift 2
      ;;
    --init)
      INIT_TERRAFORM=1
      shift
      ;;
    --print-json)
      PRINT_JSON=1
      shift
      ;;
    --parameters)
      if [[ -z "${2:-}" ]]; then
        echo "error: --parameters requires KEY=VALUE" >&2
        exit 1
      fi
      EXTRA_CDK_ARGS+=(--parameters "$2")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      EXTRA_CDK_ARGS+=("$@")
      break
      ;;
    *)
      EXTRA_CDK_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -n "${FLEET_CDK_ARGS:-}" ]]; then
  # shellcheck disable=SC2206
  EXTRA_CDK_ARGS+=(${FLEET_CDK_ARGS})
fi

if [[ -z "${ENVIRONMENT}" ]]; then
  echo "error: --environment or OPENSRE_INFRA_AWS_ENVIRONMENT is required" >&2
  usage >&2
  exit 1
fi

if [[ ! -d "${INFRA_DIR}" ]]; then
  echo "error: infra checkout missing: ${INFRA_DIR}" >&2
  echo "hint: git submodule update --init platform/deployment_fargate/opensre-infra" >&2
  exit 1
fi

SHARED_STACK="${INFRA_DIR}/stacks/shared"
if [[ ! -d "${SHARED_STACK}" ]]; then
  echo "error: missing shared stack directory: ${SHARED_STACK}" >&2
  exit 1
fi

if [[ "${INIT_TERRAFORM}" -eq 1 ]]; then
  if ! command -v terraform >/dev/null 2>&1; then
    echo "error: terraform is not on PATH" >&2
    exit 1
  fi
  terraform -chdir="${SHARED_STACK}" init -input=false
fi

cd "${REPO_ROOT}"

if [[ "${PRINT_JSON}" -eq 1 ]]; then
  uv run python -m platform.deployment_fargate.utils.resolve_infra_aws_memories \
    --infra-dir "${INFRA_DIR}" \
    --environment "${ENVIRONMENT}" \
    --print-json
  exit 0
fi

INFRA_PARAMS="$(
  uv run python -m platform.deployment_fargate.utils.resolve_infra_aws_memories \
    --infra-dir "${INFRA_DIR}" \
    --environment "${ENVIRONMENT}" \
    --print-cdk-args
)"

cd "${REPO_ROOT}/platform/deployment_fargate/fargate_fleet_infrastructure"
# INFRA_PARAMS is a validated space-separated list of --parameters tokens.
# shellcheck disable=SC2086
uv run --extra cdk cdk deploy OpensreFargateFleet \
  --require-approval never \
  ${INFRA_PARAMS} \
  "${EXTRA_CDK_ARGS[@]}"
