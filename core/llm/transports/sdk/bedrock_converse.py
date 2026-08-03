"""Shared helpers for the Amazon Bedrock Converse API (tool schemas and messages)."""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import TYPE_CHECKING, Any

from config.constants.aws import (
    BEDROCK_AWS_EXTERNAL_ID_ENV,
    BEDROCK_AWS_PROFILE_ENV,
    BEDROCK_AWS_REGION_ENV,
    BEDROCK_AWS_ROLE_ARN_ENV,
)
from core.llm.shared.tool_schema_normalize import (
    BEDROCK_UNSUPPORTED_SCHEMA_KEYS,
    normalize_object_tool_input_schema,
    sanitize_strict_tool_schema,
)

if TYPE_CHECKING:
    import boto3

logger = logging.getLogger(__name__)


def require_aws_region() -> str:
    """Return configured AWS region or raise with a clear configuration error.

    ``BEDROCK_AWS_REGION`` takes precedence so a cross-account Bedrock setup
    (see :func:`resolve_bedrock_aws_session`) can target a region independent
    of the region investigation tools use.
    """
    region = (
        os.getenv(BEDROCK_AWS_REGION_ENV)
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or ""
    ).strip()
    if not region:
        raise RuntimeError(
            "Bedrock requires AWS_REGION, AWS_DEFAULT_REGION, or BEDROCK_AWS_REGION to be set."
        )
    return region


def _assumed_bedrock_role_credentials(region: str) -> dict[str, Any] | None:
    """Raw STS credentials from ``BEDROCK_AWS_ROLE_ARN``, or ``None`` if unset.

    A single ``sts:AssumeRole`` snapshot, same shape as
    ``integrations/aws/verifier.py::_build_sts_client``. The returned
    credentials are valid for the assumed role's session duration (its
    ``MaxSessionDuration``, often 1 hour unless the role is configured
    longer) and do not refresh themselves — a cached client that outlives
    that window needs to be recreated. Prefer ``BEDROCK_AWS_PROFILE`` with a
    role_arn + source_profile pair in ``~/.aws/config`` when a process runs
    longer than that: :func:`resolve_bedrock_aws_session` builds a
    self-refreshing session for a named profile.
    """
    role_arn = os.getenv(BEDROCK_AWS_ROLE_ARN_ENV, "").strip()
    if not role_arn:
        return None

    import boto3

    external_id = os.getenv(BEDROCK_AWS_EXTERNAL_ID_ENV, "").strip()
    sts = boto3.client("sts", region_name=region)
    assume_role_kwargs: dict[str, str] = {
        "RoleArn": role_arn,
        "RoleSessionName": "OpenSREBedrockInvoke",
    }
    if external_id:
        assume_role_kwargs["ExternalId"] = external_id
    credentials: dict[str, Any] = sts.assume_role(**assume_role_kwargs)["Credentials"]
    return credentials


def resolve_bedrock_aws_session(region: str) -> boto3.Session | None:
    """Isolate Bedrock's AWS identity from the ambient default credential chain.

    Investigation tools (EC2, CloudWatch, ...) already work correctly across
    accounts because they read the process's single ambient ``AWS_PROFILE``
    directly. Bedrock had no equivalent: every Bedrock client reached for the
    same ambient chain, so a laptop configured for a Bedrock account reachable
    only via an AssumeRole profile in a different account than the infra being
    investigated had no way to give Bedrock a *different* identity than
    whatever profile was active for everything else (#4482).

    ``region`` is the caller's own already-resolved region (each of the three
    Bedrock clients has a different region-resolution policy — two raise when
    unset, one defaults to ``us-east-1``); this function does not re-derive
    or require it, so it can't reject a config a caller would otherwise
    accept.

    ``BEDROCK_AWS_ROLE_ARN`` takes precedence — an explicit assume-role, for
    deployments with no ``~/.aws/config`` profile file (e.g. Fargate).
    ``BEDROCK_AWS_PROFILE`` is a named profile, which may itself be an
    AssumeRole + source_profile pair — the shape the issue's own setup used;
    boto3 refreshes that automatically, since the profile is passed straight
    through rather than resolved once and frozen. Returns ``None`` when
    neither is set, so callers fall through to today's exact ambient-chain
    behavior; this function never changes behavior for a caller who hasn't
    opted in.
    """
    import boto3

    credentials = _assumed_bedrock_role_credentials(region)
    if credentials is not None:
        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=region,
        )

    profile = os.getenv(BEDROCK_AWS_PROFILE_ENV, "").strip()
    if profile:
        return boto3.Session(profile_name=profile)

    return None


def resolve_bedrock_anthropic_kwargs(region: str) -> dict[str, Any]:
    """``AnthropicBedrock`` auth kwargs for a cross-account Bedrock override.

    ``AnthropicBedrock`` only accepts a profile name (which it resolves, and
    refreshes, itself) or fully static access/secret/session-token strings —
    unlike ``boto3.Session``, there is no hook for a refreshable credentials
    provider, so the two overrides are necessarily handled differently:

    - ``BEDROCK_AWS_PROFILE`` is passed straight through as ``aws_profile``,
      so whatever refresh the named profile supports keeps working (a
      role_arn + source_profile pair in ``~/.aws/config`` auto-refreshes).
    - ``BEDROCK_AWS_ROLE_ARN`` has no such hook: the returned credentials are
      a one-time snapshot (see :func:`_assumed_bedrock_role_credentials`),
      valid only for the assumed role's session duration.

    Returns an empty dict when neither override is set, so callers fall
    through to today's exact ambient-chain behavior (``AnthropicBedrock``
    with no explicit credentials).
    """
    credentials = _assumed_bedrock_role_credentials(region)
    if credentials is not None:
        return {
            "aws_access_key": credentials["AccessKeyId"],
            "aws_secret_key": credentials["SecretAccessKey"],
            "aws_session_token": credentials["SessionToken"],
        }

    profile = os.getenv(BEDROCK_AWS_PROFILE_ENV, "").strip()
    if profile:
        return {"aws_profile": profile}

    return {}


