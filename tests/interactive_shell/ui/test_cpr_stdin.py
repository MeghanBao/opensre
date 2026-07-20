"""Tests for CPR stdin hygiene helpers."""

from __future__ import annotations

import pytest

from surfaces.interactive_shell.ui.components.cpr_stdin import (
    contains_cpr_sequence,
    strip_cpr_sequences,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\x1b[32;1R", ""),
        ("[32;1R", ""),
        ("\x9b32;1R", ""),
        ("what is our current model?[32;1R", "what is our current model?"),
        ("before \x1b[12;80R after", "before  after"),
        ("7R[25;57R23;57R", ""),
        ("25;57R", ""),
        # A reply's tail split off from its "row;" prefix by an earlier partial
        # drain (e.g. read_key_unix drained "ESC[6;" but a further-delayed "1R"
        # arrived too late and landed in the next prompt on its own).
        ("1R", ""),
        ("  23R  ", ""),
    ],
)
def test_strip_cpr_sequences_removes_terminal_cursor_replies(
    text: str,
    expected: str,
) -> None:
    assert strip_cpr_sequences(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "5R3",
        "12;34R okay",
        "12;34R5 nodes",
        "check node 5R",
        "1R please",
    ],
)
def test_strip_cpr_sequences_does_not_touch_lookalike_real_input(text: str) -> None:
    """A bare "rowR" must only be treated as a leaked CPR tail when it is the
    *entire* submitted text — never when it merely appears within otherwise
    real content, however superficially similar-looking."""
    assert strip_cpr_sequences(text) == text


def test_contains_cpr_sequence_detects_leaked_bytes() -> None:
    assert contains_cpr_sequence("\x1b[12;80R")
    assert contains_cpr_sequence("1R")
    assert not contains_cpr_sequence("plain prompt text")
    assert not contains_cpr_sequence("5R3")
