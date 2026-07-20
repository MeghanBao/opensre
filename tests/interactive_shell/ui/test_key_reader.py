"""Tests for terminal cooked-mode restore after raw-mode menus."""

from __future__ import annotations

import os
import pty
import select
import threading
from types import SimpleNamespace

import pytest

from surfaces.interactive_shell.ui.components import key_reader


def test_restore_stdin_terminal_recooks_input_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    # A raw menu (tty.setraw) clears ICRNL, so Enter (CR) stops submitting until it
    # is restored. Pin that restore re-enables the cooked-mode flags on a raw snapshot.
    termios = pytest.importorskip("termios")

    monkeypatch.setattr(key_reader.os, "name", "posix")
    monkeypatch.setattr(
        key_reader.sys, "stdin", SimpleNamespace(isatty=lambda: True, fileno=lambda: 0)
    )

    raw_attrs = [0, 0, 0, 0, 0, 0, []]  # iflag/oflag/cflag/lflag all cleared (raw mode)
    written: dict[str, list[object]] = {}
    monkeypatch.setattr(termios, "tcgetattr", lambda _fd: list(raw_attrs))
    monkeypatch.setattr(
        termios, "tcsetattr", lambda _fd, _when, attrs: written.__setitem__("attrs", attrs)
    )
    monkeypatch.setattr(termios, "tcflush", lambda _fd, _queue: None)

    key_reader.restore_stdin_terminal()

    attrs = written["attrs"]
    assert attrs[0] & termios.ICRNL  # CR -> NL so Enter submits again
    assert attrs[1] & termios.OPOST  # output newline post-processing
    assert attrs[3] & termios.ICANON  # line editing
    assert attrs[3] & termios.ECHO  # keystrokes visible


def test_restore_stdin_terminal_is_a_no_op_when_not_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(key_reader.os, "name", "posix")
    monkeypatch.setattr(
        key_reader.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: False, fileno=lambda: (_ for _ in ()).throw(AssertionError)),
    )
    key_reader.restore_stdin_terminal()  # returns without touching termios


# ── delayed CPR/DSR reply draining ──────────────────────────────────────────
#
# A leaked cursor-position reply (``ESC[row;colR``) from prompt-toolkit's
# bottom-toolbar redraw can arrive split across the picker's one-byte-at-a-time
# reads. If any part of it isn't drained, the remainder leaks into the next
# picker keypress or REPL prompt as literal, visible text (e.g. "[6;1R").


def test_read_pending_byte_unix_returns_byte_that_arrives_in_time() -> None:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"[")
        assert key_reader._read_pending_byte_unix(read_fd, timeout=0.2) == b"["
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_read_pending_byte_unix_times_out_when_nothing_arrives() -> None:
    read_fd, write_fd = os.pipe()
    try:
        assert key_reader._read_pending_byte_unix(read_fd, timeout=0.05) == b""
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_drain_trailing_csi_bytes_consumes_a_full_cpr_reply() -> None:
    """``6;1R`` (the tail of a CPR reply after ``ESC[`` was already read) must be
    fully drained, including the terminating ``R`` (a valid CSI final byte)."""
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"6;1R")
        key_reader._drain_trailing_csi_bytes(read_fd)
        assert not select.select([read_fd], [], [], 0.05)[0], "trailing bytes were left undrained"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_drain_trailing_csi_bytes_drains_a_reply_delayed_mid_sequence() -> None:
    """The most recurring symptom: the reply's digits arrive a beat after the
    first byte, spread across separate ``select()`` waits inside the drain loop.
    Nothing should be left over for the next reader to inherit."""
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"6")

        def _write_rest() -> None:
            os.write(write_fd, b";1R")

        timer = threading.Timer(0.03, _write_rest)
        timer.start()
        try:
            key_reader._drain_trailing_csi_bytes(read_fd, first=b"6")
        finally:
            timer.join()
        assert not select.select([read_fd], [], [], 0.05)[0], "trailing bytes were left undrained"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_read_key_unix_drains_a_delayed_cpr_reply_and_returns_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a full ``ESC[6;1R`` CPR reply, with the ``[6;1R`` tail
    arriving ~30ms after the lone ESC byte, must be classified as "cancel"
    (not misread as a real key) with nothing left buffered afterwards — the
    exact scenario that showed up as visible "[6;1R" garbage in the REPL.
    """
    pytest.importorskip("termios")
    pytest.importorskip("tty")
    master_fd, slave_fd = pty.openpty()
    try:
        import tty as _tty

        _tty.setraw(slave_fd)
        monkeypatch.setattr(key_reader.sys, "stdin", SimpleNamespace(fileno=lambda: slave_fd))

        # Write from a background thread *after* read_key_unix() has had a chance
        # to enter and run its own tty.setraw() — that call uses TCSAFLUSH, which
        # discards any input already queued at the moment it runs, so writing
        # before the function starts would flush our bytes instead of exercising
        # the drain path.
        def _write_delayed_cpr_reply() -> None:
            import time as _time

            _time.sleep(0.01)
            os.write(master_fd, b"\x1b")
            _time.sleep(0.03)
            os.write(master_fd, b"[6;1R")

        writer = threading.Thread(target=_write_delayed_cpr_reply)
        writer.start()
        try:
            result = key_reader.read_key_unix()
        finally:
            writer.join()

        assert result == "cancel"
        assert not select.select([slave_fd], [], [], 0.05)[0], "CPR reply leaked past read_key_unix"
    finally:
        os.close(master_fd)
        os.close(slave_fd)
