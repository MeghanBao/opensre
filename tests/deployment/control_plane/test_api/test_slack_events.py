"""Unit tests for Slack Events verification and run routing on the public forwarder."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from platform.deployment_contracts.models import (
    AgentRun,
    AgentRunSource,
    AgentRunStatus,
)
from platform.deployment_multi_tenant.lambda_public_forwarder.slack_events import (
    handle_slack_events,
)
from platform.deployment_multi_tenant.utils.http_lambda import ClientRequestError

_TEAM_ID = "T06FASL80Q1"
_ORGANIZATION_ID = "org_123"


class _FakeSlackEventRepository:
    """Run queue + workspace mapping fake mirroring the DB dedupe index."""

    def __init__(self, teams: dict[str, str] | None = None) -> None:
        self.teams = teams if teams is not None else {_TEAM_ID: _ORGANIZATION_ID}
        self.enqueued: list[dict[str, Any]] = []
        self._by_event: dict[tuple[str, str, str], AgentRun] = {}

    def upsert_slack_team_install(
        self,
        *,
        team_id: str,
        organization_id: str,
    ) -> None:
        self.teams[team_id] = organization_id

    def fetch_organization_id_by_slack_team(self, team_id: str) -> str | None:
        return self.teams.get(team_id)

    def enqueue_agent_run(
        self,
        *,
        organization_id: str,
        source: AgentRunSource,
        prompt: str,
        source_event_id: str | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> AgentRun:
        key = (organization_id, source.value, source_event_id or "")
        existing = self._by_event.get(key)
        if existing is not None:
            return existing
        self.enqueued.append(
            {
                "organization_id": organization_id,
                "source": source,
                "prompt": prompt,
                "source_event_id": source_event_id,
                "source_context": source_context,
            }
        )
        now = datetime.now(UTC)
        run = AgentRun(
            id=f"run-{len(self.enqueued)}",
            organization_id=organization_id,
            source=source,
            prompt=prompt,
            source_event_id=source_event_id,
            source_context=source_context,
            status=AgentRunStatus.QUEUED,
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        self._by_event[key] = run
        return run

    def fetch_agent_run_by_id(self, run_id: str) -> AgentRun | None:
        for run in self._by_event.values():
            if run.id == run_id:
                return run
        return None

    def claim_oldest_available_agent_run(
        self,
        *,
        organization_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> AgentRun | None:
        raise NotImplementedError

    def extend_owned_agent_run_lease(
        self,
        *,
        run_id: str,
        worker_id: str,
        lease_duration: timedelta,
    ) -> bool:
        raise NotImplementedError

    def finalize_owned_agent_run(
        self,
        *,
        run_id: str,
        worker_id: str,
        status: AgentRunStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> bool:
        raise NotImplementedError


def _signed_event(
    *,
    body: dict[str, object],
    signing_secret: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    raw = json.dumps(body, separators=(",", ":"))
    ts = timestamp or str(int(time.time()))
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        f"v0:{ts}:{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "version": "2.0",
        "rawPath": "/v1/slack/events",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": f"v0={digest}",
        },
        "body": raw,
        "isBase64Encoded": False,
    }


def _unsigned_event(body: dict[str, object]) -> dict[str, Any]:
    return {
        "version": "2.0",
        "rawPath": "/v1/slack/events",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {},
        "body": json.dumps(body, separators=(",", ":")),
        "isBase64Encoded": False,
    }


def _app_mention_callback(
    *,
    event_id: str = "Ev0001",
    team_id: str = _TEAM_ID,
    text: str = "<@U0AF9966M6F> what is going on?",
    event_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "app_mention",
        "user": "U06EY4M9CHH",
        "ts": "1785343490.812049",
        "text": text,
        "channel": "C087VC8SQHF",
        "event_ts": "1785343490.812049",
    }
    event.update(event_overrides or {})
    return {
        "type": "event_callback",
        "team_id": team_id,
        "event_id": event_id,
        "event": event,
    }


def test_valid_signature_allows_url_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-signing-secret"
    monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
    challenge = "challenge-value-123"
    event = _signed_event(
        body={"type": "url_verification", "challenge": challenge},
        signing_secret=secret,
    )

    response = handle_slack_events(event, repository=_FakeSlackEventRepository())

    assert response["statusCode"] == 200
    assert response["body"] == challenge


def test_invalid_signature_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing-secret")
    event = _signed_event(
        body={"type": "url_verification", "challenge": "x"},
        signing_secret="wrong-secret",
    )
    # Sign with wrong secret above, but Lambda expects the env secret.
    event["headers"]["X-Slack-Signature"] = "v0=deadbeef"

    with pytest.raises(ClientRequestError) as caught:
        handle_slack_events(event, repository=_FakeSlackEventRepository())

    assert caught.value.code == "invalid_slack_signature"
    assert caught.value.status_code == 401


def test_app_mention_from_mapped_workspace_enqueues_slack_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    repository = _FakeSlackEventRepository()

    response = handle_slack_events(
        _unsigned_event(_app_mention_callback()),
        repository=repository,
    )

    assert response["statusCode"] == 200
    assert repository.enqueued == [
        {
            "organization_id": _ORGANIZATION_ID,
            "source": AgentRunSource.SLACK,
            "prompt": "what is going on?",
            "source_event_id": "Ev0001",
            "source_context": {
                "channel": "C087VC8SQHF",
                "thread_ts": "1785343490.812049",
                "user": "U06EY4M9CHH",
            },
        }
    ]


def test_direct_message_enqueues_and_threaded_mention_keeps_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    repository = _FakeSlackEventRepository()

    handle_slack_events(
        _unsigned_event(
            {
                "type": "event_callback",
                "team_id": _TEAM_ID,
                "event_id": "Ev-dm",
                "event": {
                    "type": "message",
                    "channel_type": "im",
                    "user": "U06EY4M9CHH",
                    "ts": "1785343500.000100",
                    "text": "check the frontend errors",
                    "channel": "D0DMCHANNEL",
                },
            }
        ),
        repository=repository,
    )
    handle_slack_events(
        _unsigned_event(
            _app_mention_callback(
                event_id="Ev-threaded",
                event_overrides={"thread_ts": "1785343000.000001"},
            )
        ),
        repository=repository,
    )

    assert repository.enqueued[0]["prompt"] == "check the frontend errors"
    assert repository.enqueued[0]["source_context"]["channel"] == "D0DMCHANNEL"
    assert repository.enqueued[1]["source_context"]["thread_ts"] == "1785343000.000001"


def test_unmapped_workspace_acks_without_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    repository = _FakeSlackEventRepository(teams={})

    response = handle_slack_events(
        _unsigned_event(_app_mention_callback()),
        repository=repository,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"ok": True}
    assert repository.enqueued == []


def test_slack_retry_of_same_event_id_does_not_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    repository = _FakeSlackEventRepository()
    event = _unsigned_event(_app_mention_callback(event_id="Ev-retry"))

    first = handle_slack_events(event, repository=repository)
    second = handle_slack_events(event, repository=repository)

    assert first["statusCode"] == second["statusCode"] == 200
    assert len(repository.enqueued) == 1


@pytest.mark.parametrize(
    "event_overrides",
    [
        {"bot_id": "B03V1LKA7M1"},
        {"subtype": "message_changed"},
        {"text": "<@U0AF9966M6F>"},
        {"channel": ""},
    ],
)
def test_non_actionable_events_ack_without_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    event_overrides: dict[str, object],
) -> None:
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    repository = _FakeSlackEventRepository()

    response = handle_slack_events(
        _unsigned_event(_app_mention_callback(event_overrides=event_overrides)),
        repository=repository,
    )

    assert response["statusCode"] == 200
    assert repository.enqueued == []


def test_channel_message_without_mention_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mention arrives as both message and app_mention; only the latter runs."""
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    repository = _FakeSlackEventRepository()

    response = handle_slack_events(
        _unsigned_event(
            {
                "type": "event_callback",
                "team_id": _TEAM_ID,
                "event_id": "Ev-channel-msg",
                "event": {
                    "type": "message",
                    "channel_type": "group",
                    "user": "U06EY4M9CHH",
                    "ts": "1785343490.812049",
                    "text": "just chatting",
                    "channel": "C087VC8SQHF",
                },
            }
        ),
        repository=repository,
    )

    assert response["statusCode"] == 200
    assert repository.enqueued == []
