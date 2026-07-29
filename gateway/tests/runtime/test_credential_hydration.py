"""Tests for fail-closed startup credential hydration."""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path
from typing import Any

import pytest

from gateway.runtime.credential_hydration import (
    CredentialHydrationConfig,
    GatewayCredentialHydrator,
)
from gateway.runtime.errors import GatewayConfigurationError
from gateway.runtime.manager import GatewayManager
from integrations import store
from integrations.credentials_api import IntegrationStoreV2


class _Secrets:
    def __init__(self, secrets: dict[str, str] | str) -> None:
        self.secrets = {"default": secrets} if isinstance(secrets, str) else dict(secrets)
        self.calls: list[tuple[str, str | None]] = []

    def get_secret_value(self, **kwargs: Any) -> dict[str, Any]:
        secret_id = kwargs["SecretId"]
        version_stage = kwargs.get("VersionStage")
        self.calls.append((secret_id, version_stage))
        if "default" in self.secrets and secret_id not in self.secrets:
            return {"SecretString": self.secrets["default"]}
        return {"SecretString": self.secrets[secret_id]}


class _ApiClient:
    authorization: str = ""

    def __init__(self, *, bootstrap_credential: str, **_kwargs: object) -> None:
        type(self).authorization = bootstrap_credential

    def __enter__(self) -> _ApiClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def fetch(self, organization_id: str) -> IntegrationStoreV2:
        assert organization_id == "org-a"
        return IntegrationStoreV2.model_validate(
            {
                "version": 2,
                "integrations": [
                    {
                        "id": "stub-1",
                        "service": "stub",
                        "status": "active",
                        "instances": [
                            {
                                "name": "sandbox",
                                "tags": {},
                                "credentials": {"token": "runtime-only"},
                            }
                        ],
                    }
                ],
            }
        )


def test_hydrates_exact_secret_and_atomically_writes_private_v2_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "home" / ".opensre" / "integrations.json"
    monkeypatch.setattr(store, "STORE_PATH", path)
    monkeypatch.setattr(
        "gateway.runtime.credential_hydration.CredentialsApiClient",
        _ApiClient,
    )
    secrets = _Secrets(
        json.dumps(
            {
                "credentials_api_token": "bootstrap-token",
                "database_url": "postgresql://neon.invalid/test",
            }
        )
    )
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            credentials_api_url="https://credentials.example.test",
            bootstrap_secret_arn="arn:aws:secretsmanager:region:account:secret:org-a",
        ),
        secrets_client=secrets,
    )

    bootstrap = hydrator.hydrate()

    assert secrets.calls == [("arn:aws:secretsmanager:region:account:secret:org-a", "AWSCURRENT")]
    assert _ApiClient.authorization == "bootstrap-token"
    assert bootstrap.database_url == "postgresql://neon.invalid/test"
    assert json.loads(path.read_text())["version"] == 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_hydrates_integrations_secret_into_ephemeral_store(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "run" / "opensre" / "integrations.json"
    bootstrap_arn = "arn:aws:secretsmanager:region:account:secret:bootstrap"
    integrations_arn = "arn:aws:secretsmanager:region:account:secret:integrations"
    secrets = _Secrets(
        {
            bootstrap_arn: json.dumps({"credentials_api_token": "bootstrap-token"}),
            integrations_arn: json.dumps(
                {
                    "version": 2,
                    "integrations": [
                        {
                            "id": "stub-1",
                            "service": "stub",
                            "status": "active",
                            "instances": [
                                {
                                    "name": "sandbox",
                                    "tags": {},
                                    "credentials": {"token": "from-sm"},
                                }
                            ],
                        }
                    ],
                }
            ),
        }
    )
    hydrator = GatewayCredentialHydrator(
        config=CredentialHydrationConfig(
            organization_id="org-a",
            bootstrap_secret_arn=bootstrap_arn,
            integrations_secret_arn=integrations_arn,
            integrations_store_path=str(store_path),
        ),
        secrets_client=secrets,
    )

    bootstrap = hydrator.hydrate()

    assert bootstrap.integrations_hydrated is True
    assert secrets.calls == [
        (bootstrap_arn, "AWSCURRENT"),
        (integrations_arn, "AWSCURRENT"),
    ]
    written = json.loads(store_path.read_text())
    assert written["integrations"][0]["instances"][0]["credentials"]["token"] == "from-sm"
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store_path.parent.stat().st_mode) == 0o700


def test_partial_environment_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGANIZATION_ID", "org-a")
    monkeypatch.delenv("OPENSRE_CREDENTIALS_API_URL", raising=False)
    monkeypatch.delenv("OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN", raising=False)

    with pytest.raises(ValueError, match="incomplete"):
        CredentialHydrationConfig.from_environment()


def test_manager_fails_closed_with_generic_error() -> None:
    class BrokenHydrator:
        def hydrate(self) -> None:
            raise RuntimeError("secret value must not escape")

    manager = GatewayManager(
        credential_hydrator_factory=lambda: BrokenHydrator(),  # type: ignore[arg-type]
    )

    with pytest.raises(GatewayConfigurationError, match="hydration failed"):
        manager._hydrate_credentials(logging.getLogger("test"))

    assert manager.components["credentials"] == "failed"
