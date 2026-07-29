"""Tests for posting Slack-sourced run results back to the originating thread."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from gateway.slack.run_reply import SlackRunCompletionNotifier
from platform.deployment_contracts.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStatus,
)


class _RecordingClient:
    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []

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
        return "1785343999.000001"


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


def test_non_slack_runs_and_missing_channel_are_ignored() -> None:
    client = _RecordingClient()
    notifier = _notifier(client)

    notifier(_run(source=AgentRunSource.API), "all clear")
    notifier(_run(source_context=None), "all clear")
    notifier(_run(source_context={"thread_ts": "171.1"}), "all clear")

    assert client.posted == []


def test_empty_result_text_falls_back_to_placeholder() -> None:
    client = _RecordingClient()

    _notifier(client)(_run(source_context={"channel": "C1"}), "   ")

    assert len(client.posted) == 1
    assert client.posted[0]["channel"] == "C1"
    assert client.posted[0]["thread_ts"] is None
    assert client.posted[0]["text"]
