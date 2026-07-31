"""Slash ``/remote-sync`` — same service as ``opensre remote-sync`` and gateway ports."""

from __future__ import annotations

import logging

from rich.console import Console

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
    REMOTE_SYNC_ENV,
)
from config.local_settings import LocalSettingsError, local_settings_path
from platform.filestorage import OrgScopeNotSupportedError, RemoteSyncError, remote_sync_enabled
from platform.filestorage.enums import RemoteSyncSubcommand
from platform.filestorage.messages import DISABLED_HELP, format_report_lines
from platform.filestorage.operations import (
    get_sync_status,
    run_remote_sync,
    write_remote_sync_config,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui import DIM, ERROR, HIGHLIGHT
from surfaces.interactive_shell.ui.components.choice_menu import repl_tty_interactive

logger = logging.getLogger(__name__)

_USAGE = (
    f"/remote-sync [{RemoteSyncSubcommand.STATUS}|{RemoteSyncSubcommand.SYNC}"
    f"|{RemoteSyncSubcommand.SETUP}] [--pull-only|--push-only]"
)
_SETUP_USAGE = (
    f"/remote-sync {RemoteSyncSubcommand.SETUP} --bucket <name> "
    "[--provider ...] [--prefix ...] [--region ...] [--profile ...]"
)
_SETUP_FLAGS = {
    "--provider": "provider",
    "--bucket": "bucket",
    "--prefix": "prefix",
    "--region": "region",
    "--profile": "profile",
}
_SETUP_PROMPTS: tuple[tuple[str, str], ...] = (
    ("provider", f"Provider [{DEFAULT_REMOTE_SYNC_PROVIDER}]> "),
    ("bucket", "Bucket> "),
    ("prefix", f"Prefix [{DEFAULT_REMOTE_SYNC_PREFIX}]> "),
    ("region", "Region (optional)> "),
    ("profile", "Profile (optional)> "),
)


def _print_lines(
    console: Console, lines: tuple[str, ...] | list[str], *, dim_last: bool = False
) -> None:
    for index, line in enumerate(lines):
        if dim_last and index == len(lines) - 1:
            console.print(f"[{DIM}]{line}[/]")
        else:
            console.print(line)


def _print_status(console: Console) -> bool:
    status = get_sync_status()
    if not status.enabled or status.config is None:
        console.print(f"[{DIM}]{DISABLED_HELP}[/]")
        return True
    cfg = status.config
    console.print(f"Remote sync is on ({cfg.provider}) → [{HIGHLIGHT}]{cfg.bucket}/{cfg.prefix}[/]")
    for root in status.roots:
        state = "exists" if root.exists else "not created yet"
        console.print(f"  {root.name:<10} {root.path} [{DIM}]({state})[/]")
    console.print(f"[{DIM}]Never uploaded: integration credentials and model keys.[/]")
    return True


def _run_sync(console: Console, args: list[str]) -> bool:
    flags = {a.lower() for a in args}
    with console.status("syncing…", spinner="dots"):
        report = run_remote_sync(
            pull_only="--pull-only" in flags,
            push_only="--push-only" in flags,
        )
    if report is None:
        console.print(f"[{DIM}]{DISABLED_HELP}[/]")
        return True
    lines = format_report_lines(report)
    _print_lines(console, lines, dim_last=bool(report.kept_remote))
    return True


def _prompt(console: Console, text: str) -> str | None:
    """One setup prompt. ``None`` means the user cancelled (Ctrl-D/Ctrl-C)."""
    try:
        return console.input(text).strip()
    except (EOFError, KeyboardInterrupt):
        return None


def _parse_setup_flags(args: list[str]) -> dict[str, str] | None:
    """``--key value`` pairs for headless/gateway callers with no real stdin.

    ``None`` means a recognized flag was malformed — missing its value
    entirely, or followed by what looks like another flag rather than a
    value (``--bucket --provider aws`` must not silently save ``"--provider"``
    as the bucket). An empty dict means no recognized flags were present at
    all, so the caller falls back to interactive prompts or a usage message.
    """
    values: dict[str, str] = {}
    index = 0
    while index < len(args):
        key = _SETUP_FLAGS.get(args[index].lower())
        if key is None:
            index += 1
            continue
        if index + 1 >= len(args) or args[index + 1].startswith("--"):
            return None
        values[key] = args[index + 1]
        index += 2
    return values


def _prompt_setup_interactively(console: Console) -> dict[str, str] | None:
    """Ask each field in turn. ``None`` means the user cancelled partway through."""
    answers: dict[str, str] = {}
    for key, label in _SETUP_PROMPTS:
        value = _prompt(console, f"[{HIGHLIGHT}]{label}[/]")
        if value is None:
            return None
        answers[key] = value
    return answers


def _run_setup(console: Console, args: list[str]) -> bool:
    flags = _parse_setup_flags(args)
    if flags is None:
        console.print(f"[{ERROR}]each flag needs a value.[/] usage: {_SETUP_USAGE}")
        return True
    answers: dict[str, str] | None
    if flags:
        if "bucket" not in flags:
            console.print(f"[{ERROR}]--bucket is required.[/] usage: {_SETUP_USAGE}")
            return True
        answers = flags
    elif repl_tty_interactive():
        console.print("Configure remote-sync connection settings (does not enable or verify it).")
        answers = _prompt_setup_interactively(console)
        if answers is None:
            console.print(f"[{DIM}]Setup cancelled.[/]")
            return True
    else:
        # No flags and no real stdin: console.input() would block or raise
        # EOFError instead of returning a usable turn.
        console.print(f"[{DIM}]usage:[/] {_SETUP_USAGE}")
        return True
    write_remote_sync_config(
        provider=answers.get("provider") or DEFAULT_REMOTE_SYNC_PROVIDER,
        bucket=answers.get("bucket", ""),
        prefix=answers.get("prefix") or DEFAULT_REMOTE_SYNC_PREFIX,
        region=answers.get("region", ""),
        profile=answers.get("profile", ""),
    )
    console.print(f"Saved remote-sync settings to [{HIGHLIGHT}]{local_settings_path()}[/].")
    if remote_sync_enabled():
        console.print(
            f"[{ERROR}]Warning:[/] {REMOTE_SYNC_ENV} is already set in your environment, "
            "so this destination is active immediately."
        )
    else:
        console.print(f"[{DIM}]Set {REMOTE_SYNC_ENV}=1 to enable syncing.[/]")
    return True


def _parse_subcommand(raw: str) -> RemoteSyncSubcommand | None:
    try:
        return RemoteSyncSubcommand(raw)
    except ValueError:
        return None


def _cmd_remote_sync(_session: Session, console: Console, args: list[str]) -> bool:
    token = (args[0] if args else RemoteSyncSubcommand.STATUS.value).strip().lower()
    sub = RemoteSyncSubcommand.STATUS if not token else _parse_subcommand(token)
    try:
        if sub is RemoteSyncSubcommand.STATUS:
            return _print_status(console)
        if sub is RemoteSyncSubcommand.SYNC:
            return _run_sync(console, args[1:])
        if sub is RemoteSyncSubcommand.SETUP:
            return _run_setup(console, args[1:])
    except OrgScopeNotSupportedError as exc:
        # Safe to show: our own wording, no vendor or credential detail.
        console.print(f"[{DIM}]{exc}[/]")
        return True
    except (RemoteSyncError, LocalSettingsError):
        # This handler also serves gateway chat, an external surface, so the
        # provider's message never reaches the reply. Detail goes to the log.
        # LocalSettingsError covers a malformed/unwritable config.yml, which
        # setup can hit even though it never touches an ObjectStore.
        logger.warning("[remote-sync] command failed", exc_info=True)
        console.print(
            f"[{ERROR}]Command failed.[/] Check the opensre log, "
            "or run [bold]opensre remote-sync status[/bold] locally for detail."
        )
        return True
    console.print(
        f"[{ERROR}]unknown subcommand:[/] {token}  "
        f"(try [bold]/remote-sync {RemoteSyncSubcommand.STATUS}[/bold])"
    )
    return True


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand(
        "/remote-sync",
        "Mirror your sessions and memory to your own object store.",
        _cmd_remote_sync,
        usage=(_USAGE,),
        notes=(
            "Off unless OPENSRE_REMOTE_SYNC is set. Default provider is "
            f"{DEFAULT_REMOTE_SYNC_PROVIDER}; set OPENSRE_REMOTE_SYNC_PROVIDER "
            "to choose another registered backend. Integration credentials and "
            "model keys are never uploaded. Same service as "
            "`opensre remote-sync` (gateway clients share this slash command).",
        ),
        first_arg_completions=(
            (
                RemoteSyncSubcommand.STATUS.value,
                "show whether sync is on and what would be mirrored",
            ),
            (RemoteSyncSubcommand.SYNC.value, "pull remote changes, then push local ones"),
            (
                RemoteSyncSubcommand.SETUP.value,
                "configure provider/bucket/prefix/region/profile via --flags, or "
                "prompts in a real terminal (does not enable it)",
            ),
        ),
        use_cases=(
            "sync my conversations to S3",
            "back up my opensre memory",
            "set up opensre on my second laptop",
        ),
    ),
)

__all__ = ["COMMANDS"]
