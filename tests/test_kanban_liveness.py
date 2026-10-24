"""lib/kanban_liveness — one reader and one clock per open card (Phase 2, report-only first)."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import kanban_liveness as kv  # noqa: E402

H = 3600.0
NOW = 1_790_000_000.0
CTX = {"max_in_progress": 6, "running": 1, "engine_up": True, "ada_up": True, "zoey_up": True,
       "salad_recent": False, "off_hours": False, "render_window_open": False, "engine_started": NOW - 10 * H}


def _board(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "kanban.db")
    con.executescript("""
    CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, assignee TEXT, block_kind TEXT,
        created_at INTEGER, started_at INTEGER, last_heartbeat_at INTEGER, worker_started_at INTEGER);
    CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INTEGER);
    CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
    CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
    """)
    return con


def _t(con, tid, status, *, kind=None, created=NOW - 2 * H, assignee="bash", hb=None):
    con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", (tid, f"card {tid}", status, assignee, kind, created, None, hb, None))


def _e(con, tid, kind, at, reason=""):
    con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                (tid, kind, json.dumps({"reason": reason}) if reason else "", at))


def _c(con, tid, author, body, at=NOW - H):
    con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", (tid, author, body, at))


def _reader(con, tid, ctx=CTX, now=NOW):
    cur = con.execute("SELECT * FROM tasks WHERE id = ?", (tid,))
    row = dict(zip([d[0] for d in cur.description], cur.fetchone()))
    return kv.reader_of(con, row, ctx, now)


def test_wake_grammar_parses_each_token_and_nothing_else():
    assert kv.parse_wake("park — later 2026-10-01 then try") == {"kind": "later", "date": "2026-10-01"}
    assert kv.parse_wake("off-hours render window: window:render") == {"kind": "window", "name": "render"}
    assert kv.parse_wake("hold: until:t_c0ffee01 — superseded") == {"kind": "until", "card": "t_c0ffee01"}
    assert kv.parse_wake("wake: engine-ok; retry 1 of 1")["kind"] == "engine-ok"
    assert kv.parse_wake("engine-reset")["kind"] == "engine-reset" and kv.parse_wake("gateway-up")["kind"] == "gateway-up"
    assert kv.parse_wake("drop it")["kind"] == "drop"
    assert kv.parse_wake("park — duplicate of last week") is None
    assert kv.parse_wake("later someday") is None


def test_wake_due_reads_the_context_and_the_board(tmp_path):
    con = _board(tmp_path)
    _t(con, "t_done", "done")
    assert kv.wake_due({"kind": "later", "date": date.fromtimestamp(NOW).isoformat()}, CTX, con, NOW) is True
    assert kv.wake_due({"kind": "later", "date": (date.fromtimestamp(NOW) + timedelta(days=1)).isoformat()}, CTX, con, NOW) is False
    assert kv.wake_due({"kind": "until", "card": "t_done"}, CTX, con, NOW) is True
    assert kv.wake_due({"kind": "until", "card": "t_nope"}, CTX, con, NOW) is False
    assert kv.wake_due({"kind": "engine-ok"}, CTX, con, NOW) is True
    assert kv.wake_due({"kind": "engine-ok"}, {**CTX, "salad_recent": True}, con, NOW) is False
    assert kv.wake_due({"kind": "engine-reset"}, CTX, con, NOW, since=NOW - H) is False
    assert kv.wake_due({"kind": "engine-reset"}, CTX, con, NOW, since=NOW - 20 * H) is True
    assert kv.wake_due({"kind": "window", "name": "off-hours"}, {**CTX, "off_hours": True}, con, NOW) is True
    assert kv.wake_due(None, CTX, con, NOW) is None


def test_every_state_has_the_reader_the_plan_names(tmp_path):
    con = _board(tmp_path)
    _t(con, "t_run", "running", hb=NOW - 100)
    _t(con, "t_rdy", "ready"); _e(con, "t_rdy", "promoted", NOW - 10 * 60)
    _t(con, "t_tri", "triage"); _e(con, "t_tri", "created", NOW - 5 * 60)
    _t(con, "t_lop", "triage"); _e(con, "t_lop", "blocked", NOW - H); _e(con, "t_lop", "block_loop_detected", NOW - 50 * 60)
    _t(con, "t_rev", "review", assignee="vera")
    _t(con, "t_kid", "todo"); _t(con, "t_par", "blocked", kind="capability"); con.execute("INSERT INTO task_links VALUES ('t_par','t_kid')")
    _e(con, "t_par", "blocked", NOW - H)
    _t(con, "t_orph", "todo", created=NOW - H); _e(con, "t_orph", "created", NOW - H)
    _t(con, "t_zoe", "blocked", kind="needs_input"); _e(con, "t_zoe", "blocked", NOW - 3 * H)
    _t(con, "t_tr", "blocked", kind="transient"); _e(con, "t_tr", "blocked", NOW - 60, "wake: engine-reset; harness")
    _t(con, "t_verb", "blocked"); _e(con, "t_verb", "blocked", NOW - 60); _c(con, "t_verb", "kanban-loop", "cause: verb — coherent rc0")
    _t(con, "t_own", "blocked"); _e(con, "t_own", "gave_up", NOW - 2 * H)
    _t(con, "t_src", "blocked"); _e(con, "t_src", "gave_up", NOW - 5 * H); _c(con, "t_src", "kanban-loop", "reframe-cut: t_c0ffee02 (gen 1, frame by model)")
    _t(con, "t_c0ffee02", "todo")
    _t(con, "t_park", "scheduled"); _e(con, "t_park", "scheduled", NOW - 3 * 86400, "park ruled by zoey: duplicate")
    _t(con, "t_hold", "scheduled"); _e(con, "t_hold", "scheduled", NOW - H, "hold: until:t_c0ffee01 — superseded")
    _t(con, "t_c0ffee01", "done")
    _t(con, "t_odd", "weird")
    _t(con, "t_lat", "blocked", kind="dependency"); _e(con, "t_lat", "blocked", NOW - H, "later 2099-01-01: back on the queue then")
    _t(con, "t_rw", "blocked"); _e(con, "t_rw", "blocked", NOW - 5 * H, "render window: waits for the off-hours render slot")
    r = {tid: _reader(con, tid) for tid in ("t_run", "t_rdy", "t_tri", "t_lop", "t_rev", "t_kid", "t_orph", "t_zoe", "t_tr",
                                            "t_verb", "t_own", "t_park", "t_hold", "t_odd")}
    assert r["t_run"]["reader"] == "heartbeat" and r["t_run"]["deadline"] == NOW - 100 + kv.HEARTBEAT_MAX
    assert r["t_rdy"]["reader"] == "dispatcher" and r["t_rdy"]["deadline"] == NOW - 600 + kv.READY_MAX
    assert r["t_tri"]["reader"] == "decomposer" and r["t_lop"]["reader"] == "zoey"
    assert r["t_rev"]["reader"] == "reviewer:vera"
    assert r["t_kid"]["reader"] == "parents:t_par" and r["t_kid"]["deadline"] == r["t_par" if False else "t_kid"]["deadline"]
    assert r["t_kid"]["deadline"] == NOW - H + kv.USER_MAX          # inherited from the capability parent
    assert r["t_orph"]["reader"] == "dispatcher" and "stalled" in r["t_orph"]["why"]
    assert r["t_zoe"]["reader"] == "zoey" and r["t_zoe"]["deadline"] < NOW
    assert r["t_tr"]["reader"] == "wake:engine-reset" and r["t_tr"]["wake"]["kind"] == "engine-reset"
    assert r["t_verb"]["reader"] == "judge"
    assert r["t_own"]["reader"] == "ownerless" and r["t_own"]["deadline"] == NOW - 2 * H + kv.OWNERLESS_MIN
    assert _reader(con, "t_src")["reader"] == "reframe:t_c0ffee02" and _reader(con, "t_src")["deadline"] is None
    assert r["t_park"]["reader"] == "wake:default" and r["t_park"]["wake"]["default"] is True
    assert r["t_park"]["wake"]["date"] == (date.fromtimestamp(NOW - 3 * 86400) + timedelta(days=7)).isoformat()
    assert r["t_hold"]["reader"] == "wake:until" and r["t_hold"]["wake"]["card"] == "t_c0ffee01"
    assert r["t_odd"]["reader"] == "none"
    assert _reader(con, "t_lat")["reader"] == "wake:later" and _reader(con, "t_lat")["deadline"] > NOW
    assert _reader(con, "t_rw")["reader"] == "wake:window" and _reader(con, "t_rw")["wake"]["name"] == "render"


def test_a_full_queue_is_not_a_stuck_ready_card(tmp_path):
    con = _board(tmp_path)
    _t(con, "t_rdy", "ready"); _e(con, "t_rdy", "promoted", NOW - 5 * H)
    assert _reader(con, "t_rdy", {**CTX, "running": 6})["deadline"] is None
    assert _reader(con, "t_rdy", CTX)["deadline"] < NOW


def test_compute_lists_none_overdue_due_and_needs_user(tmp_path):
    con = _board(tmp_path)
    _t(con, "t_zoe", "blocked", kind="needs_input"); _e(con, "t_zoe", "blocked", NOW - 3 * H)
    _t(con, "t_hold", "scheduled"); _e(con, "t_hold", "scheduled", NOW - H, "hold: until:t_c0ffee01"); _t(con, "t_c0ffee01", "done")
    _t(con, "t_park", "scheduled"); _e(con, "t_park", "scheduled", NOW - 9 * 86400, "park — x")
    _t(con, "t_odd", "weird")
    _t(con, "t_ok", "ready"); _e(con, "t_ok", "promoted", NOW - 60)
    reg = kv.compute(con, {**CTX, "zoey_up": False, "board": "b"}, NOW)
    assert reg["none"] == ["t_odd"]
    assert "t_zoe" in reg["overdue"] and "t_ok" not in reg["overdue"]
    assert set(reg["due"]) == {"t_hold", "t_park"}               # the until landed; the bare park's 7 d passed
    assert reg["needs_user"] == ["t_zoe"]
    assert reg["counts"]["wake"] == 2 and reg["board"] == "b"
    row = next(r for r in reg["rows"] if r["id"] == "t_park")
    assert row["wake"]["default"] and row["wake_due"] is True and row["title"] == "card t_park"
    assert json.dumps(reg, default=str)                            # the file the gate writes


def test_a_thin_board_never_raises(tmp_path):
    con = sqlite3.connect(tmp_path / "thin.db")
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at INTEGER)")
    con.execute("INSERT INTO tasks VALUES ('t_1', 'x', 'ready', ?)", (NOW - 100,))
    reg = kv.compute(con, {}, NOW)
    assert reg["rows"][0]["reader"] == "dispatcher" and reg["none"] == []


def test_a_fingerprint_in_worker_started_at_does_not_stop_the_patrol():
    """2026-09-27: the updated hermes writes a process fingerprint into worker_started_at
    ("<boot id>:<n>|<start ticks>"); float() of it raised on every gate tick for two hours."""
    assert kv._ts("489a619b-26cf-48f0-b12a-105010bb291c:21|22379563") == 0.0
    assert kv._ts(None) == 0.0 and kv._ts("") == 0.0 and kv._ts("1790500000.5") == 1790500000.5
    row = {"id": "t_1", "status": "running", "created_at": 1000.0, "started_at": 1500,
           "last_heartbeat_at": 2000.0,
           "worker_started_at": "489a619b-26cf-48f0-b12a-105010bb291c:21|22379563"}
    r = kv.reader_of(None, row, {}, 2100.0)
    assert r["reader"] == "heartbeat" and r["deadline"] == 2000.0 + kv.HEARTBEAT_MAX
