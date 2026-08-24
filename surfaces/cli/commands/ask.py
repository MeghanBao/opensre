"""One-shot configured agent command."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import click
from rich.console import Console

from infrastructure.process.runtime_flags import is_json_output
from surfaces.cli.ask.approval import unknown_allowed_tools
from surfaces.cli.ask.service import (
    AskOutcome,
    AskSignal,
    AskStatus,
    ask_signal_scope,
    cancelled_outcome,
    run_ask,
)

_INVESTIGATE_COMMAND = "/investigate"


def _resolve_prompt(value: str) -> str:
    prompt = sys.stdin.read() if value == "-" else value
    prompt = prompt.strip()
    if not prompt:
        raise click.UsageError("PROMPT must not be empty.")
    return prompt


def _resolve_mode(prompt: str, *, investigate: bool) -> tuple[str, bool]:
    """Resolve explicit Ask mode selectors without inferring prose intent."""
    if prompt == _INVESTIGATE_COMMAND:
        raise click.UsageError("/investigate requires incident text.")
    if prompt.startswith(_INVESTIGATE_COMMAND) and prompt[len(_INVESTIGATE_COMMAND)].isspace():
        if investigate:
            raise click.UsageError("Specify investigation mode only once.")
        incident = prompt.removeprefix(_INVESTIGATE_COMMAND).strip()
        return incident, True
    if prompt.startswith("/"):
        if prompt.startswith("/integrations "):
            direct_command = f"opensre {prompt.removeprefix('/')}"
            raise click.UsageError(
                f"Ask only supports /investigate. Run `{direct_command}` directly."
            )
        raise click.UsageError(
            "Ask only supports /investigate. Open `opensre` for slash commands "
            "or run the equivalent `opensre` subcommand directly."
        )
    return prompt, investigate


@contextmanager
def _progress(label: str) -> Iterator[None]:
    """Show TTY-only progress on stderr without contaminating automation output."""
    if is_json_output() or not sys.stderr.isatty():
        yield
        return
    with Console(stderr=True).status(label):
        yield


def _render_outcome(outcome: AskOutcome) -> None:
    if is_json_output():
        click.echo(json.dumps(outcome.as_dict(), ensure_ascii=False))
        return
    if outcome.status is AskStatus.SUCCESS:
        click.echo(outcome.response)
        return
    if outcome.response:
        click.echo(outcome.response, err=True)
    if outcome.error is not None:
        click.echo(outcome.error.message, err=True)
        if outcome.error.suggestion:
            click.echo(f"Suggestion: {outcome.error.suggestion}", err=True)


@click.command(name="ask")
@click.argument("prompt")
@click.option(
    "--investigate",
    is_flag=True,
    help="Run the prompt through the full incident investigation pipeline.",
)
@click.option(
    "--allowed-tool",
    "allowed_tools",
    multiple=True,
    metavar="TOOL",
    help="Authorize a registered tool for this invocation. Repeat as needed.",
)
@click.option(
    "--dangerously-bypass-approvals",
    is_flag=True,
    help="Authorize every approval-gated tool for this invocation.",
)
def ask_command(
    prompt: str,
    investigate: bool,
    allowed_tools: tuple[str, ...],
    dangerously_bypass_approvals: bool,
) -> None:
    """Run one configured OpenSRE agent request and exit."""
    try:
        with ask_signal_scope():
            resolved_prompt, investigate = _resolve_mode(
                _resolve_prompt(prompt), investigate=investigate
            )
            if investigate and (allowed_tools or dangerously_bypass_approvals):
                raise click.UsageError(
                    "--allowed-tool and --dangerously-bypass-approvals cannot be combined "
                    "with --investigate."
                )
            if allowed_tools and dangerously_bypass_approvals:
                raise click.UsageError(
                    "--allowed-tool cannot be combined with --dangerously-bypass-approvals."
                )
            unknown = unknown_allowed_tools(allowed_tools)
            if unknown:
                names = ", ".join(unknown)
                raise click.BadParameter(
                    f"unknown registered tool name(s): {names}",
                    param_hint="--allowed-tool",
                )
            label = "Investigating…" if investigate else "Working…"
            with _progress(label):
                outcome = run_ask(
                    resolved_prompt,
                    allowed_tools=allowed_tools,
                    bypass_approvals=dangerously_bypass_approvals,
                    investigate=investigate,
                )
    except AskSignal as exc:
        outcome = cancelled_outcome(exc.signum)
    _render_outcome(outcome)
    if outcome.exit_code:
        raise SystemExit(int(outcome.exit_code))


__all__ = ["ask_command"]
