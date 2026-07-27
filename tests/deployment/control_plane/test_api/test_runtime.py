"""Runtime composition tests for the control-plane Lambda."""

from __future__ import annotations

import pytest

from platform.deployment_fargate.api_control_plane.runtime import RuntimeConfig


def test_runtime_config_requires_real_database_region_and_iam_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DATABASE_URL",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "OPENSRE_CONTROL_PLANE_LIFECYCLE_ROLE_ARNS",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        RuntimeConfig.from_environment()

    monkeypatch.setenv("DATABASE_URL", "postgresql://neon.example/db")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv(
        "OPENSRE_CONTROL_PLANE_LIFECYCLE_ROLE_ARNS",
        ("arn:aws:iam::123456789012:role/saas-a,arn:aws:iam::123456789012:role/saas-b"),
    )

    config = RuntimeConfig.from_environment()

    assert config.database_url == "postgresql://neon.example/db"
    assert config.aws_region == "eu-west-1"
    assert len(config.lifecycle_role_arns) == 2