def new_tool_use_id() -> str:
    """Return a short alphanumeric id suitable for Converse ``toolUseId`` fields."""
    return secrets.token_hex(5)


def sanitize_converse_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a Converse-compatible copy of *schema* with required ``type`` / ``items`` filled in."""
    return sanitize_strict_tool_schema(schema, unsupported_keys=BEDROCK_UNSUPPORTED_SCHEMA_KEYS)


def normalize_tool_input_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a tool's public input schema for ``toolSpec.inputSchema.json``."""
    return normalize_object_tool_input_schema(
        schema,
        unsupported_keys=BEDROCK_UNSUPPORTED_SCHEMA_KEYS,
    )


def build_converse_tool_specs(tools: list[Any]) -> list[dict[str, Any]]:
    """Build ``toolConfig.tools`` entries from registered tool objects."""
    specs: list[dict[str, Any]] = []
    for tool in tools:
        specs.append(
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {"json": normalize_tool_input_schema(tool.public_input_schema)},
                }
            }
        )
    return specs


def to_converse_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert investigation messages to Converse ``messages`` shape."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            converted.append({"role": message["role"], "content": [{"text": content}]})
        else:
            converted.append(message)
    return converted


def build_assistant_tool_use_message(tool_calls: list[Any]) -> dict[str, Any]:
    """Build a Converse assistant message containing ``toolUse`` blocks."""
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                }
            }
            for tc in tool_calls
        ],
    }


def build_tool_result_message(tool_calls: list[Any], results: list[Any]) -> dict[str, Any]:
    """Build the Converse ``toolResult`` user message for one round of tool calls."""
    content: list[dict[str, Any]] = []
    for tc, result in zip(tool_calls, results, strict=True):
        is_error = isinstance(result, dict) and bool(result.get("error"))
        if isinstance(result, dict):
            sanitized = json.loads(json.dumps(result, default=str))
            result_content: list[dict[str, Any]] = [{"json": sanitized}]
        else:
            result_content = [{"text": json.dumps(result, default=str)}]
        tool_result: dict[str, Any] = {
            "toolUseId": tc.id,
            "content": result_content,
        }
        if is_error:
            tool_result["status"] = "error"
        content.append({"toolResult": tool_result})
    return {"role": "user", "content": content}


def parse_converse_output(
    response: dict[str, Any],
) -> tuple[str, list[tuple[str, str, dict[str, Any]]], str, dict[str, Any]]:
    """Parse a Converse API response into text, tool calls, stop reason, and raw message."""
    output_message = response.get("output", {}).get("message", {})
    if not isinstance(output_message, dict):
        output_message = {"role": "assistant", "content": []}

    text_parts: list[str] = []
    tool_calls: list[tuple[str, str, dict[str, Any]]] = []
    for block in output_message.get("content", []):
        if not isinstance(block, dict):
            continue
        if "text" in block:
            text_parts.append(str(block["text"]))
            continue
        tool_use = block.get("toolUse")
        if not isinstance(tool_use, dict):
            continue
        raw_input = tool_use.get("input")
        tool_calls.append(
            (
                str(tool_use["toolUseId"]),
                str(tool_use["name"]),
                raw_input if isinstance(raw_input, dict) else {},
            )
        )

    stop_reason = str(response.get("stopReason", "end_turn"))
    return "".join(text_parts), tool_calls, stop_reason, output_message


def map_bedrock_client_error(model: str, err: Any) -> RuntimeError:
    """Map a ``botocore`` ``ClientError`` to a user-facing ``RuntimeError``."""
    code = err.response.get("Error", {}).get("Code", "")
    message = err.response.get("Error", {}).get("Message", "") or str(err)

    if code == "ValidationException":
        return RuntimeError(f"Bedrock request rejected (HTTP 400): {message}")
    if code == "ResourceNotFoundException":
        return RuntimeError(
            f"Bedrock model '{model}' was not found in the configured region. "
            "Check the model ID, region, or inference profile."
        )
    if code == "ThrottlingException":
        return RuntimeError(
            f"Bedrock rate limit exceeded for model '{model}'. "
            "Reduce request frequency or request a quota increase."
        )
    if code in ("AccessDeniedException", "UnauthorizedException"):
        err_msg_str = str(message)
        if (
            "INVALID_PAYMENT_INSTRUMENT" in err_msg_str
            or "payment instrument" in err_msg_str.lower()
        ):
            aws_message = err_msg_str.strip().rstrip(".")
            detail = f" Cause: {aws_message}." if aws_message else ""
            return RuntimeError(
                f"Access denied for Bedrock model '{model}'.{detail} "
                "A valid AWS payment instrument is required."
            )
        aws_message = err_msg_str.strip().rstrip(".")
        detail = f" Cause: {aws_message}." if aws_message else ""
        return RuntimeError(
            f"Access denied for Bedrock model '{model}'.{detail} "
            "Check Bedrock model access (per-region opt-in), your "
            "AWS Marketplace subscription / payment method, and "
            "IAM permissions."
        )
    return RuntimeError(f"Bedrock API request failed: {message}")


def is_non_retryable_bedrock_code(code: str) -> bool:
    """Return True when retrying the same request will not help."""
    return code in (
        "ValidationException",
        "ResourceNotFoundException",
        "AccessDeniedException",
        "UnauthorizedException",
    )
