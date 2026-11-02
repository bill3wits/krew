"""kanban_liveness — every open card has one reader and a clock (Phase 2 of
docs/KANBAN-SELF-HEALING-20260918.md; the law both plans derived on 2026-09-18).

A reader is a function of state + kind + marks, never a gate. For every open card this module
answers: who moves it next, by when, and — for a park or a transient block — what wakes it.
A card with reader `none`, or past its deadline, is the defect ("stuck between mechanisms").

Pure: `compute(conn, ctx, now)` reads the board and a context dict the caller measured
(caps, running count, engine/gateway health, off-hours, boot time, salad). It never writes.
The board gate writes the result to `~/.hermes/kanban-liveness.json` every tick, logs
`liveness=<overdue>/<none>/<due>`, and (only with KREW_LIVENESS_ACT=1) lands the wakes.

Wake grammar (one token in a block/schedule reason, a loop `hold:` comment, or a ruler's newest
comment): `later YYYY-MM-DD` · `window:render|serving-stop|off-hours` · `until:t_xxxxxxxx` ·
`engine-ok` · `engine-reset` · `gateway-up` · `drop`. A bare park gets a default wake:
`later <park + DEFAULT_WAKE_DAYS>`, and on that date the gate asks Crissa, it never respawns.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any

DEFAULT_WAKE_DAYS = 7
OPEN = ("triage", "todo", "ready", "running", "review", "blocked", "scheduled")
RULERS = tuple(os.environ.get("KREW_RULERS", "crissa,user,human,dashboard").split(","))
LOOP, GATE = "kanban-loop", "kanban-board-gate"

# clocks (seconds)
DISPATCH_TICK = 120
READY_MAX = 30 * 60
TRIAGE_MAX = 25 * 60
HEARTBEAT_MAX = 3600
REVIEW_MAX = 2 * 3600
CRISSA_MAX = 2 * 3600
JUDGE_MAX = 30 * 60
USER_MAX = 72 * 3600
OWNERLESS_MIN = 60 * 60
TRANSIENT_DEFAULT = 30 * 60

WAKE_RE = re.compile(
    r"(?P<later>\blater\s+(?P<date>\d{4}-\d{2}-\d{2})\b)"
    r"|(?P<window>\bwindow:(?P<wname>render|serving-stop|off-hours)\b)"
    r"|(?P<until>\buntil:(?P<uid>t_[0-9a-f]{8})\b)"
    r"|(?P<engine_ok>\bengine-ok\b)|(?P<engine_reset>\bengine-reset\b)|(?P<gateway_up>\bgateway-up\b)|(?P<drop>\bdrop\b)",
    re.I,
)


def parse_wake(text: str) -> dict[str, Any] | None:
    """The FIRST wake token in `text`, as {"kind": …, …}; None when there is none."""
    m = WAKE_RE.search(text or "")
    if not m:
        return None
    g = m.groupdict()
    if g.get("later"):
        try:
            return {"kind": "later", "date": date.fromisoformat(g["date"]).isoformat()}
        except ValueError:
            return None
    if g.get("window"):
        return {"kind": "window", "name": g["wname"].lower()}
    if g.get("until"):
        return {"kind": "until", "card": g["uid"]}
    for k in ("engine_ok", "engine_reset", "gateway_up", "drop"):
        if g.get(k):
            return {"kind": k.replace("_", "-")}
    return None


def wake_due(wake: dict[str, Any] | None, ctx: dict[str, Any], conn: sqlite3.Connection | None,
             now: float, since: float = 0) -> bool | None:
    """True = the wake condition holds now; False = not yet; None = unknown (no wake)."""
    if not wake:
        return None
    k = wake["kind"]
    if k == "later":
        return date.fromtimestamp(now) >= date.fromisoformat(wake["date"])
    if k == "window":
        name = wake["name"]
        if name == "render":
            return bool(ctx.get("render_window_open"))
        if name == "serving-stop":
            return bool(ctx.get("serving_stop_open"))
        return bool(ctx.get("off_hours"))
    if k == "until":
        if conn is None:
            return False
        try:
            r = conn.execute("SELECT status FROM tasks WHERE id = ?", (wake["card"],)).fetchone()
        except sqlite3.Error:
            return False
        return bool(r) and str(r[0]) in ("done", "archived")
    if k == "engine-ok":
        return bool(ctx.get("engine_up")) and not ctx.get("salad_recent") and bool(ctx.get("ada_up", True))
    if k == "engine-reset":
        return float(ctx.get("engine_started") or 0) > since
    if k == "gateway-up":
        return bool(ctx.get("crissa_up"))
    if k == "drop":
        return True
    return None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _last_event(conn: sqlite3.Connection, tid: str, kinds: tuple[str, ...]) -> tuple[str, str, float]:
    """(kind, payload_text, created_at) of the newest event of those kinds, else ('', '', 0)."""
    try:
        r = conn.execute(
            "SELECT kind, payload, created_at FROM task_events WHERE task_id = ? AND kind IN (%s) ORDER BY id DESC LIMIT 1"
            % ",".join("?" * len(kinds)), (tid, *kinds)).fetchone()
    except sqlite3.Error:
        return "", "", 0.0
    if not r:
        return "", "", 0.0
    payload = r[1] or ""
    try:
        d = json.loads(payload) if isinstance(payload, str) and payload.startswith("{") else {}
        text = str(d.get("reason") or d.get("summary") or payload) if isinstance(d, dict) else str(payload)
    except (TypeError, ValueError):
        text = str(payload)
    return str(r[0]), text, float(r[2] or 0)


def _comments(conn: sqlite3.Connection, tid: str) -> list[tuple[str, str, float]]:
    try:
        return [(str(a or "").lower(), str(b or ""), float(t or 0)) for a, b, t in conn.execute(
            "SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id", (tid,))]
    except sqlite3.Error:
        return []


def _open_parents(conn: sqlite3.Connection, tid: str) -> list[str]:
    try:
        return [str(r[0]) for r in conn.execute(
            "SELECT p.id FROM task_links l JOIN tasks p ON p.id = l.parent_id "
            "WHERE l.child_id = ? AND p.status NOT IN ('done', 'archived')", (tid,))]
    except sqlite3.Error:
        return []


def _wake_text(conn: sqlite3.Connection, tid: str, status: str) -> tuple[str, float]:
    """The text a wake token may sit in, and when the card was parked/blocked."""
    kind, text, at = _last_event(conn, tid, ("scheduled",) if status == "scheduled" else ("blocked", "gave_up"))
    parts = [text]
    for a, b, t in reversed(_comments(conn, tid)):
        if a in RULERS or (a == LOOP and b.lower().startswith("hold:")) or (a == GATE and "wake" in b.lower()):
            parts.append(b)
            if a in RULERS:
                break
    return " \n".join(parts), at


def _ts(value: Any) -> float:
    """A board timestamp as a float, 0.0 for anything that is not a number (None, "", a
    fingerprint). A column that changes its meaning must never stop the patrol."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def reader_of(conn: sqlite3.Connection, row: dict[str, Any], ctx: dict[str, Any], now: float,
              _depth: int = 0) -> dict[str, Any]:
    """{"id","status","kind","reader","deadline","wake","why"} for one open card."""
    tid, status = str(row["id"]), str(row.get("status") or "")
    kind = str(row.get("block_kind") or "") or None
    out: dict[str, Any] = {"id": tid, "status": status, "kind": kind, "reader": "none", "deadline": None,
                           "wake": None, "why": ""}
    created = float(row.get("created_at") or 0)

    if status == "running":
        # worker_started_at is NOT a time any more: since the 2026-09-26 hermes it is a process
        # fingerprint ("<boot id>:<n>|<start ticks>"), and float() of it raised on every tick from
        # the first worker of the new hermes on (04:35 09-27) — liveness, the overdue strikes, the
        # orphan reaper and the tick line all stopped, with the unit reporting success.
        hb = max(_ts(row.get("last_heartbeat_at")), _ts(row.get("worker_started_at")),
                 _ts(row.get("started_at")), created)
        out.update(reader="heartbeat", deadline=hb + HEARTBEAT_MAX, why="the worker; hermes reclaims on a dead heartbeat")
        return out

    if status == "ready":
        _k, _t, at = _last_event(conn, tid, ("promoted", "unblocked", "created"))
        at = at or created
        if int(ctx.get("running") or 0) < int(ctx.get("max_in_progress") or 0):
            out.update(reader="dispatcher", deadline=at + READY_MAX, why="a slot is free")
        else:
            out.update(reader="dispatcher", deadline=None, why="every slot is busy (the governor's number)")
        return out

    if status == "triage":
        k, _t, at = _last_event(conn, tid, ("block_loop_detected", "created"))
        if k == "block_loop_detected":
            out.update(reader="crissa", deadline=at + CRISSA_MAX, why="hermes routed a repeat block to triage; a ruling, not a decomposition")
        else:
            out.update(reader="decomposer", deadline=(at or created) + TRIAGE_MAX, why="auto-decompose (120 s) or the loop (20 min)")
        return out

    if status == "review":
        out.update(reader=f"reviewer:{row.get('assignee') or '?'}", deadline=created + REVIEW_MAX, why="native review lane")
        return out

    if status == "blocked" and kind in (None, "dependency"):
        # a ruler's `later <date>` or a gate's `window:` hold on an untyped / dependency block is a
        # wake, not an ownerless or a parents' clock (t_c28f88b5 "later 2026-09-19", t_bb49ea58 render window)
        text, at = _wake_text(conn, tid, status)
        wake = parse_wake(text) or (parse_wake("window:render") if "render window" in text.lower() else None)
        if wake:
            out.update(reader=f"wake:{wake['kind']}", wake=wake, why="a block with a wake token")
            out["deadline"] = datetime.fromisoformat(wake["date"]).timestamp() if wake["kind"] == "later" else None
            return out

    if status == "todo" or (status == "blocked" and kind == "dependency"):
        parents = _open_parents(conn, tid)
        if parents:
            deadlines = []
            if _depth < 8:
                for pid in parents[:8]:
                    try:
                        cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (pid,))
                        prow = cur.fetchone()
                        names = [d[0] for d in cur.description]
                    except sqlite3.Error:
                        prow = None
                    if prow is not None:
                        pr = reader_of(conn, dict(zip(names, prow)), ctx, now, _depth + 1)
                        if pr.get("deadline"):
                            deadlines.append(pr["deadline"])
            out.update(reader=f"parents:{' '.join(parents[:4])}", deadline=min(deadlines) if deadlines else None,
                       why="waits on its children/prerequisites; inherits their clock")
            return out
        _k, _t, at = _last_event(conn, tid, ("unblocked", "created"))
        out.update(reader="dispatcher", deadline=(at or created) + 2 * DISPATCH_TICK,
                   why="no open parent: recompute_ready promotes it next tick")
        if out["deadline"] and out["deadline"] < now:
            out["why"] = "dispatcher stalled: todo with no open parent for more than two ticks"
        return out

    if status == "blocked":
        _k, reason, at = _last_event(conn, tid, ("blocked", "gave_up"))
        at = at or created
        comments = _comments(conn, tid)
        if kind == "needs_input":
            out.update(reader="crissa", deadline=at + CRISSA_MAX, why="on the Decide queue")
            if not ctx.get("crissa_up", True):
                out["why"] = "on the Decide queue and Crissa's gateway is down"
            return out
        if kind == "capability":
            out.update(reader="user", deadline=at + USER_MAX, why="the user's hands (Daily: Needs you)")
            return out
        if kind == "transient":
            text, _ = _wake_text(conn, tid, status)
            wake = parse_wake(text) or {"kind": "engine-ok"}
            out.update(reader=f"wake:{wake['kind']}", wake=wake, deadline=at + TRANSIENT_DEFAULT,
                       why="a harness fault waiting for the engine")
            return out
        if any(a == LOOP and b.lower().startswith("cause: verb") for a, b, _ in comments):
            out.update(reader="judge", deadline=at + JUDGE_MAX, why="did the work, forgot the verb: one Judge call")
            return out
        for a, b, _t in reversed(comments):
            m = re.match(r"^\s*reframe-cut:\s*(t_[0-9a-f]{8})", b, re.I)
            if a == LOOP and m:
                try:
                    r = conn.execute("SELECT status FROM tasks WHERE id = ?", (m.group(1),)).fetchone()
                except sqlite3.Error:
                    r = None
                if r and str(r[0]) not in ("done", "archived"):
                    out.update(reader=f"reframe:{m.group(1)}", wake={"kind": "until", "card": m.group(1)}, deadline=None,
                               why="a live reframe owns its source; the loop closes it when the cut is done")
                    return out
                break
        out.update(reader="ownerless", deadline=at + OWNERLESS_MIN, why="untyped block, nobody's after an hour")
        return out

    if status == "scheduled":
        text, at = _wake_text(conn, tid, status)
        at = at or created
        wake = parse_wake(text)
        if wake:
            out.update(reader=f"wake:{wake['kind']}", wake=wake, why="a park with a wake")
            if wake["kind"] == "later":
                out["deadline"] = datetime.fromisoformat(wake["date"]).timestamp()
            elif wake["kind"] == "until":
                out["deadline"] = None
            else:
                out["deadline"] = None
        else:
            default = (date.fromtimestamp(at) + timedelta(days=int(ctx.get("default_wake_days") or DEFAULT_WAKE_DAYS))).isoformat()
            out.update(reader="wake:default", wake={"kind": "later", "date": default, "default": True},
                       deadline=datetime.fromisoformat(default).timestamp(),
                       why=f"a bare park: the gate asks Crissa on {default}")
        return out

    out.update(reader="none", why=f"status {status!r} has no reader")
    return out


