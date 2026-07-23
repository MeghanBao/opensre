"""Opt-in real-AWS S3 Files isolation harness.

The harness does not provision or delete durable AWS resources. It consumes
pre-provisioned task definitions, starts short-lived tasks, and cleans up its
disposable mounted integration files through those same mounts.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from platform.deployment.fargate.setup_task import SANDBOX_SERVICE


class LiveHarnessConfig(BaseModel):
    """Non-secret references needed to exercise pre-provisioned AWS resources."""

    model_config = ConfigDict(extra="forbid", strict=True)

    region: str
    cluster: str
    subnet_ids: list[str] = Field(min_length=1)
    security_group_ids: list[str] = Field(min_length=1)
    tenant_a_task_definition: str
    tenant_b_task_definition: str
    tenant_a_cross_mount_task_definition: str
    container_name: str = "gateway"
    assign_public_ip: bool = False
    tenant_a_organization_id: str = "s3files-live-tenant-a"
    tenant_b_organization_id: str = "s3files-live-tenant-b"

    @classmethod
    def from_path(cls, path: Path) -> LiveHarnessConfig:
        """Load explicit non-secret live configuration from disk."""

        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class EcsClient(Protocol):
    """Subset of the ECS client used by the live harness."""

    def run_task(self, **kwargs: Any) -> dict[str, Any]: ...

    def describe_tasks(self, **kwargs: Any) -> dict[str, Any]: ...

    def stop_task(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_waiter(self, waiter_name: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class TaskResult:
    task_arn: str
    stopped_reason: str
    exit_code: int | None


class S3FilesLiveHarness:
    """Run the persistence and access-point-denial checks on ECS Fargate."""

    def __init__(self, ecs: EcsClient, config: LiveHarnessConfig) -> None:
        self._ecs = ecs
        self._config = config
        self._active_task_arns: set[str] = set()

    def run(self) -> None:
        """Exercise tenant identity, persistence, permissions, and denial."""

        try:
            self._run_for_tenant(
                self._config.tenant_a_task_definition,
                self._config.tenant_a_organization_id,
                "write-sandbox",
            )
            self._run_for_tenant(
                self._config.tenant_b_task_definition,
                self._config.tenant_b_organization_id,
                "write-sandbox",
            )

            # These are replacement tasks, not the setup task that wrote the file.
            self._run_for_tenant(
                self._config.tenant_a_task_definition,
                self._config.tenant_a_organization_id,
                "validate",
            )
            self._run_for_tenant(
                self._config.tenant_b_task_definition,
                self._config.tenant_b_organization_id,
                "validate",
            )
            self._assert_cross_tenant_mount_denied()
        finally:
            self._best_effort_remove(
                self._config.tenant_a_task_definition,
                self._config.tenant_a_organization_id,
            )
            self._best_effort_remove(
                self._config.tenant_b_task_definition,
                self._config.tenant_b_organization_id,
            )
            self.close()

    def close(self) -> None:
        """Stop only tasks started by this harness that are still active."""

        pending = tuple(self._active_task_arns)
        for task_arn in pending:
            try:
                self._ecs.stop_task(
                    cluster=self._config.cluster,
                    task=task_arn,
                    reason="OpenSRE S3 Files live-test cleanup",
                )
            except Exception:  # noqa: BLE001 - cleanup remains best effort
                continue

    def _run_for_tenant(
        self,
        task_definition: str,
        organization_id: str,
        operation: str,
    ) -> TaskResult:
        result = self._run_task(
            task_definition,
            [
                "python",
                "-m",
                "platform.deployment.fargate.setup_task",
                operation,
                "--home",
                "/workspace/home",
                "--organization-id",
                organization_id,
            ],
        )
        if result.exit_code != 0:
            raise RuntimeError(f"S3 Files {operation} task did not complete successfully")
        return result

    def _assert_cross_tenant_mount_denied(self) -> None:
        result = self._run_task(
            self._config.tenant_a_cross_mount_task_definition,
            [
                "python",
                "-m",
                "platform.deployment.fargate.setup_task",
                "validate",
                "--home",
                "/workspace/home",
                "--organization-id",
                self._config.tenant_b_organization_id,
            ],
        )
        # A correctly denied access-point mount fails before the container runs,
        # so ECS has no container exit code. Never inspect or print file contents.
        if result.exit_code is not None:
            raise AssertionError("tenant A task role was able to start with tenant B access point")
        reason = result.stopped_reason.lower()
        if not any(
            marker in reason for marker in ("access", "authorization", "resourceinitialization")
        ):
            raise AssertionError("cross-tenant task failed for an unexpected reason")

    def _run_task(self, task_definition: str, command: list[str]) -> TaskResult:
        response = self._ecs.run_task(
            cluster=self._config.cluster,
            taskDefinition=task_definition,
            launchType="FARGATE",
            platformVersion="LATEST",
            count=1,
            enableExecuteCommand=False,
            startedBy="opensre-s3files-live",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": self._config.subnet_ids,
                    "securityGroups": self._config.security_group_ids,
                    "assignPublicIp": "ENABLED" if self._config.assign_public_ip else "DISABLED",
                }
            },
            overrides={
                "containerOverrides": [
                    {
                        "name": self._config.container_name,
                        "command": command,
                    }
                ]
            },
            tags=[
                {"key": "opensre:test", "value": "s3files-live"},
                {"key": "opensre:credential-type", "value": SANDBOX_SERVICE},
            ],
        )
        failures = response.get("failures", [])
        if failures:
            raise RuntimeError("ECS rejected the S3 Files live-test task")
        tasks = response.get("tasks", [])
        if len(tasks) != 1 or not tasks[0].get("taskArn"):
            raise RuntimeError("ECS did not return one live-test task")
        task_arn = str(tasks[0]["taskArn"])
        self._active_task_arns.add(task_arn)
        waiter = self._ecs.get_waiter("tasks_stopped")
        waiter.wait(
            cluster=self._config.cluster,
            tasks=[task_arn],
            WaiterConfig={"Delay": 5, "MaxAttempts": 120},
        )
        described = self._ecs.describe_tasks(
            cluster=self._config.cluster,
            tasks=[task_arn],
        )
        self._active_task_arns.discard(task_arn)
        stopped_task = described["tasks"][0]
        containers = stopped_task.get("containers", [])
        exit_code = containers[0].get("exitCode") if containers else None
        return TaskResult(
            task_arn=task_arn,
            stopped_reason=str(stopped_task.get("stoppedReason", "")),
            exit_code=cast(int | None, exit_code),
        )

    def _best_effort_remove(self, task_definition: str, organization_id: str) -> None:
        with contextlib.suppress(Exception):
            self._run_for_tenant(task_definition, organization_id, "remove")


def boto3_harness(config: LiveHarnessConfig) -> S3FilesLiveHarness:
    """Create a live harness using the normal AWS workload credential chain."""

    import boto3

    ecs = cast(EcsClient, boto3.client("ecs", region_name=config.region))
    return S3FilesLiveHarness(ecs, config)


def config_template() -> str:
    """Return a redacted example config for operator documentation/errors."""

    return json.dumps(
        {
            "region": "us-east-1",
            "cluster": "existing-cluster",
            "subnet_ids": ["subnet-..."],
            "security_group_ids": ["sg-..."],
            "tenant_a_task_definition": "tenant-a-setup:1",
            "tenant_b_task_definition": "tenant-b-setup:1",
            "tenant_a_cross_mount_task_definition": "tenant-a-role-tenant-b-mount:1",
        },
        indent=2,
    )
