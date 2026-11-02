"""Self-loop step 4 (the User 09-28): the lessons, handed to Scout, the voters and team-improve cards."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours

import idea_ledger as L  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


out = _load("outcomes_step4", "crew/scripts/improvement-outcomes.py")
rank = _load("rank_step4", "crew/scripts/team-eval-rank.py")
DAY = "2026-09-27"


def _state():
    return {
        "repo:vercel-labs/agent-browser": {"key": "repo:vercel-labs/agent-browser", "debated_days": ["2026-09-20", "2026-09-27"],
                                           "agreed_days": [], "top_days": [], "task": None, "best_sum": 2, "text": "x"},
        "repo:mksglu/context-mode": {"key": "repo:mksglu/context-mode", "debated_days": ["2026-09-24", "2026-09-26"],
                                     "agreed_days": ["2026-09-17"], "top_days": [], "task": None, "best_sum": 9},
        "team-improve:drain-the-ready-lane": {"key": "team-improve:drain-the-ready-lane", "text": "team-improve: drain the ready lane",
                                              "debated_days": ["2026-09-25", "2026-09-26"], "agreed_days": [], "top_days": [],
                                              "task": None, "best_sum": 3},
        "repo:old/one": {"key": "repo:old/one", "debated_days": ["2026-09-01", "2026-09-02"], "agreed_days": [],
                         "top_days": [], "task": None, "best_sum": 1},
        "repo:once/only": {"key": "repo:once/only", "debated_days": ["2026-09-27"], "agreed_days": [], "top_days": [],
                           "task": None, "best_sum": 1},
    }


ROWS = [
    {"item": "kit", "stage": "landed", "landed_at": 2.0, "sha": "abc123", "review": "review not armed",
     "kpi_id": "ops_tok_s", "measure": "better", "confounded": ["hermes update"]},
    {"item": "x", "stage": "closed, not landed (no commit found)"},
    {"item": "y", "stage": "closed, not landed (review FAIL)"},
]


def test_the_cooldown_is_bounded_and_honest():
    text = out.lessons(DAY, ROWS, _state())
    assert "vercel-labs/agent-browser — 2 nights" in text
    assert "house change - team-improve - drain the ready lane" in text
    assert "context-mode" not in text, "an agreed idea is not in cooldown"
    assert "old/one" not in text and "once/only" not in text, "outside the window / one night is not a cooldown"
    assert "better but confounded (not counted)" in text
    assert "1 no commit found, 1 review FAIL" in text


def test_no_lessons_line_is_an_item_to_any_parser(tmp_path):
    lessons = tmp_path / f"lessons-{DAY}.md"
    lessons.write_text(out.lessons(DAY, ROWS, _state()))
    assert not re.search(r"(nnt-pick|nnt-agreed|team-improve|architect-try|architect-watch|team-source|idea)\s*:",
                         lessons.read_text(), re.I)
    note = tmp_path / f"team-research-{DAY}.md"
    base = ("# team-research\n\n## NNT repos\n- `nnt-pick: vercel-labs/agent-browser` — url — ops — x\n\n"
            "## Proposed cards\n- `team-improve: drain the ready lane (bucket=improve-ops)` — why\n")
    note.write_text(base)
    before_rank = rank.items_from_research(base)
    before_ledger = L.parse_note(base)
    assert L.attach_lessons(lessons, note, "for the voters")
    after = note.read_text()
    assert rank.items_from_research(after) == before_rank
    assert L.parse_note(after) == before_ledger
    fl = _load("filer_step4", "crew/scripts/steward-file-architect-cards.py")
    assert fl.parse_cards(after) == fl.parse_cards(base)


def test_attach_is_last_and_replaces_its_own_copy(tmp_path):
    lessons = tmp_path / "lessons-x.md"
    lessons.write_text("# Lessons\n\n## Cooldown (1)\n\n- a/b — 2 nights\n")
    t = tmp_path / "team.md"
    t.write_text("# team\n\n## NNT repos\n- one\n")
    L.attach_lessons(lessons, t, "for Scout")
    lessons.write_text("# Lessons\n\n## Cooldown (1)\n\n- c/d — 3 nights\n")
    L.attach_lessons(lessons, t, "for Scout")
    text = t.read_text()
    assert text.count("## House lessons") == 1 and "c/d" in text and "a/b" not in text
    assert text.index("## NNT repos") < text.index("## House lessons")
    assert not L.attach_lessons(tmp_path / "missing.md", t, "x")


def test_the_lessons_run_writes_its_file_and_status_line(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_JOB_STATUS_DIR", str(tmp_path / "st"))
    monkeypatch.setattr(out, "IMPROVE", tmp_path)
    monkeypatch.setattr(L, "LEDGER", tmp_path / "ideas.jsonl")
    assert out.lessons_main(DAY, ROWS) == 0
    assert (tmp_path / f"lessons-{DAY}.md").read_text().startswith(f"# Lessons — {DAY}")
    st = json.loads((tmp_path / "st" / f"outcomes-lessons-{DAY}.json").read_text())
    assert st["status"] == "ok"
    assert not (tmp_path / "outcomes.json").exists(), "run #1 never writes the Daily's snapshot"


def test_the_schedule_and_the_runner_hand_the_lessons_over():
    sched = {j["name"]: j for j in json.loads((ROOT / "config/window-schedule.example.json").read_text())["jobs"]}
    assert sched["outcomes-lessons"]["after"] == ["ms-nightly"] and sched["outcomes-lessons"]["status_line"] == "outcomes-lessons"
    assert "outcomes-lessons" in sched["team-research"]["after"]
    assert "--lessons" in (ROOT / "crew/units/krew-improvement-lessons.service").read_text()
    sh = (ROOT / "crew/scripts/steward-architect-run.sh").read_text()
    assert sh.count("attach-lessons") == 2 and "/team.md" in sh and '"$NOTE"' in sh
    watch = (ROOT / "crew/scripts/crissa-needs-watch.py").read_text()
    assert "lessons" not in watch.lower(), "never into her Decide prompt"


def test_a_team_improve_card_carries_its_own_memory(tmp_path, monkeypatch):
    fl = _load("filer_step4b", "crew/scripts/steward-file-architect-cards.py")
    monkeypatch.setenv("KREW_DAY", DAY)
    monkeypatch.setattr(fl, "SEEN", tmp_path / "seen.jsonl")
    monkeypatch.setattr(fl, "RANK_DIR", tmp_path)
    L.record([{"key": "repo:tencent/weknora", "event": "debated", "data": {"sum": 4}},
              {"key": "repo:tencent/weknora", "event": "seen"}], "2026-09-25", "rank", path=tmp_path / "ideas.jsonl")
    (tmp_path / f"lessons-{DAY}.md").write_text(out.lessons(DAY, ROWS, _state()))
    mem = fl.idea_memory("team-improve", "tencent/weknora — kpi=ops_tok_s — 3 of 3")
    assert mem.startswith("house memory: debated 1 night(s) (2026-09-25..2026-09-25), best sum 4")
    assert "last verdicts (lessons):" in mem and "kit — landed abc123" in mem
