"""Filesystem-only setup task for disposable S3 Files credential tests.

The live harness deliberately generates its sandbox token inside the task.
Credential payloads therefore never appear in ECS task definitions, command
overrides, environment variables, or harness logs.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

from integrations.credentials_api import (
    IntegrationInstanceV2,
    IntegrationRecordV2,
    IntegrationStoreV2,
    validate_integration_store_v2,
)

STORE_RELATIVE_PATH = Path(".opensre/integrations.json")
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
SANDBOX_SERVICE = "opensre-s3files-sandbox"
SANDBOX_ENDPOINT = "https://s3files-test.invalid"


def integrations_path(home: Path) -> Path:
    """Return the fixed integration-store path beneath an absolute HOME."""

    if not home.is_absolute():
        raise ValueError("HOME must be an absolute path")
    return home / STORE_RELATIVE_PATH


def validate_store(payload: object) -> IntegrationStoreV2:
    """Validate an untrusted JSON-like payload without logging its contents."""

    return validate_integration_store_v2(payload)


def write_store_atomically(home: Path, store: IntegrationStoreV2) -> Path:
    """Atomically replace the mounted v2 store with strict POSIX permissions."""

    destination = integrations_path(home)
    destination.parent.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    destination.parent.chmod(DIRECTORY_MODE)
    serialized = store.model_dump_json(indent=2) + "\n"

    descriptor: int | None = None
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
        )
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
            descriptor = None
            temporary_file.write(serialized)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
        destination.chmod(FILE_MODE)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary_path)
    return destination


def generate_sandbox_store(organization_id: str) -> IntegrationStoreV2:
    """Generate a tenant-marked, disposable store entirely inside the task."""

    normalized_organization_id = organization_id.strip()
    if not normalized_organization_id:
        raise ValueError("organization id is required")
    return IntegrationStoreV2(
        version=2,
        integrations=[
            IntegrationRecordV2(
                id=f"{SANDBOX_SERVICE}-1",
                service=SANDBOX_SERVICE,
                status="active",
                instances=[
                    IntegrationInstanceV2(
                        name="default",
                        tags={"purpose": "s3files-live-test"},
                        credentials={
                            "endpoint": SANDBOX_ENDPOINT,
                            "organization_marker": normalized_organization_id,
                            "token": secrets.token_urlsafe(32),
                        },
                    )
                ],
            )
        ],
    )


def load_and_validate_store(home: Path, organization_id: str | None = None) -> IntegrationStoreV2:
    """Load a mounted store and optionally verify its non-secret tenant marker."""

    destination = integrations_path(home)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    store = validate_store(payload)
    if destination.stat().st_mode & 0o777 != FILE_MODE:
        raise PermissionError("integration store mode is not 0600")
    if organization_id is not None:
        credentials = store.integrations[0].instances[0].credentials
        if credentials.get("organization_marker") != organization_id:
            raise ValueError("integration store tenant marker does not match")
    return store


def load_through_opensre_store(
    home: Path,
    organization_id: str | None = None,
) -> list[dict[str, object]]:
    """Exercise the production local loader against the mounted store."""

    validated = load_and_validate_store(home, organization_id)
    from integrations import store as opensre_store

    previous_store_path = opensre_store.STORE_PATH
    try:
        opensre_store.STORE_PATH = integrations_path(home)
        loaded = opensre_store.load_integrations()
    finally:
        opensre_store.STORE_PATH = previous_store_path

    if len(loaded) != len(validated.integrations):
        raise ValueError("OpenSRE integration loader returned an unexpected record count")
    if organization_id is not None:
        credentials = loaded[0]["instances"][0]["credentials"]
        if credentials.get("organization_marker") != organization_id:
            raise ValueError("OpenSRE integration loader returned the wrong tenant")
    return loaded


def remove_store(home: Path) -> None:
    """Remove only the disposable integration-store file, if present."""

    integrations_path(home).unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage a disposable mounted integration store")
    parser.add_argument(
        "operation",
        choices=("write-sandbox", "validate", "remove"),
    )
    parser.add_argument("--home", type=Path, default=Path("/workspace/home"))
    parser.add_argument("--organization-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the setup task without emitting credential material."""

    args = _parser().parse_args(argv)
    try:
        if args.operation == "write-sandbox":
            write_store_atomically(args.home, generate_sandbox_store(args.organization_id or ""))
        elif args.operation == "validate":
            load_through_opensre_store(args.home, args.organization_id)
        else:
            remove_store(args.home)
    except Exception:  # noqa: BLE001 - external task boundary must remain generic
        print("S3 Files setup task failed", file=sys.stderr)
        return 1
    print(f"S3 Files setup task completed: {args.operation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
