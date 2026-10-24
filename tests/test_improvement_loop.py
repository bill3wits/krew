"""The improvement loop, closed (the User 2026-09-17: tie improvements to the team's KPIs;
"ignite that loop now. Ensure the source is working").

NNT repos come from the video descriptions → the scout picks repos → roles score and
name a KPI → the ranker carries it → the filer records KPI + baseline → the outcomes
step measures the KPI after the try lands."""
from __future__ import annotations

import importlib.util

import pytest
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


nnt = _load("nnt_repos", "crew/scripts/nnt-repos.py")
rank = _load("team_eval_rank", "crew/scripts/team-eval-rank.py")
filer = _load("steward_file_cards", "crew/scripts/steward-file-architect-cards.py")
out = _load("improvement_outcomes", "crew/scripts/improvement-outcomes.py")


@pytest.fixture(autouse=True)
def _landed_by_rule(monkeypatch):
    """These tests measure a landed try. Self-loop step 3's landing rule (a review word + a
    commit joined by rule) and its board-wide KPI rule have their own tests
    (tests/test_self_loop_step3.py); here every closed try has its commit."""
    monkeypatch.setattr(out, "commit_for", lambda card, *a, **k: "abc1234")
    monkeypatch.setattr(out, "review_armed", lambda: False)
    monkeypatch.setattr(out, "BOARD_WIDE_UNMEASURED", False)

FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
 <entry><title>Why is everyone using these?</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=ehab5PtgRo8"/>
  <published>2026-09-16T12:00:00+00:00</published>
  <media:group><media:description>Skills: https://github.com/anthropics/skills and
   https://github.com/vercel-labs/agent-browser. Sponsor https://github.com/sponsors/x
   again https://github.com/anthropics/skills.</media:description></media:group></entry>
 <entry><title>Amazing: FREE AI Agent from Meta</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=pkwnJcETgfE"/>
  <published>2026-09-15T12:00:00+00:00</published>
  <media:group><media:description>no links here</media:description></media:group></entry>
 <entry><title>Old</title><link rel="alternate" href="x"/>
  <published>2026-08-01T12:00:00+00:00</published>
  <media:group><media:description>https://github.com/old/repo</media:description></media:group></entry>
