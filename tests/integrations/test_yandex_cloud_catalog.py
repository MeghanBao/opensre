"""Catalog wiring for the Yandex Cloud integration.

Covers the path a credential actually travels: a stored record or a ``YC_*``
environment is classified into a typed config, survives effective resolution,
and reaches a tool through ``sources``. Asserting on the config model alone
would pass while any one of those steps dropped the record.
"""

from __future__ import annotations

import pytest

from config.constants.yandex_cloud import (
    YC_FOLDER_ID_ENV,
    YC_IAM_TOKEN_ENV,
    YC_SA_KEY_FILE_ENV,
    YC_USE_METADATA_ENV,
)
from integrations.catalog import (
    classify_integrations,
    load_env_integrations,
    resolve_effective_integrations,
)
from integrations.yandex_cloud import classify
from integrations.yandex_cloud.availability import (
    yc_available_or_backend,
    yc_credentials,
)
from integrations.yandex_cloud.config import YandexCloudIntegrationConfig
from tools.investigation.stages.gather_evidence.tools import availability_view

FOLDER = "b1gtestfolder"

STORE_RECORD = {
    "service": "yandex_cloud",
    "id": "yc-1",
    "status": "active",
    "credentials": {"folder_id": FOLDER, "iam_token": "t1.test-token"},
}


class TestClassify:
    def test_returns_typed_config_with_required_fields(self) -> None:
        cfg, key = classify(
            {"folder_id": FOLDER, "cloud_id": "b1cloud", "iam_token": "t1.token"},
            record_id="rec-1",
        )

        assert key == "yandex_cloud"
        assert isinstance(cfg, YandexCloudIntegrationConfig)
        assert cfg.folder_id == FOLDER
        assert cfg.cloud_id == "b1cloud"
        assert cfg.integration_id == "rec-1"

    def test_skips_a_record_with_no_credential(self) -> None:
        """A folder alone cannot authenticate, so the catalog must skip it.

        Publishing it would leave an integration that reads as configured and
        fails on the first call.
        """
        cfg, key = classify({"folder_id": FOLDER}, record_id="rec-1")

        assert cfg is None
        assert key is None

    def test_skips_a_record_with_no_folder(self) -> None:
        """Every Yandex Cloud read is folder-scoped; the API rejects the rest."""
        cfg, key = classify({"iam_token": "t1.token"}, record_id="rec-1")

        assert cfg is None
        assert key is None

    def test_metadata_auth_needs_no_folder(self) -> None:
        """An instance knows which folder it runs in, so it need not be told."""
        cfg, key = classify({"use_metadata": True}, record_id="rec-1")

        assert key == "yandex_cloud"
        assert cfg is not None
        assert cfg.use_metadata is True

    def test_unused_credentials_stored_as_null_are_accepted(self) -> None:
        """``integrations setup`` writes unset fields as null, not as absent.

        The config model is strict, so without normalization a record saved by
        the wizard would fail validation and read as "saved but unusable".
        """
        cfg, key = classify(
            {
                "folder_id": FOLDER,
                "iam_token": "t1.token",
                "sa_key_file": None,
                "sa_key": None,
                "oauth_token": None,
                "cloud_id": None,
            },
            record_id="rec-1",
        )

        assert key == "yandex_cloud"
        assert cfg is not None
        assert cfg.sa_key_file == ""


class TestCatalogEndToEnd:
    """Store record -> classified -> availability view -> tool credentials."""

    def test_a_stored_record_reaches_a_tool(self) -> None:
        resolved = classify_integrations([STORE_RECORD])
        view = availability_view(resolved)

        assert yc_available_or_backend(view) is True
        assert yc_credentials(view)["folder_id"] == FOLDER

    def test_a_stored_record_survives_effective_resolution(self) -> None:
        effective = resolve_effective_integrations(
            store_integrations=[STORE_RECORD], env_integrations=[]
        )

        assert "yandex_cloud" in effective

    def test_a_synthetic_backend_makes_tools_available_without_credentials(self) -> None:
        """Synthetic tests attach a fixture instead of reaching the cloud."""
        view = {"yandex_cloud": {"_backend": object()}}

        assert yc_available_or_backend(view) is True

    def test_nothing_configured_means_unavailable(self) -> None:
        assert yc_available_or_backend({}) is False


class TestEnvironmentLoading:
    def test_folder_and_token_are_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
        monkeypatch.setenv(YC_IAM_TOKEN_ENV, "t1.env-token")

        records = [r for r in load_env_integrations() if r.get("service") == "yandex_cloud"]

        assert len(records) == 1
        assert records[0]["credentials"]["folder_id"] == FOLDER

    def test_a_folder_without_a_credential_is_not_loaded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(YC_FOLDER_ID_ENV, FOLDER)
        monkeypatch.delenv(YC_IAM_TOKEN_ENV, raising=False)
        monkeypatch.delenv(YC_SA_KEY_FILE_ENV, raising=False)

        records = [r for r in load_env_integrations() if r.get("service") == "yandex_cloud"]

        assert records == []

    def test_metadata_alone_is_enough_on_an_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(YC_FOLDER_ID_ENV, raising=False)
        monkeypatch.setenv(YC_USE_METADATA_ENV, "true")

        records = [r for r in load_env_integrations() if r.get("service") == "yandex_cloud"]

        assert len(records) == 1
        assert records[0]["credentials"]["use_metadata"] is True

    def test_nothing_set_loads_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in (YC_FOLDER_ID_ENV, YC_IAM_TOKEN_ENV, YC_USE_METADATA_ENV):
            monkeypatch.delenv(name, raising=False)

        records = [r for r in load_env_integrations() if r.get("service") == "yandex_cloud"]

        assert records == []


class TestRegistryWiring:
    def test_setup_and_verify_list_the_service(self) -> None:
        from integrations.registry import SUPPORTED_SETUP_SERVICES, SUPPORTED_VERIFY_SERVICES

        assert "yandex_cloud" in SUPPORTED_SETUP_SERVICES
        assert "yandex_cloud" in SUPPORTED_VERIFY_SERVICES

    def test_setup_has_a_handler(self) -> None:
        """A spec with a setup order but no handler is accepted by Click and
        then fails to dispatch."""
        from integrations.cli import _HANDLERS

        assert "yandex_cloud" in _HANDLERS

    def test_a_verifier_is_registered(self) -> None:
        from integrations.verification import get_verifier

        assert get_verifier("yandex_cloud") is not None

    def test_the_vendor_cli_name_resolves(self) -> None:
        """``yc`` is what the vendor's own CLI is called, so people type it."""
        from integrations.registry import service_key

        assert service_key("yc") == "yandex_cloud"
        assert service_key("yandex") == "yandex_cloud"
