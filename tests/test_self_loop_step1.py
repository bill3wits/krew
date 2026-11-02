"""Self-loop step 1 (the User 09-28): the reports tell the truth.

The kill test for the whole design is here: the REAL steward-architect-run.sh against a
stub hermes that exits 0 and writes nothing (or a blind note), through the window ticker,
into the House Daily roll-call. If that replay reads ok, the check is in the wrong layer.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))   # krew: job_status, krew_hours

import krew_daily as hd  # noqa: E402
import job_status  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


wt = _load("krew_window_tick_sl", "window/scripts/krew-window-tick.py")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _tick_paths_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the module's import-time paths stay in tmp, never the real home (tests/krew_home_guard.py)."""
    keep_in_tmp(wt, tmp_path, monkeypatch)
io_ = _load("improvement_outcomes_sl", "crew/scripts/improvement-outcomes.py")
SITE = {"off_hours": "20:00-02:00"}   # krew: krew.yaml off_hours


# --- 1. the trail markdown ---------------------------------------------------------------

def _trail():
    return _load("ms_trail_sl", "ms/scripts/ms-trail-run.py")


ERR = ("service RED after apply (hindsight.service restarted but http://127.0.0.1:8888/health did not come back"
       " — cause: app-log hindsight-api.err.log: ^^^^^^^^ | |   File \"migrations.py\", line 421, in run_migrations"
       " | |     raise RuntimeError(\"Database migration failed\") from e | | RuntimeError: Database migration failed"
       " | +---- | ERROR:    Application startup failed. Exiting.) — rolled back 11 pins")
PKGS = ["boto3", "botocore", "caio", "claude-agent-sdk", "google-auth", "googleapis-common-protos",
        "mammoth", "platformdirs", "rich-rst", "sqlalchemy", "wrapt"]
APPLIED_0927 = {
    "hindsight": {"ok": False, "upgraded": [], "failed": PKGS, "changed_n": 0, "err": ERR,
                  "outcomes": [{"package": p, "outcome": "rolled-back", "err": ERR[:180]} for p in PKGS]},
    "cosyvoice": {"ok": True, "upgraded": ["platformdirs", "werkzeug"], "failed": [], "changed_n": 2, "outcomes": []},
    "comfyui": {"ok": True, "changed_n": 1, "outcomes": []},
    "cosyvoice_vllm": {"ok": True, "changed_n": 1, "outcomes": []},
    "audio8-tts": {"ok": True, "changed_n": 0},
}


def test_a_rollback_is_attempted_and_not_landed():
    m = _trail()
    t = m.apply_tally(APPLIED_0927)
    h = t["envs"]["hindsight"]
    assert (h["landed"], h["attempted"], len(h["rolled"])) == (0, 11, 11)
    assert h["cause"] == "RuntimeError: Database migration failed"
    assert (t["landed"], t["attempted"], t["rolled"]) == (4, 15, 11)
    line = m.apply_env_line(h)
    assert "landed 0 of 11 attempted" in line and line.count("Database migration failed") == 1, \
        "the rollback is named once, not once per package"
    assert m.apply_env_line(t["envs"]["audio8-tts"]) is None, "nothing attempted = no line"


def test_the_doctor_line_is_never_cut_mid_sentence():
    m = _trail()
    long = ("Move custom_providers entry 'crew' into config.yaml providers: as `providers.<key>.api: "
            "http://127.0.0.1:8005/v1` and delete it from the list — the v12 list migration ran once and does not re-fire")
    line = m.doctor_manifest_line({"ok": False, "issues_n": 3, "actionable_n": 2,
                                   "actionable": ["Browser tools (agent-browser) has 1 npm vulnerability", long]})
    assert line.endswith("does not re-fire")


# --- 2. confounded is not better; no direction is not measured ---------------------------

def test_a_kpi_with_no_direction_is_unmeasured_not_up():
    assert io_.verdict("", 10, 20).endswith("unmeasured")
    assert io_.verdict(None, 10, 5).endswith("unmeasured")
    assert io_.verdict("down", 9, 7) == "better" and io_.verdict("up", 9, 7) == "worse"
    assert io_.verdict("down", 100, 101) == "flat"


def _ideas_src(tmp: Path, rows: list[dict]) -> hd.Sources:
    imp = tmp / "imp"
    imp.mkdir()
    (imp / "seen.jsonl").write_text("")
    (imp / "outcomes.json").write_text(json.dumps({"rows": rows, "round": rows}))
    return hd.Sources(improvements=imp, inbox=tmp / "inbox")


