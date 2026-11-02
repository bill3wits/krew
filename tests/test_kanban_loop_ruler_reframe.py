"""A ruler's `frame:` is an answer, not a pending question (2026-09-21).

Her ruling verbs are try | park | later | archive | hands. None of them says "wrong
question", so a mis-framed card had only `try` (run it again) or `park` (bury it).
t_e2d5f2a3 took four passes that drifted try/try/try/park/park before anyone found
the premise false. The tool already existed — `worker_frame` reads a ruler's `frame:`
and `run_reframe` cuts from it — but `reframe_candidates` skipped anything
`needs_input` as "already with Crissa", which a frame FROM Crissa disproves.
"""
from __future__ import annotations

import importlib.util
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("kanban_loop_rr", ROOT / "crew" / "scripts" / "kanban-loop.py")
kl = importlib.util.module_from_spec(SPEC)
sys.modules["kanban_loop_rr"] = kl
SPEC.loader.exec_module(kl)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _loop_paths_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the module's import-time paths stay in tmp, never the real home (tests/krew_home_guard.py)."""
    keep_in_tmp(kl, tmp_path, monkeypatch)

FRAME = ("frame: the premise is false — the hard-stop patch already landed (t_9b4416ae, "
         "Vera approved). This is the fourth try of a replaced design.")


def _card(status="blocked", kind="needs_input", comments=(), assignee="bash", cf=0):
    return kl.Card("t_1", "a title", "body", status, assignee, kind, cf, "ada",
                   comments=list(comments), comment_at=[100 + i for i in range(len(comments))])


def test_a_rulers_frame_is_read_as_hers():
    assert kl.ruler_frame(_card(comments=[("crissa", FRAME)])) == FRAME
    assert kl.ruler_frame(_card(comments=[("user", FRAME)])) == FRAME


def test_a_workers_frame_is_not_a_rulers():
    assert kl.ruler_frame(_card(comments=[("bash", FRAME)])) == ""


def test_the_loops_own_frame_is_not_a_rulers():
    assert kl.ruler_frame(_card(comments=[("kanban-loop", FRAME)])) == ""


def test_her_frame_makes_a_needs_input_card_reframable():
    """THE FIX: the needs_input skip means 'still with Crissa'; her frame disproves it."""
    c = _card(comments=[("crissa", FRAME)])
    assert c in kl.reframe_candidates([c], [])


def test_a_needs_input_card_without_her_frame_is_still_skipped():
    c = _card(comments=[("ada", "VERB: archive — superseded")])
    assert kl.reframe_candidates([c], []) == []


def test_a_parked_card_is_never_reframed_even_framed():
    """A Park ruling stays sticky — that rail is older and still holds."""
    c = _card(status="scheduled", comments=[("crissa", "park — superseded"), ("crissa", FRAME)])
    assert kl.reframe_candidates([c], []) == []


def test_capability_and_done_are_untouched():
    assert kl.reframe_candidates([_card(kind="capability", comments=[("crissa", FRAME)])], []) == []
    assert kl.reframe_candidates([_card(status="done", comments=[("crissa", FRAME)])], []) == []


def test_kill_switch_restores_the_old_skip(monkeypatch):
    monkeypatch.setenv("KANBAN_LOOP_RULER_REFRAME", "0")
    c = _card(comments=[("crissa", FRAME)])
    assert kl.reframe_candidates([c], []) == []


def test_the_ordinary_repeated_failure_path_still_works():
    c = _card(kind="", cf=kl.REFRAME_AT)
    assert c in kl.reframe_candidates([c], [])


def test_a_worker_framed_blocked_card_still_qualifies():
    """The krew's existing route — steward comments a frame:, the loop cuts."""
    c = _card(kind="", assignee="steward", comments=[("steward", FRAME)])
    assert c in kl.reframe_candidates([c], [])
