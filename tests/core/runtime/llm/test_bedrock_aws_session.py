"""Tests for isolating Bedrock's AWS identity from investigation tools' (#4482).

Investigation tools already read the ambient ``AWS_PROFILE`` directly, so a
laptop configured for one AWS account already investigates that account
correctly. Bedrock had no equivalent override: every Bedrock client reached
for the same ambient default credential chain, so a cross-account setup
(Bedrock reachable only via an AssumeRole profile in a different account
than the infra being investigated) had no way to give Bedrock a different
identity than whatever profile was active for everything else.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.llm.transports.sdk.bedrock_converse import (
    require_aws_region,
    resolve_bedrock_anthropic_kwargs,
    resolve_bedrock_aws_session,
)


def _clear_bedrock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "BEDROCK_AWS_REGION",
        "BEDROCK_AWS_PROFILE",
        "BEDROCK_AWS_ROLE_ARN",
        "BEDROCK_AWS_EXTERNAL_ID",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- #
# require_aws_region — BEDROCK_AWS_REGION precedence                          #
# --------------------------------------------------------------------------- #


def test_require_aws_region_prefers_bedrock_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_AWS_REGION", "eu-west-1")

    assert require_aws_region() == "eu-west-1"


def test_require_aws_region_falls_back_to_aws_region(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    assert require_aws_region() == "us-east-1"


# --------------------------------------------------------------------------- #
# resolve_bedrock_aws_session (boto3.client consumers)                        #
# --------------------------------------------------------------------------- #


def test_resolve_bedrock_aws_session_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No override set: callers must fall through to today's ambient-chain behavior."""
    _clear_bedrock_env(monkeypatch)

    assert resolve_bedrock_aws_session("us-east-1") is None


def test_resolve_bedrock_aws_session_uses_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_AWS_PROFILE", "bedrock-account")
    calls: list[dict[str, Any]] = []

    class _FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    monkeypatch.setattr("boto3.Session", _FakeSession)

    session = resolve_bedrock_aws_session("us-east-1")

    assert isinstance(session, _FakeSession)
    assert calls == [{"profile_name": "bedrock-account"}]


