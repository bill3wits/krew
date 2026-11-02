#!/usr/bin/env python3
"""Kanban chatter → the wall's log roll (the KANBAN chip).

the User 2026-09-11: "please turn on kanban chatter. I asked for that back the
other day but haven't seen it yet." The wall already had the chip
(log_kanban, on by default) and the roll (~/.hermes/crissa-tool-roll.jsonl,
kind "kanban"), but the only writer was Crissa's own tool callout, and Crissa
does not work the board — the crew does, through Ada's dispatcher. Nothing
ever wrote a kanban line. This tails the CURRENT daily board's task_events
and appends one roll line per board event (no heartbeats), so the wall
reads "ada filed 'M&S dep review…' → triage", "bash closed 'Gather raw
changelog'". Idempotent: a cursor file remembers the last event id per
board. Runs from a user timer every 20 s.

    python3 scripts/kanban-chatter.py            # one pass
    python3 scripts/kanban-chatter.py --dry-run  # print, write nothing
"""
from __future__ import annotations
import argparse, json, os, sqlite3, time
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HERMES = Path(os.environ.get("HERMES_HOME") or KH.hermes_home())
ROLL = Path(os.environ.get("CRISSA_TOOL_ROLL", HERMES / "crissa-tool-roll.jsonl"))
CURSOR = Path(os.environ.get("KANBAN_CHATTER_CURSOR", HERMES / "kanban-chatter.cursor.json"))
SKIP = {"heartbeat", "spawned", "attached", "specified"}
VERB = {
    "created": "filed", "claimed": "picked up", "completed": "closed", "blocked": "blocked",
    "unblocked": "unblocked", "commented": "commented on", "archived": "archived",
    "promoted": "promoted", "timed_out": "timed out on", "gave_up": "gave up on",
    "assigned": "assigned", "review_requested": "asked for review on",
    "protocol_violation": "broke protocol on", "suspected_hallucinated_references": "cited thin air on",
}


def board_db() -> Path | None:
    try:
        slug = (HERMES / "kanban" / "current").read_text().strip()
    except OSError:
        return None
    p = HERMES / "kanban" / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def _to_epoch(v) -> float:
    """Normalize a board created_at to an epoch float.

    The board stores created_at as ISO-8601 strings ('2026-09-13T05:11:13Z');
    some writers may still emit a numeric epoch. Return the numeric epoch in
    either case so the roll line can carry a real timestamp. Fall back to now
    on a blank/unparseable value rather than crashing the whole pass.
    """
    if v is None:
        return time.time()
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return time.time()
    try:
        # sqlite3 returns int/float; a string epoch is handled below.
        return float(s)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def lines_since(db_path: Path, after_id: int, limit: int = 40) -> tuple[list[dict], int]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    out = []
    last = after_id
    for ev in db.execute("select e.id, e.task_id, e.kind, e.payload, e.created_at, t.title, t.assignee, t.status "
                         "from task_events e left join tasks t on t.id = e.task_id "
                         "where e.id > ? order by e.id asc limit ?", (after_id, limit)):
        last = int(ev["id"])
        kind = str(ev["kind"] or "")
        if kind in SKIP:
            continue
        try:
            pay = json.loads(ev["payload"]) if ev["payload"] else {}
        except ValueError:
            pay = {}
        if not isinstance(pay, dict):
            pay = {}
        who = str(pay.get("author") or pay.get("assignee") or ev["assignee"] or "board").strip().lower()
        if kind == "claimed":
            who = str(pay.get("lock") or who).split(":")[0].replace("-main", "") or who
        title = (ev["title"] or ev["task_id"] or "a card").strip()
        if len(title) > 56:
            title = title[:53].rstrip() + "…"
        verb = VERB.get(kind, kind.replace("_", " "))
        tail = ""
        if kind == "created" and pay.get("status"):
            tail = " → " + str(pay["status"])
        elif kind == "blocked" and pay.get("reason"):
            tail = " — " + str(pay["reason"])[:60].rstrip()
        elif kind == "completed" and pay.get("summary"):
            tail = " — " + str(pay["summary"])[:60].rstrip()
        text = f"{who} {verb} “{title}”{tail}"
        out.append({"ts": _to_epoch(ev["created_at"]), "text": text, "tool": "kanban_" + kind,
                    "kind": "kanban", "who": who, "voice": None, "task": ev["task_id"]})
    return out, last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", type=int, default=0, help="on a fresh cursor, emit this many recent events (default 0 = start at now)")
    a = ap.parse_args()
    db = board_db()
    if db is None:
        print("no current board", flush=True)
        return 0
    try:
        cur = json.loads(CURSOR.read_text())
    except (OSError, ValueError):
        cur = {}
    key = str(db)
    after = cur.get(key)
    if after is None:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        top = conn.execute("select coalesce(max(id),0) from task_events").fetchone()[0]
        after = max(0, int(top) - int(a.backfill))
    rows, last = lines_since(db, int(after))
    for r in rows:
        print(time.strftime("%H:%M:%S", time.localtime(r["ts"])), r["text"], flush=True)
    if not a.dry_run:
        if rows:
            with ROLL.open("a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        cur[key] = last
        CURSOR.write_text(json.dumps(cur))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
