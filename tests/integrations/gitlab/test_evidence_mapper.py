"""GitLab tools must surface their diagnostic output as citeable evidence."""

from typing import Any

import pytest

from tools.investigation.stages.gather_evidence.tools import merge_tool_evidence


@pytest.mark.parametrize(
    ("tool_name", "result_key", "label", "noun"),
    [
        ("list_gitlab_commits", "commits", "GitLab Commits", "recent commits"),
        ("list_gitlab_mrs", "mrs", "GitLab Merge Requests", "recent merge requests"),
        ("list_gitlab_pipelines", "pipelines", "GitLab Pipelines", "recent pipelines"),
    ],
)
def test_gitlab_list_tools_record_counts_as_evidence(
    tool_name: str, result_key: str, label: str, noun: str
) -> None:
    evidence: dict[str, Any] = {}

    merge_tool_evidence(evidence, tool_name, {result_key: [{"id": 1}, {"id": 2}]}, {})

    entry = evidence["catalog_entries"][0]
    assert entry["source"] == tool_name
    assert entry["label"] == label
    assert entry["summary"] == f"2 {noun}"

    empty: dict[str, Any] = {}
    merge_tool_evidence(empty, tool_name, {result_key: []}, {})
    assert "catalog_entries" not in empty


def test_get_gitlab_file_records_identity_not_contents() -> None:
    evidence: dict[str, Any] = {}

    merge_tool_evidence(
        evidence,
        "get_gitlab_file",
        {"file": {"file_path": "config/app.yaml", "content": "a: 1\nb: 2\nc: 3"}},
        {},
    )

    entry = evidence["catalog_entries"][0]
    assert entry["source"] == "get_gitlab_file"
    assert entry["label"] == "GitLab File"
    assert entry["summary"] == "config/app.yaml (3 lines, 14 chars)"
    # The file body must not be dumped into the citeable summary.
    assert "a: 1" not in entry["summary"]


def test_get_gitlab_file_records_nothing_when_empty_or_unavailable() -> None:
    payloads: tuple[dict[str, Any], ...] = (
        {"file": {}},
        {"file": {"content": ""}},
        {"available": False},
    )
    for payload in payloads:
        evidence: dict[str, Any] = {}
        merge_tool_evidence(evidence, "get_gitlab_file", payload, {})
        assert "catalog_entries" not in evidence
