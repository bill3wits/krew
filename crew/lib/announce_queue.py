"""PA dump-queue. Depth 20 newest; dump the rest. Quiet hours / mute / a hold dump all.

Depth was 3 (2026-08-25: ~32 s a line, ~96 s of backlog). The User 2026-09-30 wants to hear more of what kanban
does: 20 deep, and when more than 3 wait the drain says ONE roundup line for all of them (krew-announce-drain,
summary_line). Every dropped line is logged (lib/announce_log.py).
Enqueue/pop rewrite the jsonl under KREW_ANNOUNCE_LOCK.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

QUEUE = Path(
    os.environ.get(
        "KREW_ANNOUNCE_QUEUE",
        str(Path.home() / ".hermes" / "krew-announce-queue.jsonl"),
    )
).expanduser()
DUMP_LOG = Path(
    os.environ.get(
        "KREW_ANNOUNCE_DUMP_LOG",
        str(Path.home() / ".hermes" / "krew-announce-dump.jsonl"),
    )
).expanduser()
LOCK = Path(
    os.environ.get(
        "KREW_ANNOUNCE_LOCK",
        str(Path.home() / ".hermes" / "krew-announce.lock"),
    )
).expanduser()
MAX_QUEUE = int(os.environ.get("KREW_ANNOUNCE_QUEUE_MAX", "20"))
MAX_LINE = 4000


def _lock_fd():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _unlock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _dump(item: dict, reason: str) -> None:
    DUMP_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = dict(item)
    row["dump_ts"] = time.time()
    row["reason"] = reason
    line = (json.dumps(row, ensure_ascii=False) + "\n").encode()
    if len(line) > MAX_LINE:
        line = line[: MAX_LINE - 1] + b"\n"
    with DUMP_LOG.open("ab") as fh:
        fh.write(line)
    try:
        import announce_log
        announce_log.record("dropped", _text_of(item), category=str(item.get("category") or ""), reason=reason)
    except Exception:  # noqa: BLE001 - the dump file above is the record of last resort
        pass


def _text_of(item: dict) -> str:
    title, summary = str(item.get("title") or "").strip(), str(item.get("summary") or "").strip()
    return f"{title}. {summary}" if title and summary and summary.lower() not in title.lower() else (title or summary)


def depth() -> int:
    fd = _lock_fd()
    try:
        return len(_read_unlocked())
    finally:
        _unlock(fd)


def pop_all() -> list[dict[str, Any]]:
    """Everything waiting, oldest first, in one locked step (the drain's roundup)."""
    fd = _lock_fd()
    try:
        items = _read_unlocked()
        _write_unlocked([])
        return items
    finally:
        _unlock(fd)


def enqueue(
    *,
    category: str,
    title: str,
    summary: str = "",
    dedup_key: str = "",
) -> str:
    item = {
        "ts": time.time(),
        "category": category,
        "title": (title or "")[:200],
        "summary": (summary or "")[:240],
        "dedup_key": dedup_key or "",
    }
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    fd = _lock_fd()
    try:
        items = _read_unlocked()
        items.append(item)
        dumped = 0
        while len(items) > MAX_QUEUE:
            old = items.pop(0)
            _dump(old, "overflow")
            dumped += 1
        _write_unlocked(items)
        return f"queued ({len(items)} deep, dumped {dumped})"
    finally:
        _unlock(fd)


def enqueue_front(
    *,
    category: str,
    title: str,
    summary: str = "",
    dedup_key: str = "",
) -> str:
    """Cut-line requeue: the interrupted PA line plays next, not last."""
    item = {
        "ts": time.time(),
        "category": category,
        "title": (title or "")[:200],
        "summary": (summary or "")[:240],
        "dedup_key": dedup_key or "",
        "requeued": True,
    }
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    fd = _lock_fd()
    try:
        items = _read_unlocked()
        items.insert(0, item)
        dumped = 0
        while len(items) > MAX_QUEUE:
            old = items.pop()
            _dump(old, "overflow")
            dumped += 1
        _write_unlocked(items)
        return f"requeued ({len(items)} deep, dumped {dumped})"
    finally:
        _unlock(fd)


def _read_unlocked() -> list[dict]:
    if not QUEUE.is_file():
        return []
    out: list[dict] = []
    for line in QUEUE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _write_unlocked(items: list[dict]) -> None:
    tmp = QUEUE.with_suffix(".jsonl.tmp")
    body = "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items)
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(QUEUE)


def dump_all(reason: str) -> int:
    fd = _lock_fd()
    try:
        items = _read_unlocked()
        for it in items:
            _dump(it, reason)
        _write_unlocked([])
        return len(items)
    finally:
        _unlock(fd)


def pop_one() -> dict[str, Any] | None:
    fd = _lock_fd()
    try:
        items = _read_unlocked()
        if not items:
            return None
        item = items.pop(0)
        _write_unlocked(items)
        return item
    finally:
        _unlock(fd)
