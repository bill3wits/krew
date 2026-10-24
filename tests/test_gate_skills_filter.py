"""gate_skills_filter — strip foreign skill pins after a card changes hands (t_197285fb).

`hermes kanban reassign` does not touch `tasks.skills`, so a card reassigned from
profile A to profile B keeps A's pins. A pin the new assignee's registry cannot
resolve is a guaranteed rc=1: hermes raises `Unknown skill(s)` at preload and the
worker dies at `Initializing agent...` (t_47c4bebd `bash-map` on a steward card
x22). The gate inspects every open card, drops the names absent from the target
profile's skill directory, and comments one `Dropped <skill>: not present in
<profile> profile` line per drop.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
# The gate reads the real profile homes off the REAL home (HERMES_REAL_HOME default),
# not the worker's profile-home: the skip and the fixture both key off that.
HERMES = Path(os.environ.get("HERMES_REAL_HOME", "~")) / ".hermes" / "hermes-agent"

if str(HERMES) not in sys.path:
    sys.path.insert(0, str(HERMES))
SPEC = importlib.util.spec_from_file_location(
    "kanban_board_gate", ROOT / "crew" / "scripts" / "kanban-board-gate.py")
g = importlib.util.module_from_spec(SPEC)
sys.modules["kanban_board_gate"] = g
SPEC.loader.exec_module(g)

import pytest

pytestmark = pytest.mark.skipif(not HERMES.is_dir(), reason="hermes tree not installed")


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A bare board db + a kanban recorder + a two-profile registry under a fake HERMES
    root (never the live profile homes). The fake runner emulates the real verb:
    it drops exactly the named pins from the column (write_txn is the real script's job)."""
    db = tmp_path / "board.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, "
                 "assignee TEXT, skills TEXT, created_at INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, "
                 "author TEXT, body TEXT, created_at INTEGER)")
    conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, "
                 "kind TEXT, payload TEXT, created_at INTEGER)")
    conn.commit()

    home = tmp_path / "home"
    for prof, skills in (("steward", ("steward-map", "steward-sweep")),
                         ("bash", ("bash-map", "bash-code"))):
        for name in skills:
            d = home / ".hermes" / "profiles" / prof / "skills" / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text("# stub")
    monkeypatch.setattr(g, "HERMES", home / ".hermes")
    monkeypatch.setattr(g, "LOG", tmp_path / "kanban-board-gate.log")
    # log() prints every line to stdout: in the test run that is captured, so
    # only the failure diff stays on the terminal.
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    monkeypatch.delenv("KREW_SKILLS_FILTER", raising=False)
    monkeypatch.delenv("KREW_SKILLS_FILTER_MAX", raising=False)

    comments = []
    calls = []

    def kanban(verb, tid, body=None, *extra, dry=False):
        """Record every verb the way the gate calls it: (verb, task, body, …flags).
        The comment verb ALSO lands in task_comments exactly as the real writer
        stamps it — created_at = int(time.time()), epoch — because the gate's
        once-per-card skip reads the mark's ROW (its created_at is the epoch the
        mark's ts encodes), not the body alone."""
        comments.append(list((verb, tid, *((body,) if body is not None else ()), *extra)))
        if verb == "comment":
            conn.execute("INSERT INTO task_comments (task_id, author, body, created_at) "
                         "VALUES (?,?,?,?)",
                         (tid, extra[-1] if extra else "", body or "", int(time.time())))
            conn.commit()
        return 0, "ok"

    def runner(args):
        calls.append(list(args))
        tid = args[1]
        drop = set(args[2:-2]) if "--remove" in args else set()
        row = conn.execute("SELECT skills FROM tasks WHERE id = ?", (tid,)).fetchone()
        have = json.loads(row[0] or "[]") or []
        conn.execute("UPDATE tasks SET skills = ? WHERE id = ?",
                     (json.dumps([s for s in have if s not in drop]), tid))
        conn.commit()
        return SimpleNamespace(returncode=0, stdout="ok")

    monkeypatch.setattr(g, "kanban", kanban)
    yield SimpleNamespace(conn=conn, runner=runner, calls=calls, comments=comments)
    conn.close()


