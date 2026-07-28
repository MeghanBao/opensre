"""Resolve S3 Files fleet parameters from opensre-infra-aws Terraform outputs.

Reads the shared-stack ``memories`` output from a separately checked-out
https://github.com/Tracer-Cloud/opensre-infra-aws checkout. This module never
reads raw Terraform state and never runs ``terraform init`` / ``plan`` /
``apply``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.constants.opensre_infra_aws import (
    FLEET_CDK_PARAM_S3_FILE_SYSTEM_ARN,
    FLEET_CDK_PARAM_S3_FILE_SYSTEM_ID,
    FLEET_CDK_PARAM_S3_FILES_CLIENT_SECURITY_GROUP_ID,
    OPENSRE_INFRA_AWS_BUCKET_ARN_FIELD,
    OPENSRE_INFRA_AWS_BUCKET_NAME_FIELD,
    OPENSRE_INFRA_AWS_CLIENT_SECURITY_GROUP_ID_FIELD,
    OPENSRE_INFRA_AWS_DEFAULT_WORKSPACE,
    OPENSRE_INFRA_AWS_DIR_ENV,
    OPENSRE_INFRA_AWS_ENVIRONMENT_ENV,
    OPENSRE_INFRA_AWS_FILE_SYSTEM_ARN_FIELD,
    OPENSRE_INFRA_AWS_FILE_SYSTEM_ID_FIELD,
    OPENSRE_INFRA_AWS_MEMORIES_OUTPUT_NAME,
    OPENSRE_INFRA_AWS_REPO_URL,
    OPENSRE_INFRA_AWS_SHARED_STACK_RELATIVE_PATH,
    OPENSRE_INFRA_AWS_SUBNET_IDS_FIELD,
)

_FILE_SYSTEM_ID_RE = re.compile(r"^fs-[0-9a-f]+$", re.IGNORECASE)
_SECURITY_GROUP_ID_RE = re.compile(r"^sg-[0-9a-f]+$", re.IGNORECASE)
_FILE_SYSTEM_ARN_PREFIX = "arn:aws:s3files:"


class InfraAwsMemoriesResolutionError(RuntimeError):
    """Raised when Terraform memories output cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class InfraAwsMemoriesConfig:
    """Validated memories identifiers for one shared-stack environment."""

    environment: str
    file_system_id: str
    file_system_arn: str
    client_security_group_id: str
    bucket_name: str
    bucket_arn: str
    subnet_ids: tuple[str, ...]

    def cdk_parameter_args(self) -> tuple[str, ...]:
        """Return CDK CLI ``--parameters`` tokens for S3 Files fleet inputs."""
        return (
            "--parameters",
            f"{FLEET_CDK_PARAM_S3_FILE_SYSTEM_ID}={self.file_system_id}",
            "--parameters",
            f"{FLEET_CDK_PARAM_S3_FILE_SYSTEM_ARN}={self.file_system_arn}",
            "--parameters",
            (
                f"{FLEET_CDK_PARAM_S3_FILES_CLIENT_SECURITY_GROUP_ID}"
                f"={self.client_security_group_id}"
            ),
        )


def resolve_infra_aws_memories(
    *,
    infra_dir: str | Path,
    environment: str,
    terraform_bin: str | None = None,
) -> InfraAwsMemoriesConfig:
    """Resolve and validate ``memories[environment]`` from a local checkout."""
    checkout = _validated_checkout(Path(infra_dir).expanduser())
    shared_stack = checkout / OPENSRE_INFRA_AWS_SHARED_STACK_RELATIVE_PATH
    if not shared_stack.is_dir():
        raise InfraAwsMemoriesResolutionError(
            f"Missing shared stack directory: {shared_stack} "
            f"(expected under {OPENSRE_INFRA_AWS_REPO_URL})"
        )
    if not (shared_stack / ".terraform").is_dir():
        raise InfraAwsMemoriesResolutionError(
            f"Terraform backend is not initialized under {shared_stack}. "
            "Run `terraform init -input=false` there before resolving outputs."
        )

    selected_environment = environment.strip()
    if not selected_environment:
        raise InfraAwsMemoriesResolutionError("Environment must be non-empty")

    memories = _read_memories_output(
        shared_stack=shared_stack,
        terraform_bin=terraform_bin or _require_terraform_bin(),
    )
    return parse_memories_environment(
        memories,
        environment=selected_environment,
    )


def parse_memories_environment(
    memories: Mapping[str, Any],
    *,
    environment: str,
) -> InfraAwsMemoriesConfig:
    """Parse one environment entry from a Terraform ``memories`` output map."""
    if environment not in memories:
        available = ", ".join(sorted(str(key) for key in memories)) or "(none)"
        raise InfraAwsMemoriesResolutionError(
            f"Environment {environment!r} is absent from Terraform memories "
            f"output; available: {available}"
        )
    raw = memories[environment]
    if not isinstance(raw, Mapping):
        raise InfraAwsMemoriesResolutionError(f"memories[{environment!r}] must be an object")

    file_system_id = _required_string(raw, OPENSRE_INFRA_AWS_FILE_SYSTEM_ID_FIELD)
    file_system_arn = _required_string(raw, OPENSRE_INFRA_AWS_FILE_SYSTEM_ARN_FIELD)
    client_security_group_id = _required_string(
        raw,
        OPENSRE_INFRA_AWS_CLIENT_SECURITY_GROUP_ID_FIELD,
    )
    bucket_name = _required_string(raw, OPENSRE_INFRA_AWS_BUCKET_NAME_FIELD)
    bucket_arn = _required_string(raw, OPENSRE_INFRA_AWS_BUCKET_ARN_FIELD)
    subnet_ids = _required_string_tuple(raw, OPENSRE_INFRA_AWS_SUBNET_IDS_FIELD)

    if not _FILE_SYSTEM_ID_RE.fullmatch(file_system_id):
        raise InfraAwsMemoriesResolutionError(
            f"Invalid {OPENSRE_INFRA_AWS_FILE_SYSTEM_ID_FIELD}: {file_system_id!r}"
        )
    if not file_system_arn.startswith(_FILE_SYSTEM_ARN_PREFIX):
        raise InfraAwsMemoriesResolutionError(
            f"Invalid {OPENSRE_INFRA_AWS_FILE_SYSTEM_ARN_FIELD}: {file_system_arn!r}"
        )
    if not _SECURITY_GROUP_ID_RE.fullmatch(client_security_group_id):
        raise InfraAwsMemoriesResolutionError(
            "Invalid "
            f"{OPENSRE_INFRA_AWS_CLIENT_SECURITY_GROUP_ID_FIELD}: "
            f"{client_security_group_id!r}"
        )

    return InfraAwsMemoriesConfig(
        environment=environment,
        file_system_id=file_system_id,
        file_system_arn=file_system_arn,
        client_security_group_id=client_security_group_id,
        bucket_name=bucket_name,
        bucket_arn=bucket_arn,
        subnet_ids=subnet_ids,
    )


