"""Postgres implementation of the remote Gateway repository contracts."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from platform.deployment_fargate.api_control_plane.contracts.contracts import (
    AgentRun,
    AgentRunSource,
    AgentRunStatus,
    DeploymentActualState,
    DeploymentDesiredState,
    SizeProfile,
    TenantApiCredential,
    TenantDeployment,
)

_POOL_MIN_CONNECTIONS = 1
_POOL_MAX_CONNECTIONS = 10

_DEPLOYMENT_COLUMNS = """
organization_id, desired_state, actual_state, size_profile, cluster_arn,
service_arn, task_definition_arn, task_role_arn, s3_filesystem_arn,
s3_access_point_arn, bootstrap_secret_arn, last_error_code, created_at, updated_at
"""

_RUN_COLUMNS = """
id, organization_id, source, source_event_id, prompt, status, result, error_code,
claimed_by, lease_expires_at, attempt_count, created_at, updated_at
"""

_CREDENTIAL_COLUMNS = """
key_id, organization_id, secret_arn, enabled, created_at, updated_at, rotated_at
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenant_deployments (
    organization_id TEXT PRIMARY KEY,
    desired_state TEXT NOT NULL,
    actual_state TEXT NOT NULL,
    size_profile TEXT NOT NULL,
    cluster_arn TEXT,
    service_arn TEXT,
    task_definition_arn TEXT,
    task_role_arn TEXT,
    s3_filesystem_arn TEXT,
    s3_access_point_arn TEXT,
    bootstrap_secret_arn TEXT,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS tenant_api_credentials (
    key_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    secret_arn TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    rotated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS tenant_api_credentials_org
    ON tenant_api_credentials (organization_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_event_id TEXT,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    result JSONB,
    error_code TEXT,
    claimed_by TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_runs_source_event
    ON agent_runs (organization_id, source, source_event_id)
    WHERE source_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_runs_claim
    ON agent_runs (organization_id, status, created_at);
"""


def _deployment_from_row(row: tuple[Any, ...]) -> TenantDeployment:
    return TenantDeployment(
        organization_id=row[0],
        desired_state=DeploymentDesiredState(row[1]),
        actual_state=DeploymentActualState(row[2]),
        size_profile=SizeProfile(row[3]),
        cluster_arn=row[4],
        service_arn=row[5],
        task_definition_arn=row[6],
        task_role_arn=row[7],
        s3_filesystem_arn=row[8],
        s3_access_point_arn=row[9],
        bootstrap_secret_arn=row[10],
        last_error_code=row[11],
        created_at=row[12],
        updated_at=row[13],
    )


def _json_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object in agent run result")
    return parsed


def _run_from_row(row: tuple[Any, ...]) -> AgentRun:
    return AgentRun(
        id=row[0],
        organization_id=row[1],
        source=AgentRunSource(row[2]),
        source_event_id=row[3],
        prompt=row[4],
        status=AgentRunStatus(row[5]),
        result=_json_object(row[6]),
        error_code=row[7],
        claimed_by=row[8],
        lease_expires_at=row[9],
        attempt_count=row[10],
        created_at=row[11],
        updated_at=row[12],
    )


def _credential_from_row(row: tuple[Any, ...]) -> TenantApiCredential:
    return TenantApiCredential(
        key_id=row[0],
        organization_id=row[1],
        secret_arn=row[2],
        enabled=row[3],
        created_at=row[4],
        updated_at=row[5],
        rotated_at=row[6],
    )


def _lease_seconds(lease_duration: timedelta) -> float:
    seconds = lease_duration.total_seconds()
    if seconds <= 0:
        raise ValueError("lease_duration must be positive")
    return seconds


class PostgresControlPlaneStore:
    """Pooled Postgres store for deployment, run, and credential metadata."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any = None
        self._pool_lock = threading.Lock()
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(SCHEMA)

    def _get_pool(self) -> Any:
        with self._pool_lock:
            if self._pool is None:
                from psycopg2.pool import ThreadedConnectionPool

                self._pool = ThreadedConnectionPool(
                    _POOL_MIN_CONNECTIONS,
                    _POOL_MAX_CONNECTIONS,
                    self._dsn,
                )
            return self._pool

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        pool = self._get_pool()
        connection = pool.getconn()
        try:
            with connection:
                yield connection
        finally:
            pool.putconn(connection)

    def save_deployment(self, deployment: TenantDeployment) -> TenantDeployment:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO tenant_deployments (
                    organization_id, desired_state, actual_state, size_profile,
                    cluster_arn, service_arn, task_definition_arn, task_role_arn,
                    s3_filesystem_arn, s3_access_point_arn, bootstrap_secret_arn,
                    last_error_code, created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (organization_id) DO UPDATE SET
                    desired_state = EXCLUDED.desired_state,
                    actual_state = EXCLUDED.actual_state,
                    size_profile = EXCLUDED.size_profile,
                    cluster_arn = EXCLUDED.cluster_arn,
                    service_arn = EXCLUDED.service_arn,
                    task_definition_arn = EXCLUDED.task_definition_arn,
                    task_role_arn = EXCLUDED.task_role_arn,
                    s3_filesystem_arn = EXCLUDED.s3_filesystem_arn,
                    s3_access_point_arn = EXCLUDED.s3_access_point_arn,
                    bootstrap_secret_arn = EXCLUDED.bootstrap_secret_arn,
                    last_error_code = EXCLUDED.last_error_code,
                    updated_at = EXCLUDED.updated_at
                RETURNING {_DEPLOYMENT_COLUMNS}
                """,
                (
                    deployment.organization_id,
                    deployment.desired_state.value,
                    deployment.actual_state.value,
                    deployment.size_profile.value,
                    deployment.cluster_arn,
                    deployment.service_arn,
                    deployment.task_definition_arn,
                    deployment.task_role_arn,
                    deployment.s3_filesystem_arn,
                    deployment.s3_access_point_arn,
                    deployment.bootstrap_secret_arn,
                    deployment.last_error_code,
                    deployment.created_at,
                    deployment.updated_at,
                ),
            )
            return _deployment_from_row(cursor.fetchone())

    def get_deployment(self, organization_id: str) -> TenantDeployment | None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_DEPLOYMENT_COLUMNS}
                FROM tenant_deployments
                WHERE organization_id = %s
                """,
                (organization_id,),
            )
            row = cursor.fetchone()
            return _deployment_from_row(row) if row else None

    def count_running_deployments(
        self,
        *,
        exclude_organization_id: str | None = None,
    ) -> int:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM tenant_deployments
                WHERE desired_state = %s
                  AND (%s IS NULL OR organization_id <> %s)
                """,
                (
                    DeploymentDesiredState.RUNNING.value,
                    exclude_organization_id,
                    exclude_organization_id,
                ),
            )
            row = cursor.fetchone()
            return int(row[0])

    def enqueue_run(
        self,
        *,
        organization_id: str,
        source: AgentRunSource,
        prompt: str,
        source_event_id: str | None = None,
    ) -> AgentRun:
        run_id = str(uuid.uuid4())
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO agent_runs (
                    id, organization_id, source, source_event_id, prompt, status,
                    attempt_count, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 0, now(), now())
                ON CONFLICT (organization_id, source, source_event_id)
                    WHERE source_event_id IS NOT NULL
                DO UPDATE SET updated_at = agent_runs.updated_at
                RETURNING {_RUN_COLUMNS}
                """,
                (
                    run_id,
                    organization_id,
                    source.value,
                    source_event_id,
                    prompt,
                    AgentRunStatus.QUEUED.value,
                ),
            )
            return _run_from_row(cursor.fetchone())

    def get_run(self, run_id: str) -> AgentRun | None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {_RUN_COLUMNS} FROM agent_runs WHERE id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
            return _run_from_row(row) if row else None

    def claim_next_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None:
        lease_seconds = _lease_seconds(lease_duration)
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE agent_runs
                SET status = %s,
                    claimed_by = %s,
                    lease_expires_at = now() + make_interval(secs => %s),
                    attempt_count = attempt_count + 1,
                    updated_at = now()
                WHERE id = (
                    SELECT id
                    FROM agent_runs
                    WHERE organization_id = %s
                      AND (
                          status = %s
                          OR (status = %s AND lease_expires_at < now())
                      )
                    ORDER BY created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING {_RUN_COLUMNS}
                """,
                (
                    AgentRunStatus.RUNNING.value,
                    worker_id,
                    lease_seconds,
                    organization_id,
                    AgentRunStatus.QUEUED.value,
                    AgentRunStatus.RUNNING.value,
                ),
            )
            row = cursor.fetchone()
            return _run_from_row(row) if row else None

    def renew_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool:
        lease_seconds = _lease_seconds(lease_duration)
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_runs
                SET lease_expires_at = now() + make_interval(secs => %s),
                    updated_at = now()
                WHERE id = %s
                  AND claimed_by = %s
                  AND status = %s
                  AND lease_expires_at > now()
                """,
                (
                    lease_seconds,
                    run_id,
                    worker_id,
                    AgentRunStatus.RUNNING.value,
                ),
            )
            return int(cursor.rowcount) == 1

    def finish_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        if status not in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED}:
            raise ValueError("finish_run requires a terminal status")
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_runs
                SET status = %s,
                    result = %s::jsonb,
                    error_code = %s,
                    claimed_by = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE id = %s
                  AND claimed_by = %s
                  AND status = %s
                  AND lease_expires_at > now()
                """,
                (
                    status.value,
                    json.dumps(result) if result is not None else None,
                    error_code,
                    run_id,
                    worker_id,
                    AgentRunStatus.RUNNING.value,
                ),
            )
            return int(cursor.rowcount) == 1

    def save_api_credential(self, credential: TenantApiCredential) -> TenantApiCredential:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO tenant_api_credentials (
                    key_id, organization_id, secret_arn, enabled,
                    created_at, updated_at, rotated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (key_id) DO UPDATE SET
                    organization_id = EXCLUDED.organization_id,
                    secret_arn = EXCLUDED.secret_arn,
                    enabled = EXCLUDED.enabled,
                    updated_at = EXCLUDED.updated_at,
                    rotated_at = EXCLUDED.rotated_at
                RETURNING {_CREDENTIAL_COLUMNS}
                """,
                (
                    credential.key_id,
                    credential.organization_id,
                    credential.secret_arn,
                    credential.enabled,
                    credential.created_at,
                    credential.updated_at,
                    credential.rotated_at,
                ),
            )
            return _credential_from_row(cursor.fetchone())

    def get_api_credential(self, key_id: str) -> TenantApiCredential | None:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {_CREDENTIAL_COLUMNS}
                FROM tenant_api_credentials
                WHERE key_id = %s
                """,
                (key_id,),
            )
            row = cursor.fetchone()
            return _credential_from_row(row) if row else None

    def disable_api_credentials(self, organization_id: str) -> int:
        with self._connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE tenant_api_credentials
                SET enabled = FALSE, updated_at = now()
                WHERE organization_id = %s AND enabled = TRUE
                """,
                (organization_id,),
            )
            return int(cursor.rowcount)
