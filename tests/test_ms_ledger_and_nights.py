"""M&S audit fixes (the owner 2026-09-17: "please land the fixes") — part A.

1 ledger fails closed · 2 unknown is not clean · 3 skipped 0.5B is UNVERIFIED ·
4 one env's exception is not the night's end · 5 per-package apply failures ·
6 ad-hoc reports never overwrite the night · 7 no --triage · 8 cards matched by
idempotency key. Every path is tmp_path; nothing here touches /data.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
SCRIPT = ROOT / "ms" / "scripts" / "ms-trail-run.py"
SPEC = importlib.util.spec_from_file_location("ms_trail_run_ledger_nights", SCRIPT)
assert SPEC and SPEC.loader
ms = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ms)


@pytest.fixture
def box(tmp_path: Path, monkeypatch):
    """Every path the trail writes, redirected into tmp_path."""
    out = tmp_path / "ms"
    out.mkdir()
    monkeypatch.setattr(ms, "OUT_ROOT", out)
    monkeypatch.setattr(ms, "DECISIONS_PATH", out / "decisions.json")
    monkeypatch.setattr(ms, "REPORT_DIR", out / "reports")
    monkeypatch.setattr(ms, "MANIFEST_DIR", out / "manifests")
    monkeypatch.setattr(ms, "KANBAN_DIR", out / "kanban-queue")
    monkeypatch.setattr(ms, "WINDOW_PATH", out / "maintenance-window.json")
    monkeypatch.setattr(ms, "TREND_PATH", out / "trend.jsonl")
    monkeypatch.setattr(ms, "HERMES_REPORT_DIR", out / "hermes")
    monkeypatch.setattr(ms, "FINDINGS_PATH", out / "findings.json")
    monkeypatch.setattr(ms, "PROMOTIONS_PATH", out / "promotions.json")
    monkeypatch.setattr(ms, "DRIFT_PATH", out / "drift.jsonl")
    # never the LIVE ~/.hermes/ms-env-hold.json (09-30: comfyui's live hold turned 5 cards
    # into 4): a temp copy that holds nothing, same principle as the launcher guard
    hold = out / "ms-env-hold.json"
    hold.write_text("{}\n")
    monkeypatch.setattr(ms, "MS_ENV_HOLD_FILE", hold)
    monkeypatch.setattr(ms, "DELIVERABLES_DIR", tmp_path / "deliverables")
    monkeypatch.setenv("MS_KANBAN_DB", str(tmp_path / "no-board.db"))
    return out


def _row(env="e1", pkg="somepkg", decision="hold", **kw):
    return {"env": env, "package": pkg, "decision": decision, "reason": "t",
            "ts": _dt.date.today().isoformat(), **kw}


def _write_ledger(box: Path, rows, **top):
    (box / "decisions.json").write_text(
        json.dumps({"schema": "ms_decisions/v1", "decisions": rows, **top}, indent=2) + "\n")


# --- 1. the ledger fails closed ----------------------------------------------

def test_missing_ledger_is_still_empty(box):
    assert ms._load_decisions()["decisions"] == []


@pytest.mark.parametrize("text", ["{not json", "[1, 2, 3]", '{"decisions": {"a": 1}}', ""])
def test_unreadable_ledger_raises_and_names_last_good(box, text):
    _write_ledger(box, [_row()])
    ms._load_decisions()                                   # good parse → LAST-GOOD
    lg = box / "decisions.LAST-GOOD.json"
    assert lg.read_bytes() == (box / "decisions.json").read_bytes()
    (box / "decisions.json").write_text(text)
    with pytest.raises(ms.LedgerUnreadable) as ei:
        ms._load_decisions()
    assert str(lg) in str(ei.value)
    # a hold must still block: the apply filter refuses to run blind
    with pytest.raises(ms.LedgerUnreadable):
        ms.filter_apply_blocked("e1", [{"package": "somepkg"}])
    # and no writer may overwrite the file with an empty ledger
    with pytest.raises(ms.LedgerUnreadable):
        ms.record_decision(env="e1", package="other", decision="hold", reason="x")
    assert (box / "decisions.json").read_text() == text
    assert lg.is_file()


def test_ledger_strict_kill_switch_restores_fail_open(box, monkeypatch):
    (box / "decisions.json").write_text("{broken")
    monkeypatch.setattr(ms, "MS_LEDGER_STRICT", False)
    assert ms._load_decisions()["decisions"] == []


def test_last_good_is_not_replaced_by_an_empty_ledger(box):
    _write_ledger(box, [_row(), _row(pkg="b")])
    ms._load_decisions()
    good = (box / "decisions.LAST-GOOD.json").read_bytes()
    _write_ledger(box, [])
    ms._load_decisions()
    assert (box / "decisions.LAST-GOOD.json").read_bytes() == good


def test_save_is_atomic_replace_under_the_lock(box, monkeypatch):
    replaced = []
    real_replace = os.replace

    def spy(src, dst):
        assert Path(src).parent == Path(dst).parent       # tmp in the same dir
        replaced.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr(ms.os, "replace", spy)
    ms.record_decision(env="e1", package="a", decision="hold", reason="x")
    assert [Path(d).name for _, d in replaced] == ["decisions.json"]
    assert not [p for p in box.iterdir() if p.name.endswith(".tmp")]
    assert (box / "decisions.json.lock").is_file()


def _child_env(box: Path) -> dict:
    return {**os.environ, "MS_OUT": str(box), "MS_DECISIONS": str(box / "decisions.json"),
            "MS_TREND": str(box / "trend.jsonl"), "MS_FINDINGS": str(box / "findings.json")}


_WRITER = r"""
import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("ms_w", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
if len(sys.argv) > 4:  # barrier: both writers start together
    open(sys.argv[4] + "-" + sys.argv[2], "w").close()
    while not os.path.exists(sys.argv[4]):
        time.sleep(0.01)
for i in range(int(sys.argv[3])):
    m.record_decision(env="e1", package=f"{sys.argv[2]}-{i}", decision="hold", reason="race")
"""


def test_two_processes_writing_lose_no_rows(box):
    # Without the lock this loses rows (measured: 57 of 80 survived).
    n = 40
    go = box / "go"
    procs = [
        subprocess.Popen([sys.executable, "-c", _WRITER, str(SCRIPT), tag, str(n), str(go)],
                         env=_child_env(box), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for tag in ("a", "b")
    ]
    deadline = time.time() + 120
    while not all((box / f"go-{t}").exists() for t in ("a", "b")) and time.time() < deadline:
        time.sleep(0.05)
    go.write_text("")
    for p in procs:
        _, err = p.communicate(timeout=240)
        assert p.returncode == 0, err.decode()[-800:]
    rows = json.loads((box / "decisions.json").read_text())["decisions"]
    assert sorted(r["package"] for r in rows) == sorted(
        [f"a-{i}" for i in range(n)] + [f"b-{i}" for i in range(n)])


def test_a_held_lock_blocks_a_second_writer(box):
    lock = open(box / "decisions.json.lock", "a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    p = subprocess.Popen([sys.executable, "-c", _WRITER, str(SCRIPT), "w", "1"],
                         env=_child_env(box), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        time.sleep(3.0)
        assert p.poll() is None, "writer did not wait for the ledger lock"
        assert not (box / "decisions.json").exists()
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    _, err = p.communicate(timeout=120)
    assert p.returncode == 0, err.decode()[-800:]
    assert json.loads((box / "decisions.json").read_text())["decisions"][0]["package"] == "w-0"


def test_census_counts_invalid_rows_and_stray_keys_without_rewriting(box):
    _write_ledger(
        box,
        [_row(), _row(decision="staged"), {"env": "e1", "from": "1", "to": "2", "decision": "hold"}, "junk"],
        updated_by="steward", pygments={"x": 1}, landfill=[],
    )
    before = (box / "decisions.json").read_bytes()
    census = ms._ledger_census()
    assert census["invalid"] == 3 and census["stray_keys"] == 2
    assert census["hold"] == 1
    assert (box / "decisions.json").read_bytes() == before
    body = ms._card_body({"totals": {}, "env_summaries": [], "trend": {"ledger": census}}, {}, "2026-09-17")
    assert "ledger has 3 invalid rows / 2 stray keys — write through --record-decision" in body


def test_census_of_unreadable_ledger_says_so(box):
    (box / "decisions.json").write_text("{")
    assert ms._ledger_census() == {"unreadable": 1}
    lines = ms._trend_lines({"day": "2026-09-17", "ledger": {}}, {})
    assert any("UNREADABLE" in ln for ln in lines)


# --- 2. could not measure is not clean ---------------------------------------

def test_outdated_none_when_uv_and_pip_both_fail(box, monkeypatch, tmp_path):
    py = tmp_path / "python"
    py.write_text("")
    monkeypatch.setattr(ms, "UV", "/bin/uv")
    monkeypatch.setattr(ms, "run", lambda cmd, timeout=0, extra_env=None: (1, "", "boom"))
    assert ms.outdated("e1", str(py)) is None
    monkeypatch.setattr(ms, "run", lambda cmd, timeout=0, extra_env=None: (0, "", ""))
    assert ms.outdated("e1", str(py)) == []                # uv answered: current


def test_doctor_and_security_timeouts_are_unknown(box, monkeypatch, tmp_path):
    hb = tmp_path / "hermes"
    hb.write_text("")
    monkeypatch.setattr(ms, "_hermes_bin", lambda: str(hb))
    monkeypatch.setattr(ms, "run", lambda cmd, timeout=0, extra_env=None: (124, "", "timeout"))
    doc = ms.run_hermes_doctor()
    sec = ms.run_hermes_security()
    assert doc["measured"] is False and doc["ok"] is False
    assert sec["measured"] is False
    assert "UNKNOWN" in ms.doctor_manifest_line(doc)
    body = ms._card_body({"totals": {}, "env_summaries": [], "hermes": {"doctor": doc, "security": sec}},
                         {}, "2026-09-17")
    assert "security: UNKNOWN" in body and "doctor:** UNKNOWN" in body
    monkeypatch.setattr(ms, "run", lambda cmd, timeout=0, extra_env=None: (
        0, "stuff\n  All checks passed! \n", ""))
    assert ms.run_hermes_doctor()["measured"] is True


def test_unknown_retires_nothing(box, monkeypatch):
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(ms.subprocess, "run", fake_run)
    cards = [
        {"id": "t_00000001", "status": "todo", "title": "Fix the doctor thing", "idempotency_key": "ms-doctor:x"},
        {"id": "t_00000002", "status": "todo", "title": "Patch OSV", "idempotency_key": "ms-security:GHSA-1"},
        {"id": "t_00000003", "status": "todo", "title": "Mitigate uvicorn for e1", "idempotency_key": "ms-mitigation:e1"},
    ]
    report = {
        "hermes": {"doctor": {"measured": False, "actionable_n": None},
                   "security": {"measured": False, "finding_count": None}},
        "envs": {"e1": {"outdated_unknown": True, "details": {"risk": []}}},
    }
    ms.retire_spent_ms_cards(report, "2026-09-17", cards=cards)
    assert not [c for c in calls if "complete" in c]
    # measured and clean: all three retire, matched by key despite rewritten titles
    report["hermes"] = {"doctor": {"measured": True, "actionable_n": 0},
                        "security": {"measured": True, "finding_count": 0}}
    report["envs"]["e1"]["outdated_unknown"] = False
    ms.retire_spent_ms_cards(report, "2026-09-17", cards=cards)
    done = sorted(c[c.index("complete") + 1] for c in calls if "complete" in c)
    assert done == ["t_00000001", "t_00000002", "t_00000003"]


def test_summary_card_says_unknown_not_clean(box):
    s = {"totals": {}, "env_summaries": [{"env": "e1", "outdated_n": 0, "outdated_unknown": True,
                                          "green_candidate": 0, "risk": 0, "never": 0}]}
    assert "outdated=UNKNOWN" in ms._card_body(s, {}, "2026-09-17")


# --- 3. skipped 0.5B is UNVERIFIED --------------------------------------------

def test_window_plan_defers_the_canary_env_when_unverified(monkeypatch):
    msg = "n/a 0.5B free 100 MiB < 7109"
    assert ms.window_apply_plan(ms.CANARY_ENV, canary_ok=True, canary_unverified=msg) == "skip_canary_unverified"
    assert ms.window_apply_plan("audio8-tts", canary_ok=True, canary_unverified=msg) == "apply"
    monkeypatch.setattr(ms, "MS_CANARY_SKIP_OK", True)
    assert ms.window_apply_plan(ms.CANARY_ENV, canary_ok=True, canary_unverified=msg) == "apply"


def test_05b_precheck_names_the_skip(monkeypatch):
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 100)
    monkeypatch.setattr(ms, "gpu_total_mib", lambda: 97887)
    got = ms.canary_05b_precheck()
    assert ms.canary_05b_skipped(got) and "100" in got
    monkeypatch.setattr(ms, "gpu_free_mib", lambda: 90000)
    monkeypatch.setattr(ms, "_port_open", lambda p: False)
    assert ms.canary_05b_precheck() == ""


def test_apply_green_rolls_back_an_unverified_install(box, monkeypatch, tmp_path):
    pre, post = tmp_path / "pre.txt", tmp_path / "post.txt"
    pre.write_text("somepkg==1.0.0\n")
    post.write_text("somepkg==1.0.1\n")
    frozen = iter([pre, post])
    runs = []
    monkeypatch.setattr(ms, "own_prefix_packages", lambda py: {"somepkg"})
    monkeypatch.setattr(ms, "_pip_check_set", lambda py: set())
    monkeypatch.setattr(ms, "freeze_env", lambda name, py, write_latest=True: next(frozen))
    monkeypatch.setattr(ms, "run", lambda cmd, timeout=0, extra_env=None: runs.append(cmd) or (0, "", ""))
    monkeypatch.setattr(ms, "smoke", lambda name, py: True)
    monkeypatch.setattr(ms, "live_canary", lambda name, include_05b=False: (
        True, "ops pong; n/a 0.5B free 100 MiB < 7109"))
    got = ms.apply_green(ms.CANARY_ENV, "/x/python", [{"package": "somepkg", "current": "1.0.0", "latest": "1.0.1"}])
    assert got["ok"] is True and got["upgraded"] == [] and "n/a 0.5B" in got["unverified"]
    assert any("somepkg==1.0.0" in c for c in runs[-1])     # rolled back to the old pin


# --- main() under a fake box --------------------------------------------------

@pytest.fixture
def night(box, monkeypatch, tmp_path):
    py = tmp_path / "python"
    py.write_text("")
    state = {"apply_calls": [], "outdated": {}, "apply_result": None}
    monkeypatch.setattr(ms, "VENVS", {"e1": (str(py), "role", False), "e2": (str(py), "role", False)})
    # an env with no declared smoke test is report-only (the User 09-29), so
    # the fake box declares one for each env it expects M&S to apply to
    monkeypatch.setitem(ms.SMOKE_IMPORT, "e1", {"code": "pass"})
    monkeypatch.setitem(ms.SMOKE_IMPORT, "e2", {"code": "pass"})
    # ...and says `apply: green` for them (step 3: apply: report is every env's default)
    monkeypatch.setattr(ms, "env_apply_mode", lambda name, promotions=None: "green")
    monkeypatch.setattr(ms, "require_packaging", lambda: None)
    monkeypatch.setattr(ms, "health_lanes", lambda: ({"presence": True, "camera": True}, {}))
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "normal")
    monkeypatch.setattr(ms, "survey_render_tools", lambda sota=True: {})
    monkeypatch.setattr(ms, "os_update_snapshot", lambda: {})
    monkeypatch.setattr(ms, "live_venv_users", lambda: {})
    monkeypatch.setattr(ms, "survey_env", lambda name, py: {"ok": True})
    monkeypatch.setattr(ms, "freeze_env", lambda name, py, write_latest=True: None)
    monkeypatch.setattr(ms, "collect_installed_constraints", lambda py: [])
    monkeypatch.setattr(ms, "smoke", lambda name, py: True)
    monkeypatch.setattr(ms, "unused_packages", lambda py: None)
    monkeypatch.setattr(ms, "survey_hermes_profiles", lambda: {})
    monkeypatch.setattr(ms, "run_hermes_doctor", lambda: {"ok": True, "measured": True, "issues_n": 0, "actionable_n": 0})
    monkeypatch.setattr(ms, "run_hermes_security", lambda: {"measured": True, "finding_count": 0, "findings": []})
    monkeypatch.setattr(ms, "run_hermes_tree_verify", lambda: {"ok": None})
    monkeypatch.setattr(ms, "notify_user", lambda text: "")
    monkeypatch.setattr(ms, "write_live_manifest", lambda report, day: None)
    monkeypatch.setattr(ms, "ingest_kanban_ms_go", lambda *a, **k: ({}, {}))

    def fake_outdated(name, py):
        v = state["outdated"].get(name, [])
        if isinstance(v, Exception):
            raise v
        return v

    def fake_classify(od, pins, env=None, role=None):
        rows = [{"package": r["name"], "current": r["version"], "latest": r["latest_version"]} for r in od]
        return {"green_candidate": rows, "risk": [], "never": [], "pinned": [], "group-wait": []}

    def fake_apply(name, py, rows):
        state["apply_calls"].append((name, [r["package"] for r in rows]))
        if state["apply_result"]:
            return state["apply_result"]
        return {"ok": True, "upgraded": [f"{r['package']} {r['current']}→{r['latest']}" for r in rows], "failed": []}

    monkeypatch.setattr(ms, "outdated", fake_outdated)
    monkeypatch.setattr(ms, "classify_outdated", fake_classify)
    monkeypatch.setattr(ms, "apply_green", fake_apply)

    def main(*argv):
        monkeypatch.setattr(sys, "argv", ["ms-trail-run.py", "--no-kanban", *argv])
        return ms.main()

    state["main"] = main
    return state


def _od(pkg="somepkg"):
    return [{"name": pkg, "version": "1.0.0", "latest_version": "1.0.1"}]


def test_corrupt_ledger_blocks_every_apply_and_exits_nonzero(night, box):
    night["outdated"] = {"e1": _od(), "e2": _od()}
    (box / "decisions.json").write_text('{"decisions": [')
    rc = night["main"]("--apply-green")
    assert rc == 3
    assert night["apply_calls"] == []
    day = _dt.date.today().isoformat()
    rep = json.loads((box / "reports" / f"ms-trail-{day}.json").read_text())
    assert "unreadable" in rep["ledger_unreadable"]
    assert {a["skipped"] for a in rep["applied"].values()} == {"ledger_unreadable"}
    assert (box / "decisions.json").read_text() == '{"decisions": ['   # never overwritten


def test_survey_still_runs_with_a_corrupt_ledger(night, box):
    (box / "decisions.json").write_text("{")
    assert night["main"]("--survey") == 0


def test_env_exception_continues_and_exits_nonzero(night, box):
    night["outdated"] = {"e1": RuntimeError("uv exploded"), "e2": _od()}
    rc = night["main"]("--apply-green")
    assert rc == 3
    assert night["apply_calls"] == [("e2", ["somepkg"])]
    day = _dt.date.today().isoformat()
    rep = json.loads((box / "reports" / f"ms-trail-{day}.json").read_text())
    assert rep["env_exceptions"] == ["e1"]
    assert "e1:env_exception" in rep["trend"]["apply_failures"]
    assert (box / "trend.jsonl").is_file() and (box / "reports" / "ms-trail-LATEST.json").is_file()


def test_clean_night_exits_zero(night, box):
    night["outdated"] = {"e1": _od(), "e2": []}
    assert night["main"]("--apply-green") == 0


def test_outdated_unknown_rides_the_report(night, box):
    night["outdated"] = {"e1": None, "e2": _od()}
    assert night["main"]("--apply-green") == 0
    day = _dt.date.today().isoformat()
    rep = json.loads((box / "reports" / f"ms-trail-{day}.json").read_text())
    assert rep["envs"]["e1"]["outdated_unknown"] is True and rep["envs"]["e1"]["measured"] is False
    assert rep["envs"]["e2"]["outdated_unknown"] is False


def test_per_package_refusals_become_apply_failures(night, box):
    night["outdated"] = {"e1": _od(), "e2": []}
    night["apply_result"] = {"ok": True, "upgraded": [], "failed": [
        {"package": "somepkg", "err": "No solution found when resolving"}]}
    night["main"]("--apply-green")
    day = _dt.date.today().isoformat()
    rep = json.loads((box / "reports" / f"ms-trail-{day}.json").read_text())
    assert "e1:refused:somepkg" in rep["trend"]["apply_failures"]
    # two nights of it → one diagnose card
    hist = [{"day": "2026-09-16", "apply_failures": ["e1:refused:somepkg"]}]
    got = ms.repeat_failures(hist, rep["trend"])
    assert [(x["env"], x["reason"]) for x in got] == [("e1", "refused:somepkg")]


def test_diagnose_cards_are_capped_per_run(box, monkeypatch):
    keys = [f"e1:refused:p{i}" for i in range(9)]
    (box / "trend.jsonl").write_text(json.dumps({"day": "2026-09-16", "apply_failures": keys}) + "\n")
    filed = []
    monkeypatch.setattr(ms, "_kanban_create", lambda **kw: filed.append(kw) or True)
    n = ms.file_diagnose_cards({"trend": {"day": "2026-09-17", "apply_failures": keys}}, "2026-09-17", "")
    assert n == ms.MS_DIAGNOSE_MAX == len(filed)


def test_canary_unverified_defers_to_the_window(night, box, monkeypatch):
    monkeypatch.setattr(ms, "VENVS", {ms.CANARY_ENV: (night_py := str(box / "py"), "ops", True)})
    Path(night_py).write_text("")
    night["outdated"] = {ms.CANARY_ENV: _od()}
    monkeypatch.setattr(ms, "canary_vllm", lambda: (True, "ops pong"))
    monkeypatch.setattr(ms, "canary_05b_precheck", lambda: "n/a 0.5B free 100 MiB < 7109")
    monkeypatch.setattr(ms, "TRAIN_BUSY", box / "no-train-busy")
    assert night["main"]("--apply-green") == 0
    assert night["apply_calls"] == []
    rows = json.loads((box / "decisions.json").read_text())["decisions"]
    assert [(r["package"], r["decision"]) for r in rows] == [("somepkg", "pending_apply")]
    assert rows[0]["reason"].startswith("canary unverified: n/a 0.5B")
    day = _dt.date.today().isoformat()
    rep = json.loads((box / "reports" / f"ms-trail-{day}.json").read_text())
    assert rep["applied"][ms.CANARY_ENV]["skipped"] == "canary_unverified"
    assert "UNVERIFIED" in ms.applied_label(rep["applied"][ms.CANARY_ENV], considered_apply=True, green_n=1)


def test_canary_skip_ok_kill_switch_lands_as_before(night, box, monkeypatch):
    monkeypatch.setattr(ms, "VENVS", {ms.CANARY_ENV: (str(box / "py"), "ops", True)})
    (box / "py").write_text("")
    night["outdated"] = {ms.CANARY_ENV: _od()}
    monkeypatch.setattr(ms, "canary_vllm", lambda: (True, "ops pong"))
    monkeypatch.setattr(ms, "canary_05b_precheck", lambda: "n/a 0.5B free 100 MiB < 7109")
    monkeypatch.setattr(ms, "TRAIN_BUSY", box / "no-train-busy")
    monkeypatch.setattr(ms, "MS_CANARY_SKIP_OK", True)
    night["main"]("--apply-green")
    assert night["apply_calls"] == [(ms.CANARY_ENV, ["somepkg"])]


# --- 6. ad-hoc runs never overwrite the night ---------------------------------

def test_adhoc_run_writes_its_own_report_and_no_trend(night, box):
    night["outdated"] = {"e1": [], "e2": []}
    day = _dt.date.today().isoformat()
    for argv in ((), ("--only", "e1"), ("--only", "e1", "--apply-green")):
        assert night["main"](*argv) == 0
    reports = sorted(p.name for p in (box / "reports").iterdir())
    assert reports and all(n.startswith(f"ms-trail-{day}-adhoc-") and n.endswith(".json") for n in reports)
    assert not (box / "trend.jsonl").exists()
    night["main"]("--apply-green")
    assert (box / "reports" / f"ms-trail-{day}.json").is_file()
    assert (box / "reports" / "ms-trail-LATEST.json").is_file()
    assert (box / "trend.jsonl").is_file()


def test_adhoc_kill_switch_overwrites_as_before(night, box, monkeypatch):
    monkeypatch.setattr(ms, "MS_ADHOC_REPORTS", False)
    night["outdated"] = {"e1": [], "e2": []}
    night["main"]()
    day = _dt.date.today().isoformat()
    assert (box / "reports" / f"ms-trail-{day}.json").is_file()


# --- 7. no --triage on the trail's cards --------------------------------------

@pytest.fixture
def board_calls(box, monkeypatch):
    calls: list = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(ms.subprocess, "run", fake_run)
    monkeypatch.setenv("HERMES_BIN", "/bin/true")
    return calls


def _creates(calls):
    return [c for c in calls if "create" in c]


def test_summary_card_is_plain_todo_not_triage(board_calls):
    summary = {
        "env_summaries": [{"env": "e1", "outdated_n": 1, "green_candidate": 0, "risk": 1,
                           "never": 0, "smoke_baseline": True, "role": "x", "heavy": False}],
        "totals": {"actionable_risk": 1}, "top_packages": [], "apply_failures": [],
        "hermes": {}, "parked_envs": [],
    }
    ms.write_kanban_stub(summary, "2026-09-17", {})
    (c,) = _creates(board_calls)
    assert "--triage" not in c
    assert c[c.index("--idempotency-key") + 1] == "ms-dep-summary:2026-09-17"
    assert c[c.index("--assignee") + 1] == "ada"
    body = c[c.index("--body") + 1]
    assert body.startswith("Summary only — not for decomposition. Env changes land only through "
                           "the nightly apply or an ms-land card filed by the trail.")


def test_no_trail_card_goes_through_triage(board_calls, box):
    ms._kanban_create(title="M&S diagnose: e1 — x 2x", body="b", assignee="bash", idemp="k")
    ms.file_render_sota_card({"render": {"sota": {"behind": True, "local": "a", "origin_latest": "b"}}},
                             "2026-09-17", "")
    ms.file_mitigation_cards(
        {"envs": {"e1": {"role": "r", "details": {"risk": [
            {"package": "protobuf", "current": "4.0.0", "latest": "6.0.0"}]}}}},
        "2026-09-17")
    ms.file_hermes_issue_cards(
        {"hermes": {"doctor": {"issues": ["broken plugin x"]},
                    "security": {"findings": [{"vuln_id": "GHSA-1", "severity": "HIGH", "package": "p"}]}}},
        "2026-09-17", "")
    creates = _creates(board_calls)
    assert len(creates) == 5
    for c in creates:
        assert "--triage" not in c, c[:6]
        assert "not for decomposition" in c[c.index("--body") + 1].lower()


# --- 8. cards matched by idempotency key --------------------------------------

def _board(path: Path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, assignee TEXT, status TEXT, "
                "idempotency_key TEXT, created_at INTEGER)")
    con.executemany("INSERT INTO tasks (id, status, assignee, title, idempotency_key) VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return path


REWRITTEN = [
    ("t_9c768401", "blocked", "bash", "M&S mitigation: rail-stt — peft 0.21.0 risk assessment & ledger", "ms-mitigation:rail-stt"),
    ("t_2ff6f49d", "done", "bash", "Mitigate uvicorn bump for hindsight and spawn landing child", "ms-mitigation:hindsight"),
    ("t_4cdf4ebc", "done", "bash", "M&S Mitigation: vllm-0271-cu132 uvicorn bump", "ms-mitigation:vllm-0271-cu132"),
    ("t_f502b061", "todo", "ada", "Review the nightly dependency report", "ms-dep-summary:2026-09-16"),
    ("t_aa11bb22", "ready", "steward", "Land cosyvoice protobuf upgrade", "ms-land:cosyvoice:t_11111111"),
    ("t_77777777", "archived", "bash", "Mitigate peft for unsloth", "ms-mitigation:unsloth"),
    ("t_88888888", "ready", "steward", "M&S land: comfyui", ""),
]


def test_board_cards_carry_keys_read_only(tmp_path):
    db = _board(tmp_path / "kanban.db", REWRITTEN)
    before = db.read_bytes()
    cards = ms.kanban_board_cards(db=db)
    assert {c["id"] for c in cards} == {r[0] for r in REWRITTEN} - {"t_77777777"}
    assert {c["idempotency_key"] for c in cards if c["id"] == "t_2ff6f49d"} == {"ms-mitigation:hindsight"}
    assert db.read_bytes() == before


def test_board_db_comes_from_the_profile_cli(monkeypatch, tmp_path):
    monkeypatch.delenv("MS_KANBAN_DB", raising=False)
    seen = []

    def fake_run(cmd, **_kw):
        seen.append(cmd)

        class R:
            returncode = 0
            stdout = json.dumps([{"slug": "old", "is_current": False, "db_path": "/x"},
                                 {"slug": "daily", "is_current": True, "db_path": str(tmp_path / "k.db")}])
            stderr = ""
        return R()

    monkeypatch.setattr(ms.subprocess, "run", fake_run)
    assert ms._kanban_board_db() == tmp_path / "k.db"
    assert seen[0][1:3] == ["-p", ms.MS_KANBAN_PROFILE]


def test_rewritten_titles_still_match_by_key(tmp_path):
    cards = ms.kanban_board_cards(db=_board(tmp_path / "kanban.db", REWRITTEN))
    assert ms._kanban_task_ids("", "M&S mitigation: hindsight — ", cards=cards,
                               key="ms-mitigation:hindsight") == ["t_2ff6f49d"]
    assert ms._kanban_task_ids("", "M&S mitigation: hindsight — ", cards=cards,
                               key="ms-mitigation:hindsight", open_only=True) == []
    # a keyless card still matches by title
    assert ms._card_ids(cards, key_prefix="ms-land:comfyui:", title_substr="M&S land: comfyui") == ["t_88888888"]


MITIGATION_TEMPLATE_BODY = """Nightly M&S 2026-09-15: **actionable** major bumps in `hindsight` (x).
3. **Out of kanban — land without User:**
   **GREEN:** nightly 22:33 `uv --no-deps` + smoke/canary. Ledger `pending_apply` on a green target is the deferral queue.
   `hold`/`wontfix` only if it must never land.
4. **Comments are status only.** Ada: refuse complete if there is no ledger row and no smoke.
## Actionable packages
- uvicorn 0.30.0 → 1.0.0
"""


def test_go_parse_ignores_the_templates_own_words():
    assert ms.specialist_go_from_card_text(
        MITIGATION_TEMPLATE_BODY + "\nWorker: recorded pending_apply for uvicorn 1.0.0") is True
    assert ms.specialist_go_from_card_text(
        MITIGATION_TEMPLATE_BODY + "\nVerdict: hold — ASGI break") is False


def test_plan_lands_from_rewritten_cards_and_reads_only_what_workers_wrote(monkeypatch, tmp_path):
    cards = ms.kanban_board_cards(db=_board(tmp_path / "kanban.db", REWRITTEN))
    shows = {
        "t_2ff6f49d": {"task": {"body": MITIGATION_TEMPLATE_BODY, "result": None},
                       "latest_summary": "GO: pending_apply recorded for uvicorn",
                       "comments": [], "runs": []},
        "t_4cdf4ebc": {"task": {"body": MITIGATION_TEMPLATE_BODY.replace("hindsight", "vllm-0271-cu132")},
                       "latest_summary": None, "comments": [{"body": "decision: hold, do not apply"}],
                       "runs": []},
        "t_aa11bb22": {"task": {"body": "## Actionable packages\n- protobuf 4.25.0 → 6.33.6\n"},
                       "latest_summary": None, "comments": [], "runs": []},
    }

    def fake_run(cmd, **_kw):
        tid = cmd[cmd.index("show") + 1]
        assert "--json" in cmd

        class R:
            returncode = 0
            stdout = json.dumps(shows.get(tid) or {})
            stderr = ""
        return R()

    monkeypatch.setattr(ms.subprocess, "run", fake_run)
    by_env, land_ids, spawns = ms.plan_kanban_ms_land("", cards=cards)
    assert land_ids == {"cosyvoice": ["t_aa11bb22"]}
    assert [r["package"] for r in by_env["cosyvoice"]] == ["protobuf"]
    assert [(s["env"], s["parent_id"], [r["package"] for r in s["rows"]]) for s in spawns] == [
        ("hindsight", "t_2ff6f49d", ["uvicorn"])]


def test_mitigation_dedup_sees_a_rewritten_open_card(board_calls, box, monkeypatch, tmp_path):
    monkeypatch.setenv("MS_KANBAN_DB", str(_board(tmp_path / "kanban.db", REWRITTEN)))
    report = {"envs": {"rail-stt": {"role": "r", "details": {"risk": [
        {"package": "peft", "current": "0.17.0", "latest": "0.21.0"}]}}}}
    ms.file_mitigation_cards(report, "2026-09-17")
    assert _creates(board_calls) == []


# --- --no-kanban keeps M&S off the board entirely (09-29) --------------------

def test_no_kanban_reads_and_writes_no_board(night, monkeypatch):
    """--no-kanban stopped the summary card only: the GO ingest still read the
    board (and could spawn land cards) and its rows fed apply_green, and land
    cards were still completed. Now the flag means off the board."""
    seen = {"ingest": 0, "complete": 0}
    monkeypatch.setattr(ms, "ingest_kanban_ms_go",
                        lambda *a, **k: seen.__setitem__("ingest", seen["ingest"] + 1) or
                        ({"e2": [{"package": "boardpkg", "current": "1", "latest": "2"}]}, {"e2": ["t_x"]}))
    monkeypatch.setattr(ms, "complete_ms_land_cards",
                        lambda *a, **k: seen.__setitem__("complete", seen["complete"] + 1))
    night["outdated"] = {"e2": _od()}
    night["main"]("--apply-green")
    assert seen == {"ingest": 0, "complete": 0}
    assert all("boardpkg" not in pkgs for _e, pkgs in night["apply_calls"])


def test_no_kanban_kill_switch_restores_the_old_reach(night, monkeypatch):
    seen = {"ingest": 0}
    monkeypatch.setattr(ms, "MS_KANBAN_INGEST_FOLLOWS_FLAG", False)
    monkeypatch.setattr(ms, "ingest_kanban_ms_go",
                        lambda *a, **k: seen.__setitem__("ingest", seen["ingest"] + 1) or ({}, {}))
    night["outdated"] = {"e2": _od()}
    night["main"]("--apply-green")
    assert seen["ingest"] == 1



# --- a gate's report row survives the night (09-29) --------------------------

def test_a_gated_env_keeps_its_report_row_and_queues_nothing(night, monkeypatch):
    """When a gate empties an env's batches (hold / no smoke / apply: report), the night used
    to overwrite its row with a plain "+0" and queue the greens "for the next window"."""
    monkeypatch.delitem(ms.SMOKE_IMPORT, "e2")                  # e2: no smoke test
    queued = []
    monkeypatch.setattr(ms, "queue_greens_for_window", lambda name, rows, reason="": queued.append(name) or len(rows))
    night["outdated"] = {"e1": _od(), "e2": _od()}
    night["main"]("--apply-green")
    rep = json.loads(sorted((ms.REPORT_DIR).glob("ms-trail-*.json"))[-1].read_text())
    assert rep["applied"]["e2"]["skipped"] == "no_declared_smoke" and rep["applied"]["e2"]["held_n"] == 1
    assert "e2" not in queued
    assert [c[0] for c in night["apply_calls"]] == ["e1"]


def test_the_box_reads_no_live_hold(box):
    assert ms.MS_ENV_HOLD_FILE.parent == box
    assert not str(ms.MS_ENV_HOLD_FILE).startswith(str(Path.home() / ".hermes"))
    assert ms.PROMOTIONS_PATH.parent == box and ms.DRIFT_PATH.parent == box