def test_a_confounded_better_is_not_counted_and_says_why(tmp_path):
    rows = [
        {"item": "kit", "stage": "landed", "kpi_id": "kanban_blocked", "baseline": 9, "after": 7, "measure": "better",
         "confounded": ["hermes tree updated ~17,500 commits"]},
        {"item": "drain the ready lane", "stage": "landed", "kpi_id": "kanban_oldest_ready_sec", "measure": "better",
         "confounded": ["1 other landed try watch the same KPI"]},
        {"item": "phantom refs", "stage": "landed", "kpi_id": "unnamed", "measure": "no KPI named"},
    ]
    now = dt.datetime.now()
    r = hd.a_ideas(now, now - dt.timedelta(days=1), _ideas_src(tmp_path, rows))
    f = {x["k"]: x["v"] for x in r["facts"]}
    better = [b for b in f["ideas.funnel"] if b["label"] == "Measured better"][0]
    assert better["n"] == 0 and "2 more moved but are confounded" in better["note"]
    assert f["ideas.value"].startswith("0 attributable")
    assert "hermes tree updated" in f["ideas.note"], "the confounder is printed next to the number"
    assert r["status"] != "ok", "a confounded better never lights Improving"
    assert [x["measure"] for x in f["ideas.round"]][:2] == ["better, confounded (not counted)"] * 2


def test_an_unconfounded_better_still_counts(tmp_path):
    rows = [{"item": "x", "stage": "landed", "kpi_id": "k", "measure": "better"}]
    now = dt.datetime.now()
    r = hd.a_ideas(now, now - dt.timedelta(days=1), _ideas_src(tmp_path, rows))
    assert r["status"] == "ok"


# --- 3. the fence's release order is the checkpoint's -------------------------------------

# Dropped from the reference: test_the_temporaries_follow_the_checkpoint_readme_order (reads the reference's
# fence checkpoint README and its dated fence temporary; krew ships neither: its fence is the unit defaults).


# --- 4. the job's own word, through the ticker, into the Daily ----------------------------

def test_status_verdict_is_pure_and_strict():
    assert wt.status_verdict("ok", None, None) == "ok", "a row with no status line is systemd's word alone"
    assert wt.status_verdict("ok", "world", None).startswith("failed: exited 0 but wrote no status line")
    assert wt.status_verdict("ok", "world", {"status": "ok"}) == "ok"
    assert wt.status_verdict("ok", "world", {"status": "skipped", "reason": "occupancy=beast"}) == "skipped: occupancy=beast"
    assert wt.status_verdict("ok", "world", {"status": "failed", "reason": "no note"}) == "failed: no note"
    assert wt.status_verdict("failed: result=exit-code exit=4", "team-eval", {"status": "ok"}).startswith("failed: result")


def test_a_stale_line_is_not_tonights_word(tmp_path):
    job_status.write("world", "2026-09-27", "ok", d=tmp_path)
    p = job_status.path_for("world", "2026-09-27", tmp_path)
    rec = json.loads(p.read_text())
    rec["ts"] = 1000.0
    p.write_text(json.dumps(rec))
    assert job_status.read("world", "2026-09-27", since=5000.0, d=tmp_path) is None
    assert job_status.read("world", "2026-09-27", since=0, d=tmp_path)["status"] == "ok"


def _stub_hermes(tmp: Path, body: str) -> Path:
    b = tmp / "hermes"
    b.write_text("#!/usr/bin/env bash\n" + body + "\nexit 0\n")
    b.chmod(0o755)
    return b


def _run_world(tmp: Path, hermes_body: str, *, bundle: bool, mode: str = "normal") -> subprocess.CompletedProcess:
    day = "2099-01-02"
    ms, inbox, st = tmp / "ms", tmp / "inbox", tmp / "status"
    if bundle:
        (ms / "architect-collect" / day).mkdir(parents=True)
        (ms / "architect-collect" / day / "INDEX.md").write_text("# collect\n- github.md 10B\n")
    (tmp / "mode").write_text(mode + "\n")
    env = dict(os.environ, KREW_DAY=day, ARCHITECT_LOCK=str(tmp / "lock"), ARCHITECT_MS_ROOT=str(ms),
               ARCHITECT_INBOX=str(inbox), ARCHITECT_GPU_MODE_FILE=str(tmp / "mode"),
               ARCHITECT_HERMES_BIN=str(_stub_hermes(tmp, hermes_body.replace("$NOTE", str(inbox / f"architect-world-{day}.md")))),
               KREW_JOB_STATUS_DIR=str(st))
    return subprocess.run(["bash", str(ROOT / "crew/scripts/steward-architect-run.sh"), "world"], env=env,
                          capture_output=True, text=True, timeout=120)


