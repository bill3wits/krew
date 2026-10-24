"""An announcer line is one plain sentence on what happened and who did it (the User 2026-09-30). Fake crew, tmp
board: nothing here calls a model or opens the live board."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours
import announce_rewrite as ar  # noqa: E402

ITEM = {"category": "stage", "title": "land, five red tests findings", "summary": "", "dedup_key": "stage:t_0a1b2c3d"}
OLD = "Staged. land, five red tests findings"


@pytest.fixture
def board(tmp_path):
    home = tmp_path / "kanban"
    (home / "boards" / "house").mkdir(parents=True)
    (home / "current").write_text("house\n")
    con = sqlite3.connect(home / "boards" / "house" / "kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, assignee TEXT, status TEXT, result TEXT)")
    con.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, profile TEXT, outcome TEXT)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.execute("INSERT INTO tasks VALUES ('t_0a1b2c3d', 'land, five red tests findings', 'bash', 'running', "
                "'Applying the five test fixes from the review.')")
    # 09-30 as it was: Steward landed the fix on t_9d3ed1dc; Ada's close card t_5a0c7d02 waited on it and cites it
    con.execute("INSERT INTO tasks VALUES ('t_9d3ed1dc', 'M&S land: hermes PyJWT', 'steward', 'done', 'landed')")
    con.execute("INSERT INTO tasks VALUES ('t_5a0c7d02', 'M&S Hermes security (HIGH)', 'ada', 'done', "
                "'Closed as applied. Fix landed in PyJWT by root land card t_9d3ed1dc (steward).')")
    con.execute("INSERT INTO task_links VALUES ('t_9d3ed1dc', 't_5a0c7d02')")
    con.executemany("INSERT INTO task_runs (task_id, profile, outcome) VALUES (?, ?, ?)", [
        ("t_9d3ed1dc", "steward", "timed_out"), ("t_9d3ed1dc", "steward", "completed"),
        ("t_5a0c7d02", "ada", "blocked"), ("t_5a0c7d02", "ada", "completed")])
    con.commit()
    con.close()
    return home


def test_a_card_title_becomes_one_plain_sentence_with_the_workers_name(board):
    seen = {}

    def post(payload):
        seen.update(json.loads(payload["messages"][1]["content"]))
        return "Bash is starting to apply the five test fixes from the review."
    said, how = ar.plain_line(ITEM, OLD, post=post, kanban_home=board)
    assert (said, how) == ("Bash is starting to apply the five test fixes from the review.", "plain")
    assert seen == {"event": "staged", "card title": "land, five red tests findings", "worker": "Bash",
                    "what the worker reported": "Applying the five test fixes from the review."}


def test_the_board_is_opened_read_only_and_a_missing_card_is_not_an_error(board, tmp_path):
    assert ar.card_of(ITEM, board)["assignee"] == "bash"
    assert ar.card_of({"dedup_key": "stage:t_ffffffff"}, board) == {"id": "t_ffffffff"}
    assert ar.card_of({"dedup_key": "txt:no card here"}, board) == {}
    assert ar.card_of(ITEM, tmp_path / "no-board") == {"id": "t_0a1b2c3d"}
    assert "mode=ro" in (ROOT / "crew" / "lib" / "announce_rewrite.py").read_text()
    assert "immutable" not in (ROOT / "crew" / "lib" / "announce_rewrite.py").read_text().replace("never ?immutable=1", "")


@pytest.mark.parametrize("reply", [
    "",                                                          # nothing
    "Done.",                                                     # too short to say anything
    "Bash finished t_0a1b2c3d.",                                 # a card id
    "Bash finished. Then he merged. Then he left.",              # three sentences
    "Bash finished the fixes and",                               # cut off mid-sentence
    "x" * 250 + ".",                                             # too long
])
def test_a_sentence_that_cannot_be_said_cleanly_says_the_old_line(board, reply):
    assert ar.plain_line(ITEM, OLD, post=lambda p: reply, kanban_home=board) == (OLD, "fallback:unsayable")


def test_a_crew_that_is_down_or_slow_says_the_old_line(board):
    def down(payload):
        raise TimeoutError("crew")
    assert ar.plain_line(ITEM, OLD, post=down, kanban_home=board) == (OLD, "fallback:TimeoutError")


def test_quotes_and_think_tags_are_stripped(board):
    said, _ = ar.plain_line(ITEM, OLD, post=lambda p: '"<think></think>Bash is starting the test fixes."', kanban_home=board)
    assert said == "Bash is starting the test fixes."


def test_the_kill_switch_says_the_old_line_without_asking(board, monkeypatch):
    monkeypatch.setattr(ar, "ON", False)
    assert ar.plain_line(ITEM, OLD, post=lambda p: pytest.fail("asked crew"), kanban_home=board) == (OLD, "fallback:off")


def test_the_prompt_asks_for_one_sentence_what_and_who():
    assert "ONE sentence" in ar.SYSTEM and "who" in ar.SYSTEM and "worker's name" in ar.SYSTEM
    assert "'land' in a title" in ar.SYSTEM and "'Chief-of-Staff' is Crissa" in ar.SYSTEM


def test_the_drain_says_the_plain_sentence_and_logs_the_card_title_beside_it(board, monkeypatch):
    spec = importlib.util.spec_from_file_location("drain", ROOT / "crew" / "scripts" / "krew-announce-drain.py")
    d = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(d)
    said = []
    monkeypatch.setattr(d, "announce", lambda text, **kw: said.append((text, kw)) or {"ok": True})
    monkeypatch.setattr(d.announce_rewrite, "plain_line",
                        lambda item, fallback: ("Bash is starting the test fixes.", "plain"))
    d._say(dict(ITEM))
    ((text, kw),) = said
    assert text == "Bash is starting the test fixes."
    assert kw["note"] == "plain · card: land, five red tests findings"


# ---------------------------------------------------------------- credit whoever did the work (the User 2026-09-30)
CLOSE = {"category": "completion", "title": "M S Hermes security, HIGH", "dedup_key": "completion:t_5a0c7d02"}


def test_a_close_card_credits_the_worker_who_did_the_fix_not_the_closer(board):
    """09-30: "Ada landed the Hermes security fix" - Steward landed it; Ada closed the finding."""
    seen = {}

    def post(payload):
        seen.update(json.loads(payload["messages"][1]["content"]))
        return "Steward landed the Hermes security fix."
    said, how = ar.plain_line(CLOSE, "Done. M S Hermes security, HIGH.", post=post, kanban_home=board)
    assert said == "Steward landed the Hermes security fix." and how == "plain"
    assert seen["worker"] == "Steward" and seen["closed by (do not credit)"] == "Ada"
    assert "never the one under 'closed by (do not credit)'" in ar.SYSTEM


def test_credit_reads_prerequisites_and_cited_cards_and_only_done_ones(board):
    con = sqlite3.connect(f"file:{board / 'boards' / 'house' / 'kanban.db'}?mode=ro", uri=True)
    try:
        assert ar.credit(con, "t_5a0c7d02", "") == (["steward"], "ada")                     # by the link alone
        assert ar.credit(con, "t_5a0c7d02", "see t_0a1b2c3d") == (["steward"], "ada")       # a citation adds nothing
        assert ar.credit(con, "t_9d3ed1dc", "landed") == (["steward"], "steward")            # its own completing run
        assert ar.credit(con, "t_0a1b2c3d", "") == (["bash"], "bash")                        # no runs: the assignee
    finally:
        con.close()


def test_stuck_and_staged_lines_name_the_current_worker(board):
    seen = {}

    def post(payload):
        seen.update(json.loads(payload["messages"][1]["content"]))
        return "Bash is starting to apply the five test fixes."
    ar.plain_line(ITEM, OLD, post=post, kanban_home=board)
    assert seen["worker"] == "Bash" and "closed by (do not credit)" not in seen


def test_a_card_the_result_only_cites_is_not_credit(board):
    """09-30 15:43: a steward sweep that listed the cards it looked at was announced as "Bash finished a board sweep"."""
    db = board / "boards" / "house" / "kanban.db"
    con = sqlite3.connect(db)
    con.execute("INSERT INTO tasks VALUES ('t_5eeeeeee', 'steward sweep: blocked cards', 'steward', 'done', "
                "'Unblocked the stale mitigation card t_9d3ed1dc for verification.')")
    con.execute("INSERT INTO task_runs (task_id, profile, outcome) VALUES ('t_5eeeeeee', 'steward', 'completed')")
    con.commit()
    con.close()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert ar.credit(con, "t_5eeeeeee", "Unblocked the stale mitigation card t_9d3ed1dc.") == (["steward"], "steward")
    finally:
        con.close()
