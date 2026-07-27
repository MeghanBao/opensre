"""Control-plane database client entry point (deployments, runs, credentials)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from platform.deployment_fargate.api_control_plane.db.connection_pool import (
    PostgresConnectionPool,
)
from platform.deployment_fargate.api_control_plane.db.schema import (
    CREDENTIAL_COLUMNS,
    DEPLOYMENT_COLUMNS,
    RUN_COLUMNS,
    SCHEMA,
)
from platform.deployment_fargate.api_control_plane.utils.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStatus,
    DeploymentActualState,
    DeploymentDesiredState,
    SizeProfile,
    TenantApiCredential,
    TenantDeployment,
)


class ControlPlaneDbClient:
    """Pooled Postgres client for deployment, run, and credential metadata."""

    def __init__(self, dsn: str, *, initialize_schema: bool = True) -> None:
        self._pool = PostgresConnectionPool(dsn)
        if initialize_schema:
            with self._pool.connection() as conn, conn.cursor() as cursor:
                cursor.execute(SCHEMA)

    def upsert_tenant_deployment(self, deployment: TenantDeployment) -> TenantDeployment:
        with self._pool.connection() as conn, conn.cursor() as cursor:
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
                RETURNING {DEPLOYMENT_COLUMNS}
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
            return _map_deployment_row_to_tenant_deployment(cursor.fetchone())

    def fetch_tenant_deployment_by_organization_id(
        self,
        organization_id: str,
    ) -> TenantDeployment | None:
        with self._pool.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {DEPLOYMENT_COLUMNS}
                FROM tenant_deployments
                WHERE organization_id = %s
                """,
                (organization_id,),
            )
            row = cursor.fetchone()
            return _map_deployment_row_to_tenant_deployment(row) if row else None

    def count_deployments_with_running_desired_state(
        self,
        *,
        exclude_organization_id: str | None = None,
    ) -> int:
        with self._pool.connection() as conn, conn.cursor() as cursor:
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

    def enqueue_agent_run(
        self,
        *,
        organization_id: str,
        source: AgentRunSource,
        prompt: str,
        source_event_id: str | None = None,
    ) -> AgentRun:
        run_id = str(uuid.uuid4())
        with self._pool.connection() as conn, conn.cursor() as cursor:
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
                RETURNING {RUN_COLUMNS}
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
            return _map_run_row_to_agent_run(cursor.fetchone())

    def fetch_agent_run_by_id(self, run_id: str) -> AgentRun | None:
        with self._pool.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"SELECT {RUN_COLUMNS} FROM agent_runs WHERE id = %s",
                (run_id,),
            )
            row = cursor.fetchone()
            return _map_run_row_to_agent_run(row) if row else None

    def claim_oldest_available_agent_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None:
        lease_seconds = _require_positive_lease_seconds(lease_duration)
        with self._pool.connection() as conn, conn.cursor() as cursor:
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
                RETURNING {RUN_COLUMNS}
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
            return _map_run_row_to_agent_run(row) if row else None

    def extend_owned_agent_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool:
        lease_seconds = _require_positive_lease_seconds(lease_duration)
        with self._pool.connection() as conn, conn.cursor() as cursor:
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

    def finalize_owned_agent_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        if status not in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED}:
            raise ValueError("finalize_owned_agent_run requires a terminal status")
        with self._pool.connection() as conn, conn.cursor() as cursor:
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

    def upsert_tenant_api_credential(
        self,
        credential: TenantApiCredential,
    ) -> TenantApiCredential:
        with self._pool.connection() as conn, conn.cursor() as cursor:
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
                RETURNING {CREDENTIAL_COLUMNS}
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
            return _map_credential_row_to_tenant_api_credential(cursor.fetchone())

    def fetch_tenant_api_credential_by_key_id(
        self,
        key_id: str,
    ) -> TenantApiCredential | None:
        with self._pool.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {CREDENTIAL_COLUMNS}
                FROM tenant_api_credentials
                WHERE key_id = %s
                """,
                (key_id,),
            )
            row = cursor.fetchone()
            return _map_credential_row_to_tenant_api_credential(row) if row else None

    def disable_active_tenant_api_credentials_for_organization(
        self,
        organization_id: str,
    ) -> int:
        with self._pool.connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE tenant_api_credentials
                SET enabled = FALSE, updated_at = now()
                WHERE organization_id = %s AND enabled = TRUE
                """,
                (organization_id,),
            )
            return int(cursor.rowcount)


def _map_deployment_row_to_tenant_deployment(row: tuple[Any, ...]) -> TenantDeployment:
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


def _parse_optional_agent_run_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object in agent run result")
    return parsed


def _map_run_row_to_agent_run(row: tuple[Any, ...]) -> AgentRun:
    return AgentRun(
        id=row[0],
        organization_id=row[1],
        source=AgentRunSource(row[2]),
        source_event_id=row[3],
        prompt=row[4],
        status=AgentRunStatus(row[5]),
        result=_parse_optional_agent_run_result(row[6]),
        error_code=row[7],
        claimed_by=row[8],
        lease_expires_at=row[9],
        attempt_count=row[10],
        created_at=row[11],
        updated_at=row[12],
    )


def _map_credential_row_to_tenant_api_credential(row: tuple[Any, ...]) -> TenantApiCredential:
    return TenantApiCredential(
        key_id=row[0],
        organization_id=row[1],
        secret_arn=row[2],
        enabled=row[3],
        created_at=row[4],
        updated_at=row[5],
        rotated_at=row[6],
    )


def _require_positive_lease_seconds(lease_duration: timedelta) -> float:
    seconds = lease_duration.total_seconds()
    if seconds <= 0:
        raise ValueError("lease_duration must be positive")
    return seconds
