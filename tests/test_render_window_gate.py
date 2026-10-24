"""Kanban workers cannot start a render outside the User's off-hours."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))
import render_window_gate as g  # noqa: E402

DAY = dt.datetime(2026, 9, 17, 14, 0)
NIGHT = dt.datetime(2026, 9, 17, 21, 0)
HOURS = {"window": "20:00-02:00", "start": 1200, "end": 120, "source": "init", "why": ""}


def _hours(monkeypatch):
    monkeypatch.setattr(g.krew_hours, "off_hours", lambda site=None: dict(HOURS))


def test_studio_and_user_cli_are_not_gated(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_HERMES", str(tmp_path))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    _hours(monkeypatch)
    assert g.refuse_reason("t_aaaaaaa1", now=DAY) == ""


def test_kanban_worker_is_refused_by_day_and_allowed_at_night(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_HERMES", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_aaaaaaa1")
    monkeypatch.delenv("KREW_RENDER_NOW", raising=False)
    _hours(monkeypatch)
    why = g.refuse_reason("t_aaaaaaa1", now=DAY)
    assert why and "off-hours" in why
    night_why = g.refuse_reason("t_aaaaaaa1", now=NIGHT)
    assert night_why and "released batch" in night_why
    (tmp_path / "render-window.json").write_text(
        '{"window_key": "2026-09-17", "phase": "released", "released": ["t_aaaaaaa1"]}\n'
    )
    assert g.refuse_reason("t_aaaaaaa1", now=NIGHT) == ""
    # Yesterday's released list must not leak into the day.
    why = g.refuse_reason("t_aaaaaaa1", now=DAY)
    assert why and "off-hours" in why


def test_off_switch_and_unset_hours_are_idle(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_HERMES", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_aaaaaaa1")
    _hours(monkeypatch)
    (tmp_path / "render-window.off").write_text("")
    assert g.refuse_reason(now=DAY) == ""
    (tmp_path / "render-window.off").unlink()
    monkeypatch.setenv("KREW_RENDER_WINDOW", "0")
    assert g.refuse_reason(now=DAY) == ""
    monkeypatch.delenv("KREW_RENDER_WINDOW")
    monkeypatch.setattr(
        g.krew_hours, "off_hours",
        lambda site=None: {"window": None, "start": None, "end": None, "source": "unset", "why": "x"},
    )
    assert g.refuse_reason(now=DAY) == ""


def test_a_worker_cannot_exempt_itself(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_HERMES", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_bbbbbbbb")
    monkeypatch.delenv("KREW_RENDER_NOW", raising=False)
    _hours(monkeypatch)
    dbp = tmp_path / "kanban" / "boards" / "daily-2026-09-17" / "kanban.db"
    dbp.parent.mkdir(parents=True)
    (tmp_path / "kanban" / "current").write_text("daily-2026-09-17\n")
    con = sqlite3.connect(dbp)
    con.executescript(
        "CREATE TABLE tasks (id TEXT, body TEXT);"
        "CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT);"
        "INSERT INTO tasks VALUES ('t_bbbbbbbb', 'mint via render-submit');"
        "INSERT INTO task_comments (task_id, author, body) VALUES ('t_bbbbbbbb', 'bash', 'render-now');"
    )
    con.commit()
    con.close()
    assert g.refuse_reason("t_bbbbbbbb", now=DAY)


def test_render_now_in_body_or_zoey_comment_exempts(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_HERMES", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_bbbbbbbb")
    monkeypatch.delenv("KREW_RENDER_NOW", raising=False)
    _hours(monkeypatch)
    dbp = tmp_path / "kanban" / "boards" / "daily-2026-09-17" / "kanban.db"
    dbp.parent.mkdir(parents=True)
    (tmp_path / "kanban" / "current").write_text("daily-2026-09-17\n")
    con = sqlite3.connect(dbp)
    con.executescript(
        "CREATE TABLE tasks (id TEXT, body TEXT);"
        "CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT);"
        "INSERT INTO tasks VALUES ('t_bbbbbbbb', 'mint via render-submit');"
        "INSERT INTO tasks VALUES ('t_cccccccc', 'render-now: User wants it');"
    )
    con.commit()
    con.close()
    assert g.refuse_reason("t_bbbbbbbb", now=DAY)
    con = sqlite3.connect(dbp)
    con.execute(
        "INSERT INTO task_comments (task_id, author, body) VALUES (?,?,?)",
        ("t_bbbbbbbb", "zoey", "render-now — do it"),
    )
    con.commit()
    con.close()
    assert g.refuse_reason("t_bbbbbbbb", now=DAY) == ""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_cccccccc")
    assert g.refuse_reason("t_cccccccc", now=DAY) == ""


def test_krew_render_now_env_exempts(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_HERMES", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_aaaaaaa1")
    monkeypatch.setenv("KREW_RENDER_NOW", "1")
    _hours(monkeypatch)
    assert g.refuse_reason(now=DAY) == ""


def test_submit_and_plugin_call_the_gate():
    submit = (ROOT / "crew" / "scripts" / "render-submit.py").read_text()
    assert "render_window_gate.refuse_reason" in submit
    plug = (ROOT / "plugins" / "krew-comfy" / "__init__.py").read_text()
    assert "def _window_refuse" in plug
    assert "if mode == \"render\":" in plug and "_window_refuse()" in plug


# ── t_73d9e1dc: a worker sandbox repoints $HOME; the anchor must follow the state ──

def test_anchor_prefers_env_home_when_it_carries_kanban_state(tmp_path):
    home_env = tmp_path / "real"
    passwd = tmp_path / "pw"
    (home_env / ".hermes" / "kanban").mkdir(parents=True)
    (home_env / ".hermes" / "kanban" / "current").write_text("daily\n")
    assert g._anchor_home(home_env, passwd) == home_env
    # No state anywhere -> passwd home (the host writes there).
    assert g._anchor_home(tmp_path / "empty", passwd) == passwd


def test_passwd_home_fallback_when_sandbox_home_lacks_state(monkeypatch, tmp_path):
    """t_73d9e1dc signature: $HOME is the profile sandbox (has .hermes, no kanban
    board, no render-window.json); the released batch lives under the passwd home.
    Pre-fix, the gate anchored on $HOME and refused the released card."""
    sandbox = tmp_path / "sandbox"
    (sandbox / ".hermes").mkdir(parents=True)  # has .hermes, but no state
    passwd = tmp_path / "pw"
    state = passwd / ".hermes"
    (state / "kanban" / "boards" / "daily-2026-09-17").mkdir(parents=True)
    (state / "kanban" / "current").write_text("daily-2026-09-17\n")
    con = sqlite3.connect(state / "kanban" / "boards" / "daily-2026-09-17" / "kanban.db")
    con.executescript(
        "CREATE TABLE tasks (id TEXT, body TEXT);"
        "CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT);"
        "INSERT INTO tasks VALUES ('t_sandbox001', 'mint via the minter');"
    )
    con.commit()
    con.close()
    (state / "render-window.json").write_text(
        '{"window_key": "2026-09-17", "phase": "released", "released": ["t_sandbox001"]}\n'
    )
    monkeypatch.delenv("KREW_HERMES", raising=False)
    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_sandbox001")
    monkeypatch.delenv("KREW_RENDER_NOW", raising=False)
    monkeypatch.setattr(g, "_passwd_home", lambda: passwd)
    _hours(monkeypatch)
    # The anchor must land on the passwd home, not the empty sandbox.
    assert g.hermes_home() == state
    assert g.in_released_batch("t_sandbox001", now=NIGHT)
    assert g.refuse_reason("t_sandbox001", now=NIGHT) == ""


def _demo_board(tmp_path: Path) -> Path:
    dbp = tmp_path / "kanban" / "boards" / "daily-2026-09-17" / "kanban.db"
    dbp.parent.mkdir(parents=True)
    (tmp_path / "kanban" / "current").write_text("daily-2026-09-17\n")
    con = sqlite3.connect(dbp)
    con.executescript(
        "CREATE TABLE tasks (id TEXT, body TEXT);"
        "CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT);"
        "CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT);"
        "INSERT INTO tasks VALUES ('t_demo0001', 'debut reel');"
        "INSERT INTO tasks VALUES ('t_child001', 'lila renders');"
        "INSERT INTO tasks VALUES ('t_child002', 'direct child, not listed yet');"
        "INSERT INTO tasks VALUES ('t_grand003', 'a second split');"
        "INSERT INTO tasks VALUES ('t_other004', 'someone else');"
    )
    con.execute(
        "INSERT INTO task_events (task_id, kind, payload) VALUES (?,?,?)",
        ("t_child002", "created", json.dumps({"from_decompose_of": "t_demo0001"})),
    )
    con.execute(
        "INSERT INTO task_events (task_id, kind, payload) VALUES (?,?,?)",
        ("t_grand003", "created", json.dumps({"from_decompose_of": "t_child001"})),
    )
    con.commit()
    con.close()
    return dbp


def test_demo_render_override_covers_only_that_lap_and_turns_off(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_HERMES", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_other004")
    monkeypatch.delenv("KREW_RENDER_NOW", raising=False)
    _hours(monkeypatch)
    _demo_board(tmp_path)
    path = tmp_path / "demo-render-now.json"
    assert "off-hours" in g.refuse_reason("t_demo0001", now=DAY)
    path.write_text(json.dumps({
        "on": True,
        "parents": ["t_demo0001"],
        "cards": ["t_demo0001", "t_child001"],
        "pid": os.getpid(),
    }))
    assert g.refuse_reason("t_demo0001", now=DAY) == ""
    assert g.refuse_reason("t_child001", now=DAY) == ""
    assert g.refuse_reason("t_child002", now=DAY) == ""
    assert "off-hours" in g.refuse_reason("t_grand003", now=DAY)
    assert "off-hours" in g.refuse_reason("t_other004", now=DAY)
    path.write_text(json.dumps({
        "on": True, "parents": ["t_demo0001"], "cards": ["t_demo0001"], "pid": 2147483646,
    }))
    assert "off-hours" in g.refuse_reason("t_demo0001", now=DAY)
    path.write_text(json.dumps({"on": False, "parents": ["t_demo0001"], "cards": ["t_demo0001"]}))
    assert "off-hours" in g.refuse_reason("t_demo0001", now=DAY)
    path.write_text(json.dumps({
        "on": True, "parents": ["t_demo0001"], "cards": ["t_demo0001", "t_child001"], "pid": os.getpid(),
    }))
    assert g.refuse_reason("t_child002", now=DAY) == ""
    path.unlink()
    assert "off-hours" in g.refuse_reason("t_demo0001", now=DAY)
    assert "off-hours" in g.refuse_reason("t_child001", now=DAY)
    assert "off-hours" in g.refuse_reason("t_child002", now=DAY)
