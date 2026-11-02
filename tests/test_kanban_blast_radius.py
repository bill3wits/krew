"""The edge nobody reported: which parked cards hold live work.

The 09-16 board sat five days with every todo card waiting on an open prerequisite
and zero dispatchable — not a jam, the honest consequence of parked decisions at the
roots. These pin the two things that make the report trustworthy: the inverted
`task_links` column naming, and ROOT-ness (a card whose own prerequisite is still
open is not waiting on Crissa, and hermes would refuse to promote it).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_blast", ROOT / "crew" / "scripts" / "kanban-blast-radius.py")
br = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(br)


@pytest.fixture(autouse=True)
def _never_live(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(br, "kanban", lambda *a, **k: (calls.append(a), (0, ""))[1])
    br._calls = calls
    return calls


def _db(tmp_path, tasks, links=(), comments=()):
    """tasks: (id, status). links: (prerequisite, dependent) — the REAL column order."""
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, title TEXT, assignee TEXT, priority INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT)")
    con.executemany("INSERT INTO tasks (id,status,title,assignee) VALUES (?,?,'t','bash')", tasks)
    con.executemany("INSERT INTO task_links VALUES (?,?)", links)
    con.executemany("INSERT INTO task_comments (task_id, body) VALUES (?,?)", comments)
    con.commit(); con.close()
    return p


def test_link_direction_parent_is_the_prerequisite(tmp_path):
    """THE TRAP: parent_id is what must finish; child_id is the frame waiting on it.
    Read it the intuitive way round and every conclusion inverts."""
    db = _db(tmp_path, [("t_park", "scheduled"), ("t_frame", "todo")], links=[("t_park", "t_frame")])
    rows = br.rank(db)
    assert [r["id"] for r in rows] == ["t_park"]          # the PARK is what blocks
    assert rows[0]["blocks"] == 1 and rows[0]["blocked"] == ["t_frame"]


def test_blast_is_transitive(tmp_path):
    db = _db(tmp_path, [("t_p", "scheduled"), ("t_a", "todo"), ("t_b", "todo"), ("t_c", "todo")],
             links=[("t_p", "t_a"), ("t_a", "t_b"), ("t_b", "t_c")])
    assert br.rank(db)[0]["blocks"] == 3


def test_a_closed_dependent_is_not_counted(tmp_path):
    db = _db(tmp_path, [("t_p", "scheduled"), ("t_done", "done"), ("t_live", "todo")],
             links=[("t_p", "t_done"), ("t_p", "t_live")])
    r = br.rank(db)[0]
    assert r["blocks"] == 1 and r["blocked"] == ["t_live"]


def test_root_vs_waiting(tmp_path):
    """t_mid holds work but is itself waiting — not Crissa's to rule."""
    db = _db(tmp_path, [("t_deep", "scheduled"), ("t_mid", "scheduled"), ("t_leaf", "todo")],
             links=[("t_deep", "t_mid"), ("t_mid", "t_leaf")])
    by = {r["id"]: r for r in br.rank(db)}
    assert by["t_deep"]["root"] is True
    assert by["t_mid"]["root"] is False and by["t_mid"]["waiting_on"] == ["t_deep"]


def test_ranked_most_blocking_first_roots_ahead_of_ties(tmp_path):
    db = _db(tmp_path, [("t_big", "scheduled"), ("t_small", "scheduled"),
                        ("t_1", "todo"), ("t_2", "todo"), ("t_3", "todo")],
             links=[("t_big", "t_1"), ("t_big", "t_2"), ("t_small", "t_3")])
    assert [r["id"] for r in br.rank(db)] == ["t_big", "t_small"]


def test_a_park_holding_nothing_is_not_reported(tmp_path):
    db = _db(tmp_path, [("t_lonely", "scheduled")])
    assert br.rank(db) == []


def test_idle_note_counts_what_is_actually_dispatchable(tmp_path):
    db = _db(tmp_path, [("t_p", "scheduled"), ("t_waiting", "todo"), ("t_free", "todo")],
             links=[("t_p", "t_waiting")])
    idle = br.idle_note(db)
    assert idle["todo"] == 2 and idle["dispatchable"] == 1 and idle["free_ids"] == ["t_free"]


# --- routing ----------------------------------------------------------------

def test_route_takes_roots_only(tmp_path, _never_live):
    db = _db(tmp_path, [("t_deep", "scheduled"), ("t_mid", "scheduled"), ("t_leaf", "todo")],
             links=[("t_deep", "t_mid"), ("t_mid", "t_leaf")])
    assert br.route(db, br.rank(db), 5) == ["t_deep"]        # t_mid is waiting, never routed


def test_route_uses_unblock_then_typed_block(tmp_path, _never_live):
    """A scheduled card's only exit is unblock; hermes blocks only running/ready."""
    db = _db(tmp_path, [("t_p", "scheduled"), ("t_a", "todo")], links=[("t_p", "t_a")])
    br.route(db, br.rank(db), 1)
    verbs = [c[0] for c in _never_live]
    assert verbs == ["comment", "unblock", "block"]
    assert "needs_input" in _never_live[-1]


