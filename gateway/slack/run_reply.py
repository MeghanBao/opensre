"""Deliver durable Slack-sourced run results back to the originating thread.

The public forwarder enqueues ``AgentRunSource.SLACK`` runs with the channel
and thread in ``source_context``; the remote run worker calls this notifier
after persisting each finished run.
"""

from __future__ import annotations

import logging

from slack_sdk.web import WebClient

from gateway.slack.client import SlackMessagingClient, SlackWebApiClient
from gateway.slack.settings import (
    SlackGatewayEnv,
    choose_bot_token,
    load_slack_credentials,
)
from platform.deployment_contracts.models import AgentRun, AgentRunSource

_EMPTY_RESULT_FALLBACK = "The agent finished without producing a reply."


class SlackRunCompletionNotifier:
    """Post one finished Slack-sourced run's final text via ``chat.postMessage``."""

    def __init__(
        self,
        *,
        client: SlackMessagingClient,
        logger: logging.Logger,
    ) -> None:
        self._client = client
        self._logger = logger

    def __call__(self, run: AgentRun, final_text: str) -> None:
        if run.source is not AgentRunSource.SLACK:
            return
        context = run.source_context or {}
        channel = context.get("channel")
        if not isinstance(channel, str) or not channel:
            self._logger.warning(
                "Slack run %s has no channel in source_context; reply dropped",
                run.id,
            )
            return
        thread_ts = context.get("thread_ts")
        posted = self._client.post_message(
            channel=channel,
            text=final_text.strip() or _EMPTY_RESULT_FALLBACK,
            thread_ts=thread_ts if isinstance(thread_ts, str) and thread_ts else None,
        )
        if posted is None:
            self._logger.warning("Slack reply for run %s failed to post", run.id)


def build_slack_run_completion_notifier(
    logger: logging.Logger,
) -> SlackRunCompletionNotifier | None:
    """Build the notifier from the hydrated bot token, or ``None`` when unset.

    Only the bot token is required — unlike the Socket Mode worker, webhook
    replies need no app-level token or user allowlist.
    """
    try:
        env = SlackGatewayEnv()
        token = choose_bot_token(env, load_slack_credentials())
    except Exception:
        logger.info("Slack bot token unavailable; Slack run replies disabled")
        return None
    return SlackRunCompletionNotifier(
        client=SlackWebApiClient(WebClient(token=token)),
        logger=logger,
    )


__all__ = [
    "SlackRunCompletionNotifier",
    "build_slack_run_completion_notifier",
]