def _tick_and_roll(tmp: Path, fired_status_line: str = "world") -> tuple[dict, dict]:
    """The ticker reads a unit that succeeded (systemd's word) plus the runner's status line,
    in one tick; then the Daily's roll-call reads the ticker's state."""
    state = tmp / "tick.json"
    fired_at = dt.datetime(2099, 1, 2, 20, 40)
    state.write_text(json.dumps({"window_key": "2099-01-02", "window": "20:00-02:00", "fired": {
        "architect-world": {"at": fired_at.isoformat(), "epoch": fired_at.timestamp(), "unit": "krew-architect-world.service",
                            "status": "started", "status_line": fired_status_line, "scope": "system"}}}))
    jobs = [{"name": "architect-world", "unit": "krew-architect-world.service", "scope": "system",
             "status_line": "world", "after": ["x"]},
            {"name": "architect-stack", "unit": "krew-architect-stack.service", "scope": "system",
             "status_line": "stack", "after": ["architect-world"]}]
    started: list[str] = []
    ok_unit = lambda unit, fired=0.0: {"active": "inactive", "result": "success", "status": "0",   # noqa: E731
                                       "started": fired_at.timestamp() + 5}
    reader = lambda k, day, since: job_status.read(k, day, 0, d=tmp / "status")                  # noqa: E731
    r = wt.tick(dt.datetime(2099, 1, 2, 20, 45), site=SITE, jobs=jobs, state_path=state, day_env=None,
                starter=lambda u: started.append(u) or (0, ""), legacy=lambda u: False, active=lambda u: False,
                result=ok_unit, status_reader=reader)
    tick = json.loads(state.read_text())
    sched = tmp / "sched.json"
    sched.write_text(json.dumps({"jobs": jobs, "watch": []}))
    src = hd.Sources(tick_state=state, schedule=sched, gpu_mode=tmp / "mode", site=SITE,
                     unit_show=lambda unit, scope: {}, inbox=tmp / "inbox")
    rc = hd.roll_call(dt.datetime(2099, 1, 3, 1, 50), dt.datetime(2099, 1, 2, 2, 0), src, {})
    row = next(x for x in rc["rows"] if x["routine"] == "architect-world")
    return {"tick": r, "state": tick, "started": started}, row


def test_kill_replay_hermes_0_and_no_note_reads_failed_everywhere(tmp_path):
    p = _run_world(tmp_path, "true", bundle=True)
    assert p.returncode != 0, "the note gate must flip the exit code: the 09-28 research exit-0 miss"
    line = job_status.read("world", "2099-01-02", d=tmp_path / "status")
    assert line["status"] == "failed" and "no note written" in line["reason"]
    t, row = _tick_and_roll(tmp_path)
    assert t["state"]["fired"]["architect-world"]["status"].startswith("failed: no note written")
    assert row["status"] == "bad" and "no note written" in row["evidence"]
    assert any("architect-world: failed" in o for o in t["tick"]["outcomes"]), "the verdict landed in the same tick"


def test_kill_replay_no_bundle_never_calls_hermes(tmp_path):
    p = _run_world(tmp_path, "echo CALLED > \"$(dirname \"$NOTE\")/../called\"", bundle=False)
    assert p.returncode == 3 and not (tmp_path / "called").exists()
    line = job_status.read("world", "2099-01-02", d=tmp_path / "status")
    assert line["status"] == "failed" and "INDEX.md missing" in line["reason"]
    t, row = _tick_and_roll(tmp_path)
    assert row["status"] == "bad" and "no collect bundle" in row["evidence"]


def test_a_note_written_this_run_is_ok_even_a_thin_one(tmp_path):
    p = _run_world(tmp_path, "mkdir -p \"$(dirname \"$NOTE\")\"; printf '# world\\n\\nideas: none\\n' > \"$NOTE\"", bundle=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert job_status.read("world", "2099-01-02", d=tmp_path / "status")["status"] == "ok"
    t, row = _tick_and_roll(tmp_path)
    assert row["status"] == "ok"


def test_a_beast_skip_is_skipped_and_leaves_reporting_unlit(tmp_path):
    p = _run_world(tmp_path, "true", bundle=True, mode="beast")
    assert p.returncode == 0
    line = job_status.read("world", "2099-01-02", d=tmp_path / "status")
    assert line["status"] == "skipped" and "beast" in line["reason"]
    t, row = _tick_and_roll(tmp_path)
    assert t["state"]["fired"]["architect-world"]["status"] == "skipped: occupancy=beast"
    assert row["status"] == "skipped", "its own word: neither ok nor red"
    assert t["started"] == ["krew-architect-stack.service"], "a skip is final: the chain continues"


def test_the_kill_switch_is_systemd_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_JOB_STATUS_LINE", "0")
    (tmp_path / "status").mkdir()
    t, row = _tick_and_roll(tmp_path)
    assert t["state"]["fired"]["architect-world"]["status"] == "ok"


def test_world_and_stack_no_longer_collect():
    sh = (ROOT / "crew/scripts/steward-architect-run.sh").read_text()
    code = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    assert "steward-architect-collect.py" not in code, "the evening unit is the only writer of INDEX.md"
    assert re.search(r"INDEX\.md", code)