def _card(conn, tid, assignee, skills, status="ready"):
    conn.execute("INSERT INTO tasks (id, title, body, status, assignee, skills) "
                 "VALUES (?,?,?,?,?,?)",
                 (tid, "card", "", status, assignee, json.dumps(skills)))
    conn.commit()


def _pin_added(board, tid, skill, created_at):
    """Record a pin the way the real writer does: kanban-skills-add.py updates the card
    and appends one `skills_added` task_event per write — that event stream is the
    gate's re-arm signal (a newer pin than the newest strip mark re-arms)."""
    have = _skills(board.conn, tid)
    if skill not in have:
        have.append(skill)
    board.conn.execute("UPDATE tasks SET skills = ? WHERE id = ?", (json.dumps(have), tid))
    board.conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                       (tid, "skills_added", json.dumps({"added": [skill]}), created_at))
    board.conn.commit()


def _skills(conn, tid):
    raw = conn.execute("SELECT skills FROM tasks WHERE id = ?", (tid,)).fetchone()[0]
    return json.loads(raw or "[]")


def test_foreign_pins_are_dropped_and_logged(board):
    """Acceptance: reassigning a card with profile-specific skills strips them and logs each."""
    _card(board.conn, "t1", "steward", ["bash-map", "bash-code", "steward-sweep"])
    n = g.gate_skills_filter(board.conn, runner=board.runner)
    assert n == 1
    assert board.calls and "--remove" in board.calls[0], board.calls
    assert "bash-map" in board.calls[0] and "bash-code" in board.calls[0]
    assert "steward-sweep" not in board.calls[0]
    assert _skills(board.conn, "t1") == ["steward-sweep"]


def test_the_comment_lists_each_dropped_pin_and_why(board):
    _card(board.conn, "t1", "steward", ["bash-map", "steward-sweep"])
    g.gate_skills_filter(board.conn, runner=board.runner)
    # the gate's kanban recorder: (verb, task, body, "--author", author)
    assert any(c[0] == "comment" and c[1] == "t1"
               and "Dropped bash-map: not present in steward profile" in c[2]
               and g.SKILLS_FILTER_MARK in c[2]
               for c in board.comments), board.comments


def test_clean_cards_are_untouched(board):
    _card(board.conn, "t1", "steward", ["steward-map", "steward-sweep"])
    assert g.gate_skills_filter(board.conn, runner=board.runner) == 0
    assert board.calls == []


def test_marked_card_is_not_reprocessed(board):
    """Once per card: after the mark lands the gate leaves the card alone, even if a
    foreign pin is re-added with an OLDER timestamp than the mark — reassigns must
    never ping-pong."""
    _card(board.conn, "t1", "steward", ["bash-map"])
    assert g.gate_skills_filter(board.conn, runner=board.runner) == 1
    _pin_added(board, "t1", "bash-code", 1)
    assert g.gate_skills_filter(board.conn, runner=board.runner) == 0
    assert len(board.calls) == 1
    assert _skills(board.conn, "t1") == ["bash-code"], "an unstrip-able old pin is left, not looped"


def test_pin_added_after_the_strip_re_arms_the_gate(board):
    """The mark is not permanent: a foreign pin that lands AFTER the strip comment
    (ts newer than the mark's ts) re-arms the gate — the docstring's re-arm case."""
    _card(board.conn, "t1", "steward", ["bash-map"])
    assert g.gate_skills_filter(board.conn, runner=board.runner) == 1
    _pin_added(board, "t1", "bash-code", 3_000_000_001)
    assert g.gate_skills_filter(board.conn, runner=board.runner) == 1
    assert len(board.calls) == 2
    assert "bash-code" in board.calls[1]
    assert _skills(board.conn, "t1") == [], "the late foreign pin is stripped too"
    assert sum(1 for c in board.comments if g.SKILLS_FILTER_MARK in c[2]) == 2, \
        "one strip comment per strip pass"


