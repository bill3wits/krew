#!/usr/bin/env python3
# krew: hermes-python
"""kanban-crashed-exit — move ONE card out of `crashed`, the status no hermes verb leaves.

`crashed` is not a stock hermes status (VALID_STATUSES has no such word): unlanded worktree code

cards). `block` takes running/ready, `unblock` blocked/scheduled, `schedule` todo/ready/running/


kanban-skills-add: through kanban_db.connect + write_txn with one event, never a raw write.

  scripts/kanban-crashed-exit.py <task-id> --to scheduled|todo|blocked --why <text>
                                 [--board <slug>] [--dry-run] [--json]
  scripts/kanban-crashed-exit.py <task-id> --max-retries N --why <text>   (any open card)

  scheduled  a ruler's park / later: status scheduled, a `scheduled` event whose reason is the
             ruling (kanban-loop run_later and ms-trail read the date from it)
  todo       a harness crash, retried ONCE: consecutive_failures 0 and max_retries 1, so a second
             failure trips hermes's breaker (blocked) and kanban-loop hands it to Crissa
  blocked    everything else: blocked needs_input — Crissa's Decide queue

Only a card still in `crashed` is moved (the UPDATE is guarded on it). The decision is
kanban-loop's (`run_crashed_exit`). Exit 0 = moved or dry; 2 = unknown card; 3 = not crashed;
4 = no board. Kill: KREW_CRASHED_EXIT=0 (exit 0, writes nothing).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

sys.path.insert(0, str(KH.hermes_tree()))
import logging  # noqa: E402

logging.disable(logging.CRITICAL)

_REAL_HOME = Path(os.environ.get("HERMES_REAL_HOME", str(Path.home())))
KANBAN = KH.kanban_dir()
TARGETS = ("scheduled", "todo", "blocked")
_CLEAR = ("claim_lock = NULL, claim_expires = NULL, worker_pid = NULL, current_run_id = NULL")


def _board_db(slug: str | None) -> Path | None:
    pinned = os.environ.get("KREW_CRASHED_EXIT_DB", "").strip()      # tests
    if pinned:
        return Path(pinned)
    if not slug:
        try:
            slug = (KANBAN / "current").read_text().strip()
        except OSError:
            return None
    p = KANBAN / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def move(task_id: str, to: str, why: str, *, board: str | None = None, dry_run: bool = False) -> dict:
    if to not in TARGETS:
        return {"ok": False, "rc": 1, "error": f"--to must be one of {TARGETS}"}
    db = _board_db(board)
    if db is None:
        return {"ok": False, "rc": 4, "error": "no board"}
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    conn = kbc.connect(db_path=db)
    try:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return {"ok": False, "rc": 2, "error": "unknown card"}
        if row["status"] != "crashed":
            return {"ok": False, "rc": 3, "error": f"not crashed ({row['status']})"}
        if dry_run:
            return {"ok": True, "dry_run": True, "to": to}
        why = why or ""
        with kb.write_txn(conn):
            if to == "scheduled":
                sql = f"UPDATE tasks SET status = 'scheduled', block_kind = NULL, {_CLEAR} "
                event, payload = "scheduled", {"reason": why, "from": "crashed", "by": "kanban-crashed-exit"}
            elif to == "todo":
                # max_retries 1: the next failure trips hermes's own breaker (blocked, and
                # recompute_ready never lifts a card at its limit); kanban-loop hands it to Crissa.
                sql = (f"UPDATE tasks SET status = 'todo', block_kind = NULL, consecutive_failures = 0, "
                       f"max_retries = 1, last_failure_error = NULL, {_CLEAR} ")
                event, payload = "crashed_exit", {"to": "todo", "why": why, "by": "kanban-crashed-exit"}
            else:
                sql = f"UPDATE tasks SET status = 'blocked', block_kind = 'needs_input', {_CLEAR} "
                event, payload = "blocked", {"kind": "needs_input", "reason": why, "from": "crashed",
                                             "by": "kanban-crashed-exit"}
            n = conn.execute(sql + "WHERE id = ? AND status = 'crashed'", (task_id,)).rowcount
            if n != 1:
                return {"ok": False, "rc": 3, "error": "left crashed under us"}
            kb._append_event(conn, task_id, event, payload)
        return {"ok": True, "to": to}
    finally:
        conn.close()


def cap_retries(task_id: str, n: int, why: str, *, board: str | None = None, dry_run: bool = False) -> dict:
    """Set a card's own breaker (`max_retries`) on an open card: its n-th failure blocks it."""
    db = _board_db(board)
    if db is None:
        return {"ok": False, "rc": 4, "error": "no board"}
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    conn = kbc.connect(db_path=db)
    try:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return {"ok": False, "rc": 2, "error": "unknown card"}
        if row["status"] in ("done", "archived"):
            return {"ok": False, "rc": 3, "error": f"closed ({row['status']})"}
        if dry_run:
            return {"ok": True, "dry_run": True, "max_retries": n}
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET max_retries = ? WHERE id = ?", (int(n), task_id))
            kb._append_event(conn, task_id, "max_retries_set",
                             {"max_retries": int(n), "why": why or "", "by": "kanban-crashed-exit"})
        return {"ok": True, "max_retries": n}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("task_id")
    ap.add_argument("--to", choices=TARGETS)
    ap.add_argument("--max-retries", type=int, help="set the card's own breaker instead of moving it")
    ap.add_argument("--why", default="")
    ap.add_argument("--board")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if os.environ.get("KREW_CRASHED_EXIT", "1").strip().lower() in ("0", "off", "false", "no"):
        print("KREW_CRASHED_EXIT=0: nothing moved")
        return 0
    if a.max_retries is not None:
        r = cap_retries(a.task_id, a.max_retries, a.why, board=a.board, dry_run=a.dry_run)
    elif a.to:
        r = move(a.task_id, a.to, a.why, board=a.board, dry_run=a.dry_run)
    else:
        ap.error("one of --to / --max-retries")
    print(json.dumps(r) if a.json else (f"{a.task_id} -> {a.to}" if r.get("ok") else f"{a.task_id}: {r.get('error')}"))
    return 0 if r.get("ok") else int(r.get("rc") or 1)


if __name__ == "__main__":
    raise SystemExit(main())
