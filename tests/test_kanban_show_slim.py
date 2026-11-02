"""kanban_show says everything twice; say it once (the User 2026-09-30)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import kanban_show_slim as ks

ROOT = Path(__file__).resolve().parents[1]
BODY = "Goal: implement the validation middleware. " * 8
C_SHORT = "sweep 2026-09-30: this crash cluster is stale, the work already landed."
C_LONG = "A very long review. " * 200                      # over the context's 2K per-comment cap
CTX = (f"# Kanban task t_aaaa1111: middleware\n\nAssignee: bash\n\n## Body\n{BODY}\n\n"
       "## Prior attempts on this task\n### Attempt 1 — crashed\nerror text\n\n"
       "## Recent work by @bash\n- t_bbbb2222 done: something unrelated\n- t_cccc3333 done: another\n\n"
       f"## Comment thread\ncomment from worker `steward`:\n{C_SHORT}\n\ncomment from `vera`:\n{C_LONG[:2048]}… [truncated]\n")


def _card(**over):
    d = {"task": {"id": "t_aaaa1111", "title": "middleware", "body": BODY, "status": "blocked"},
         "parents": [], "children": [], "unsatisfied_parents": [],
         "comments": [{"author": "steward", "body": C_SHORT, "created_at": 1}, {"author": "vera", "body": C_LONG, "created_at": 2}],
         "events": [{"kind": "commented", "payload": {"body": C_LONG}, "created_at": i, "run_id": None} for i in range(20)],
         "runs": [{"id": i, "status": "crashed", "summary": "s" * 900, "error": None} for i in range(6)],
         "worker_context": CTX}
    d.update(over)
    return d


def test_what_the_context_already_says_is_folded_and_counted():
    out = json.loads(ks.slim(json.dumps(_card()), own_task="t_aaaa1111"))
    assert out["worker_context"] == CTX, "his own card: the context is whole"
    assert out["task"]["body"] == ks.POINTER and out["task"]["title"] == "middleware"
    assert [c["author"] for c in out["comments"]] == ["vera"], "a comment the context TRUNCATED is kept in full"
    assert out["comments"][0]["body"] == C_LONG
    assert len(out["events"]) == ks.EVENTS_KEPT and out["events"][-1]["created_at"] == 19
    assert len(json.dumps(out["events"][0]["payload"])) < 400
    assert len(out["runs"]) == ks.RUNS_KEPT and len(out["runs"][0]["summary"]) < 340
    assert out["folded_into_worker_context"] == {"body": len(BODY), "comments_in_context": 1, "older_events": 12, "older_runs": 3}


def test_a_reader_of_another_card_is_not_handed_the_assignees_recent_work():
    text = ks.slim(json.dumps(_card()), own_task="t_zzzz9999")
    out = json.loads(text)
    assert "## Recent work by @bash" not in out["worker_context"]
    for kept in ("## Body", "## Prior attempts on this task", "## Comment thread", C_SHORT):
        assert kept in out["worker_context"], kept
    assert out["folded_into_worker_context"]["recent_work_section_chars"] > 50
    assert "## Recent work by @bash" not in json.loads(ks.slim(json.dumps(_card())))["worker_context"], "no task: a reader"


def test_anything_that_is_not_a_card_with_a_context_is_left_alone():
    assert ks.slim('{"error": "no such task"}') is None
    assert ks.slim("[kanban_show] (23,771 chars result)") is None
    assert ks.slim(json.dumps(_card(worker_context=""))) is None
    assert ks.slim(json.dumps({"task": {"id": "t_aaaa1111"}, "worker_context": "short"}), own_task="t_aaaa1111") is None, "nothing to fold: untouched"
    assert ks.slim(None) is None


def test_a_body_or_comment_the_context_does_not_hold_whole_stays():
    card = _card(task={"id": "t_aaaa1111", "body": BODY + " and a tail the context never saw"})
    out = json.loads(ks.slim(json.dumps(card), own_task="t_aaaa1111"))
    assert out["task"]["body"].endswith("never saw")
    assert "body" not in out["folded_into_worker_context"]


def test_the_hook_is_wired_in_the_plugin_every_crew_profile_loads(monkeypatch):
    spec = importlib.util.spec_from_file_location("team_crissa_slim", ROOT / "plugins" / "team-crissa" / "__init__.py")
    tz = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tz)
    hooks = {}

    class Ctx:
        profile_name = "steward"

        def register_tool(self, *a, **k):
            return None

        def register_hook(self, name, fn):
            hooks.setdefault(name, []).append(fn)

        def __getattr__(self, item):
            return lambda *a, **k: None

    monkeypatch.setenv("KREW_TEAM_BOARD_FROM_KREW_BOARD", "0")   # krew: else krew-board registers the same hook
    tz.register(Ctx())
    (hook,) = hooks["transform_tool_result"]
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_aaaa1111")
    raw = json.dumps(_card())
    out = hook(tool_name="kanban_show", args={"task_id": "t_aaaa1111"}, result=raw, duration_ms=3)
    assert isinstance(out, str) and len(out) < len(raw) and json.loads(out)["worker_context"] == CTX
    assert hook(tool_name="read_file", result=raw) is None, "only kanban_show"
    assert hook(tool_name="kanban_show", result='{"error": "x"}') is None
    monkeypatch.setenv("KREW_KANBAN_SHOW_SLIM", "0")
    assert hook(tool_name="kanban_show", result=raw) is None, "the kill switch leaves the result whole"
