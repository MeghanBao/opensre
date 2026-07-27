"""Opt-in real-AWS gate for S3 Files access-point isolation.

Set ``OPENSRE_S3FILES_LIVE_CONFIG`` to a JSON config file containing only
non-secret AWS resource references. The task definitions must be prepared by
the deployment adapter and use the image under test. AWS credentials come from
the normal boto3 chain and must never be included in that file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from platform.deployment_fargate.control_plane.verification.live_harness import (
    LiveHarnessConfig,
    S3FilesLiveHarness,
    boto3_harness,
)

LIVE_CONFIG_ENV = "OPENSRE_S3FILES_LIVE_CONFIG"


def _config() -> LiveHarnessConfig:
    return LiveHarnessConfig(
        region="eu-west-1",
        cluster="existing-cluster",
        subnet_ids=["subnet-existing"],
        security_group_ids=["sg-existing"],
        tenant_a_task_definition="tenant-a:1",
        tenant_b_task_definition="tenant-b:1",
        tenant_a_cross_mount_task_definition="tenant-a-role-b-mount:1",
    )


class _FakeWaiter:
    def wait(self, **_kwargs: Any) -> None:
        return None


class _FakeEcs:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self.task_definitions: dict[str, str] = {}
        self.stop_calls: list[dict[str, Any]] = []

    def run_task(self, **kwargs: Any) -> dict[str, Any]:
        self.run_calls.append(kwargs)
        task_arn = f"arn:aws:ecs:eu-west-1:123456789012:task/{len(self.run_calls)}"
        self.task_definitions[task_arn] = kwargs["taskDefinition"]
        return {"tasks": [{"taskArn": task_arn}], "failures": []}

    def describe_tasks(self, **kwargs: Any) -> dict[str, Any]:
        task_arn = kwargs["tasks"][0]
        is_cross_mount = self.task_definitions[task_arn] == "tenant-a-role-b-mount:1"
        if is_cross_mount:
            return {
                "tasks": [
                    {
                        "taskArn": task_arn,
                        "stoppedReason": "ResourceInitializationError: access denied",
                        "containers": [{}],
                    }
                ]
            }
        return {
            "tasks": [
                {
                    "taskArn": task_arn,
                    "stoppedReason": "Essential container exited",
                    "containers": [{"exitCode": 0}],
                }
            ]
        }

    def stop_task(self, **kwargs: Any) -> dict[str, Any]:
        self.stop_calls.append(kwargs)
        return {}

    def get_waiter(self, waiter_name: str) -> _FakeWaiter:
        assert waiter_name == "tasks_stopped"
        return _FakeWaiter()


def test_live_harness_uses_only_ecs_commands_and_cleans_disposable_files() -> None:
    ecs = _FakeEcs()

    S3FilesLiveHarness(ecs, _config()).run()

    assert len(ecs.run_calls) == 7
    operations = [
        call["overrides"]["containerOverrides"][0]["command"][3] for call in ecs.run_calls
    ]
    assert operations == [
        "write-sandbox",
        "write-sandbox",
        "validate",
        "validate",
        "validate",
        "remove",
        "remove",
    ]
    for call in ecs.run_calls:
        assert call["enableExecuteCommand"] is False
        assert "environment" not in call["overrides"]["containerOverrides"][0]
        assert "PutObject" not in repr(call)


def test_live_config_rejects_inline_secret_fields() -> None:
    payload = _config().model_dump()
    payload["aws_secret_access_key"] = "must-not-be-accepted"

    with pytest.raises(ValidationError):
        LiveHarnessConfig.model_validate(payload)


@pytest.mark.e2e
def test_s3files_permissions_persistence_and_cross_tenant_denial() -> None:
    config_path_value = os.getenv(LIVE_CONFIG_ENV, "").strip()
    if not config_path_value:
        pytest.skip(f"set {LIVE_CONFIG_ENV} to opt into the destructive real-AWS gate")

    config_path = Path(config_path_value)
    if not config_path.is_file():
        pytest.fail(f"{LIVE_CONFIG_ENV} does not name a readable config file")

    config = LiveHarnessConfig.from_path(config_path)
    with _managed_harness(config) as harness:
        harness.run()


class _managed_harness:
    def __init__(self, config: LiveHarnessConfig) -> None:
        self._harness = boto3_harness(config)

    def __enter__(self):
        return self._harness

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._harness.close()
