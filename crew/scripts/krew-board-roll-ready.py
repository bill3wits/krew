#!/usr/bin/env python3
"""krew-board-roll-ready — may the day board roll? Only when yesterday is finished.

the User 2026-09-17: "The rollover should wait until kanban issues are completed from
the day before." Exit 0 = roll, 1 = wait (systemd ExecCondition: a skip, not a failure).

Why a gate and not a better carry: krew-board-carry re-mints every open card (new id,
no parent links, no comments, no block kind, zero failures). Measured on the 09-15 → 09-16
roll: 30 of 33 carried cards were claimed and spawned at once — 7 of 7 parked
(`scheduled`), 12 of 13 blocked, every todo without its parents. A drained board gives
the carry nothing to break, so EVERY open status counts, parks included: a parked card
that is carried is an un-parked card.

Kill: KREW_BOARD_ROLL_GATE=0 (always roll — the old behaviour).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

KANBAN = Path(os.environ.get("KREW_KANBAN_HOME", str(KH.kanban_dir())))
# krew-board-carry's OPEN_STATUSES: what it would copy is what must be gone.
OPEN = ("todo", "ready", "blocked", "running", "scheduled", "triage", "in_progress", "review")


def open_counts(db: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        marks = ",".join("?" for _ in OPEN)
        return dict(con.execute(
            f"SELECT status, COUNT(*) FROM tasks WHERE status IN ({marks}) GROUP BY status", OPEN).fetchall())
    finally:
        con.close()


def verdict(kanban: Path = KANBAN) -> tuple[bool, str]:
    if os.environ.get("KREW_BOARD_ROLL_GATE", "1").strip().lower() in ("0", "false", "no", "off"):
        return True, "gate off (KREW_BOARD_ROLL_GATE=0)"
    cur = kanban / "current"
    slug = cur.read_text(encoding="utf-8").strip() if cur.is_file() else ""
    db = kanban / "boards" / slug / "kanban.db"
    if not slug or not db.is_file():
        return True, f"no current board db ({slug or 'unset'}) — nothing to strand"
    try:
        counts = open_counts(db)
    except sqlite3.Error as e:
        return False, f"{slug}: unreadable ({e}) — an unmeasured board does not roll"
    n = sum(counts.values())
    if n == 0:
        return True, f"{slug}: drained"
    detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    return False, f"{slug}: {n} open ({detail}) — the roll waits"


def main() -> int:
    ok, why = verdict()
    print(f"krew-board-roll-ready: {'roll' if ok else 'wait'} — {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