def test_route_is_capped_and_idempotent(tmp_path, _never_live):
    tasks = [("t_p%d" % i, "scheduled") for i in range(4)] + [("t_a%d" % i, "todo") for i in range(4)]
    links = [("t_p%d" % i, "t_a%d" % i) for i in range(4)]
    db = _db(tmp_path, tasks, links)
    assert len(br.route(db, br.rank(db), 2)) == 2
    db2 = _db(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, tasks, links,
              comments=[("t_p0", br.ROUTE_MARK + " — holds 1")])
    assert "t_p0" not in br.route(db2, br.rank(db2), 5)


def test_route_kill_switch(tmp_path, monkeypatch, _never_live):
    monkeypatch.setenv("KREW_BLAST_ROUTE", "0")
    db = _db(tmp_path, [("t_p", "scheduled"), ("t_a", "todo")], links=[("t_p", "t_a")])
    assert br.route(db, br.rank(db), 5) == [] and _never_live == []


def test_priority_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_BLAST_PRIORITY", "0")
    db = _db(tmp_path, [("t_p", "scheduled"), ("t_a", "todo")], links=[("t_p", "t_a")])
    assert br.set_priority(db, br.rank(db)) == 0


# --- the triage guard (learned the hard way, 2026-09-21) --------------------

def _db_kinds(tmp_path, tasks, links=(), comments=()):
    """tasks: (id, status, block_kind)."""
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, title TEXT, assignee TEXT, "
                "priority INTEGER DEFAULT 0, block_kind TEXT)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT)")
    con.executemany("INSERT INTO tasks (id,status,title,assignee,block_kind) VALUES (?,?,'t','bash',?)", tasks)
    con.executemany("INSERT INTO task_links VALUES (?,?)", links)
    con.executemany("INSERT INTO task_comments (task_id, body) VALUES (?,?)", comments)
    con.commit(); con.close()
    return p


def test_a_card_already_needs_input_is_commented_never_re_blocked(tmp_path, _never_live):
    """A same-cause re-block trips BLOCK_RECURRENCE_LIMIT and hermes sends the card to
    `triage` — OFF her queue. That is what happened to t_84fa8600 and t_e2d5f2a3."""
    db = _db_kinds(tmp_path, [("t_p", "scheduled", "needs_input"), ("t_a", "todo", None)],
                   links=[("t_p", "t_a")])
    assert br.route(db, br.rank(db), 1) == ["t_p"]
    verbs = [c[0] for c in _never_live]
    assert verbs == ["comment"]                     # no unblock, no second block
    assert "decide:" in _never_live[0][2]


def test_a_card_not_yet_hers_still_gets_the_full_route(tmp_path, _never_live):
    db = _db_kinds(tmp_path, [("t_p", "scheduled", None), ("t_a", "todo", None)],
                   links=[("t_p", "t_a")])
    assert br.route(db, br.rank(db), 1) == ["t_p"]
    assert [c[0] for c in _never_live] == ["comment", "unblock", "block"]


def test_a_transient_block_is_not_treated_as_hers(tmp_path, _never_live):
    db = _db_kinds(tmp_path, [("t_p", "blocked", "transient"), ("t_a", "todo", None)],
                   links=[("t_p", "t_a")])
    br.route(db, br.rank(db), 1)
    assert [c[0] for c in _never_live] == ["comment", "unblock", "block"]


# --- the two patrols that now carry it --------------------------------------

def test_steward_sweep_injects_the_blocking_parks_block():
    """Every other diagnostic it gets reads a card's OWN state; this is the edge."""
    src = (ROOT / "crew" / "scripts" / "steward-sweep").read_text()
    assert "kanban-blast-radius.py" in src
    assert "=== BLOCKING PARKS" in src
    assert "SCOPE ADD (9)" in src
    # the sweep must be told NOT to treat dependency-idleness as stuck workers
    assert "idle BY DEPENDENCY" in src
    assert "escalate what it waits on" in src


def test_krew_daily_reads_it_and_fails_quiet(monkeypatch):
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "crew" / "lib"))
    import krew_daily as hd
    got = hd._blocking_parks()
    assert isinstance(got, dict)                       # live board: shape only
    monkeypatch.setattr(hd, "Path", None)              # break it on purpose
    assert hd._blocking_parks() == {}                  # a report never breaks the page


def test_only_routes_exactly_those_cards(tmp_path, _never_live):
    """Rank order is the right default, not always the right answer: a hand naming
    two cards must not also sweep up whatever ties with them."""
    tasks = [("t_big", "scheduled"), ("t_tie", "scheduled"), ("t_named", "scheduled"),
             ("t_a", "todo"), ("t_b", "todo"), ("t_c", "todo"), ("t_d", "todo")]
    links = [("t_big", "t_a"), ("t_big", "t_b"), ("t_tie", "t_c"), ("t_named", "t_d")]
    db = _db(tmp_path, tasks, links)
    assert br.route(db, br.rank(db), 0, only=("t_named",)) == ["t_named"]


def test_only_reports_a_name_that_holds_nothing(tmp_path, _never_live, capsys):
    db = _db(tmp_path, [("t_p", "scheduled"), ("t_a", "todo")], links=[("t_p", "t_a")])
    assert br.route(db, br.rank(db), 0, only=("t_p", "t_ghost")) == ["t_p"]
    assert "t_ghost is not a park holding live work" in capsys.readouterr().out
