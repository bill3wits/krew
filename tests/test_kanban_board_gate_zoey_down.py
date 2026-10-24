"""The Zoey-down fallback (2026-09-21).

Every self-clearing path in the house ends at Zoey — the Decide watch is a cron
inside her gateway, the reframe rule asks her, the steward sweep reaches a verdict
it cannot execute. So the fault that stalls the board (a Render hold parks ops, her
brain) is the same fault that disables everything built to unstall it. This gate
carries a sweeper's verdict out while she is down: only her gateway being dead, only
a card untouched for a day, and only `archive` — the verb that keeps the card.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_board_gate_zd", ROOT / "crew" / "scripts" / "kanban-board-gate.py")
bg = importlib.util.module_from_spec(SPEC)
sys.modules["kanban_board_gate_zd"] = bg
SPEC.loader.exec_module(bg)

NOW = 1_000_000.0
OLD = int(NOW - 48 * 3600)
FRESH = int(NOW - 3600)
VERB = "VERB: archive — superseded; nothing left to do"


@pytest.fixture(autouse=True)
def _never_live(monkeypatch, tmp_path):
    """No systemd, no kanban CLI, no live board."""
    calls: list[tuple] = []
    monkeypatch.setattr(bg, "kanban", lambda *a, **k: (calls.append(a), (0, ""))[1])
    monkeypatch.setattr(bg, "LOG", tmp_path / "kanban-board-gate.log")   # never the live log (09-28)
    monkeypatch.setattr(bg, "_unit_active", lambda unit: False)          # Zoey is down
    monkeypatch.setattr(bg, "orchestrator", lambda: "ada")
    monkeypatch.setattr(bg, "ZOEY_DOWN_ON", True)
    monkeypatch.setattr(bg, "ZOEY_DOWN_VERBS", {"archive"})
    monkeypatch.setattr(bg, "ZOEY_DOWN_H", 24.0)
    bg._test_calls = calls
    return calls


def _db(tmp_path, tasks, comments=()):
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, "
                "body TEXT, created_at INTEGER)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,0)", tasks)
    con.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", comments)
    con.commit()
    con.close()
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def test_a_stale_sweeper_verdict_is_carried_out_while_she_is_down(tmp_path, _never_live):
    conn = _db(tmp_path, [("t_1", "stuck card", "scheduled")], [("t_1", "steward", VERB, OLD)])
    assert bg.gate_zoey_down(conn, now=NOW) == 1
    verbs = [c[0] for c in _never_live]
    assert "archive" in verbs and "comment" in verbs
    note = [c for c in _never_live if c[0] == "comment"][0][2]
    assert "Nothing is deleted" in note and bg.ZOEY_DOWN_MARK in note


def test_nothing_happens_while_her_gateway_is_up(tmp_path, monkeypatch, _never_live):
    monkeypatch.setattr(bg, "_unit_active", lambda unit: True)
    conn = _db(tmp_path, [("t_1", "stuck", "scheduled")], [("t_1", "steward", VERB, OLD)])
    assert bg.gate_zoey_down(conn, now=NOW) == 0
    assert _never_live == []


def test_a_fresh_card_is_left_for_her_to_rule(tmp_path, _never_live):
    conn = _db(tmp_path, [("t_1", "stuck", "scheduled")], [("t_1", "steward", VERB, FRESH)])
    assert bg.gate_zoey_down(conn, now=NOW) == 0
    assert _never_live == []


def test_only_archive_by_default(tmp_path, _never_live):
    """complete/park/reassign are NOT carried out without her."""
    for verb in ("complete", "park", "reassign", "zoey", "hands"):
        conn = _db(tmp_path / verb if (tmp_path / verb).mkdir() is None else tmp_path,
                   [("t_1", "stuck", "scheduled")],
                   [("t_1", "steward", f"VERB: {verb} — because", OLD)])
        assert bg.gate_zoey_down(conn, now=NOW) == 0
    assert _never_live == []


def test_a_stranger_cannot_move_a_card(tmp_path, _never_live):
    conn = _db(tmp_path, [("t_1", "stuck", "scheduled")], [("t_1", "bash", VERB, OLD)])
    assert bg.gate_zoey_down(conn, now=NOW) == 0


def test_an_already_executed_verdict_is_not_repeated(tmp_path, _never_live):
    conn = _db(tmp_path, [("t_1", "stuck", "scheduled")],
               [("t_1", "steward", VERB, OLD),
                ("t_1", bg.GATE_AUTHOR, bg.ZOEY_DOWN_MARK + " — carried out", OLD + 10)])
    assert bg.gate_zoey_down(conn, now=NOW) == 0


def test_the_ownerless_batch_is_left_to_gate_routed(tmp_path, _never_live):
    conn = _db(tmp_path, [("t_1", "stuck", "blocked")],
               [("t_1", bg.GATE_AUTHOR, bg.OWNERLESS_MARK + " batched in t_aaaaaaaa", OLD),
                ("t_1", "ada", VERB, OLD)])
    assert bg.gate_zoey_down(conn, now=NOW) == 0


def test_kill_switch(tmp_path, monkeypatch, _never_live):
    monkeypatch.setattr(bg, "ZOEY_DOWN_ON", False)
    conn = _db(tmp_path, [("t_1", "stuck", "scheduled")], [("t_1", "steward", VERB, OLD)])
    assert bg.gate_zoey_down(conn, now=NOW) == 0


def test_a_dry_run_counts_but_touches_nothing(tmp_path, _never_live):
    conn = _db(tmp_path, [("t_1", "stuck", "scheduled")], [("t_1", "steward", VERB, OLD)])
    assert bg.gate_zoey_down(conn, dry=True, now=NOW) == 1
    assert _never_live == []


def test_open_cards_that_are_not_blocked_or_parked_are_ignored(tmp_path, _never_live):
    conn = _db(tmp_path, [("t_1", "running card", "running")], [("t_1", "steward", VERB, OLD)])
    assert bg.gate_zoey_down(conn, now=NOW) == 0


def test_the_batch_is_capped(tmp_path, monkeypatch, _never_live):
    monkeypatch.setattr(bg, "ZOEY_DOWN_MAX", 2)
    tasks = [(f"t_{i}", "stuck", "scheduled") for i in range(5)]
    comments = [(f"t_{i}", "steward", VERB, OLD) for i in range(5)]
    conn = _db(tmp_path, tasks, comments)
    assert bg.gate_zoey_down(conn, now=NOW) == 2
