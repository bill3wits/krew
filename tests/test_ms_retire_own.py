"""Stage 3 (the User 09-28): M&S closes its own cards — per finding, per type, with its reason;
a card closed in the last 14 days is not filed again; render-SOTA only on a minor bump."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_spec = importlib.util.spec_from_file_location("ms_retire_own", ROOT / "ms" / "scripts" / "ms-trail-run.py")
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

DAY = "2026-09-28"


def c(tid, key, status="todo", title="x"):
    return {"id": tid, "idempotency_key": key, "status": status, "title": title}


def rep(doc=None, sec=None, trend=None):
    return {"hermes": {"doctor": doc or {"measured": False}, "security": sec or {"measured": False}},
            "trend": trend or {}}


def ids(plan, what=None):
    return sorted(t for t, _r, w in plan if what is None or w == what)


def test_doctor_retires_per_issue_and_never_on_a_truncated_or_unknown_list():
    doc = {"measured": True, "issues": ["Browser tools (agent-browser) has 1 npm vulnerability"], "actionable_n": 1}
    # 0 actionable is the old all-or-nothing path's; this pass never doubles it
    assert ms.plan_own_retires(rep(doc={"measured": True, "issues": [], "actionable_n": 0}), DAY,
                               [c("t_x", "ms-doctor:x")], trend=[], drift_rows=[]) == []
    gone = c("t_gone", "ms-doctor:move-custom-providers-entry-crew-into-config-yam:2026-W39")
    stays = c("t_stay", "ms-doctor:" + ms._issue_slug(doc["issues"][0]) + ":2026-W39")
    plan = ms.plan_own_retires(rep(doc=doc), DAY, [gone, stays], trend=[], drift_rows=[])
    assert ids(plan, "doctor") == ["t_gone"]
    truncated = dict(doc, actionable_n=14)                      # the report keeps 12 issues
    assert ids(ms.plan_own_retires(rep(doc=truncated), DAY, [gone], trend=[], drift_rows=[])) == []
    assert ids(ms.plan_own_retires(rep(doc={"measured": False}), DAY, [gone], trend=[], drift_rows=[])) == []


def test_security_retires_the_vuln_that_is_gone_and_keeps_any_still_reported():
    sec = {"measured": True, "finding_count": 3, "findings": [
        {"vuln_id": "GHSA-keep", "package": "a", "severity": "HIGH"},
        {"vuln_id": "GHSA-down", "package": "b", "severity": "LOW"},       # downgraded, still there
        {"vuln_id": "", "package": "noid", "severity": "MODERATE"}]}
    cards = [c("t_keep", "ms-security:GHSA-keep:2026-W39"), c("t_down", "ms-security:GHSA-down:2026-W39"),
             c("t_gone", "ms-security:GHSA-gone:2026-W39"), c("t_noid", "ms-security:noid:2026-W39"),
             c("t_low", "ms-security-low:2026-09-26")]
    plan = ms.plan_own_retires(rep(sec=sec), DAY, cards, trend=[], drift_rows=[])
    assert ids(plan, "security") == ["t_gone"]
    assert ids(plan, "security-low") == []                      # a LOW finding is still reported
    clean = {"measured": True, "finding_count": 1,
             "findings": [{"vuln_id": "GHSA-keep", "package": "a", "severity": "HIGH"}]}
    assert ids(ms.plan_own_retires(rep(sec=clean), DAY, cards, trend=[], drift_rows=[]), "security-low") == ["t_low"]
    assert ids(ms.plan_own_retires(rep(sec={"measured": False, "findings": []}), DAY, cards,
                                   trend=[], drift_rows=[])) == []


def test_a_park_or_a_running_card_is_never_retired():
    sec = {"measured": True, "finding_count": 1, "findings": [{"vuln_id": "GHSA-other", "severity": "HIGH"}]}
    cards = [c("t_park", "ms-security:GHSA-gone:2026-W39", status="scheduled"),
             c("t_run", "ms-security:GHSA-gone2:2026-W39", status="running"),
             c("t_open", "ms-security:GHSA-gone3:2026-W39", status="blocked")]
    assert ids(ms.plan_own_retires(rep(sec=sec), DAY, cards, trend=[], drift_rows=[])) == ["t_open"]


def test_dep_review_is_superseded_by_tonights():
    cards = [c("t_old", "ms-dep-summary:2026-09-26"), c("t_now", "ms-dep-summary:2026-09-28")]
    assert ids(ms.plan_own_retires(rep(), DAY, cards, trend=[], drift_rows=[]), "dep-review") == ["t_old"]


def test_drift_closes_after_quiet_nights_only():
    cards = [c("t_quiet", "ms-drift:vllm:2026-09-24"), c("t_loud", "ms-drift:hindsight:2026-09-24"),
             c("t_new", "ms-drift:comfyui:2026-09-27")]
    rows = [{"env": "hindsight", "day": "2026-09-27", "unexplained": ["x"]},
            {"env": "vllm", "day": "2026-09-24", "unexplained": ["pytest-asyncio"]}]   # its own night
    assert ids(ms.plan_own_retires(rep(), DAY, cards, trend=[], drift_rows=rows), "drift") == ["t_quiet"]


def test_diagnose_closes_when_its_failure_is_absent_three_nights():
    cards = [c("t_hind", "ms-diagnose-hindsight-apply_green_failed-2026w39"),
             c("t_comfy", "ms-diagnose-comfyui-refused:kornia-rs-2026w39")]
    trend = [{"day": "2026-09-26", "apply_failures": ["comfyui:refused:kornia-rs"]},
             {"day": "2026-09-27", "apply_failures": []}]
    tonight = {"day": DAY, "apply_failures": []}
    plan = ms.plan_own_retires(rep(trend=tonight), DAY, cards, trend=trend, drift_rows=[])
    assert ids(plan, "diagnose") == ["t_hind"]                   # comfyui still inside the 3 nights
    assert ids(ms.plan_own_retires(rep(trend=tonight), DAY, cards, trend=trend[1:], drift_rows=[])) == []  # 2 nights


def _closed_db(tmp_path, rows):
    db = tmp_path / "k.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, idempotency_key TEXT)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, created_at INTEGER)")
    for tid, st, key, kind, at in rows:
        con.execute("INSERT INTO tasks VALUES (?,?,?)", (tid, st, key))
        con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES (?,?,?)", (tid, kind, at))
    con.commit()
    con.close()
    return db


def test_a_key_closed_inside_14_days_is_not_filed_again(tmp_path, monkeypatch):
    now = time.time()
    db = _closed_db(tmp_path, [
        ("t_mit", "archived", "ms-mitigation:hindsight:2026-W39", "archived", now - 3 * 86400),
        ("t_old", "done", "ms-mitigation:vllm:2026-W37", "completed", now - 20 * 86400),
        ("t_dia", "done", "ms-diagnose-hindsight-apply_green_failed-2026w39", "completed", now - 86400),
        ("t_sum", "done", "ms-dep-summary:2026-09-27", "completed", now - 86400)])
    assert ms.recently_closed("ms-mitigation:hindsight:2026-W40", db=db, now=now) == "t_mit"
    assert ms.recently_closed("ms-mitigation:vllm:2026-W40", db=db, now=now) == ""          # 20 d ago
    assert ms.recently_closed("ms-mitigation:hindsight-x:2026-W40", db=db, now=now) == ""   # other base
    assert ms.recently_closed("ms-diagnose-hindsight-apply_green_failed-2026w40", db=db, now=now) == "t_dia"
    assert ms.recently_closed("ms-dep-summary:2026-09-28", db=db, now=now) == ""            # exempt
    assert ms.recently_closed("ms-mitigation:hindsight:2026-W40", db=db, now=now, days=0) == ""
    ran = []
    monkeypatch.setattr(ms, "_BOARD_DB_CACHE", [db])
    monkeypatch.setattr(ms.subprocess, "run", lambda *a, **k: ran.append(a))
    assert ms._kanban_create(title="M&S mitigation: hindsight", body="b", assignee="bash",
                             idemp="ms-mitigation:hindsight:2026-W40") is False
    assert ran == []


def test_render_sota_is_a_card_on_a_minor_bump_only():
    assert ms.minor_bump("v0.37.0", "v0.37.4") is False
    assert ms.minor_bump("v0.37.4", "v0.38.0") is True and ms.minor_bump("0.9.9", "1.0.0") is True
    assert ms.minor_bump("abc123", "v0.38.0") is None


def test_render_sota_patch_bump_files_nothing(monkeypatch):
    ran = []
    monkeypatch.setattr(ms.subprocess, "run", lambda *a, **k: ran.append(a))
    ms.file_render_sota_card({"render": {"sota": {"behind": True, "local": "v0.37.0",
                                                  "origin_latest": "v0.37.4"}}}, DAY, "", cards=[])
    assert ran == []


def test_a_land_card_with_nothing_left_to_land_is_closed(monkeypatch):
    done = []
    monkeypatch.setattr(ms, "_ms_complete", lambda tid, result, what: done.append((tid, what)) or True)
    monkeypatch.setattr(ms, "_ledger_or_none", lambda: {"decisions": [
        {"env": "vllm", "package": "p", "decision": "pending_apply"}]})
    monkeypatch.setattr(ms, "filter_actionable_risk", lambda name, risk, ledger: ([], []))

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(ms.subprocess, "run", lambda *a, **k: R())
    cards = [c("t_land", "ms-land:comfyui:t_x"), c("t_wland", "", title="M&S land: comfyui"),
             c("t_vllm", "ms-land:vllm:t_y"), c("t_busy", "ms-land:comfyui:t_z", status="running")]
    report = {"hermes": {}, "envs": {"comfyui": {"details": {"risk": []}}, "vllm": {"details": {"risk": []}}}}
    ms.retire_spent_ms_cards(report, DAY, cards=cards)
    lands = sorted(t for t, w in done if w == "land")
    assert lands == ["t_land", "t_wland"]          # vllm has a pending apply: its card is the queue
    monkeypatch.setattr(ms, "MS_RETIRE_OWN", False)
    done.clear()
    ms.retire_spent_ms_cards(report, DAY, cards=cards)
    assert [t for t, w in done if w == "land"] == []
