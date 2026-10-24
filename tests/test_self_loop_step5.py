"""Self-loop step 5 (the User 09-28): the three claims, with denominators and zero rules."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours

import krew_daily as hd  # noqa: E402
import self_claims as sc  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


NOW = dt.datetime(2026, 9, 28, 1, 50)
PKGS = [f"p{i}" for i in range(11)]
APPLIED = {"hindsight": {"ok": False, "failed": PKGS, "changed_n": 0,
                         "outcomes": [{"package": p, "outcome": "rolled-back"} for p in PKGS]},
           "cosyvoice": {"ok": True, "changed_n": 2}, "comfyui": {"ok": True, "changed_n": 1},
           "cosyvoice_vllm": {"ok": True, "changed_n": 1}}


def test_maintaining_counts_a_rollback_as_attempted(tmp_path):
    p = tmp_path / "trail.json"
    p.write_text(json.dumps({"day": "2026-09-27", "applied": {"hindsight": APPLIED["hindsight"]}}))
    m = sc.maintaining(p)
    assert (m["landed"], m["attempted"]) == (0, 11) and m["text"] == "0 of 11 applies landed; rolled back: hindsight 11"
    p.write_text(json.dumps({"day": "2026-09-27", "applied": APPLIED}))
    assert sc.maintaining(p)["text"].startswith("4 of 15 applies landed")
    p.write_text(json.dumps({"day": "2026-09-27", "applied": {"x": {"ok": True, "changed_n": 0}}}))
    m = sc.maintaining(p)
    assert m["value"] is None and m["text"].startswith("nothing owed")


def test_the_report_tally_and_the_claim_agree():
    trail = _load("mst_step5", "ms/scripts/ms-trail-run.py")
    t = trail.apply_tally(APPLIED)
    assert sc.tally_applied(APPLIED) | {"rollbacks": []} == {"landed": t["landed"], "attempted": t["attempted"],
                                                             "rolled": t["rolled"], "rollbacks": []}


def test_improving_counts_only_attributable_better_over_shipped(tmp_path):
    p = tmp_path / "outcomes.json"
    ts = NOW.timestamp() - 86400
    p.write_text(json.dumps({"rows": [
        {"item": "a", "stage": "landed", "sha": "abc", "landed_at": ts, "measure": "better"},
        {"item": "b", "stage": "landed", "sha": "def", "landed_at": ts, "measure": "better", "confounded": ["x"]},
        {"item": "c", "stage": "landed", "sha": "123", "landed_at": ts, "measure": "no KPI named"},
        {"item": "d", "stage": "landed", "landed_at": ts, "measure": "better"},          # no sha: not shipped
        {"item": "e", "stage": "closed, not landed (no commit found)"},
    ]}))
    i = sc.improving(p, NOW)
    assert (i["better"], i["shipped"], i["unmeasured"]) == (1, 3, 2)
    assert i["text"] == "1 of 3 shipped changes measured better (attributable); 2 shipped, unmeasured"
    p.write_text(json.dumps({"rows": []}))
    assert sc.improving(p, NOW)["text"] == "nothing shipped in 30 days" and sc.improving(p, NOW)["value"] is None


def test_healing_reads_the_four_named_actuators_by_their_proxies(tmp_path):
    since = NOW - dt.timedelta(days=7)
    gate = tmp_path / "gate.log"
    gate.write_text("2026-09-26T12:50:25 orphan t_de176469 pid=11 (+1 desc) leftover of run 3415 (ended 711s ago, card done)\n"
                    "2026-09-26T12:50:25 orphan t_de176469 pid=11 run=3415 -> SIGTERM sent\n"
                    "2026-09-26T12:51:00 orphan t_aaaaaaaa pid=12 (+0 desc) leftover of run 1 (ended 1s ago, card done)\n"
                    "2026-09-27T19:53:14 salad: 1 session(s) ['t_1669976a'] — clearly garbage ['t_1669976a']\n"
                    "2026-09-27T19:53:17 tick x salad=1 orphans=2\n")
    salad = tmp_path / "salad.log"
    salad.write_text("2026-09-27T08:33:42 salad-tripwire: release: t_3e12faff unblocked\n"
                     "2026-09-27T19:53:15 salad-tripwire: release: leave t_1669976a — status running\n")
    hold = tmp_path / "hold.jsonl"
    hold.write_text("".join(json.dumps({"event": "flag_home_launched", "rc": 0, "ts": f"2026-09-27T22:{m}:47"}) + "\n"
                            for m in ("34", "39", "44")) + json.dumps({"event": "flag_home_launched", "ts": "2026-09-26T10:00:00"}) + "\n")
    jd = tmp_path / "jan"
    jd.mkdir()
    (jd / "janitor-20260927.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"ts": "2026-09-27T18:00:00", "units": {"d.service": {"active": "failed", "red": True, "action": "restart"},
                                                "a.service": {"active": "failed", "red": True, "action": "restart"}}},
        {"ts": "2026-09-27T18:05:00", "units": {"d.service": {"active": "active", "red": False},
                                                "a.service": {"active": "failed", "red": True, "action": "card"}}}]))
    assert (sc.heal_orphans(since, gate)["detected"], sc.heal_orphans(since, gate)["cleared"]) == (2, 1)
    s = sc.heal_salad(since, gate, salad)
    assert (s["detected"], s["cleared"]) == (1, 1), "a `release: leave` is not a clear"
    w = sc.heal_way_home(since, hold)
    assert (w["detected"], w["cleared"]) == (4, 2), "three launches five minutes apart clear once"
    j = sc.heal_janitor(since, jd)
    assert (j["detected"], j["cleared"]) == (3, 1)


def test_silent_greens_are_status_line_failures_only(tmp_path):
    p = tmp_path / "tick.json"
    p.write_text(json.dumps({"fired": {
        "architect-world": {"status": "failed: no note written: x.md", "status_line": "world"},
        "ms-nightly": {"status": "failed: result=exit-code exit=1"},
        "team-eval": {"status": "failed: result=exit-code exit=4", "status_line": "team-eval"},
        "architect-logs": {"status": "ok", "status_line": "logs"}}}))
    assert [g["routine"] for g in sc.silent_greens(p)] == ["architect-world"]


def test_a_battery_only_night_warns_and_names_the_split(tmp_path):
    logs = tmp_path / "bat"
    logs.mkdir()
    (logs / "nightly-20260927.log").write_text("x\n=== done 2026-09-27T20:13:44-04:00 trail_rc=0 battery_rc=1 ===\n")
    assert hd._nightly_rcs(logs, "2026-09-27") == (0, 1)
    assert hd._nightly_rcs(logs, "2026-09-26") is None


def test_the_self_review_says_zero_instead_of_a_percentage(tmp_path, monkeypatch):
    sr = _load("self_review_step5", "crew/scripts/self-review.py")
    monkeypatch.setattr(sr, "IMPROVE", tmp_path)
    monkeypatch.setattr(sr, "TRAIL", tmp_path / "trail.json")
    (tmp_path / "trail.json").write_text(json.dumps({"day": "2026-09-27", "applied": {}}))
    r = sr.build(NOW, healing=lambda now: {"text": "no fault detected", "value": None, "cleared": 0, "detected": 0,
                                           "days": 7, "actuators": [], "breaker": {"mode": "report", "trips": 0,
                                                                                  "false_alarm_label": "unassigned"}})
    md = sr.render(r, sr.trend([], r["claims"]))
    assert "nothing owed" in md and "nothing shipped in 30 days (—)" in md and "false-alarm label unassigned" in md
    assert set(r["claims"]) == {"week", "generated_at", "maintaining", "healing", "improving"}


def test_the_sources_night_writes_counters_to_data_not_the_doc(tmp_path, monkeypatch):
    col = _load("collect_step5", "crew/scripts/team-research-collect.py")
    hits = {}
    src = {"id": "nnt", "trust": "high", "hits": 5, "misses": 0}
    for _ in range(3):
        col.count(src, False, hits)
    assert hits["nnt"] == hits["nnt"] | {"hits": 5, "misses": 3, "stale": True}
    assert col.effective(src, hits)["trust"] == "stale" and src["trust"] == "high", "the doc's own row is untouched"
    assert col.effective(dict(src, trust="pinned"), hits)["trust"] == "pinned"
    col.count(src, True, hits)
    assert hits["nnt"]["misses"] == 0 and not hits["nnt"]["stale"]
    assert col.COUNTERS_IN_DOCS is False


def test_the_sunday_run_writes_the_review_beside_the_note():
    sh = (ROOT / "crew/scripts/steward-architect-run.sh").read_text()
    ops = sh[sh.index('if [ "$JOB" = ops ]; then\n  python3 "$ROOT/scripts/steward-file-architect-cards.py" --ops'):]
    assert "self-review.py" in ops.split("\nfi")[0]
