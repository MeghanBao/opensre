"""REPL/gateway ``/remote-sync`` — same shared service as the CLI."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from config.constants.filestorage import DEFAULT_REMOTE_SYNC_PREFIX
from platform.filestorage.config import RemoteSyncConfig
from platform.filestorage.engine import SyncReport
from platform.filestorage.enums import SyncRootName
from platform.filestorage.errors import RemoteSyncConfigError
from platform.filestorage.operations import SyncRootStatus, SyncStatus
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.runtime.slash_adapter import headless_slash_ports
from tools.interactive_shell.shared.slash_catalog import MCP_BY_COMMAND


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_remote_sync_registered_in_slash_and_catalog() -> None:
    assert "/remote-sync" in SLASH_COMMANDS
    assert "/remote-sync" in MCP_BY_COMMAND
    assert "/sync" not in SLASH_COMMANDS


def test_gateway_headless_ports_expose_remote_sync() -> None:
    ports = headless_slash_ports()
    assert ports.command_exists("/remote-sync") is True
    assert ports.tty_interactive() is False


def test_status_off_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(config=None, roots=()),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync status", Session(), console) is True
    assert "Remote sync is off" in buf.getvalue()


def test_status_enabled_shows_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(
            config=RemoteSyncConfig(bucket="b", provider="aws", prefix="p"),
            roots=(SyncRootStatus(name=SyncRootName.SESSIONS, path=Path("/s"), exists=True),),
        ),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync", Session(), console) is True
    out = buf.getvalue()
    assert "Remote sync is on (aws)" in out
    assert "b/p" in out
    assert "sessions" in out


def test_sync_subcommand_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def _run(*, pull_only: bool = False, push_only: bool = False) -> SyncReport:
        seen["pull_only"] = pull_only
        seen["push_only"] = push_only
        return SyncReport(uploaded=["memory/a.md"], skipped=0)

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _run,
    )
    # console.status context manager — Rich Console.status works without a real TTY
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --push-only", Session(), console) is True
    assert seen == {"pull_only": False, "push_only": True}
    assert "1 uploaded" in buf.getvalue()


def test_sync_disabled_prints_help(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        lambda **_kwargs: None,
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync", Session(), console) is True
    assert "Remote sync is off" in buf.getvalue()


def test_sync_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs: object) -> SyncReport:
        raise RemoteSyncConfigError("bad flags")

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        _boom,
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --pull-only --push-only", Session(), console) is True
    out = buf.getvalue()
    assert "Command failed" in out
    # This handler also serves gateway chat, so provider detail must not appear.
    assert "bad flags" not in out, "error detail reached the chat reply"


def test_unknown_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    console, buf = _capture()
    assert dispatch_slash("/remote-sync nope", Session(), console) is True
    assert "unknown subcommand" in buf.getvalue()


def test_gateway_dispatch_uses_same_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.get_sync_status",
        lambda: SyncStatus(config=None, roots=()),
    )
    ports = headless_slash_ports()
    console, buf = _capture()
    ok = ports.dispatch(
        "/remote-sync status",
        session=Session(),
        console=console,
        confirm_fn=None,
        is_tty=False,
    )
    assert ok is True
    assert "Remote sync is off" in buf.getvalue()


def test_slash_command_metadata_for_planner() -> None:
    cmd = SLASH_COMMANDS["/remote-sync"]
    assert cmd.first_arg_completions is not None
    labels = {label for label, _hint in cmd.first_arg_completions}
    assert labels == {"status", "sync", "setup"}
    assert any("OPENSRE_REMOTE_SYNC" in note for note in (cmd.notes or ()))
    catalog = MCP_BY_COMMAND["/remote-sync"]
    assert "status" in catalog.llm_description
    assert "sync" in catalog.llm_description


def test_sync_shows_kept_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.run_remote_sync",
        lambda **_kwargs: SyncReport(kept_remote=["sessions/newer.jsonl"]),
    )
    console, buf = _capture()
    assert dispatch_slash("/remote-sync sync --push-only", Session(), console) is True
    out = buf.getvalue()
    assert "sessions/newer.jsonl" in out
    assert "newer copy" in out.lower() or "push-only" in out.lower()


def test_help_section_includes_remote_sync() -> None:
    from surfaces.interactive_shell.command_registry.help import _help_sections

    sections = dict(_help_sections())
    assert "/remote-sync" in {c.name for c in sections["Remote sync"]}


def test_repl_and_headless_dispatch_same_command_object() -> None:
    """Gateway headless ports must not register a fork of /remote-sync."""
    from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports

    repl = repl_slash_ports()
    headless = headless_slash_ports()
    assert repl.command_exists("/remote-sync")
    assert headless.command_exists("/remote-sync")
    assert SLASH_COMMANDS["/remote-sync"].handler is not None


def test_setup_prompts_and_writes_config(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["aws", "my-bucket", "opensre", "", ""])
    captured: dict[str, str] = {}

    def _capture_write(**kwargs: str) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.write_remote_sync_config",
        _capture_write,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: True,
    )
    console, buf = _capture()
    monkeypatch.setattr(console, "input", lambda _prompt: next(answers))

    assert dispatch_slash("/remote-sync setup", Session(), console) is True

    assert captured == {
        "provider": "aws",
        "bucket": "my-bucket",
        "prefix": "opensre",
        "region": "",
        "profile": "",
    }
    out = buf.getvalue()
    assert "Saved remote-sync settings" in out
    assert "enable syncing" in out


def test_setup_uses_defaults_on_blank_provider_and_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(["", "my-bucket", "", "us-east-1", "work"])
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.write_remote_sync_config",
        lambda **kwargs: captured.update(kwargs),
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: True,
    )
    console, _buf = _capture()
    monkeypatch.setattr(console, "input", lambda _prompt: next(answers))

    assert dispatch_slash("/remote-sync setup", Session(), console) is True

    assert captured["provider"] == "aws"
    assert captured["prefix"] == "opensre"
    assert captured["region"] == "us-east-1"
    assert captured["profile"] == "work"


def test_setup_failure_shows_generic_message_not_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """External surface (gateway chat shares this handler): never leak str(exc)."""

    def _boom(**_kwargs: str) -> None:
        raise RemoteSyncConfigError("SECRET-INTERNAL-DETAIL should never reach chat")

    answers = iter(["aws", "my-bucket", "opensre", "", ""])
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.write_remote_sync_config",
        _boom,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: True,
    )
    console, buf = _capture()
    monkeypatch.setattr(console, "input", lambda _prompt: next(answers))

    assert dispatch_slash("/remote-sync setup", Session(), console) is True

    out = buf.getvalue()
    assert "SECRET-INTERNAL-DETAIL" not in out
    assert "Command failed" in out


def test_setup_refuses_headless_dispatch_without_prompting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless callers with no flags get usage text; setup must not call console.input()."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: False,
    )

    def _fail_if_called(_prompt: str) -> str:
        raise AssertionError("console.input() must not be called in a headless turn")

    console, buf = _capture()
    monkeypatch.setattr(console, "input", _fail_if_called)

    assert dispatch_slash("/remote-sync setup", Session(), console) is True

    out = " ".join(buf.getvalue().split())
    assert "usage" in out
    assert "--bucket" in out


def test_setup_headless_dispatch_with_flags_writes_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway/headless callers configure setup via flags, without any prompting."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: False,
    )
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.write_remote_sync_config",
        lambda **kwargs: captured.update(kwargs),
    )

    def _fail_if_called(_prompt: str) -> str:
        raise AssertionError("console.input() must not be called when flags are given")

    console, buf = _capture()
    monkeypatch.setattr(console, "input", _fail_if_called)

    assert (
        dispatch_slash(
            "/remote-sync setup --provider aws --bucket my-bucket --region us-east-1",
            Session(),
            console,
        )
        is True
    )

    assert captured == {
        "provider": "aws",
        "bucket": "my-bucket",
        "prefix": DEFAULT_REMOTE_SYNC_PREFIX,
        "region": "us-east-1",
        "profile": "",
    }
    assert "Saved remote-sync settings" in buf.getvalue()


def test_setup_headless_dispatch_without_bucket_flag_shows_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flag other than --bucket alone must not be treated as a complete setup."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: False,
    )
    console, buf = _capture()

    assert dispatch_slash("/remote-sync setup --provider aws", Session(), console) is True

    out = buf.getvalue()
    assert "--bucket is required" in out


def test_setup_rejects_empty_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty bucket must not silently save a config the sync loader rejects."""
    from platform.filestorage.operations import write_remote_sync_config

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.write_remote_sync_config",
        write_remote_sync_config,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: True,
    )
    answers = iter(["aws", "", "opensre", "", ""])
    console, buf = _capture()
    monkeypatch.setattr(console, "input", lambda _prompt: next(answers))

    assert dispatch_slash("/remote-sync setup", Session(), console) is True

    out = buf.getvalue()
    assert "Command failed" in out
    assert "Saved remote-sync settings" not in out


