"""A hermes cron job that fails three times in a row gets ONE card (the User 2026-09-30)."""
from __future__ import annotations

import json
from pathlib import Path

import cron_streak as cs

TB = ("Restart-safe cron worker dispatch failed: cron external worker exited before ownership acknowledgement (exit 1); "
      "worker stderr: Traceback (most recent call last):\n  File \"x.py\", line 1\nModuleNotFoundError: No module named 'ruamel'")


def _home(tmp_path, crissa_jobs, default_jobs=None):
    (tmp_path / "profiles" / "crissa" / "cron").mkdir(parents=True)
    (tmp_path / "profiles" / "crissa" / "cron" / "jobs.json").write_text(json.dumps({"jobs": crissa_jobs}))
    (tmp_path / "profiles" / "judge").mkdir()                       # a profile with no cron file
    if default_jobs is not None:
        (tmp_path / "cron").mkdir()
        (tmp_path / "cron" / "jobs.json").write_text(json.dumps(default_jobs))
    return tmp_path


def _job(**over):
    j = {"id": "5a7faf67f2ae", "name": "needs-crissa-watch", "enabled": True, "failure_streak": 190,
         "last_status": "error", "last_error": TB, "last_run_at": "2026-09-30T10:12:56"}
    j.update(over)
    return j


def test_three_in_a_row_is_named_and_two_is_not(tmp_path):
    home = _home(tmp_path, [_job(), _job(id="b", name="two", failure_streak=2), _job(id="c", name="ok", failure_streak=0, last_status="ok"),
                            _job(id="d", name="off", enabled=False), _job(id="e", name="paused", paused_at="2026-09-29"),
                            _job(id="f", name="recovered", failure_streak=5, last_status="ok")],
                 default_jobs=[_job(id="g", name="bridge-job", failure_streak=3)])
    rows = cs.failing(home)
    assert [(r["profile"], r["name"], r["streak"]) for r in rows] == [("default", "bridge-job", 3), ("crissa", "needs-crissa-watch", 190)]


def test_an_unreadable_jobs_file_is_skipped_not_an_alarm(tmp_path):
    home = _home(tmp_path, [])
    (home / "profiles" / "crissa" / "cron" / "jobs.json").write_text("{not json")
    assert cs.failing(home) == []


def test_the_card_names_the_cause_not_the_wrapper(tmp_path):
    row = cs.failing(_home(tmp_path, [_job()]))[0]
    title, body = cs.card_for(row)
    assert title == "cron: crissa's job needs-crissa-watch has failed 190 times in a row"
    assert "cause: ModuleNotFoundError: No module named 'ruamel'" in body
    assert body.startswith("source: hermes cron job 5a7faf67f2ae") and "evidence: failure_streak=190" in body
    assert cs.cause_line("plain failure line\nmore") == "plain failure line"
    assert cs.cause_line("") == "no error text recorded"


def test_one_card_per_streak(tmp_path):
    rows = cs.failing(_home(tmp_path, [_job()]))
    state = {}
    assert cs.due(rows, state) == rows
    cs.mark(state, rows[0], "t_abc12345")
    assert cs.due(rows, state) == [], "the streak already has its card"
    assert cs.settle(rows, state) == state
    assert cs.settle([], state) == {}, "a clean run ends the streak; the next one is a new card"
    path = tmp_path / "state.json"
    cs.save_state(path, state)
    assert cs.load_state(path)["crissa:5a7faf67f2ae"]["card"] == "t_abc12345"
    assert cs.load_state(tmp_path / "missing.json") == {}


def test_a_job_that_is_running_right_now_is_not_carded(tmp_path):
    """10:23 on 09-30: the first run after the fix was still ruling cards while `last_status` read error."""
    running = _job(last_run_at="2026-09-30T10:12:56", last_dispatch={"dispatched_at": "2026-09-30T10:23:56"})
    died_at_dispatch = _job(id="x", name="dies", last_run_at="2026-09-30T10:12:56.79", last_dispatch={"dispatched_at": "2026-09-30T10:12:56.63"})
    rows = cs.failing(_home(tmp_path, [running, died_at_dispatch]))
    assert [r["name"] for r in rows] == ["dies"]
    assert cs.in_flight(running) and not cs.in_flight(died_at_dispatch) and not cs.in_flight(_job())


def test_the_board_gate_files_one_triage_card_per_streak(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("board_gate_cron", Path(__file__).resolve().parents[1] / "crew" / "scripts" / "kanban-board-gate.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    home = _home(tmp_path, [_job()])
    calls, logs = [], []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: (calls.append(a) or (0, "Created t_abc12345  (triage, assignee=ada)")))
    monkeypatch.setattr(g, "log", logs.append)
    state = tmp_path / "cards.json"
    assert g.gate_cron_streak(state_path=state, home=home) == 1
    a = calls[0]
    assert a[0] == "create" and "needs-crissa-watch has failed 190 times in a row" in a[1]
    assert a[a.index("--assignee") + 1] == "ada" and "--triage" in a and a[a.index("--created-by") + 1] == g.GATE_AUTHOR
    assert a[a.index("--idempotency-key") + 1].startswith("cron-streak:crissa:5a7faf67f2ae:")
    assert "ModuleNotFoundError" in a[a.index("--body") + 1]
    assert g.gate_cron_streak(state_path=state, home=home) == 1 - 1 and len(calls) == 1, "the streak has its card"
    (home / "profiles" / "crissa" / "cron" / "jobs.json").write_text(json.dumps({"jobs": [_job(failure_streak=0, last_status="ok")]}))
    assert g.gate_cron_streak(state_path=state, home=home) == 0 and json.loads(state.read_text()) == {}, "a clean run ends it"
    (home / "profiles" / "crissa" / "cron" / "jobs.json").write_text(json.dumps({"jobs": [_job(failure_streak=3)]}))
    assert g.gate_cron_streak(state_path=state, home=home) == 1 and len(calls) == 2, "a new streak is a new card"
    before = state.read_text()
    assert g.gate_cron_streak(dry=True, state_path=tmp_path / "dry.json", home=home) == 1 and not (tmp_path / "dry.json").exists()
    assert state.read_text() == before