def test_failed_remove_gives_up_after_the_cap_not_forever(board):
    """A poison card (remove always fails) must not burn the per-tick budget forever:
    after SKILLS_FILTER_FAILS_MAX consecutive failures the gate writes a give-up mark
    and stops touching the card — no strip mark (nothing was stripped), and the pin
    stays until a human intervenes."""
    _card(board.conn, "t1", "steward", ["bash-map"])

    def bad(args):
        board.calls.append(list(args))
        return SimpleNamespace(returncode=3, stdout="no such board")

    for _ in range(g.SKILLS_FILTER_FAILS_MAX):
        assert g.gate_skills_filter(board.conn, runner=bad) == 0
        assert _skills(board.conn, "t1") == ["bash-map"], "the pin stays until the removal lands"
    # one give-up mark after the cap, not one per tick
    giveups = [c for c in board.comments if g.SKILLS_FILTER_GIVEUP_MARK in c[2]]
    assert len(giveups) == 1, board.comments
    assert not any(g.SKILLS_FILTER_MARK in c[2] for c in board.comments), \
        "no strip mark: nothing was ever stripped"
    # and the next tick the gate leaves the card entirely alone
    n_calls = len(board.calls)
    assert g.gate_skills_filter(board.conn, runner=bad) == 0
    assert len(board.calls) == n_calls, "capped: no further remove attempts"


def test_non_roster_assignee_is_skipped(board):
    _card(board.conn, "t1", "grok", ["bash-map"])
    assert g.gate_skills_filter(board.conn, runner=board.runner) == 0
    assert board.calls == []


def test_missing_profile_skills_dir_is_skipped(board, tmp_path):
    """Case 3 — target profile has no skills defined at all: nothing to compare
    against, so the card is left alone (never stripped to zero on an empty
    registry) and no mark is written — the gate re-examines it every tick."""
    (board_home := g.HERMES.parent / "profiles" / "lila").mkdir(parents=True, exist_ok=True)
    _card(board.conn, "t1", "lila", ["bash-map"])
    assert g.gate_skills_filter(board.conn, runner=board.runner) == 0
    assert board.calls == []
    assert _skills(board.conn, "t1") == ["bash-map"], "the pin survives an empty-registry target"
    assert board.comments == [], "no strip comment may be written on an uncomparable card"


def test_dry_run_reports_but_writes_nothing(board):
    _card(board.conn, "t1", "steward", ["bash-map"])
    assert g.gate_skills_filter(board.conn, dry=True, runner=board.runner) == 1
    assert board.calls == []
    assert _skills(board.conn, "t1") == ["bash-map"]


def test_a_broken_skills_filter_import_is_logged_and_never_aborts_the_tick(board, monkeypatch, tmp_path):
    """The User 09-30: the live gate imported the (then untracked) lib/skills_filter.py
    bare inside the card loop - a missing or broken module raised out of the step and
    ended the tick before gate_cause / liveness / orphans. Now: one clear ERROR line,
    nothing stripped, the step returns."""
    monkeypatch.setitem(sys.modules, "skills_filter", None)      # `import` now raises ImportError
    _card(board.conn, "t1", "steward", ["bash-map", "steward-sweep"])
    assert g.gate_skills_filter(board.conn, runner=board.runner) == 0
    assert board.calls == []                                       # nothing removed
    assert _skills(board.conn, "t1") == ["bash-map", "steward-sweep"]
    text = (tmp_path / "kanban-board-gate.log").read_text()
    assert "ERROR lib/skills_filter.py would not import" in text
    assert text.count("would not import") == 1                     # once, not per card


def test_the_gate_calls_the_step_before_the_steps_it_must_not_block():
    src = (ROOT / "crew" / "scripts" / "kanban-board-gate.py").read_text()
    body = src.split("def gate_skills_filter(", 1)[1].split("\ndef ", 1)[0]
    assert "from skills_filter import" not in body, "the import lives in _skills_filter_fn, guarded"
    i = src.index("sf = gate_skills_filter(conn, dry=dry)")
    assert i < src.index("cz = gate_cause(conn, dry=dry)") < src.index("gate_liveness(conn, dry=dry)")
