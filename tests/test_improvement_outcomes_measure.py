"""The outcome stage can measure (2026-09-27, from the structure review).

Ten reports read "0 measured": the day-close archives a done try at 36 h, the measure is due at
7 days and skipped an archived try; and a reviewer's verdict lives in the run's `summary` on the
updated hermes, not in `tasks.result`, so a FAILed try read as landed.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DAY = 86400.0
NOW = 2_000_000_000.0


def _mod(monkeypatch, **env):
    for k in ("IMPROVE_MEASURE_ARCHIVED", "IMPROVE_REVIEW_FROM_RUNS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("improvement_outcomes_under_test",
                                                  ROOT / "crew" / "scripts" / "improvement-outcomes.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "crew" / "lib"))
    sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours
    spec.loader.exec_module(mod)
    # self-loop step 3's landing + board-wide rules have their own tests (test_self_loop_step3.py);
    # here a closed try has its commit and kanban_blocked is measurable, as these tests assume
    mod.commit_for = lambda card, *a, **k: "abc1234"
    mod.review_armed = lambda: False
    mod.BOARD_WIDE_UNMEASURED = False
    return mod


KPIS = {"kanban_blocked": {"direction": "down", "samples": [(NOW - 9 * DAY, 9.0), (NOW - 8 * DAY, 9.0),
                                                            (NOW - 1 * DAY, 7.0), (NOW, 7.0)]}}


def _try(status, completed, review="", kpi="kanban_blocked"):
    return {"id": "t_try", "title": f"try: team-improve: kit — kpi={kpi}", "status": status,
            "created_at": NOW - 9 * DAY, "completed_at": completed, "comments": [], "board": "b",
            "body": f"watch: kpi={kpi} baseline=9", "children": [], "review": review}


def _stub_values(monkeypatch, m):
    monkeypatch.setattr(m, "value_near", lambda k, ts: 9.0 if ts < NOW - 4 * DAY else 7.0)


def test_an_archived_try_that_was_completed_is_measured(monkeypatch):
    m = _mod(monkeypatch)
    _stub_values(monkeypatch, m)
    (row,) = m.try_rows([_try("archived", NOW - 8 * DAY)], set(), KPIS, NOW)
    assert row["stage"] == "landed" and row["landed_at"] == NOW - 8 * DAY
    assert row.get("after") == 7.0 and row.get("baseline") == 9.0 and "measure" in row


def test_an_archived_try_with_no_completion_was_retired_unbuilt(monkeypatch):
    m = _mod(monkeypatch)
    (row,) = m.try_rows([_try("archived", None)], set(), KPIS, NOW)
    assert row["stage"] == "try archived" and "after" not in row


def test_a_failed_review_is_not_a_landing_even_after_the_archive(monkeypatch):
    m = _mod(monkeypatch)
    for status in ("done", "archived"):
        (row,) = m.try_rows([_try(status, NOW - 8 * DAY, review="FAIL. the change regressed the gate")], set(), KPIS, NOW)
        assert row["stage"] == "closed, not landed (review FAIL)", status


def test_the_kill_switch_is_the_old_behaviour(monkeypatch):
    m = _mod(monkeypatch, IMPROVE_MEASURE_ARCHIVED="0")
    (row,) = m.try_rows([_try("archived", NOW - 8 * DAY)], set(), KPIS, NOW)
    assert row["stage"] == "try archived"
    (row,) = m.try_rows([_try("done", NOW - 8 * DAY)], set(), KPIS, NOW)
    assert row["stage"] == "landed"


def _board(tmp_path, runs=True):
    d = tmp_path / "boards" / "b"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "kanban.db")
    con.executescript("""
        CREATE TABLE tasks(id TEXT, title TEXT, status TEXT, created_at REAL, completed_at REAL, body TEXT, result TEXT);
        CREATE TABLE task_links(parent_id TEXT, child_id TEXT);
        CREATE TABLE task_comments(id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT);
    """)
    if runs:
        con.execute("CREATE TABLE task_runs(id INTEGER PRIMARY KEY, task_id TEXT, summary TEXT)")
    rows = [("t_try", "try: team-improve: kit", "done", 1.0, 50.0, "", None),
            ("t_build", "Implement the kit", "done", 2.0, 20.0, "", None),
            ("t_rev1", "review: Implement the kit", "done", 3.0, 30.0, "", "PASS. looks right"),
            ("t_rev2", "review: Implement the kit", "done", 4.0, 40.0, "", "")]
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", rows)
    # parent = PREREQUISITE: the try waits on the build; a review hangs under the build
    con.executemany("INSERT INTO task_links VALUES (?,?)",
                    [("t_build", "t_try"), ("t_build", "t_rev1"), ("t_build", "t_rev2")])
    if runs:
        con.executemany("INSERT INTO task_runs(task_id, summary) VALUES (?,?)",
                        [("t_rev2", "first pass, unsure"), ("t_rev2", "FAIL. the second look found a regression")])
    con.commit()
    con.close()
    return tmp_path / "boards"


def test_the_newest_review_speaks_from_its_run_summary(monkeypatch, tmp_path):
    m = _mod(monkeypatch)
    (card,) = [c for c in m.board_cards(_board(tmp_path)) if c["id"] == "t_try"]
    assert card["review"].startswith("FAIL.")                      # the NEWEST review, its newest run
    m = _mod(monkeypatch, IMPROVE_REVIEW_FROM_RUNS="0")
    (card,) = [c for c in m.board_cards(tmp_path / "boards") if c["id"] == "t_try"]
    assert card["review"].startswith("PASS.")                      # the old read: tasks.result only


def test_a_board_with_no_runs_table_still_reads(monkeypatch, tmp_path):
    m = _mod(monkeypatch)
    (card,) = [c for c in m.board_cards(_board(tmp_path, runs=False)) if c["id"] == "t_try"]
    assert card["review"].startswith("PASS.")


def _landed(tid, kpi, landed_at, measure="better"):
    return {"item": tid, "try": tid, "kpi_id": kpi, "stage": "landed", "landed_at": landed_at, "measure": measure,
            "baseline": 9.0, "after": 7.0}


def test_a_verdict_inside_a_krew_event_is_confounded_not_deleted(monkeypatch):
    """The 09-26 hermes update took kanban_open 13 -> 49 by itself: a try that landed the day
    before reads 'worse' and the try did nothing."""
    m = _mod(monkeypatch)
    rows = [_landed("t_a", "kanban_open", NOW - 8 * DAY, "worse"), _landed("t_b", "kanban_blocked", NOW - 30 * DAY)]
    m.mark_confounded(rows, events=[(NOW - 6 * DAY, "hermes tree updated")])
    assert rows[0]["measure"] == "worse" and "hermes tree updated" in rows[0]["confounded"][0]
    assert "confounded" not in rows[1]                          # its window closed before the event


def test_two_tries_on_one_kpi_cannot_both_own_the_move(monkeypatch):
    m = _mod(monkeypatch)
    rows = [_landed("t_a", "kanban_blocked", NOW - 9 * DAY), _landed("t_b", "kanban_blocked", NOW - 8 * DAY),
            _landed("t_c", "kanban_ready_age", NOW - 8 * DAY)]
    m.mark_confounded(rows, events=[])
    assert "1 other landed try watch kanban_blocked" in rows[0]["confounded"][0]
    assert "confounded" in rows[1] and "confounded" not in rows[2]


def test_the_report_says_how_many_can_be_read_as_the_trys_own(monkeypatch):
    m = _mod(monkeypatch)
    rows = [_landed("t_a", "kanban_blocked", NOW - 9 * DAY), _landed("t_b", "kanban_blocked", NOW - 8 * DAY),
            _landed("t_c", "kanban_ready_age", NOW - 8 * DAY, "flat")]
    md = m.render("2026-09-27", m.mark_confounded(rows, events=[]))
    assert "3 measured (2 better, 0 worse, 1 flat) · 2 of them confounded, 1 readable as the try's own." in md
    assert "## Confounded" in md and "better: 9.0 → 7.0 (confounded)" in md


def test_an_unmeasured_row_is_never_marked_and_the_kill_switch(monkeypatch):
    m = _mod(monkeypatch)
    rows = [_landed("t_a", "kanban_open", NOW - 2 * DAY, "due 2026-10-02")]
    assert "confounded" not in m.mark_confounded(rows, events=[(NOW - 1 * DAY, "x")])[0]
    m = _mod(monkeypatch, IMPROVE_CONFOUNDED="0")
    rows = [_landed("t_a", "kanban_open", NOW - 8 * DAY)]
    assert "confounded" not in m.mark_confounded(rows, events=[(NOW - 6 * DAY, "x")])[0]


def test_the_krew_events_file_reads_and_a_broken_one_is_no_events(monkeypatch, tmp_path):
    m = _mod(monkeypatch)
    ev = m.krew_events(ROOT / "crew" / "config" / "improvement-confounders.json")
    assert len(ev) >= 2 and any("hermes tree updated" in w for _, w in ev)
    bad = tmp_path / "x.json"
    bad.write_text("not json")
    assert m.krew_events(bad) == [] and m.krew_events(tmp_path / "none.json") == []
