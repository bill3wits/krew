"""Standing-board card keys (the owner 2026-09-17: "adding the ISO week to those keys"
— yes). hermes dedupes an idempotency key against every NON-archived card, so on
the standing board an undated ms-doctor / ms-security / ms-mitigation key parked
once suppressed that issue forever."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_spec = importlib.util.spec_from_file_location("ms_trail_keys", ROOT / "ms" / "scripts" / "ms-trail-run.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

DAY = "2026-09-17"          # ISO 2026-W38
NEXT_WEEK = "2026-09-24"    # ISO 2026-W39


def test_weekly_key_carries_the_iso_week():
    assert ms.iso_week(DAY) == "2026-W38"
    assert ms.weekly_key("ms-doctor:config-version", DAY) == "ms-doctor:config-version:2026-W38"
    assert ms.weekly_key("ms-mitigation:vllm", NEXT_WEEK).endswith(":2026-W39")


def _card(key, status, cid="t_1"):
    return {"id": cid, "idempotency_key": key, "status": status, "title": "x"}


def test_an_open_working_card_still_stops_a_duplicate_across_weeks():
    base = "ms-doctor:config-version"
    for st in ("todo", "ready", "running", "blocked", "triage"):
        assert ms.issue_card_open([_card(f"{base}:2026-W38", st)], base, NEXT_WEEK) == ["t_1"], st
    assert ms.issue_card_open([_card(base, "blocked")], base, NEXT_WEEK) == ["t_1"], "an undated old card"


def test_a_parked_card_is_sticky_for_its_week_not_forever():
    base = "ms-security:GHSA-x"
    parked = [_card(f"{base}:2026-W38", "scheduled")]
    assert ms.issue_card_open(parked, base, DAY) == ["t_1"]
    assert ms.issue_card_open(parked, base, NEXT_WEEK) == []
    assert ms.issue_card_open([_card(base, "scheduled")], base, DAY) == [], "an undated park no longer gags it"
    assert ms.issue_card_open([_card(f"{base}:2026-W38", "done")], base, DAY) == []


def test_other_issues_do_not_match_by_prefix():
    assert ms.issue_card_open([_card("ms-doctor:config-version-2:2026-W38", "todo")],
                              "ms-doctor:config-version", DAY) == []


def test_retire_and_env_parsing_see_weekly_keys():
    cards = [_card("ms-mitigation:vllm:2026-W38", "todo", "t_a"), _card("ms-mitigation:vllm", "todo", "t_b")]
    ids = ms._card_ids(cards, key="ms-mitigation:vllm", key_prefix="ms-mitigation:vllm:", open_only=True)
    assert sorted(ids) == ["t_a", "t_b"]
    assert ms._card_env({"idempotency_key": "ms-mitigation:vllm:2026-W38"}, "ms-mitigation:", lambda t: None) == "vllm"


# The User 09-28: 16 M&S cards parked "later 2026-10-04" carried W39 keys, and the
# week rule freed every issue at W40 — the nightly would re-file them against his park.

W40 = "2026-09-28"


def _board(tmp_path, cards, events):
    import json
    import sqlite3
    db = tmp_path / "kanban.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, assignee TEXT, title TEXT, idempotency_key TEXT)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, run_id INTEGER, "
                "kind TEXT, payload TEXT, created_at INTEGER)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)", cards)
    con.executemany("INSERT INTO task_events (task_id, kind, payload) VALUES (?,?,?)",
                    [(t, k, json.dumps({"reason": r})) for t, k, r in events])
    con.commit()
    con.close()
    return db


def test_a_dated_park_holds_its_issue_until_the_date_whatever_the_week(tmp_path):
    db = _board(tmp_path, [
        ("t_sec", "scheduled", "ada", "M&S Hermes security: GHSA-x (HIGH)", "ms-security:GHSA-x:2026-W39"),
        ("t_und", "scheduled", "ada", "M&S Hermes doctor: y", "ms-doctor:y:2026-W39"),
    ], [
        ("t_sec", "scheduled", "later 2026-10-04 — parked by the User's plan"),
        ("t_und", "scheduled", "transient crash — no date"),
    ])
    cards = ms.kanban_board_cards("", db=db)
    got = {c["id"]: c["park_until"] for c in cards}
    assert got == {"t_sec": "2026-10-04", "t_und": ""}
    assert ms.issue_card_open(cards, "ms-security:GHSA-x", W40) == ["t_sec"]
    assert ms.issue_card_open(cards, "ms-security:GHSA-x", "2026-10-04") == ["t_sec"], "held through its day"
    assert ms.issue_card_open(cards, "ms-security:GHSA-x", "2026-10-05") == []
    assert ms.issue_card_open(cards, "ms-doctor:y", W40) == [], "an undated park keeps the week rule"


def test_the_latest_park_wins(tmp_path):
    db = _board(tmp_path, [("t_a", "scheduled", "ada", "x", "ms-doctor:z:2026-W39")],
                [("t_a", "scheduled", "later 2026-10-04"), ("t_a", "scheduled", "park, no date")])
    assert ms.issue_card_open(ms.kanban_board_cards("", db=db), "ms-doctor:z", W40) == []


def test_park_date_hold_kill_switch(monkeypatch):
    card = {"id": "t_1", "idempotency_key": "ms-security:GHSA-x:2026-W39", "status": "scheduled",
            "title": "x", "park_until": "2026-10-04"}
    assert ms.issue_card_open([card], "ms-security:GHSA-x", W40) == ["t_1"]
    monkeypatch.setattr(ms, "MS_PARK_DATE_HOLDS", False)
    assert ms.issue_card_open([card], "ms-security:GHSA-x", W40) == []


def test_the_survey_files_no_drift_card_by_default():
    src = (ROOT / "ms" / "scripts" / "ms-trail-run.py").read_text()
    assert ms.MS_SURVEY_CARDS is False
    assert 'source="survey",\n                                 file_card=MS_SURVEY_CARDS and not args.no_kanban)' in src


def test_the_low_unknown_summary_honours_a_dated_park_when_the_listing_is_empty(monkeypatch):
    filed = []
    monkeypatch.setattr(ms, "_kanban_create", lambda **kw: filed.append(kw["idemp"]) or True)
    parked = [{"id": "t_low", "status": "scheduled", "title": "M&S Hermes security: 19 low/unknown (2026-09-26)",
               "idempotency_key": "ms-security-low:2026-09-26", "park_until": "2026-10-04"}]
    report = {"hermes": {"doctor": {"issues": []},
                         "security": {"findings": [{"package": "p", "vuln_id": "GHSA-l", "severity": "LOW"}]}}}
    ms.file_hermes_issue_cards(report, W40, "", cards=parked)
    assert filed == []
    ms.file_hermes_issue_cards(report, "2026-10-05", "", cards=parked)
    assert filed == ["ms-security-low:2026-10-05"]