def test_setup_reports_unwritable_settings_location_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real filesystem failure (not a mock) must not leak into the chat reply."""
    from config.constants import paths as paths_mod
    from platform.filestorage.operations import write_remote_sync_config

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(paths_mod, "OPENSRE_HOME_DIR", blocker / "home")
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.write_remote_sync_config",
        write_remote_sync_config,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: True,
    )
    answers = iter(["aws", "my-bucket", "opensre", "", ""])
    console, buf = _capture()
    monkeypatch.setattr(console, "input", lambda _prompt: next(answers))

    assert dispatch_slash("/remote-sync setup", Session(), console) is True

    out = buf.getvalue()
    assert "Command failed" in out
    assert "not a directory" not in out


def test_setup_cancelled_on_eof_does_not_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-D at any prompt is a normal cancellation, not a turn error."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: True,
    )
    write_called = False

    def _fail_if_called(**_kwargs: str) -> None:
        nonlocal write_called
        write_called = True

    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.write_remote_sync_config",
        _fail_if_called,
    )
    answers = iter(["aws", "my-bucket"])

    def _input(_prompt: str) -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    console, buf = _capture()
    monkeypatch.setattr(console, "input", _input)

    assert dispatch_slash("/remote-sync setup", Session(), console) is True

    assert write_called is False
    assert "Setup cancelled" in buf.getvalue()


def test_setup_cancellation_stops_at_the_first_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-D on the first prompt must not still ask the remaining four."""
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.remote_sync_cmds.repl_tty_interactive",
        lambda: True,
    )
    calls = 0

    def _input(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        raise EOFError

    console, buf = _capture()
    monkeypatch.setattr(console, "input", _input)

    assert dispatch_slash("/remote-sync setup", Session(), console) is True

    assert calls == 1
    assert "Setup cancelled" in buf.getvalue()