def _install_fake_assume_role(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Patch the real STS network call inside AssumeRoleCredentialFetcher.

    Returns the list of ``assume_role``-shaped kwargs the fetcher was called
    with (RoleArn/RoleSessionName/ExternalId), one entry per real fetch.
    Leaves the real ``DeferredRefreshableCredentials``/``AssumeRoleCredentialFetcher``
    wiring un-mocked, so a test can call ``get_frozen_credentials()`` and get
    values produced by a genuine (if network-stubbed) assume-role flow.
    """
    from botocore.credentials import AssumeRoleCredentialFetcher

    calls: list[dict[str, Any]] = []
    counter = iter(range(1, 1000))

    def _fake_get_credentials(self: Any) -> dict[str, Any]:
        n = next(counter)
        calls.append(dict(self._assume_kwargs))
        return {
            "Credentials": {
                "AccessKeyId": f"AKIA-TEMP-{n}",
                "SecretAccessKey": f"secret-temp-{n}",
                "SessionToken": f"token-temp-{n}",
                # Far enough out that a single get_frozen_credentials() call
                # doesn't itself trigger a second fetch via botocore's own
                # advisory-refresh window.
                "Expiration": "2999-01-01T00:00:00+00:00",
            }
        }

    monkeypatch.setattr(AssumeRoleCredentialFetcher, "_get_credentials", _fake_get_credentials)
    return calls


def test_resolve_bedrock_aws_session_assumes_role(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_AWS_ROLE_ARN", "arn:aws:iam::999:role/BedrockInvoke")
    monkeypatch.setenv("BEDROCK_AWS_EXTERNAL_ID", "shared-secret")
    calls = _install_fake_assume_role(monkeypatch)

    session = resolve_bedrock_aws_session("us-east-1")

    assert session is not None
    frozen = session.get_credentials().get_frozen_credentials()
    assert frozen.access_key == "AKIA-TEMP-1"
    assert frozen.secret_key == "secret-temp-1"
    assert frozen.token == "token-temp-1"
    assert calls == [
        {
            "RoleArn": "arn:aws:iam::999:role/BedrockInvoke",
            "RoleSessionName": "OpenSREBedrockInvoke",
            "ExternalId": "shared-secret",
        }
    ]


def test_resolve_bedrock_aws_session_role_arn_credentials_are_refreshable_not_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defining fix for #4482's repeated P1: BEDROCK_AWS_ROLE_ARN must
    produce a self-refreshing credentials object, not a static one-time
    snapshot (the round-1/round-2 bug). ``DeferredRefreshableCredentials``
    is real, heavily-used botocore/boto3 machinery (the same mechanism
    boto3 uses internally for a role_arn + source_profile profile) -- its
    own time-based refresh-on-expiry behavior is botocore's tested concern,
    not ours; what this asserts is that OpenSRE's wiring actually produces
    that type instead of a plain, permanently-static Credentials object."""
    from botocore.credentials import DeferredRefreshableCredentials

    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_AWS_ROLE_ARN", "arn:aws:iam::999:role/BedrockInvoke")
    _install_fake_assume_role(monkeypatch)

    session = resolve_bedrock_aws_session("us-east-1")

    assert session is not None
    assert isinstance(session.get_credentials(), DeferredRefreshableCredentials)


def test_resolve_bedrock_aws_session_role_arn_takes_precedence_over_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both set: the explicit assume-role wins, the profile is never touched."""
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_AWS_ROLE_ARN", "arn:aws:iam::999:role/BedrockInvoke")
    monkeypatch.setenv("BEDROCK_AWS_PROFILE", "should-be-ignored")
    _install_fake_assume_role(monkeypatch)
    session_calls: list[dict[str, Any]] = []
    monkeypatch.setattr("boto3.Session", lambda **kwargs: session_calls.append(kwargs) or object())

    resolve_bedrock_aws_session("us-east-1")

    assert len(session_calls) == 1
    assert "profile_name" not in session_calls[0]


def test_resolve_bedrock_aws_session_role_arn_without_external_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ExternalId is optional; omit it entirely rather than sending an empty string."""
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_AWS_ROLE_ARN", "arn:aws:iam::999:role/BedrockInvoke")
    calls = _install_fake_assume_role(monkeypatch)

    session = resolve_bedrock_aws_session("us-east-1")
    assert session is not None
    session.get_credentials().get_frozen_credentials()

    assert "ExternalId" not in calls[0]


def test_resolve_bedrock_aws_session_never_requires_a_region_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller's region is a parameter, not re-derived (regression: a
    BEDROCK_AWS_ROLE_ARN-only config must not reject a caller's own
    permissive us-east-1 default just because AWS_REGION is unset)."""
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_AWS_ROLE_ARN", "arn:aws:iam::999:role/BedrockInvoke")
    _install_fake_assume_role(monkeypatch)

    # No AWS_REGION/AWS_DEFAULT_REGION/BEDROCK_AWS_REGION set anywhere: this
    # must not raise, since the caller decided "us-east-1" is an acceptable
    # default and passed it in directly.
    session = resolve_bedrock_aws_session("us-east-1")

    assert session is not None
    assert session.region_name == "us-east-1"


# --------------------------------------------------------------------------- #
# resolve_bedrock_anthropic_kwargs (AnthropicBedrock consumers)                #
# --------------------------------------------------------------------------- #


def test_resolve_bedrock_anthropic_kwargs_empty_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_bedrock_env(monkeypatch)

    assert resolve_bedrock_anthropic_kwargs("us-east-1") == {}


def test_resolve_bedrock_anthropic_kwargs_passes_profile_through_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named profile must reach AnthropicBedrock as aws_profile=, not be
    resolved-and-frozen into static keys — freezing would silently drop the
    refresh a role_arn + source_profile pair in ~/.aws/config provides."""
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_AWS_PROFILE", "bedrock-account")

    assert resolve_bedrock_anthropic_kwargs("us-east-1") == {"aws_profile": "bedrock-account"}


def test_resolve_bedrock_anthropic_kwargs_assumes_role(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_bedrock_env(monkeypatch)
    monkeypatch.setenv("BEDROCK_AWS_ROLE_ARN", "arn:aws:iam::999:role/BedrockInvoke")

    class _FakeStsClient:
        def assume_role(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "Credentials": {
                    "AccessKeyId": "AKIA-TEMP",
                    "SecretAccessKey": "secret-temp",
                    "SessionToken": "token-temp",
                }
            }

    monkeypatch.setattr("boto3.client", lambda *_a, **_k: _FakeStsClient())

    assert resolve_bedrock_anthropic_kwargs("us-east-1") == {
        "aws_access_key": "AKIA-TEMP",
        "aws_secret_key": "secret-temp",
        "aws_session_token": "token-temp",
    }
