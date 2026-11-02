"""`done` closes a park (2026-09-21).

A park had no close verb. `archive` retires a card the ruler is finished with;
nothing said "the work landed — close it". So a park whose work was done had one
exit, by hand and in two verbs: `unblock`, then `complete` — with a dispatcher
window between them, because hermes refuses `complete` on `scheduled` outright
(`kanban_db.complete_task`: running|ready|blocked|review).

Measured on t_84fa8600: four children done, Crissa's own ruling "superseded",
7 runs in park, and the board's top blocking park holding 4 live cards. The User
pressed Complete on the dashboard and the verb was refused before it read the card.

`done` cannot be spelled `archive`: `run_complete_frames` counts only `done`, so
archiving a decompose child freezes its frame for good — t_0e36e644 did exactly
that to t_2593eae2 the same morning.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_loop_dv", ROOT / "crew" / "scripts" / "kanban-loop.py")
kl = importlib.util.module_from_spec(SPEC)
sys.modules["kanban_loop_dv"] = kl   # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(kl)


@pytest.fixture(autouse=True)
def _quiet_log(tmp_path, monkeypatch):
    """Never write the house log from a test (these runs landed 12 `done t_1` lines
    in ~/.hermes/logs/kanban-loop.log before this was here)."""
    monkeypatch.setattr(kl, "LOG", tmp_path / "kanban-loop.log")


def _card(status="scheduled", comments=(), cid="t_1", body="body", **kw):
    return kl.Card(cid, "a title", body, status, "bash", "", 0, "ada",
                   comments=list(comments), comment_at=[100 + i for i in range(len(comments))], **kw)


class Cli:
    """Records calls; never the live board."""
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, *args, **kw):
        self.calls.append(tuple(str(a) for a in args))
        return self.rc, ""

    def verbs(self):
        return [c[0] for c in self.calls]


# --- the verdict ------------------------------------------------------------

@pytest.mark.parametrize("word", ["done", "done — all four children landed", "DONE", "done: superseded"])
def test_done_is_its_own_verdict(word):
    assert kl.verdict_of(word)[0] == "done"


@pytest.mark.parametrize("word", ["close", "closed", "finished", "landed"])
def test_the_synonyms_were_trimmed(word):
    """Five spellings for one verdict multiplied the false-positive surface on a
    ruler's prose and added no meaning (the User, 2026-09-21 pm). `done` is the word
    her watch prompt names, so it is the only one the loop reads."""
    assert kl.verdict_of(word)[0] == ""


def test_unblock_prefix_still_read():
    assert kl.verdict_of("UNBLOCK: done — the work landed under the children")[0] == "done"


@pytest.mark.parametrize("word,expect", [
    ("try", "try"), ("go", "try"), ("park", "park"), ("no", "park"),
    ("archive", "archive"), ("drop", "archive"), ("hands", "hands"), ("", ""),
])
def test_the_other_verdicts_are_untouched(word, expect):
    assert kl.verdict_of(word)[0] == expect


def test_complete_is_not_a_closing_word():
    """Only the FIRST word is read, and rulers open sentences with `complete`."""
    assert kl.verdict_of("complete the smoke first, then we talk")[0] == ""


def test_kill_switch_makes_the_words_mean_nothing_again(monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_DONE_VERB", "0")
    assert kl.verdict_of("done — the work landed")[0] == ""
    assert kl.done_ruling(_card(comments=[("crissa", "done — the work landed")])) is None


# --- reading the ruling -----------------------------------------------------

def test_a_rulers_done_is_read_as_a_done_ruling():
    c = _card(comments=[("crissa", "done — superseded; t_8fca1cc4 carries the work")])
    dr = kl.done_ruling(c)
    assert dr is not None and dr[1] == "crissa"
    assert kl.park_ruling(c) is None and kl.archive_ruling(c) is None


def test_a_later_try_outranks_an_earlier_done():
    c = _card(comments=[("crissa", "done — landed"), ("crissa", "try — new evidence")])
    assert kl.done_ruling(c) is None
    assert kl.run_done_rulings([c], Cli()) == 0


def test_a_non_ruler_cannot_close_a_card_here():
    """steward's `VERB: done` is the board gate's business, not a ruling."""
    assert kl.done_ruling(_card(comments=[("steward", "VERB: done — nothing left")])) is None


def test_the_dashboard_rules_as_the_user():
    assert kl.done_ruling(_card(comments=[("dashboard", "done — closed from the board")])) is not None


# --- closing the card -------------------------------------------------------

def test_a_parked_card_is_unblocked_promoted_and_completed_in_one_pass():
    """The two hand verbs, without the dispatcher window between them."""
    c = _card(status="scheduled", comments=[("crissa", "done — all four children landed")])
    cli = Cli()
    assert kl.run_done_rulings([c], cli) == 1
    assert cli.verbs() == ["unblock", "promote", "complete", "comment"]   # mark last
    assert c.status == "done"


def test_it_never_reaches_for_archive():
    """An archived card is not `done`: run_complete_frames counts only `done`,
    so archiving a decompose child would freeze its frame (t_0e36e644)."""
    c = _card(status="scheduled", comments=[("crissa", "done — landed")])
    cli = Cli()
    kl.run_done_rulings([c], cli)
    assert "archive" not in cli.verbs()


def test_a_blocked_card_completes_without_an_unblock():
    c = _card(status="blocked", comments=[("crissa", "done — landed")])
    cli = Cli()
    assert kl.run_done_rulings([c], cli) == 1
    assert cli.verbs() == ["complete", "comment"]


def test_a_todo_card_is_promoted_first():
    c = _card(status="todo", comments=[("crissa", "done — landed")])
    cli = Cli()
    assert kl.run_done_rulings([c], cli) == 1
    assert cli.verbs() == ["promote", "complete", "comment"]


def test_the_note_carries_who_ruled_and_the_words():
    c = _card(comments=[("crissa", "done — superseded by t_8fca1cc4")])
    cli = Cli()
    kl.run_done_rulings([c], cli)
    note = [x for x in cli.calls if x[0] == "comment"][0]
    assert kl.DONE_MARK in note[2] and "crissa" in note[2] and "superseded" in note[2]


def test_open_children_are_named_but_do_not_veto():
    """The ruler is the authority on whether the work landed; the note says what was open."""
    c = _card(cid="t_root", comments=[("crissa", "done — the children carry it")])
    kid = _card(cid="t_kid", status="todo", decompose_of="t_root")
    cli = Cli()
    assert kl.run_done_rulings([c, kid], cli, index=kl.index_of([c, kid])) == 1
    note = [x for x in cli.calls if x[0] == "comment"][0]
    assert "t_kid" in note[2]


def test_the_mark_is_written_only_after_the_close(tmp_path):
    """FOUND IN PRODUCTION 2026-09-21 17:15. The mark was written FIRST and used as
    the idempotency key, so a refused close left it behind and every later tick
    skipped the card — "attempted" reading as "done". t_57a06e7c: the unblock landed
    it in `todo` behind a parent that closed three seconds later, complete refused,
    and the retry that would have worked never came."""
    c = _card(comments=[("crissa", "done — landed")])
    cli = Cli()
    kl.run_done_rulings([c], cli)
    verbs = cli.verbs()
    assert verbs.index("complete") < verbs.index("comment"), "the mark preceded the close"
    assert kl.DONE_MARK in [x[2] for x in cli.calls if x[0] == "comment"][0]


def test_a_refusal_does_not_block_the_retry():
    c = _card(comments=[("crissa", "done — landed")])
    assert kl.run_done_rulings([c], Cli(rc=1)) == 0
    assert c.status != "done"
    # the refusal is recorded, and it is NOT the success mark
    c.comments.append(("kanban-loop", kl.DONE_REFUSED_MARK + " would not close"))
    cli = Cli()
    assert kl.run_done_rulings([c], cli) == 1, "a refused card must be retried"
    assert "complete" in cli.verbs()


def test_the_refusal_is_said_once():
    c = _card(comments=[("crissa", "done — landed")])
    cli = Cli(rc=1)
    kl.run_done_rulings([c], cli)
    notes = [x for x in cli.calls if x[0] == "comment"]
    assert len(notes) == 1 and kl.DONE_REFUSED_MARK in notes[0][2]
    c.comments.append(("kanban-loop", notes[0][2]))
    cli2 = Cli(rc=1)
    kl.run_done_rulings([c], cli2)
    assert not [x for x in cli2.calls if x[0] == "comment"], "said the refusal twice"


def test_a_closed_card_is_not_closed_again():
    """The status check is the whole idempotency now: a mark can only mean closed."""
    cli = Cli()
    for st in ("done", "archived"):
        assert kl.run_done_rulings([_card(status=st, comments=[("crissa", "done")])], cli) == 0
    assert cli.calls == []


def test_done_and_archived_cards_are_skipped():
    cli = Cli()
    for st in ("done", "archived"):
        assert kl.run_done_rulings([_card(status=st, comments=[("crissa", "done")])], cli) == 0
    assert cli.calls == []


def test_a_park_is_still_parked_not_closed():
    c = _card(comments=[("crissa", "park — waiting on the render lane")])
    cli = Cli()
    assert kl.run_done_rulings([c], cli) == 0
    assert cli.calls == []


def test_the_kill_switch_stops_the_close(monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_DONE_VERB", "0")
    c = _card(comments=[("crissa", "done — landed")])
    cli = Cli()
    assert kl.run_done_rulings([c], cli) == 0
    assert cli.calls == []


# --- a hand's unblock is a ruling (2026-09-21 pm) ---------------------------

def _parked(comments):
    return kl.Card("t_p", "a park", "body", "scheduled", "bash", "", 0, "ada",
                   comments=list(comments), comment_at=[100 + i for i in range(len(comments))])


def test_a_rulers_bare_unblock_lifts_the_park():
    """run_park re-read the latest ruling, so an unblock with no verdict left `park`
    on top and the loop re-parked the card 20 min later — her older word beating her
    own newer act."""
    c = _parked([("crissa", "park — waiting on the render lane"),
                 ("crissa", "UNBLOCK: putting it back in the column")])
    assert kl.park_ruling(c) is None
    assert kl.run_park([c], Cli()) == 0


def test_the_bare_cli_counts_as_a_hand():
    """`default` is a terminal: every gate script names its profile (the board gate
    and render-window run as `ada`)."""
    c = _parked([("crissa", "park — not now"), ("default", "UNBLOCK: releasing it")])
    assert kl.park_ruling(c) is None


def test_a_machines_unblock_does_not_lift_it():
    for who in ("ada", "steward", "kanban-board-gate", "render-window"):
        c = _parked([("crissa", "park — not now"), (who, "UNBLOCK: releasing a stuck ownerless batch")])
        assert kl.park_ruling(c) is not None, who


def test_an_explicit_word_still_wins():
    c = _parked([("crissa", "try — go"), ("crissa", "UNBLOCK: park — on second thoughts, not yet")])
    assert kl.park_ruling(c) is not None


def test_a_plain_comment_with_no_verdict_is_still_not_a_ruling():
    """Only the unblock AUDIT line counts — a chat line with no verdict never lifted
    a park and still does not."""
    c = _parked([("crissa", "park — not now"), ("crissa", "looking at this again tomorrow")])
    assert kl.park_ruling(c) is not None


def test_the_hand_unblock_kill_switch(monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_HAND_UNBLOCK", "0")
    c = _parked([("crissa", "park — not now"), ("crissa", "UNBLOCK: releasing it")])
    assert kl.park_ruling(c) is not None


# --- the gate keeps its own grammar -----------------------------------------

def test_a_gate_card_answered_done_is_re_asked_not_closed():
    """A gate asks "green this try?"; `done` is not an answer to it, and was not
    one before this verb existed either."""
    gate = _card(status="ready", body="Filed from note.\ngate: green\nDo not implement.\n",
                 comments=[("crissa", "done")])
    assert kl.is_gate(gate) is True
    assert kl.gate_answer(gate)[0] == ""
    cli = Cli()
    assert kl.run_done_rulings([gate], cli) == 0
    assert cli.calls == []


# --- a frame whose children are all resolved, one retired (2026-09-21 pm) ----

def _frame_board(tmp_path, kid_statuses):
    """A reframe card plus its decompose children, on a real sqlite board."""
    import sqlite3
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT, "
                "block_kind TEXT, consecutive_failures INTEGER, created_by TEXT, last_failure_error TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT)")
    con.execute("INSERT INTO tasks VALUES ('t_f','reframe: a thing','body','todo','ada','',0,'kanban-loop','')")
    for i, st in enumerate(kid_statuses):
        kid = f"t_k{i}"
        con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                    (kid, f"child {i}", "body", st, "bash", "", 0, "auto-decomposer", ""))
        con.execute("INSERT INTO task_events (task_id, kind, payload) VALUES (?,?,?)",
                    (kid, "created", '{"by": "auto-decomposer", "from_decompose_of": "t_f"}'))
    con.commit(); con.close()
    return p


def test_a_frame_with_a_retired_child_is_archived_not_completed(tmp_path):
    """Only `done` counts as delivered — the frame's `done` is what closes its SOURCE
    as "superseded … which is done". So a retired child must never produce one."""
    db = _frame_board(tmp_path, ["done", "done", "archived"])
    cli = Cli()
    assert kl.run_complete_frames(db, cli) == 1
    assert "archive" in cli.verbs() and "complete" not in cli.verbs()
    note = [x for x in cli.calls if x[0] == "comment"][0]
    assert kl.FRAME_RETIRED_MARK in note[2] and "t_k2" in note[2]


def test_all_children_done_still_completes(tmp_path):
    db = _frame_board(tmp_path, ["done", "done"])
    cli = Cli()
    assert kl.run_complete_frames(db, cli) == 1
    assert "complete" in cli.verbs() and "archive" not in cli.verbs()


def test_an_open_child_still_holds_the_frame(tmp_path):
    """One child still running: the frame waits, it is not retired."""
    db = _frame_board(tmp_path, ["done", "archived", "running"])
    cli = Cli()
    kl.run_complete_frames(db, cli)
    assert "archive" not in cli.verbs() and "complete" not in cli.verbs()
    assert "schedule" in cli.verbs()          # held, per the frame-hold rule


def test_the_frame_retire_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_FRAME_RETIRE", "0")
    db = _frame_board(tmp_path, ["done", "archived"])
    cli = Cli()
    kl.run_complete_frames(db, cli)
    assert "archive" not in cli.verbs()


def test_it_does_not_retire_twice(tmp_path):
    import sqlite3
    db = _frame_board(tmp_path, ["done", "archived"])
    con = sqlite3.connect(db)
    con.execute("INSERT INTO task_comments (task_id, author, body) VALUES (?,?,?)",
                ("t_f", "kanban-loop", kl.FRAME_RETIRED_MARK + " already retired"))
    con.commit(); con.close()
    cli = Cli()
    kl.run_complete_frames(db, cli)
    assert "archive" not in cli.verbs()
