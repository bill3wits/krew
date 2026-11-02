"""Coach, don't cap: a cut of text an agent relies on says it was cut and where the rest is.

A silent cut is a mine: the card, comment, ruling or note reads as whole when it is not. Every cut in the window
goes through clip(), which leaves text that fits alone and otherwise keeps the head (or, tail=True, the end) with

    [cut at N of M chars, full text: cat ~/.krew/logs/cut-text/<label>-<sha>.txt]

The whole text is saved to that file ($KREW_HOME/logs/cut-text, or $KREW_CUT_TEXT_DIR), named by its content, so
the same text cut twice is one file. Files older than KEEP_DAYS are dropped when a new one is written. `where=`
names another real place the rest already lives (a card comment, a log file) and saves nothing. If the file
cannot be written the marker says so: the cut is still visible, never silent.

hard=True (default): the result, marker included, fits in `limit` (for receivers with a hard cap: a kanban body,
a list title). hard=False keeps `limit` chars of text and adds the marker after them (short notes, where the
marker would eat most of the room).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

KEEP_DAYS = float(os.environ.get("KREW_CUT_TEXT_KEEP_DAYS", "30"))


def cut_dir() -> Path:
    d = os.environ.get("KREW_CUT_TEXT_DIR")
    if d:
        return Path(d).expanduser()
    return Path(os.environ.get("KREW_HOME") or Path.home() / ".krew").expanduser() / "logs" / "cut-text"


def show_path(p: Path) -> str:
    """The path as an agent would type it: ~ for the home directory."""
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if home and (s == home or s.startswith(home + os.sep)) else s


def _prune(d: Path, now: float) -> None:
    cutoff = now - KEEP_DAYS * 86400
    for f in d.glob("*.txt"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def save_full(text: str, label: str) -> Path:
    """Write the whole text; return its file. Raises OSError when it cannot."""
    d = cut_dir()
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", label or "text").strip("-") or "text"
    p = d / f"{safe}-{hashlib.sha1(text.encode('utf-8', 'replace')).hexdigest()[:12]}.txt"
    if p.is_file():
        os.utime(p)
    else:
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    _prune(d, time.time())
    return p


def marker(kept: int, total: int, where: str, *, tail: bool = False) -> str:
    head = f"last {kept} of {total} chars" if tail else f"cut at {kept} of {total} chars"
    return f"[{head}, full text: {where}]"


def clip(text, limit: int, *, label: str, where: str | None = None, tail: bool = False, hard: bool = True) -> str:
    text = "" if text is None else str(text)
    total = len(text)
    if total <= limit:
        return text
    if where is None:
        try:
            where = "cat " + show_path(save_full(text, label))
        except OSError as e:
            where = f"not saved ({type(e).__name__}: {e}); only this part exists"
    sep = "\n" if "\n" in text else " "
    if hard:
        room = limit - len(sep) - len(marker(limit, total, where, tail=tail))
        kept = max(0, room)
    else:
        kept = limit
    mk = marker(kept, total, where, tail=tail)
    if kept == 0:
        return mk
    if tail:
        return mk + sep + text[total - kept:]
    return text[:kept] + sep + mk


def flat(text) -> str:
    """One log line: whitespace collapsed, nothing dropped."""
    return " ".join(str(text or "").split())