</feed>"""


def test_the_repos_come_from_the_descriptions_not_the_titles():
    import datetime as dt
    vids = nnt.videos(FEED, now=dt.datetime(2026, 9, 17, tzinfo=dt.timezone.utc))
    assert [v["title"] for v in vids] == ["Why is everyone using these?", "Amazing: FREE AI Agent from Meta"]
    assert vids[0]["repos"] == ["anthropics/skills", "vercel-labs/agent-browser"], "deduped, sponsors dropped"
    assert vids[1]["repos"] == []


def test_the_section_replaces_itself_in_team_md():
    section = nnt.SECTION + "\n\n- one\n"
    once = nnt.replace_section("# team\n\n## HN\n- a\n", section)
    twice = nnt.replace_section(once + "\n## Later\n- b\n", nnt.SECTION + "\n\n- two\n")
    assert once.count(nnt.SECTION) == 1 and "- one" in once
    assert twice.count(nnt.SECTION) == 1 and "- two" in twice and "- one" not in twice and "## Later" in twice


def test_a_role_names_the_kpi_and_the_ranker_carries_it():
    note = ("score: kanban-worker-no-terminal-call = 3 — " + "x" * 200 + " — kpi: kanban_blocked\n"
            "score: vllm-watch = 2 — maybe — kpi: none\n"
            "score: other = 1 — nice\n")
    v = rank.scores_from_note(note)
    assert rank.kpi_of(v["kanban-worker-no-terminal-call"][1]) == "kanban_blocked", "a long reason keeps its KPI"
    per = {"ada": (3, "a — kpi: kanban_blocked"), "bash": (2, "b — kpi: kanban_open"),
           "lila": (2, "c — kpi: kanban_blocked"), "vera": (1, "d — kpi: ops_tok_s")}
    assert rank.item_kpi(per) == "kanban_blocked", "most named among the 2s and 3s; a 1 does not vote"
    assert rank.item_kpi({"ada": (2, "x — kpi: none")}) == "none"
    rows = [{"item": "kanban-worker-no-terminal-call", "kpi": "kanban_blocked", "top": True, "all_roles": True,
             "breadth": 3, "sum": 7, "min": 2, "scores": {}, "why": {}, "rank": 1}]
    md = rank.render("2026-09-17", rows, ["ada", "bash", "lila"])
    assert "nnt-agreed: kanban-worker-no-terminal-call — kpi=kanban_blocked" in md


def test_the_filer_records_the_kpi_and_its_baseline(tmp_path):
    kpis = tmp_path / "kpis.json"
    kpis.write_text(json.dumps({"kpis": {"kanban_blocked": {"direction": "down", "unit": "count",
                                                           "current": 80.0, "updated_at": 1.0}}}))
    title = "kanban-worker-no-terminal-call — kpi=kanban_blocked — 3 of 5 roles"
    assert filer.kpi_baseline(title, kpis) == {"kpi_id": "kanban_blocked", "kpi_known": True, "direction": "down",
                                               "unit": "count", "baseline": 80.0, "baseline_ts": 1.0}
    assert filer.kpi_baseline("x — kpi=made_up", kpis)["kpi_known"] is False
    assert filer.kpi_baseline("x — no kpi", kpis) == {}


def _board(tmp_path, rows):
    d = tmp_path / "boards" / "standing"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at REAL, completed_at REAL)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at REAL)")
    for r in rows:
        con.execute("INSERT INTO tasks VALUES (?,?,?,?,?)", r[:5])
        for c in r[5:]:
            con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,0)", (r[0], "kanban-loop", c))
    con.commit()
    con.close()
    return tmp_path / "boards"


def test_the_outcome_follows_gate_try_landing_and_measures_in_the_kpis_direction(tmp_path, monkeypatch):
    day = 86400
    landed = 1_000_000.0
    boards = _board(tmp_path, [
        ("t_gate", "team-improve: kanban-worker-no-terminal-call — bucket", "archived", landed - 3 * day, None,
         "green: try -> t_try"),
        ("t_try", "try: team-improve: kanban-worker-no-terminal-call — bucket", "done", landed - 2 * day, landed),
        ("t_park", "team-improve: vllm-watch — x", "archived", landed, None, "green: parked by Zoey; archiving"),
    ])
    cards = out.board_cards(boards)
    kpis = {"kanban_blocked": {"direction": "down", "history": [
        {"ts": landed, "value": 80.0}, {"ts": landed + 7 * day, "value": 60.0}]}}
    p1 = {"title": "kanban-worker-no-terminal-call — kpi=kanban_blocked", "kpi_id": "kanban_blocked", "baseline": 80.0}
    early = out.outcome(p1, cards, kpis, landed + 2 * day)
    assert early["stage"] == "landed" and early["measure"].startswith("due ")
    late = out.outcome(p1, cards, kpis, landed + 8 * day)
    assert late["measure"] == "better" and late["after"] == 60.0 and late["delta"] == -20.0
    parked = out.outcome({"title": "vllm-watch — kpi=none"}, cards, kpis, landed)
    assert parked["stage"] == "parked by Crissa"
    assert out.outcome({"title": "never-filed — kpi=kanban_open"}, cards, kpis, landed)["stage"] == "agreed, not filed"


def test_verdict_uses_the_direction_and_a_flat_band():
    assert out.verdict("down", 80, 60) == "better" and out.verdict("up", 80, 60) == "worse"
    assert out.verdict("down", 80, 78) == "flat"


def test_a_rerank_does_not_duplicate_an_idea(tmp_path):
    ledger = tmp_path / "proposals.jsonl"
    assert filer.record_proposal("x/repo — kpi=ops_tok_s — 3 of 3 roles", Path("n"), path=ledger) is True
    assert filer.record_proposal("x/repo — kpi=ops_tok_s — 4 of 4 roles", Path("n"), path=ledger) is False
    ledger.write_text(ledger.read_text() + json.dumps({"kind": "nnt-agreed", "title": "x/repo — 4 of 5", "status": "withdrawn"}) + "\n")
    rows = out.load_proposals(ledger)
    assert len(rows) == 1 and rows[0]["status"] == "withdrawn"


def test_the_gate_question_is_not_a_park():
    assert out.parked_ruling({"author": "steward-architect", "body": "Green this try? try | park | later <date>"}) is False
    assert out.parked_ruling({"author": "zoey", "body": "Park — not this month"}) is True
    assert out.parked_ruling({"author": "kanban-loop", "body": "green: parked by Zoey; archiving"}) is True
