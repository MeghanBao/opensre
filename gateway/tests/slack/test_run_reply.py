"""Tests for posting Slack-sourced run results back to the originating thread."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.slack.run_reply import (
    SlackRunCompletionNotifier,
    SlackRunReplyDeliveryError,
    build_slack_run_completion_notifier,
)
from platform.deployment_contracts.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStatus,
)


class _RecordingClient:
    def __init__(self, *, post_result: str | None = "1785343999.000001") -> None:
        self.posted: list[dict[str, Any]] = []
        self._post_result = post_result

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Any = None,
    ) -> str | None:
        _ = blocks
        self.posted.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return self._post_result


def _run(
    *,
    source: AgentRunSource = AgentRunSource.SLACK,
    source_context: dict[str, Any] | None = None,
) -> AgentRun:
    now = datetime.now(UTC)
    return AgentRun(
        id="run-1",
        organization_id="org-a",
        source=source,
        prompt="investigate",
        status=AgentRunStatus.SUCCEEDED,
        attempt_count=1,
        created_at=now,
        updated_at=now,
        source_context=source_context,
    )


def _notifier(client: _RecordingClient) -> SlackRunCompletionNotifier:
    return SlackRunCompletionNotifier(
        client=client,  # type: ignore[arg-type]
        logger=logging.getLogger("test"),
    )


def test_slack_run_reply_is_posted_to_originating_thread() -> None:
    client = _RecordingClient()

    _notifier(client)(
        _run(source_context={"channel": "C1", "thread_ts": "171.1", "user": "U1"}),
        "all clear",
    )

    assert client.posted == [{"channel": "C1", "text": "all clear", "thread_ts": "171.1"}]


def test_non_slack_runs_are_ignored_and_missing_channel_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RecordingClient()
    notifier = _notifier(client)
    report = MagicMock()
    monkeypatch.setattr("gateway.slack.run_reply.report_exception", report)

    notifier(_run(source=AgentRunSource.API), "all clear")
    notifier(_run(source_context=None), "all clear")
    notifier(_run(source_context={"thread_ts": "171.1"}), "all clear")

    assert client.posted == []
    assert report.call_count == 2
    assert all(
        isinstance(call.args[0], SlackRunReplyDeliveryError) for call in report.call_args_list
    )


def test_post_message_failure_is_reported_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RecordingClient(post_result=None)
    report = MagicMock()
    monkeypatch.setattr("gateway.slack.run_reply.report_exception", report)

    _notifier(client)(_run(source_context={"channel": "C1", "thread_ts": "171.1"}), "all clear")

    assert client.posted == [{"channel": "C1", "text": "all clear", "thread_ts": "171.1"}]
    report.assert_called_once()
    assert isinstance(report.call_args.args[0], SlackRunReplyDeliveryError)
    assert report.call_args.kwargs["extras"]["channel"] == "C1"
    assert report.call_args.kwargs["extras"]["run_id"] == "run-1"


def test_build_notifier_reports_when_bot_token_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = MagicMock()
    monkeypatch.setattr("gateway.slack.run_reply.report_exception", report)
    monkeypatch.setattr(
        "gateway.slack.run_reply.choose_bot_token",
        MagicMock(side_effect=RuntimeError("missing token")),
    )
    monkeypatch.setattr(
        "gateway.slack.run_reply.load_slack_credentials",
        MagicMock(return_value={}),
    )
    monkeypatch.setattr(
        "gateway.slack.run_reply.SlackGatewayEnv",
        MagicMock(return_value=MagicMock()),
    )

    assert build_slack_run_completion_notifier(logging.getLogger("test")) is None
    report.assert_called_once()
    assert report.call_args.kwargs["severity"] == "warning"


def test_empty_result_text_falls_back_to_placeholder() -> None:
    client = _RecordingClient()

    _notifier(client)(_run(source_context={"channel": "C1"}), "   ")

    assert len(client.posted) == 1
    assert client.posted[0]["channel"] == "C1"
    assert client.posted[0]["thread_ts"] is None
    assert client.posted[0]["text"]
