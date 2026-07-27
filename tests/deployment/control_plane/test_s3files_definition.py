from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from integrations.credentials_api import CredentialsApiError
from platform.deployment_fargate.api_control_plane.build.s3files_definition import (
    validate_task_definition,
    validate_tenant_mount_policy,
)
from platform.deployment_fargate.api_control_plane.verification.setup_task import (
    FILE_MODE,
    SANDBOX_SERVICE,
    generate_sandbox_store,
    integrations_path,
    load_and_validate_store,
    load_through_opensre_store,
    main,
    validate_store,
    write_store_atomically,
)

FILE_SYSTEM_ARN = "arn:aws:s3files:eu-west-1:123456789012:file-system/fs-tenant"
ACCESS_POINT_ARN = f"{FILE_SYSTEM_ARN}/access-point/fsap-tenant-a"
IMAGE = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/opensre@sha256:" + ("a" * 64)


def _task_definition() -> dict[str, object]:
    return {
        "taskRoleArn": "arn:aws:iam::123456789012:role/tenant-a-task",
        "executionRoleArn": "arn:aws:iam::123456789012:role/shared-execution",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "containerDefinitions": [
            {
                "name": "gateway",
                "image": IMAGE,
                "user": "1000:1000",
                "privileged": False,
                "mountPoints": [
                    {
                        "sourceVolume": "tenant-workspace",
                        "containerPath": "/workspace",
                        "readOnly": False,
                    }
                ],
                "environment": [
                    {"name": "HOME", "value": "/workspace/home"},
                    {"name": "OPENSRE_WORKSPACE", "value": "/workspace/files"},
                    {"name": "ORGANIZATION_ID", "value": "tenant-a"},
                    {
                        "name": "OPENSRE_CREDENTIALS_API_URL",
                        "value": "https://credentials.example.invalid",
                    },
                    {
                        "name": "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN",
                        "value": "arn:aws:secretsmanager:eu-west-1:123456789012:secret:tenant-a",
                    },
                ],
            }
        ],
        "volumes": [
            {
                "name": "tenant-workspace",
                "s3filesVolumeConfiguration": {
                    "fileSystemArn": FILE_SYSTEM_ARN,
                    "rootDirectory": "/",
                    "transitEncryptionPort": 2049,
                    "accessPointArn": ACCESS_POINT_ARN,
                },
            }
        ],
    }


def test_task_definition_uses_access_point_and_no_plaintext_secrets() -> None:
    validate_task_definition(
        _task_definition(),
        file_system_arn=FILE_SYSTEM_ARN,
        access_point_arn=ACCESS_POINT_ARN,
        container_name="gateway",
    )


def test_task_definition_rejects_secret_in_environment() -> None:
    task_definition = _task_definition()
    container = task_definition["containerDefinitions"][0]  # type: ignore[index]
    container["environment"].append({"name": "API_TOKEN", "value": "not-allowed"})  # type: ignore[index]

    with pytest.raises(ValueError, match="secret-like"):
        validate_task_definition(
            task_definition,
            file_system_arn=FILE_SYSTEM_ARN,
            access_point_arn=ACCESS_POINT_ARN,
            container_name="gateway",
        )


def test_task_definition_rejects_filesystem_root_instead_of_access_point() -> None:
    task_definition = _task_definition()
    volume = task_definition["volumes"][0]  # type: ignore[index]
    del volume["s3filesVolumeConfiguration"]["accessPointArn"]  # type: ignore[index]

    with pytest.raises(ValueError, match="access point"):
        validate_task_definition(
            task_definition,
            file_system_arn=FILE_SYSTEM_ARN,
            access_point_arn=ACCESS_POINT_ARN,
            container_name="gateway",
        )


def test_tenant_policy_is_scoped_and_excludes_root_and_direct_s3() -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3files:ClientMount", "s3files:ClientWrite"],
                "Resource": FILE_SYSTEM_ARN,
                "Condition": {"ArnEquals": {"s3files:AccessPointArn": ACCESS_POINT_ARN}},
            }
        ],
    }

    validate_tenant_mount_policy(policy, access_point_arn=ACCESS_POINT_ARN)
    serialized = json.dumps(policy)
    assert "ClientRootAccess" not in serialized
    assert "PutObject" not in serialized


@pytest.mark.parametrize("action", ["s3files:ClientRootAccess", "s3:PutObject", "s3:GetObject"])
def test_tenant_policy_rejects_root_or_direct_object_access(action: str) -> None:
    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3files:ClientMount", action],
                "Condition": {"StringEquals": {"s3files:AccessPointArn": ACCESS_POINT_ARN}},
            }
        ]
    }

    with pytest.raises(ValueError, match="root or direct"):
        validate_tenant_mount_policy(policy, access_point_arn=ACCESS_POINT_ARN)


def test_setup_task_atomically_writes_v2_store_with_posix_modes(tmp_path: Path) -> None:
    home = tmp_path / "workspace" / "home"
    store = generate_sandbox_store("tenant-a")

    destination = write_store_atomically(home, store)

    assert destination == integrations_path(home)
    assert stat.S_IMODE(destination.stat().st_mode) == FILE_MODE
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert load_and_validate_store(home, "tenant-a") == store
    loaded = load_through_opensre_store(home, "tenant-a")
    assert loaded[0]["service"] == SANDBOX_SERVICE
    assert not list(destination.parent.glob(f".{destination.name}.*"))


def test_invalid_v2_payload_does_not_replace_existing_store(tmp_path: Path) -> None:
    home = tmp_path / "home"
    original = generate_sandbox_store("tenant-a")
    destination = write_store_atomically(home, original)
    before = destination.read_bytes()

    with pytest.raises(CredentialsApiError):
        validate_store({"version": 1, "integrations": []})

    assert destination.read_bytes() == before


def test_setup_task_output_never_contains_generated_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_secret = "sandbox-value-that-must-not-be-logged"
    monkeypatch.setattr(
        "platform.deployment_fargate.api_control_plane.verification.setup_task.secrets.token_urlsafe",
        lambda _: generated_secret,
    )

    assert (
        main(
            [
                "write-sandbox",
                "--home",
                str(tmp_path / "home"),
                "--organization-id",
                "tenant-a",
            ]
        )
        == 0
    )

    output = capsys.readouterr()
    assert generated_secret not in output.out
    assert generated_secret not in output.err
    assert SANDBOX_SERVICE not in output.out