def compute(conn: sqlite3.Connection, ctx: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """The registry for every open card, plus the lists the gate and the Daily act on."""
    now = time.time() if now is None else now
    cols = _cols(conn, "tasks")
    try:
        # every card that is not closed — an unknown status must show up as reader `none`, not vanish
        cur = conn.execute("SELECT * FROM tasks WHERE status NOT IN ('done', 'archived') ORDER BY created_at, id")
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
    except sqlite3.Error as e:
        return {"at": now, "error": str(e), "rows": [], "counts": {}, "none": [], "overdue": [], "due": [], "needs_user": []}
    out_rows, counts = [], {}
    none, overdue, due, needs_user = [], [], [], []
    for row in rows:
        r = reader_of(conn, row, ctx, now)
        r["title"] = str(row.get("title") or "")[:80]
        r["age_h"] = round((now - float(row.get("created_at") or now)) / 3600, 1)
        if r["deadline"]:
            r["overdue_min"] = int(max(0.0, now - r["deadline"]) / 60)
        wake = r.get("wake")
        if wake:
            since = float(_last_event(conn, r["id"], ("scheduled", "blocked"))[2] or 0)
            r["wake_due"] = wake_due(wake, ctx, conn, now, since)
            if r["wake_due"]:
                due.append(r["id"])
        base = r["reader"].split(":", 1)[0]
        counts[base] = counts.get(base, 0) + 1
        if r["reader"] == "none":
            none.append(r["id"])
        elif r["deadline"] and r["deadline"] < now and not (wake and wake.get("kind") == "later"):
            overdue.append(r["id"])
        if r["reader"] == "crissa" and not ctx.get("crissa_up", True):
            needs_user.append(r["id"])
        out_rows.append(r)
    return {"at": now, "board": ctx.get("board", ""), "act": bool(ctx.get("act")), "rows": out_rows, "counts": counts,
            "none": none, "overdue": overdue, "due": due, "needs_user": needs_user,
            "has_cols": sorted(c for c in ("last_heartbeat_at", "block_kind", "skills") if c in cols)}
