"""Postgres schema and column projections for the control-plane database."""

from __future__ import annotations

import hashlib

DEPLOYMENT_COLUMNS = """
organization_id, desired_state, actual_state, size_profile, cluster_arn,
service_arn, task_definition_arn, task_role_arn, s3_filesystem_arn,
s3_access_point_arn, bootstrap_secret_arn, last_error_code, created_at, updated_at
"""

RUN_COLUMNS = """
id, organization_id, source, source_event_id, prompt, status, result, error_code,
claimed_by, lease_expires_at, attempt_count, created_at, updated_at
"""

CREDENTIAL_COLUMNS = """
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

MIGRATION_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_plane_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# Append-only. Existing entries must never be edited after deployment.
MIGRATIONS: tuple[tuple[str, str], ...] = (("0001_initial", SCHEMA),)

SCHEMA_VERSION = hashlib.sha256(
    "\n".join(f"{version}\n{statement}" for version, statement in MIGRATIONS).encode()
).hexdigest()
