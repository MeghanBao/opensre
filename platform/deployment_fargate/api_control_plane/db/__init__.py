"""Control-plane Postgres database package."""

from platform.deployment_fargate.api_control_plane.db.db_client import ControlPlaneDbClient
from platform.deployment_fargate.api_control_plane.db.schema import SCHEMA

__all__ = [
    "ControlPlaneDbClient",
    "SCHEMA",
]
