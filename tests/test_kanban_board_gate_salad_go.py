"""A crew relapse ruled go is owed to the restart window, never a worker card (the User 2026-09-30).

09-30 10:33 Crissa ruled `go` on the relapse card; Ada filed a bash card and the worker restarted crew under running
workers at 10:49, past the approvals refusal by a script. The gate now writes the restart-owed row from her go
(dated at the ruling) so the window's executor restarts crew after its workers finish.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_board_gate_salad_go", ROOT / "crew" / "scripts" / "kanban-board-gate.py")
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: the restart window lives in window/lib
import restart_window as rw  # noqa: E402

NOW = 1_790_800_000.0
GO_TS = NOW - 3600
TITLE = "Restart window: crew engine relapse — 1 salad session(s) 2026-09-29"


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "LOG", tmp_path / "gate.log")
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: (calls.append(a), (0, "ok"))[1])
    return calls


def _board(tmp_path, comments, *, title=TITLE, status="done", created=NOW - 86400):
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, "
                "created_at INTEGER)")
    con.execute("INSERT INTO tasks VALUES ('t_da19d3cb', ?, ?, ?)", (title, status, int(created)))
    con.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES ('t_da19d3cb', ?, ?, ?)",
                    comments)
    con.commit()
    return con


def _go(tmp_path, comments, started, **kw):
    owed = tmp_path / "restart-owed.json"
    n = g.gate_salad_go(_board(tmp_path, comments, **kw), now=NOW, rw=rw, started=lambda: started, owed_path=owed)
    rows = json.loads(owed.read_text()) if owed.is_file() else {}
    return n, rows


def test_crissas_go_becomes_the_owed_row_dated_at_the_ruling(tmp_path, _sandbox):
    n, rows = _go(tmp_path, [("ada", "BLOCKED: go | wait?", GO_TS - 60), ("crissa", "go", GO_TS)], started=GO_TS - 9999)
    assert n == 1 and rows["crew.service"]["since"] == GO_TS
    assert rows["crew.service"]["by"] == "kanban-board-gate" and "t_da19d3cb" in rows["crew.service"]["why"]
    (comment,) = [c for c in _sandbox if c[0] == "comment"]
    assert comment[1] == "t_da19d3cb" and g.SALAD_GO_MARK in comment[2]


def test_a_go_crew_has_already_restarted_past_is_left_alone(tmp_path, _sandbox):
    """The 09-30 card: crew restarted at 10:49 after the 10:33 go. No row, no comment."""
    n, rows = _go(tmp_path, [("crissa", "go", GO_TS)], started=GO_TS + 960)
    assert n == 0 and rows == {} and _sandbox == []


@pytest.mark.parametrize("ruling", ["park", "wait 2", "later 23:00", "hands"])
def test_anything_but_go_owes_nothing(tmp_path, _sandbox, ruling):
    n, rows = _go(tmp_path, [("crissa", ruling, GO_TS)], started=None)
    assert n == 0 and rows == {}


def test_only_a_ruler_rules(tmp_path, _sandbox):
    n, rows = _go(tmp_path, [("ada", "go", GO_TS), ("bash", "go", GO_TS + 1)], started=None)
    assert n == 0 and rows == {}


def test_once_per_card(tmp_path, _sandbox):
    n, _ = _go(tmp_path, [("crissa", "go", GO_TS), ("kanban-board-gate", g.SALAD_GO_MARK + ": done", GO_TS + 5)],
               started=None)
    assert n == 0


def test_other_restart_window_cards_and_old_cards_are_not_this_gates(tmp_path, _sandbox):
    n, _ = _go(tmp_path, [("crissa", "go", GO_TS)], started=None, title="Restart window: crew.service, ear-bridge")
    assert n == 0
    (tmp_path / "kanban.db").unlink()
    n, _ = _go(tmp_path, [("crissa", "go", GO_TS)], started=None, created=NOW - 30 * 86400)
    assert n == 0


def test_dry_run_writes_nothing(tmp_path, _sandbox):
    owed = tmp_path / "restart-owed.json"
    con = _board(tmp_path, [("crissa", "go", GO_TS)])
    assert g.gate_salad_go(con, dry=True, now=NOW, rw=rw, started=lambda: None, owed_path=owed) == 1
    assert not owed.exists() and _sandbox == []


def test_the_tick_carries_it_with_a_kill():
    src = (ROOT / "crew" / "scripts" / "kanban-board-gate.py").read_text()
    assert "salad_go={sg}" in src and "KREW_SALAD_GO_OWES" in src


def test_ada_is_told_a_go_is_the_executors_in_both_copies():
    import os
    soul = ROOT / "souls" / "ada" / "SOUL.md"
    skill = ROOT / "skills" / "ada-orchestrate" / "SKILL.md"
    assert "`Restart window:` card ruled **go** is never a worker card" in soul.read_text()
    assert "## Restart window: a go goes to the executor" in skill.read_text()
    assert "no worker card" in skill.read_text()
    # krew: the house also compared the live profile's copies; an install's own profile is not this repo's to pin.