def _validated_checkout(infra_dir: Path) -> Path:
    if not infra_dir.exists():
        raise InfraAwsMemoriesResolutionError(
            f"Infra checkout does not exist: {infra_dir} (clone {OPENSRE_INFRA_AWS_REPO_URL})"
        )
    if not infra_dir.is_dir():
        raise InfraAwsMemoriesResolutionError(
            f"Infra checkout path is not a directory: {infra_dir}"
        )
    return infra_dir.resolve()


def _require_terraform_bin() -> str:
    terraform_bin = shutil.which("terraform")
    if terraform_bin is None:
        raise InfraAwsMemoriesResolutionError("terraform is not on PATH; install Terraform >= 1.5")
    return terraform_bin


def _read_memories_output(
    *,
    shared_stack: Path,
    terraform_bin: str,
) -> dict[str, Any]:
    command = (
        terraform_bin,
        f"-chdir={shared_stack}",
        "output",
        "-json",
        OPENSRE_INFRA_AWS_MEMORIES_OUTPUT_NAME,
    )
    env = os.environ.copy()
    env["TF_WORKSPACE"] = OPENSRE_INFRA_AWS_DEFAULT_WORKSPACE
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except OSError as exc:
        raise InfraAwsMemoriesResolutionError(
            f"Failed to execute terraform: {type(exc).__name__}"
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise InfraAwsMemoriesResolutionError(
            "terraform output failed" + (f": {detail}" if detail else "")
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InfraAwsMemoriesResolutionError("terraform output returned invalid JSON") from exc
    return _unwrap_terraform_output(payload)


def _unwrap_terraform_output(payload: Any) -> dict[str, Any]:
    """Accept either raw value JSON or wrapped ``{value, type, sensitive}``."""
    if isinstance(payload, Mapping) and "value" in payload and "type" in payload:
        value = payload["value"]
    else:
        value = payload
    if not isinstance(value, Mapping):
        raise InfraAwsMemoriesResolutionError(
            "Terraform memories output must be an object keyed by environment"
        )
    return {str(key): item for key, item in value.items()}


def _required_string(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InfraAwsMemoriesResolutionError(f"memories field {field!r} must be a string")
    return value.strip()


def _required_string_tuple(raw: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = raw.get(field)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InfraAwsMemoriesResolutionError(f"memories field {field!r} must be a list of strings")
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(items) != len(value) or not items:
        raise InfraAwsMemoriesResolutionError(
            f"memories field {field!r} must contain non-empty strings"
        )
    return items


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve opensre-infra-aws shared memories Terraform outputs into "
            "Fargate fleet CDK parameters."
        ),
    )
    parser.add_argument(
        "--infra-dir",
        default=os.getenv(OPENSRE_INFRA_AWS_DIR_ENV, ""),
        help=f"Path to opensre-infra-aws checkout (or set {OPENSRE_INFRA_AWS_DIR_ENV})",
    )
    parser.add_argument(
        "--environment",
        default=os.getenv(OPENSRE_INFRA_AWS_ENVIRONMENT_ENV, ""),
        help=(
            "Shared-stack environment key such as dev or prod "
            f"(or set {OPENSRE_INFRA_AWS_ENVIRONMENT_ENV})"
        ),
    )
    parser.add_argument(
        "--print-cdk-args",
        action="store_true",
        help="Print CDK --parameters tokens on one line for Make consumption",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the resolved memories config as JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint used by ``make cdk-deploy-fleet-from-infra-aws``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.infra_dir:
        parser.error(f"--infra-dir or {OPENSRE_INFRA_AWS_DIR_ENV} is required")
    if not args.environment:
        parser.error(f"--environment or {OPENSRE_INFRA_AWS_ENVIRONMENT_ENV} is required")
    try:
        config = resolve_infra_aws_memories(
            infra_dir=args.infra_dir,
            environment=args.environment,
        )
    except InfraAwsMemoriesResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.print_json:
        print(
            json.dumps(
                {
                    "environment": config.environment,
                    "file_system_id": config.file_system_id,
                    "file_system_arn": config.file_system_arn,
                    "client_security_group_id": config.client_security_group_id,
                    "bucket_name": config.bucket_name,
                    "bucket_arn": config.bucket_arn,
                    "subnet_ids": list(config.subnet_ids),
                    "repo": OPENSRE_INFRA_AWS_REPO_URL,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    # Default and --print-cdk-args: emit CDK parameter tokens.
    print(" ".join(config.cdk_parameter_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
