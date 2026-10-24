"""day-close retires a park that already carries an archive verdict (2026-09-21).

`scheduled` had no exit: this script read `done` only, so a parked card outlived
its own ruling. On the 09-16 board six parks carried a `VERB: archive` from the
steward sweep or Ada that nothing downstream ever executed — one of them with the
steward saying so on the card. A park gets a longer rope than a done card and the
same family guards, and nothing is purged.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_day_close_parks", ROOT / "crew" / "scripts" / "kanban-day-close.py")
dc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dc)
dc.LOG = Path(tempfile.mkdtemp(prefix="day-close-parks-test-")) / "day-close.log"


def _no_live_cli(*a, **k):
    raise AssertionError(f"test reached the LIVE kanban CLI: {a}")


dc.kanban = _no_live_cli

NOW = 1_000_000.0
OLD = int(NOW - 96 * 3600)     # older than the 72 h park rope
FRESH = int(NOW - 3600)


def _db(tmp_path, tasks, comments=(), links=()):
    """tasks: (id, status). comments: (task_id, author, body, created_at)."""
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, completed_at INTEGER, created_at INTEGER)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, "
                "body TEXT, created_at INTEGER)")
    con.executemany("INSERT INTO tasks VALUES (?,?,NULL,0)", tasks)
    con.executemany("INSERT INTO task_links VALUES (?,?)", links)
    con.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", comments)
    con.commit()
    con.close()
    return p


VERB = "VERB: archive — superseded; nothing left to do"


def test_a_park_with_an_unexecuted_sweep_verb_is_retired(tmp_path):
    """The exact 09-16 shape: steward ruled archive, the loop kept it scheduled."""
    db = _db(tmp_path, [("t_park", "scheduled")], [("t_park", "steward", VERB, OLD)])
    ids, held = dc.archivable_parks(db, now=NOW)
    assert ids == ["t_park"] and held == {}


def test_adas_routed_verb_counts_too(tmp_path):
    db = _db(tmp_path, [("t_park", "scheduled")], [("t_park", "ada", VERB, OLD)])
    assert dc.archivable_parks(db, now=NOW)[0] == ["t_park"]


def test_a_rulers_own_archive_word_counts(tmp_path):
    db = _db(tmp_path, [("t_park", "scheduled")], [("t_park", "zoey", "archive — superseded", OLD)])
    assert dc.archivable_parks(db, now=NOW)[0] == ["t_park"]


def test_a_park_with_no_archive_verdict_stays(tmp_path):
    """Zoey parked it. A park is a card the house still owes an answer on."""
    db = _db(tmp_path, [("t_park", "scheduled")],
             [("t_park", "zoey", "park — waiting on the render lane", OLD)])
    ids, held = dc.archivable_parks(db, now=NOW)
    assert ids == [] and held == {"no archive verdict": 1}


def test_a_stranger_cannot_retire_a_card(tmp_path):
    db = _db(tmp_path, [("t_park", "scheduled")], [("t_park", "bash", VERB, OLD)])
    assert dc.archivable_parks(db, now=NOW)[0] == []


def test_a_fresh_verdict_waits_out_the_rope(tmp_path):
    db = _db(tmp_path, [("t_park", "scheduled")], [("t_park", "steward", VERB, FRESH)])
    ids, held = dc.archivable_parks(db, now=NOW)
    assert ids == [] and held == {"young": 1}


def test_a_park_an_open_card_waits_on_is_held(tmp_path):
    db = _db(tmp_path, [("t_park", "scheduled"), ("t_frame", "todo")],
             [("t_park", "steward", VERB, OLD)], links=[("t_park", "t_frame")])
    ids, held = dc.archivable_parks(db, now=NOW)
    assert ids == [] and held == {"open card waits on it": 1}


def test_a_park_an_open_source_reads_as_its_reframe_is_held(tmp_path):
    """`reframe-cut:` names the CUT; the open source still waits to read it."""
    db = _db(tmp_path, [("t_0a1b2c3d", "scheduled"), ("t_src", "todo")],
             [("t_0a1b2c3d", "steward", VERB, OLD),
              ("t_src", "kanban-loop", "reframe-cut: t_0a1b2c3d (gen 1)", OLD)])
    ids, held = dc.archivable_parks(db, now=NOW)
    assert ids == [] and held == {"open source reads it": 1}


def test_only_parks_are_considered(tmp_path):
    """A blocked or todo card with an archive verdict is somebody else's call."""
    db = _db(tmp_path, [("t_b", "blocked"), ("t_t", "todo")],
             [("t_b", "steward", VERB, OLD), ("t_t", "steward", VERB, OLD)])
    assert dc.archivable_parks(db, now=NOW)[0] == []


def test_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("DAY_CLOSE_ARCHIVE_PARKS", "0")
    db = _db(tmp_path, [("t_park", "scheduled")], [("t_park", "steward", VERB, OLD)])
    assert dc.archivable_parks(db, now=NOW) == ([], {})


def test_the_done_path_is_untouched_by_all_this(tmp_path):
    """archivable() must still ignore parks entirely."""
    db = _db(tmp_path, [("t_park", "scheduled"), ("t_done", "done")],
             [("t_park", "steward", VERB, OLD)])
    con = sqlite3.connect(db)
    con.execute("UPDATE tasks SET completed_at = ? WHERE id = 't_done'", (OLD,))
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT)")
    con.commit()
    con.close()
    ids, _held = dc.archivable(db, now=NOW, min_age_h=36)
    assert ids == ["t_done"]
