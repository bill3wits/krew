"""kanban-day-close archives closed done cards on the standing board — and only closed trees."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_day_close", ROOT / "crew" / "scripts" / "kanban-day-close.py")
dc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dc)
# ISOLATION: the module logs to ~/.hermes/logs and its CLI is the live board (2026-09-17: a
# refused-archive test wrote "t_bad00000" into the LIVE day-close log).
import tempfile as _tempfile
dc.LOG = Path(_tempfile.mkdtemp(prefix="day-close-test-")) / "day-close.log"
def _no_live_cli(*a, **k):
    raise AssertionError(f"test reached the LIVE kanban CLI: {a}")
dc.kanban = _no_live_cli

NOW = 1_000_000.0
OLD = NOW - 48 * 3600
FRESH = NOW - 3600


def _db(tmp_path, tasks, links=(), events=(), comments=()):
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, completed_at INTEGER)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?)", tasks)
    con.executemany("INSERT INTO task_links VALUES (?,?)", links)
    con.executemany("INSERT INTO task_events (task_id, kind, payload) VALUES (?,?,?)", events)
    con.executemany("INSERT INTO task_comments (task_id, body) VALUES (?,?)", comments)
    con.commit()
    con.close()
    return p


def test_old_closed_done_cards_go_fresh_ones_stay_for_the_reports(tmp_path):
    db = _db(tmp_path, [("t_old", "done", OLD), ("t_new", "done", FRESH), ("t_open", "blocked", None)])
    ids, held = dc.archivable(db, now=NOW, min_age_h=36)
    assert ids == ["t_old"] and held == {"young": 1}


def test_done_child_of_an_open_frame_is_held(tmp_path):
    """kanban-loop completes a frame only when its children read `done`; hermes links the
    child as the frame's PARENT. Archive it and the frame never completes."""
    db = _db(tmp_path, [("t_kid", "done", OLD), ("t_frame", "todo", None)], links=[("t_kid", "t_frame")])
    ids, held = dc.archivable(db, now=NOW, min_age_h=36)
    assert ids == [] and held == {"open card waits on it": 1}
    db2 = _db(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path,
              [("t_kid", "done", OLD), ("t_frame", "done", OLD)], links=[("t_kid", "t_frame")])
    assert sorted(dc.archivable(db2, now=NOW, min_age_h=36)[0]) == ["t_frame", "t_kid"]   # closed tree goes


def test_decomposed_child_waits_for_its_root(tmp_path):
    db = _db(tmp_path, [("t_kid", "done", OLD), ("t_root", "blocked", None)],
             events=[("t_kid", "created", json.dumps({"by": "auto-decomposer", "from_decompose_of": "t_root"}))])
    assert dc.archivable(db, now=NOW, min_age_h=36) == ([], {"root still open": 1})


def test_done_reframe_stays_until_its_source_closes(tmp_path):
    """A superseded source completes only when its reframe READS done."""
    db = _db(tmp_path, [("t_aaaaaaaa", "done", OLD), ("t_bbbbbbbb", "blocked", None)],
             comments=[("t_bbbbbbbb", "reframe-cut: t_aaaaaaaa (gen 1, frame by model)")])
    assert dc.archivable(db, now=NOW, min_age_h=36) == ([], {"open source reads it": 1})


def test_never_purges_and_help_changes_nothing(capsys):
    code = "\n".join(l for l in (ROOT / "crew" / "scripts" / "kanban-day-close.py").read_text().split('"""', 2)[2].splitlines()
                     if not l.lstrip().startswith("#"))
    assert '"--rm"' not in code and '"archive"' in code and '"gc"' in code
    assert dc.main(["--help"]) == 0 and "usage:" in capsys.readouterr().out
    assert dc.main(["--bogus"]) == 2


def test_a_refused_archive_fails_the_run(monkeypatch):
    """Exit 0 for work not done is a lie the ticker would record as ok."""
    calls = []
    def cli(*a):
        calls.append(a)
        return (1, "unknown id") if a[0] == "archive" and "t_bad00000" in a else (0, "")
    assert dc.run_archive([f"t_{i:08x}" for i in range(30)], cli=cli) == (30, 0)
    assert dc.run_archive(["t_bad00000"], cli=cli) == (0, 1)
    src = (ROOT / "crew" / "scripts" / "kanban-day-close.py").read_text()
    # 2026-09-21: the park backstop added `parked_refused` to the same return — a refused
    # retirement is work not done too, and must fail the run for the same reason.
    assert "return 1 if (refused or parked_refused or rc != 0) else 0" in src
