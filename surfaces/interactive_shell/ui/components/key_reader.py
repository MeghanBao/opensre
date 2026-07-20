"""Low-level terminal key reader for TTY-first interactive menus.

Shared between :mod:`choice_menu` (REPL inline picker) and
:mod:`feedback` (post-investigation rating prompt) so the raw-mode
terminal I/O lives in one place.

Return values from :func:`read_key_unix` / :func:`read_key_windows`:
  ``"up"``, ``"down"``, ``"enter"``, ``"cancel"``, ``"tab"``,
  ``"right"``, ``"left"``, ``"eof"``, ``"ignore"``.
"""

from __future__ import annotations

import contextlib
import os
import sys


def flush_stdin_unix() -> None:
    """Discard pending stdin bytes before raw-mode reading."""
    with contextlib.suppress(Exception):
        import termios

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)  # type: ignore[attr-defined]


def restore_stdin_terminal() -> None:
    """Return stdin to canonical echo mode after Live/raw investigation UI.

    Investigation progress uses a background Ctrl+O watcher that puts stdin in
    non-canonical mode without echo. If nested watchers restore the wrong
    snapshot, the shell prompt appears to accept input but characters are not
    echoed. Call this after investigation UI teardown and before line prompts.
    """
    if os.name == "nt" or not sys.stdin.isatty():
        return
    import termios

    with contextlib.suppress(Exception):
        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)  # type: ignore[attr-defined]
        # Restore cooked-mode flags a raw menu clears: ICRNL so Enter (CR) submits,
        # OPOST for output newlines, ICANON/ECHO/ISIG for line editing and signals.
        attrs[0] |= termios.BRKINT | termios.ICRNL | termios.IXON  # type: ignore[attr-defined]
        attrs[1] |= termios.OPOST  # type: ignore[attr-defined]
        attrs[3] |= termios.ICANON | termios.ECHO | termios.ISIG  # type: ignore[attr-defined]
        if hasattr(termios, "IEXTEN"):
            attrs[3] |= termios.IEXTEN  # type: ignore[attr-defined]
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)  # type: ignore[attr-defined]
        termios.tcflush(fd, termios.TCIFLUSH)  # type: ignore[attr-defined]


# How long to wait for a byte that should immediately follow ESC (an arrow-key
# prefix, or a CPR/DSR reply from prompt-toolkit's bottom-toolbar redraw) before
# treating ESC as a standalone keypress. Generous enough for a laggy pty/terminal
# multiplexer (tmux, an IDE's integrated terminal) without making a real Escape
# press feel sluggish.
_ESCAPE_FOLLOWUP_TIMEOUT_SECONDS = 0.15


def _read_pending_byte_unix(fd: int, *, timeout: float = _ESCAPE_FOLLOWUP_TIMEOUT_SECONDS) -> bytes:
    """Read one byte from ``fd`` if it arrives within ``timeout``, else ``b""``."""
    import select as _sel

    if not _sel.select([fd], [], [], timeout)[0]:
        return b""
    return os.read(fd, 1)


def _drain_trailing_csi_bytes(fd: int, first: bytes = b"") -> None:
    """Drain the remainder of a CSI-looking escape sequence.

    Terminal replies we don't otherwise recognize — most commonly a delayed CPR
    (cursor-position) reply, ``ESC[row;colR``, from prompt-toolkit's bottom-toolbar
    redraw — must not leak into the next read (a picker keypress, or the next REPL
    prompt) as literal characters. The VT/xterm spec defines 0x40-0x7E as valid CSI
    final bytes, so keep reading until one shows up or nothing more arrives.
    """
    b = first
    while True:
        if not b:
            b = _read_pending_byte_unix(fd)
        if not b or (0x40 <= b[0] <= 0x7E):
            return
        b = b""


def read_key_unix(*, also_cancel: tuple[bytes, ...] = ()) -> str:
    """Read one logical keypress in raw mode; return a normalised key name.

    Possible return values: ``"up"``, ``"down"``, ``"enter"``,
    ``"cancel"``, ``"tab"``, ``"right"``, ``"left"``, ``"eof"``,
    ``"ignore"``.

    ``also_cancel`` treats additional single-byte keys as ``"cancel"`` (e.g.
    ``(b"s", b"S")`` for an explicit skip shortcut).
    """
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)  # type: ignore[attr-defined]
    try:
        tty.setraw(fd)  # type: ignore[attr-defined]
        ch = os.read(fd, 1)
        if not ch:
            return "eof"
        b = ch[0]
        if b in (3, 4) or ch in also_cancel:  # Ctrl-C / Ctrl-D / caller shortcuts
            return "cancel"
        if b in (10, 13, 32):  # LF / CR / Space
            return "enter"
        if b == 9:  # Tab
            return "tab"
        if ch in (b"j", b"J"):
            return "down"
        if ch in (b"k", b"K"):
            return "up"
        if ch in (b"q", b"Q"):
            return "cancel"
        if b == 27:  # ESC or arrow-key prefix (or a leaked CPR/DSR reply)
            nxt = _read_pending_byte_unix(fd)
            if nxt == b"[":
                arr = _read_pending_byte_unix(fd)
                if arr == b"A":
                    return "up"
                if arr == b"B":
                    return "down"
                if arr == b"C":
                    return "right"
                if arr == b"D":
                    return "left"
                # Not an arrow key — most likely a CPR/DSR reply; drain its tail.
                _drain_trailing_csi_bytes(fd, arr)
            elif nxt:
                # An escape sequence we don't recognize — drain whatever follows
                # rather than let it leak into the next read as literal text.
                _drain_trailing_csi_bytes(fd, nxt)
            # Otherwise nothing followed within the timeout: a genuine standalone
            # Escape keypress. (A reply arriving later still is mopped up by the
            # settle-then-drain done before the next prompt/picker read.)
            return "cancel"
        return "ignore"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)  # type: ignore[attr-defined]


def read_key_windows(*, also_cancel: tuple[bytes, ...] = ()) -> str:
    """Read one logical keypress on Windows; return a normalised key name.

    Possible return values: ``"up"``, ``"down"``, ``"enter"``,
    ``"cancel"``, ``"tab"``, ``"right"``, ``"left"``, ``"eof"``,
    ``"ignore"``.

    ``also_cancel`` treats additional single-byte keys as ``"cancel"``.
    """
    import msvcrt  # type: ignore[import,attr-defined]

    ch = msvcrt.getch()  # type: ignore[attr-defined]
    if ch in (b"\x03", b"\x1b") or ch in also_cancel:
        return "cancel"
    if ch in (b"\r", b"\n", b" "):
        return "enter"
    if ch == b"\t":
        return "tab"
    if ch in (b"j", b"J"):
        return "down"
    if ch in (b"k", b"K"):
        return "up"
    if ch in (b"q", b"Q"):
        return "cancel"
    if ch in (b"\xe0", b"\x00"):
        ch2 = msvcrt.getch()  # type: ignore[attr-defined]
        if ch2 == b"H":
            return "up"
        if ch2 == b"P":
            return "down"
        if ch2 == b"M":
            return "right"
        if ch2 == b"K":
            return "left"
        return "ignore"
    return "ignore"


__all__ = ["flush_stdin_unix", "read_key_unix", "read_key_windows", "restore_stdin_terminal"]
