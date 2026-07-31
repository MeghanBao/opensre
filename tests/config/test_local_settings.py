"""save_local_settings wraps filesystem failures like load_local_settings does."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants import paths
from config.local_settings import LocalSettingsError, local_settings_path, save_local_settings


def test_save_local_settings_wraps_oserror_in_local_settings_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file where a directory is needed must not surface a bare OSError."""
    # Arrange: "home" needs to be a directory, but a plain file sits there.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", blocker / "home")

    # Act / Assert
    with pytest.raises(LocalSettingsError, match="could not write"):
        save_local_settings({"remote_sync": {"bucket": "b"}})


def test_save_local_settings_writes_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)

    save_local_settings({"remote_sync": {"bucket": "b"}})

    assert local_settings_path().exists()
