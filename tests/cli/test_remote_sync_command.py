"""CLI adapter for ``opensre remote-sync`` — thin layer over the shared service."""

from __future__ import annotations

from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from config.constants.filestorage import REMOTE_SYNC_BUCKET_ENV, REMOTE_SYNC_ENV
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import SyncReport
from platform.filestorage.enums import BuiltInProvider, RemoteSyncSubcommand, SyncRootName
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.operations import SyncRootStatus, SyncStatus
from surfaces.cli.commands.remote_sync import remote_sync_command


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_remote_sync_help_lists_status_and_sync(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["--help"])
    assert result.exit_code == 0
    assert RemoteSyncSubcommand.STATUS in result.output
    assert RemoteSyncSubcommand.SYNC in result.output


def test_status_explains_off_when_disabled(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.delenv(REMOTE_SYNC_BUCKET_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)

    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_status_shows_provider_when_enabled(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = SyncStatus(
        config=RemoteSyncConfig(bucket="my-bucket", provider=BuiltInProvider.AWS, prefix="opensre"),
        roots=(
            SyncRootStatus(name=SyncRootName.SESSIONS, path=Path("/tmp/sessions"), exists=True),
            SyncRootStatus(name=SyncRootName.MEMORY, path=Path("/tmp/memory"), exists=False),
        ),
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.get_sync_status",
        lambda: status,
    )

    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code == 0
    assert "Remote sync is on (aws)" in result.output
    assert "my-bucket/opensre" in result.output
    assert "sessions" in result.output
    assert "not created yet" in result.output


def test_sync_when_disabled_prints_help(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: None,
    )
    result = runner.invoke(remote_sync_command, ["sync"])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_sync_prints_report(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    report = SyncReport(uploaded=["sessions/a.jsonl"], downloaded=[], skipped=1)
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: report,
    )
    result = runner.invoke(remote_sync_command, ["sync"])
    assert result.exit_code == 0
    assert "1 uploaded" in result.output
    assert "already current" in result.output


def test_sync_passes_direction_flags(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def _capture(*, pull_only: bool = False, push_only: bool = False) -> SyncReport:
        seen["pull_only"] = pull_only
        seen["push_only"] = push_only
        return SyncReport()

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        _capture,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--pull-only"])
    assert result.exit_code == 0
    assert seen == {"pull_only": True, "push_only": False}


def test_sync_failure_exits_nonzero(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: object) -> SyncReport:
        raise RemoteSyncConfigError("choose one of --pull-only or --push-only, not both")

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        _boom,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--pull-only", "--push-only"])
    assert result.exit_code != 0
    assert "Sync failed" in result.output


def test_default_invocation_is_status(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths as paths_mod

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)
    result = runner.invoke(remote_sync_command, [])
    assert result.exit_code == 0
    assert "Remote sync is off" in result.output


def test_cli_group_registered_on_main() -> None:
    from surfaces.cli.__main__ import cli

    ctx = click.Context(cli)
    assert "remote-sync" in cli.list_commands(ctx)


def test_top_level_opensre_remote_sync_help(runner: CliRunner) -> None:
    from surfaces.cli.__main__ import cli

    result = runner.invoke(cli, ["remote-sync", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output
    assert "sync" in result.output


def test_status_error_exits_nonzero(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> SyncStatus:
        raise RemoteSyncConfigError("OPENSRE_REMOTE_SYNC is on but no bucket")

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.get_sync_status",
        _boom,
    )
    result = runner.invoke(remote_sync_command, ["status"])
    assert result.exit_code != 0
    assert "no bucket" in result.output


def test_sync_prints_kept_remote_hint(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    report = SyncReport(
        uploaded=[],
        downloaded=[],
        kept_remote=["sessions/newer.jsonl"],
        skipped=0,
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.run_remote_sync",
        lambda **_kwargs: report,
    )
    result = runner.invoke(remote_sync_command, ["sync", "--push-only"])
    assert result.exit_code == 0
    assert "sessions/newer.jsonl" in result.output
    assert "full sync" in result.output.lower() or "no --push-only" in result.output


def test_sync_help_documents_direction_flags(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["sync", "--help"])
    assert result.exit_code == 0
    assert "--pull-only" in result.output
    assert "--push-only" in result.output


def test_setup_prompts_and_writes_config(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.write_remote_sync_config",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr("surfaces.cli.commands.remote_sync.remote_sync_enabled", lambda: False)
    result = runner.invoke(
        remote_sync_command,
        ["setup"],
        input="aws\nmy-bucket\nopensre\n\n\n",
    )
    assert result.exit_code == 0
    assert captured == {
        "provider": "aws",
        "bucket": "my-bucket",
        "prefix": "opensre",
        "region": "",
        "profile": "",
    }
    assert "Saved remote-sync settings" in result.output
    assert "enable syncing" in result.output


def test_setup_warns_when_env_override_makes_new_destination_active(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPENSRE_REMOTE_SYNC=1 overrides the stored enabled: false — say so."""
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.write_remote_sync_config",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("surfaces.cli.commands.remote_sync.remote_sync_enabled", lambda: True)
    result = runner.invoke(
        remote_sync_command,
        ["setup"],
        input="aws\nmy-bucket\nopensre\n\n\n",
    )
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "active immediately" in result.output
    assert "enable syncing" not in result.output


def test_setup_uses_defaults_on_blank_provider_and_prefix(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.write_remote_sync_config",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr("surfaces.cli.commands.remote_sync.remote_sync_enabled", lambda: False)
    result = runner.invoke(
        remote_sync_command,
        ["setup"],
        input="\nmy-bucket\n\nus-east-1\nwork\n",
    )
    assert result.exit_code == 0
    assert captured["provider"] == "aws"
    assert captured["prefix"] == "opensre"
    assert captured["region"] == "us-east-1"
    assert captured["profile"] == "work"


def test_setup_failure_exits_nonzero(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: str) -> None:
        raise RemoteSyncConfigError("could not write settings")

    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.write_remote_sync_config",
        _boom,
    )
    result = runner.invoke(
        remote_sync_command,
        ["setup"],
        input="aws\nmy-bucket\nopensre\n\n\n",
    )
    assert result.exit_code != 0
    assert "could not write settings" in result.output


def test_setup_reprompts_for_bucket_on_blank_input(runner: CliRunner) -> None:
    """click.prompt (no default) re-asks rather than accepting an empty bucket."""
    result = runner.invoke(
        remote_sync_command,
        ["setup"],
        # Blank line for Bucket the first time; Click must ask again rather
        # than proceed with an empty value.
        input="aws\n\n",
    )
    assert result.output.count("Bucket:") >= 2


def test_remote_sync_help_lists_setup(runner: CliRunner) -> None:
    result = runner.invoke(remote_sync_command, ["--help"])
    assert result.exit_code == 0
    assert RemoteSyncSubcommand.SETUP in result.output


def test_setup_reports_unwritable_settings_location_cleanly(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real filesystem failure (not a mock) must exit cleanly, not with a traceback."""
    from config.constants import paths as paths_mod

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", blocker / "home")

    result = runner.invoke(
        remote_sync_command,
        ["setup"],
        input="aws\nmy-bucket\nopensre\n\n\n",
    )
    assert result.exit_code != 0
    # A handled failure exits via SystemExit; anything else means it crashed
    # with an unhandled traceback instead of the command's normal error path.
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "could not write" in result.output


def test_setup_rejects_an_unregistered_provider(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unsupported provider must fail at setup, not later at sync time."""
    from config.constants import paths as paths_mod

    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", tmp_path)

    result = runner.invoke(
        remote_sync_command,
        ["setup"],
        input="azure-typo\nmy-bucket\nopensre\n\n\n",
    )
    assert result.exit_code != 0
    assert "unknown remote-sync provider" in result.output
