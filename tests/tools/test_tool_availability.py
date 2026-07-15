"""Tests for the shared tool-unavailable payload helper."""

from __future__ import annotations

from core.tool_framework.utils.tool_availability import tool_unavailable


def test_tool_unavailable_base_shape() -> None:
    payload = tool_unavailable("helm", "helm integration is not configured.")

    assert payload == {
        "source": "helm",
        "available": False,
        "error": "helm integration is not configured.",
    }


def test_tool_unavailable_merges_extra_fields() -> None:
    payload = tool_unavailable("groundcover", "query failed", data=[], summary={}, truncated=False)

    assert payload == {
        "source": "groundcover",
        "available": False,
        "error": "query failed",
        "data": [],
        "summary": {},
        "truncated": False,
    }


def test_tool_unavailable_extra_field_does_not_disturb_base_fields() -> None:
    payload = tool_unavailable("posthog_mcp", "not configured", tool="list_flags")

    assert payload["source"] == "posthog_mcp"
    assert payload["available"] is False
    assert payload["error"] == "not configured"
    assert payload["tool"] == "list_flags"
