#!/usr/bin/env python3
"""Long-running PA drain. One line at a time.

Dump on quiet hours / tool-hold / mute. Active/LISTENING and mic-busy
HOLD the queue (cards run off when Paused). User 2026-08-25: queue limit 3.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB))

from announce_queue import depth, dump_all, pop_all, pop_one  # noqa: E402
from krew_announce import MUTE, announce, mic_busy, pa_hold  # noqa: E402
from krew_announce import ANNOUNCE_END, ANNOUNCE_START  # noqa: E402
from speakable import line_for_pa  # noqa: E402
import announce_log  # noqa: E402
import announce_rewrite  # noqa: E402


def _quiet() -> bool:
    from krew_announce import in_quiet_hours  # the User's down hours, one reader

    return in_quiet_hours()


def _hold() -> bool:
    try:
        from krew_announce import _tool_hold

        return bool(_tool_hold())
    except Exception:
        return False


# A ROUNDUP WHEN MORE THAN 3 WAIT (the User 2026-09-30, "I want to hear more of what kanban does, to learn from it
# ... When more than 3 lines wait, combine them into one summary line"). One line takes ~10 s to say; with the queue
# 20 deep a burst would otherwise be minutes of PA. Each folded line is logged `summarised` with the roundup it went
# into. Kill: KREW_ANNOUNCE_ROUNDUP=0 (one line at a time, as before).
ROUNDUP_OVER = int(os.environ.get("KREW_ANNOUNCE_ROUNDUP_OVER", "3"))
ROUNDUP_ON = os.environ.get("KREW_ANNOUNCE_ROUNDUP", "1").strip().lower() not in ("0", "false", "no", "off")
LABEL = {"completion": "Done", "stuck": "Stuck", "protocol": "Protocol", "stage": "Staged", "test": "Test"}


def _short(title: str, words: int = 6) -> str:
    w = " ".join(str(title or "").replace(",", " ").split()).split(" ")
    return " ".join(w[:words]) if w != [""] else ""


def summary_line(items: list[dict], limit: int = 160) -> str:
    """One spoken line for everything waiting: the count, then by kind, repeated titles counted once
    ("M S Hermes security, times 4"), as many as fit; the rest is "and N more"."""
    groups: dict[str, dict[str, int]] = {}
    for it in items:
        kind = LABEL.get(str(it.get("category") or ""), "Other")
        title = _short(it.get("title") or it.get("summary") or "") or "untitled"
        groups.setdefault(kind, {})
        groups[kind][title] = groups[kind].get(title, 0) + 1
    head = f"Kanban roundup, {len(items)} updates."
    out, told = head, 0
    for kind in ("Stuck", "Done", "Protocol", "Staged", "Other", "Test"):
        if kind not in groups:
            continue
        # the most repeated first: "times 4" says more about the hour than one card does
        ranked = sorted(groups[kind].items(), key=lambda tn: -tn[1])
        names = [f"{t}, times {n}" if n > 1 else t for t, n in ranked]
        part = f" {kind}: "
        for name in names:
            add = (part if part else "; ") + name
            if len(out) + len(add) + 1 > limit - 16:
                break
            out += add
            part = ""
            told += groups[kind][name.rsplit(", times ", 1)[0]]
        if part == "":
            out += "."
    rest = len(items) - told
    return out + (f" And {rest} more." if rest > 0 else "")


def _roundup() -> bool:
    """More than ROUNDUP_OVER waiting: take them all, say one line. True when it did."""
    if not ROUNDUP_ON or depth() <= ROUNDUP_OVER:
        return False
    items = pop_all()
    if len(items) <= ROUNDUP_OVER:          # the drain raced an enqueue: put nothing back, just say them
        for it in items:
            _say(it)
        return True
    line = summary_line(items)
    for it in items:
        announce_log.record("summarised", line_for_pa(it.get("title") or "", it.get("summary") or "") or
                            str(it.get("title") or ""), category=str(it.get("category") or ""),
                            reason=f"folded into a roundup of {len(items)}")
    r = announce(line, category="completion", dedup_key=f"roundup:{time.time():.0f}", wait_mic=False)
    print(f"announce_roundup n={len(items)} ok={r.get('ok')} skip={r.get('skipped')} "
          f"reason={r.get('reason') or r.get('error')}", flush=True)
    return True


def _say(item: dict) -> None:
    spoken = line_for_pa(item.get("title") or "", item.get("summary") or "")
    if not spoken:
        announce_log.record("skipped", str(item.get("title") or ""), category=str(item.get("category") or ""),
                            reason="empty_after_hygiene")
        print("announce_dump reason=empty_after_hygiene", flush=True)
        return
    prefix = {
        "completion": "Done. ",
        "stuck": "Stuck. ",
        "protocol": "Protocol. ",
        "stage": "Staged. ",
    }.get(str(item.get("category") or ""), "")
    # ONE PLAIN SENTENCE: what happened and who did it (the User 2026-09-30, "Announcer lines read like raw card
    # titles"). lib/announce_rewrite.py asks crew, reading the card read-only for its worker; anything it cannot
    # say cleanly - or a crew that is down - says the old line. Kill ANNOUNCE_PLAIN=0.
    said, how = announce_rewrite.plain_line(item, prefix + spoken)
    r = announce(
        said,
        category=str(item.get("category") or "completion"),
        dedup_key=item.get("dedup_key") or None,
        wait_mic=False,
        note=f"{how} · card: {str(item.get('title') or '')[:120]}",
    )
    print(
        f"announce_drain ok={r.get('ok')} skip={r.get('skipped')} "
        f"reason={r.get('reason') or r.get('error')}",
        flush=True,
    )


def _overflow_card(state: dict) -> None:
    """More than five lines dropped in an hour is a card (the User 2026-09-30; lib/announce_overflow.py).
    Checked once a minute, one card a day, never in the way of the queue."""
    now = time.time()
    if now - state.get("at", 0.0) < 60.0:
        return
    state["at"] = now
    try:
        import announce_overflow
        tid = announce_overflow.maybe_file_card(now)
        if tid:
            print(f"announce_overflow card={tid}", flush=True)
    except Exception as e:  # noqa: BLE001 - a report must never stop the announcer
        print(f"announce_overflow error={type(e).__name__}: {str(e)[:120]}", flush=True)


def loop() -> None:
    overflow = {"at": 0.0}
    while True:
        _overflow_card(overflow)
        if MUTE.is_file() or _quiet() or _hold():
            n = dump_all("quiet_or_hold")
            if n:
                print(f"announce_dump reason=quiet_or_hold n={n}", flush=True)
            time.sleep(1.0)
            continue
        hold = pa_hold()
        if hold:
            # Active/LISTENING: pause new PA, keep the queue, let in-flight
            # paplay run off. Do not dump — those cards play when Paused.
            time.sleep(0.4)
            continue
        why = mic_busy()
        if why:
            # Same hold: his voice at the mic is not a reason to throw cards.
            time.sleep(0.4)
            continue
        if _roundup():
            continue
        item = pop_one()
        if not item:
            time.sleep(0.4)
            continue
        _say(item)


if __name__ == "__main__":
    loop()
