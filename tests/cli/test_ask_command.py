from __future__ import annotations

import json
import signal

from click.testing import CliRunner

from surfaces.cli.app import cli
from surfaces.cli.ask.service import (
    AskError,
    AskExitCode,
    AskOutcome,
    AskSignal,
    AskStatus,
)
from surfaces.cli.commands.ask import ask_command


def _success(response: str = "done") -> AskOutcome:
    return AskOutcome(status=AskStatus.SUCCESS, response=response)


def test_ask_passes_prompt_and_invocation_authority(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.commands.ask.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", fake_run)

    result = CliRunner().invoke(
        ask_command,
        ["check latency", "--allowed-tool", "grafana_query"],
    )

    assert result.exit_code == 0
    assert result.output == "done\n"
    assert captured == {
        "prompt": "check latency",
        "allowed_tools": ("grafana_query",),
        "bypass_approvals": False,
        "investigate": False,
    }


def test_root_yes_does_not_bypass_ask_approvals(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.commands.ask.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", fake_run)

    result = CliRunner().invoke(cli, ["-y", "ask", "check latency"])

    assert result.exit_code == 0
    assert captured["bypass_approvals"] is False


def test_ask_reads_prompt_from_stdin(monkeypatch) -> None:
    seen: list[str] = []

    def fake_run(prompt: str, **_kwargs: object) -> AskOutcome:
        seen.append(prompt)
        return _success()

    monkeypatch.setattr("surfaces.cli.commands.ask.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", fake_run)

    result = CliRunner().invoke(ask_command, ["-"], input="from stdin\n")

    assert result.exit_code == 0
    assert seen == ["from stdin"]


def test_ask_investigate_flag_selects_investigation_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success("Root Cause\n\nupstream timeout")

    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", fake_run)

    result = CliRunner().invoke(
        ask_command,
        ["--investigate", "checkout-api is returning 502s"],
    )

    assert result.exit_code == 0
    assert captured == {
        "prompt": "checkout-api is returning 502s",
        "allowed_tools": (),
        "bypass_approvals": False,
        "investigate": True,
    }


def test_ask_literal_investigate_alias_preserves_full_prompt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", fake_run)

    result = CliRunner().invoke(
        ask_command,
        ["/investigate checkout-api is returning 502s in us-east-1"],
    )

    assert result.exit_code == 0
    assert captured["prompt"] == "checkout-api is returning 502s in us-east-1"
    assert captured["investigate"] is True


def test_ask_does_not_infer_investigation_from_prose(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(prompt: str, **kwargs: object) -> AskOutcome:
        captured.update(prompt=prompt, **kwargs)
        return _success()

    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", fake_run)

    result = CliRunner().invoke(ask_command, ["investigate how the RCA pipeline works"])

    assert result.exit_code == 0
    assert captured["investigate"] is False


def test_ask_rejects_other_literal_slash_commands_before_execution(monkeypatch) -> None:
    called = False

    def fake_run(*_args: object, **_kwargs: object) -> AskOutcome:
        nonlocal called
        called = True
        return _success()

    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", fake_run)

    result = CliRunner().invoke(ask_command, ["/integrations setup grafana"])

    assert result.exit_code == 2
    assert "opensre integrations setup grafana" in result.output
    assert called is False


def test_ask_rejects_empty_or_duplicate_investigation_selectors(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", lambda *_a, **_kw: _success())

    empty = CliRunner().invoke(ask_command, ["/investigate"])
    duplicate = CliRunner().invoke(
        ask_command,
        ["--investigate", "/investigate checkout is slow"],
    )

    assert empty.exit_code == 2
    assert "requires incident text" in empty.output
    assert duplicate.exit_code == 2
    assert "only once" in duplicate.output


def test_ask_investigation_rejects_approval_options(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.commands.ask.unknown_allowed_tools", lambda _v: ())

    result = CliRunner().invoke(
        ask_command,
        ["--investigate", "checkout is slow", "--allowed-tool", "shell_run"],
    )

    assert result.exit_code == 2
    assert "cannot be combined with --investigate" in result.output


def test_ask_interrupt_while_reading_stdin_returns_signal_exit(monkeypatch) -> None:
    def interrupt(_value: str) -> str:
        raise AskSignal(signal.SIGINT)

    monkeypatch.setattr("surfaces.cli.commands.ask.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.commands.ask._resolve_prompt", interrupt)
    monkeypatch.setattr("surfaces.cli.commands.ask.is_json_output", lambda: True)

    result = CliRunner().invoke(ask_command, ["-"])

    assert result.exit_code == 130
    assert json.loads(result.output)["status"] == "cancelled"


def test_ask_rejects_empty_prompt(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.commands.ask.unknown_allowed_tools", lambda _v: ())

    result = CliRunner().invoke(ask_command, ["-"], input="  \n")

    assert result.exit_code == 2
    assert "PROMPT must not be empty" in result.output


def test_ask_rejects_conflicting_approval_options(monkeypatch) -> None:
    monkeypatch.setattr("surfaces.cli.commands.ask.unknown_allowed_tools", lambda _v: ())

    result = CliRunner().invoke(
        ask_command,
        ["prompt", "--allowed-tool", "one", "--dangerously-bypass-approvals"],
    )

    assert result.exit_code == 2
    assert "cannot be combined" in result.output


def test_ask_rejects_unknown_allowed_tool_before_execution(monkeypatch) -> None:
    called = False

    def fake_run(*_args: object, **_kwargs: object) -> AskOutcome:
        nonlocal called
        called = True
        return _success()

    monkeypatch.setattr(
        "surfaces.cli.commands.ask.unknown_allowed_tools",
        lambda _v: ("typo",),
    )
    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", fake_run)

    result = CliRunner().invoke(ask_command, ["prompt", "--allowed-tool", "typo"])

    assert result.exit_code == 2
    assert "unknown registered tool name(s): typo" in result.output
    assert called is False


def test_ask_json_output_is_one_stable_document(monkeypatch) -> None:
    outcome = AskOutcome(
        status=AskStatus.ERROR,
        response="",
        error=AskError(message="failed", suggestion="retry"),
        exit_code=AskExitCode.ERROR,
    )
    monkeypatch.setattr("surfaces.cli.commands.ask.unknown_allowed_tools", lambda _v: ())
    monkeypatch.setattr("surfaces.cli.commands.ask.run_ask", lambda *_a, **_kw: outcome)
    monkeypatch.setattr("surfaces.cli.commands.ask.is_json_output", lambda: True)

    result = CliRunner().invoke(ask_command, ["prompt"])

    assert result.exit_code == AskExitCode.ERROR
    assert json.loads(result.output) == {
        "status": "error",
        "response": "",
        "denied_tools": [],
        "error": {"message": "failed", "suggestion": "retry"},
    }
    assert result.stderr == ""
    assert result.output.count("\n") == 1
