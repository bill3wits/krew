"""lib/kanban_cause — a failure is classified before anything acts on it.

Principle 2 of docs/KANBAN-SELF-HEALING-20260918.md. The READERS shipped on 09-18
(lib/kanban_kpis counts three `cause:` prefixes, lib/kanban_liveness branches on
`cause: verb`) and nothing ever wrote one, so the KPIs read 0 while 76.2 % of 496 failed
runs over the three days to 2026-09-22 were harness faults nobody classified.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import kanban_cause as kc  # noqa: E402


def test_a_refused_spawn_is_the_harness_not_the_card():
    """204 of 496 runs in three days. The worker never existed; the card did nothing."""
    c = kc.classify("spawn_failed", "spawn aborted: bash kit has 55 tools, pre-spawn cap is 45")
    assert c.cause == kc.HARNESS and "before the worker started" in c.why
    # and the same text on a `gave_up` row — the breaker's own record of the same fault
    assert kc.classify("gave_up", "spawn aborted: steward kit has 49 tools").cause == kc.HARNESS


def test_the_turn_budget_is_harness():
    """Fable lists `budget` as harness: the card was never finished OR failed, so there is
    nothing in it to reframe. 109 of 496."""
    assert kc.classify("timed_out", "Iteration budget exhausted (80/80) — task could not complete").cause == kc.HARNESS
    assert kc.classify("gave_up", "Iteration budget exhausted (40/40)").cause == kc.HARNESS


def test_a_dead_process_and_an_oom_are_harness():
    assert kc.classify("crashed", "pid 999001 not alive").cause == kc.HARNESS
    assert kc.classify("crashed", "Killed process 2198782 (hermes)").cause == kc.HARNESS
    assert kc.classify("crashed", "MemoryError").cause == kc.HARNESS


def test_an_unloadable_skill_pin_is_harness():
    """It aborts at preload, before any tool call — the harness refusing, not the card."""
    assert kc.classify("spawn_failed", "Unknown skill(s): bash-map").cause == kc.HARNESS


def test_the_verb_class_is_work_done_and_not_said():
    c = kc.classify("crashed", "worker exited cleanly (rc=0) without calling kanban_complete or kanban_block")
    assert c.cause == kc.VERB
    assert kc.classify("crashed", "worker exited cleanly (rc=0) with no terminal kanban call").cause == kc.VERB


def test_a_real_failure_is_real():
    c = kc.classify("crashed", "KeyError: 'head' in pose_intake.finish")
    assert c.cause == kc.REAL and c.why == "the work itself failed"


def test_a_bare_exit_is_real_but_does_not_claim_the_work_failed():
    """78 of 496 are 'pid NNNN exited with code 1' — a number and no story. Still `real`,
    because a REPEAT of it should earn a reframe, but the line must not assert a cause the
    row does not carry (09-21: 338 reframe cuts, 142 of them mechanical)."""
    c = kc.classify("crashed", "pid 1818532 exited with code 1")
    assert c.cause == kc.REAL and "no reason recorded" in c.why


def test_silence_is_never_classified_as_real():
    """`real` is the class that earns a reframe. Inventing one from an empty row is how a
    card gets rewritten for a fault it never had."""
    assert kc.classify("crashed", "") is None
    assert kc.classify("crashed", None, None) is None
    # except spawn_failed, which is self-evidencing: the spawn IS the thing that failed
    assert kc.classify("spawn_failed", "").cause == kc.HARNESS


def test_a_success_is_not_a_failure():
    for ok in ("completed", "blocked", "reclaimed", None, ""):
        assert kc.classify(ok, "anything at all") is None


def test_the_comment_is_a_grammar_the_readers_parse():
    """lib/kanban_kpis counts `cause: harness%` / `verb%` / `real%` with a LIKE, and
    lib/kanban_liveness startswith('cause: verb'). The prefix is load-bearing."""
    for cause in (kc.HARNESS, kc.VERB, kc.REAL):
        body = kc.comment(kc.Cause(cause, "because"), 12)
        assert body.startswith(f"cause: {cause} ")
    assert kc.comment(kc.Cause(kc.HARNESS, "x"), 2377, runs=28).endswith("(run 2377, 28 runs).")
    assert kc.comment(kc.Cause(kc.HARNESS, "x"), 2382).endswith("(run 2382).")
    assert kc.comment(kc.Cause(kc.HARNESS, "x")).endswith("x.")


# --- the per-card breaker's key (the User 09-28) ------------------------------------------------

def test_run_key_repeats_when_the_ending_repeats():
    a = kc.run_key("blocked", "", "terminal-guarantee: worker exited rc=1 without landing a terminal "
                                  "kanban call; run log: ~/x/2975.log")
    b = kc.run_key("blocked", "", "terminal-guarantee: worker exited rc=2 without landing a terminal "
                                  "kanban call; run log: ~/x/2983.log")
    assert a == b and a.startswith("blocked — terminal-guarantee")
    assert kc.run_key("crashed", "Iteration budget exhausted (80/80)") == "harness — the turn budget ran out mid-task"
    assert kc.run_key("crashed", "") == "crashed — no reason recorded"
    assert kc.run_key("completed") is None and kc.run_key("scheduled") is None


def test_designed_waits_and_questions_are_not_loops():
    assert kc.run_key("blocked", "", "render window: waits for the off-hours render slot") is None
    assert kc.run_key("blocked", "", "Insufficient GPU memory: 900 MiB free of 32000 MiB") is None
    assert kc.run_key("blocked", "", "Crew engine relapse: go | wait | later HH:MM | park | hands?") is None


def test_trips_needs_the_threshold_inside_the_window():
    now = 100_000.0
    rows = [(i, "t_a", "timed_out", "Iteration budget exhausted (80/80)", "", now - 60 * i) for i in (1, 2, 3)]
    rows += [(9, "t_b", "timed_out", "Iteration budget exhausted (80/80)", "", now - 60),
             (10, "t_b", "timed_out", "Iteration budget exhausted (80/80)", "", now - 7 * 3600)]
    rows += [(11, "t_c", "timed_out", "Iteration budget exhausted", "", now - 60),
             (12, "t_c", "crashed", "pid 5 not alive", "", now - 120),
             (13, "t_c", "timed_out", "Iteration budget exhausted", "", now - 180)]
    out = kc.trips(rows, now=now)
    assert [(t.task_id, t.runs, t.newest_run) for t in out] == [("t_a", 3, 3)]
    assert kc.breaker_line(out[0], 6).startswith("breaker: 3 runs in 6 h ended the same way — harness")
