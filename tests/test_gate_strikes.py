"""lib/gate_strikes — the reframe discipline, for patrols instead of cards.

The User, 2026-09-22: "why would anything fail 60 or 70 times?" Because a gate re-derives its
verdict every tick with no memory of having already reached it. Cards have four rungs; patrols
had none.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))
import gate_strikes as gs  # noqa: E402


def test_the_same_verdict_counts_up_and_goes_quiet_at_three():
    st = {}
    assert [gs.strike(st, "liveness", "t_a", "overdue") for _ in range(4)] == [1, 2, 3, 4]
    assert gs.quiet(st, "liveness", "t_a", "overdue") is True


def test_a_changed_verdict_resets_the_count():
    """The point is repetition of the same THING. A patrol saying something new has not
    learned that it is stuck."""
    st = {}
    for _ in range(5):
        gs.strike(st, "liveness", "t_a", "overdue")
    assert gs.strike(st, "liveness", "t_a", "no reader") == 1
    assert gs.quiet(st, "liveness", "t_a", "no reader") is False
    assert gs.quiet(st, "liveness", "t_a", "overdue") is False      # the old one is gone too


def test_subjects_and_gates_do_not_bleed_into_each_other():
    st = {}
    gs.strike(st, "liveness", "t_a", "overdue")
    gs.strike(st, "liveness", "t_b", "overdue")
    gs.strike(st, "caps", "t_a", "overdue")
    assert gs.seen(st, "liveness", "t_a") == 1 and gs.seen(st, "liveness", "t_b") == 1
    assert gs.seen(st, "caps", "t_a") == 1
    assert gs.seen(st, "liveness", "t_missing") == 0


def test_a_subject_that_leaves_is_forgotten_so_it_can_escalate_again():
    """Self-cleaning, like gate_salad's `left` map: a card that stops being overdue and later
    becomes overdue again is a NEW fact, not a continuation."""
    st = {}
    for _ in range(3):
        gs.strike(st, "liveness", "t_a", "overdue")
    gs.strike(st, "liveness", "t_b", "overdue")
    assert gs.prune(st, "liveness", ["t_b"]) == 1
    assert gs.seen(st, "liveness", "t_a") == 0
    assert gs.seen(st, "liveness", "t_b") == 1              # still live, untouched


def test_prune_leaves_other_gates_alone():
    st = {}
    gs.strike(st, "liveness", "t_a", "overdue")
    gs.strike(st, "orphans", "t_a", "would term")
    gs.prune(st, "liveness", [])
    assert gs.seen(st, "liveness", "t_a") == 0 and gs.seen(st, "orphans", "t_a") == 1


def test_a_stale_verdict_is_forgotten_by_time(monkeypatch):
    """Without this a one-off from last week suppresses a real repeat today."""
    st = {}
    gs.strike(st, "liveness", "t_a", "overdue", now=1000.0)
    gs.strike(st, "liveness", "t_a", "overdue", now=1000.0 + gs.FORGET_S + 1)
    assert gs.seen(st, "liveness", "t_a") == 1              # reset, not 2
    st2 = {}
    gs.strike(st2, "liveness", "t_a", "overdue", now=1000.0)
    assert gs.prune(st2, "liveness", ["t_a"], now=1000.0 + gs.FORGET_S + 1) == 1


def test_it_round_trips_through_the_file(tmp_path):
    p = tmp_path / "strikes.json"
    st = gs.load(p)
    assert st == {}                                          # absent file is empty, never raises
    gs.strike(st, "liveness", "t_a", "overdue")
    gs.strike(st, "liveness", "t_a", "overdue")
    assert gs.save(st, p) is True
    assert gs.seen(gs.load(p), "liveness", "t_a") == 2


def test_an_unreadable_store_starts_empty_rather_than_raising(tmp_path):
    p = tmp_path / "strikes.json"
    p.write_text("{not json")
    assert gs.load(p) == {}
    p.write_text('["a list, not a dict"]')
    assert gs.load(p) == {}


def test_a_write_failure_is_REPORTED_not_swallowed(tmp_path):
    """A counter that silently reads back 0 never reaches 3 and never escalates — a latch with
    no symptom, which is the exact bug this module exists to end. save() returns False so the
    caller can say so out loud."""
    p = tmp_path / "nodir" / "deeper"
    p.mkdir(parents=True)
    assert gs.save({"x": {"n": 1}}, p) is False              # a directory is not writable as a file
