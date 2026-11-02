"""`archive` means archive, not park (2026-09-21).

Until today `verdict_of` mapped park | no | drop | archive all to ONE verdict,
`park` — and a park is sticky in `scheduled`, which nothing ever retires (day-close
reads `done` only). So the single word that means "retire this card" was the word
that made it immortal. Ten cards on the 09-16 standing board were in that state.
"""
from __future__ import annotations

import importlib.util
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_loop_av", ROOT / "crew" / "scripts" / "kanban-loop.py")
kl = importlib.util.module_from_spec(SPEC)
sys.modules["kanban_loop_av"] = kl   # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(kl)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _loop_paths_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the module's import-time paths stay in tmp, never the real home (tests/krew_home_guard.py)."""
    keep_in_tmp(kl, tmp_path, monkeypatch)


def _card(status="todo", comments=(), **kw):
    return kl.Card("t_1", "a title", "body", status, "bash", "", 0, "ada",
                   comments=list(comments), comment_at=[100] * len(comments), **kw)


class Cli:
    """Records calls; never the live board."""
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, *args, **kw):
        self.calls.append(tuple(str(a) for a in args))
        return self.rc, ""

    def verbs(self):
        return [c[0] for c in self.calls]


# --- the verdict itself -----------------------------------------------------

@pytest.mark.parametrize("word", ["archive", "drop", "Archive — superseded", "DROP it"])
def test_archive_and_drop_are_their_own_verdict(word):
    assert kl.verdict_of(word)[0] == "archive"


@pytest.mark.parametrize("word", ["park", "no", "park — superseded by the hard-stop lane"])
def test_park_is_still_park(word):
    assert kl.verdict_of(word)[0] == "park"


def test_unblock_prefix_still_read(word=None):
    assert kl.verdict_of("UNBLOCK: archive — nothing left to do")[0] == "archive"


@pytest.mark.parametrize("word,expect", [("try", "try"), ("go", "try"), ("hands", "hands"), ("", "")])
def test_the_other_verdicts_are_untouched(word, expect):
    assert kl.verdict_of(word)[0] == expect


def test_kill_switch_restores_the_old_alias(monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_ARCHIVE_VERB", "0")
    assert kl.verdict_of("archive — superseded")[0] == "park"
    assert kl.archive_ruling(_card(comments=[("crissa", "archive — superseded")])) is None


# --- a ruler's archive retires the card -------------------------------------

def test_a_rulers_archive_is_read_as_an_archive_ruling():
    c = _card(comments=[("crissa", "archive — superseded, the hard-stop lane landed")])
    ar = kl.archive_ruling(c)
    assert ar is not None and ar[1] == "crissa"
    assert kl.park_ruling(c) is None          # and NOT as a park


def test_run_archive_rulings_archives_and_says_why():
    c = _card(comments=[("crissa", "archive — superseded")])
    cli = Cli()
    assert kl.run_archive_rulings([c], cli) == 1
    assert "archive" in cli.verbs()
    note = [x for x in cli.calls if x[0] == "comment"][0]
    assert kl.ARCHIVE_MARK in note[2] and "crissa" in note[2]
    assert c.status == "archived"


def test_it_does_not_archive_twice():
    c = _card(comments=[("crissa", "archive — superseded"),
                        ("kanban-loop", kl.ARCHIVE_MARK + " crissa ruled archive")])
    cli = Cli()
    assert kl.run_archive_rulings([c], cli) == 0
    assert cli.calls == []


def test_a_park_is_still_parked_not_archived():
    c = _card(comments=[("crissa", "park — waiting on the render lane")])
    cli = Cli()
    assert kl.run_archive_rulings([c], cli) == 0
    assert cli.calls == []


def test_a_later_try_outranks_an_earlier_archive():
    c = _card(comments=[("crissa", "archive — superseded"), ("crissa", "try — new evidence")])
    assert kl.archive_ruling(c) is None
    cli = Cli()
    assert kl.run_archive_rulings([c], cli) == 0


def test_a_non_ruler_cannot_archive_a_card_here():
    """steward's VERB: archive is the board gate's business, not a ruling."""
    c = _card(comments=[("steward", "VERB: archive — superseded; nothing left to do")])
    assert kl.archive_ruling(c) is None


def test_an_archive_ruled_card_is_not_re_decomposed_while_it_waits():
    assert kl.is_parked(_card(comments=[("crissa", "archive — superseded")])) is True


def test_done_and_archived_cards_are_skipped():
    cli = Cli()
    for st in ("done", "archived"):
        assert kl.run_archive_rulings([_card(status=st, comments=[("crissa", "archive")])], cli) == 0
    assert cli.calls == []


def test_a_refused_archive_is_not_counted():
    c = _card(comments=[("crissa", "archive — superseded")])
    assert kl.run_archive_rulings([c], Cli(rc=1)) == 0
    assert c.status != "archived"


# --- a dated park keeps its alarm (2026-09-21) ------------------------------

from datetime import date


def _parked(comments, status="scheduled"):
    return kl.Card("t_p", "a park", "body", status, "bash", "", 0, "ada",
                   comments=list(comments), comment_at=[100 + i for i in range(len(comments))])


def test_a_dated_park_comes_back_when_the_date_arrives():
    """t_efb37f06's shape: routed to `scheduled` with "later <date>" as its ruling."""
    c = _parked([("crissa", "later 2026-09-22 — Q3 report spec not on the card yet")])
    assert kl.parked_later_date(c) == date(2026, 9, 22)
    cli = Cli()
    assert kl.run_later([c], cli, today=date(2026, 9, 22)) == 1
    assert "unblock" in cli.verbs() and "block" in cli.verbs()


def test_it_waits_until_the_date():
    c = _parked([("crissa", "later 2026-09-22 — not yet")])
    cli = Cli()
    assert kl.run_later([c], cli, today=date(2026, 9, 21)) == 0
    assert cli.calls == []


def test_a_plain_park_is_never_released_by_this():
    c = _parked([("crissa", "park — superseded")])
    assert kl.parked_later_date(c) is None
    assert kl.run_later([c], Cli(), today=date(2026, 12, 1)) == 0


def test_a_park_ruled_over_an_older_later_is_not_due():
    """The trailing ruling wins: parked today over last week's `later` is a PARK."""
    c = _parked([("crissa", "later 2026-09-01 — wait for the bench"),
                 ("crissa", "park — superseded, dropping it")])
    assert kl.parked_later_date(c) is None
    assert kl.run_later([c], Cli(), today=date(2026, 12, 1)) == 0


def test_a_later_ruled_over_an_older_park_is_due():
    c = _parked([("crissa", "park — not now"),
                 ("crissa", "later 2026-09-22 — revisit after the render window")])
    assert kl.parked_later_date(c) == date(2026, 9, 22)
    assert kl.run_later([c], Cli(), today=date(2026, 9, 23)) == 1


def test_the_blocked_dependency_path_still_works():
    c = kl.Card("t_d", "dep", "body", "blocked", "bash", "dependency", 0, "ada",
                comments=[("crissa", "later 2026-09-22 — waiting")], comment_at=[100])
    assert kl.run_later([c], Cli(), today=date(2026, 9, 22)) == 1


def test_kill_switch_leaves_parks_alone(monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_LATER_PARKS", "0")
    c = _parked([("crissa", "later 2026-09-22 — revisit")])
    assert kl.run_later([c], Cli(), today=date(2026, 12, 1)) == 0
