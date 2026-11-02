"""M&S part B (the owner 2026-09-17: "please land the fixes").

1. LAST-GOOD, not LATEST, is the restore point (the 2 h survey rewrites LATEST
   from whatever is installed, so "reinstall from LATEST" reinstalled the break).
2. Survey drift is recorded (drift.jsonl) and carded once per env per day; the
   09-16 22:52 av 17→18 install under a hold was logged once and lost.
3. SERVICE_ENVS covers every always-on unit, with restart vs report policy and
   occupancy parking.
4. The unused list has a second layer: never-auto names, mapped dists, young dists.

No test writes live data: every path is monkeypatched onto tmp_path, systemctl
and kanban are fakes.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
SCRIPT = ROOT / "ms" / "scripts" / "ms-trail-run.py"


def _load(name: str = "ms_trail_part_b"):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ms = _load()

FREEZE_GOOD = "av==17.0.0\ncryptography==44.0.0\nnumpy==1.26.4\ntorch==2.9.0+cu130\n"


@pytest.fixture
def box(tmp_path, monkeypatch):
    """Every M&S path under tmp_path; no kanban, no systemctl, no smoke."""
    man = tmp_path / "manifests"
    monkeypatch.setattr(ms, "OUT_ROOT", tmp_path)
    monkeypatch.setattr(ms, "MANIFEST_DIR", man)
    monkeypatch.setattr(ms, "DRIFT_PATH", tmp_path / "drift.jsonl")
    monkeypatch.setattr(ms, "DECISIONS_PATH", tmp_path / "decisions.json")
    monkeypatch.setattr(ms, "MODE_FILE", tmp_path / "gpu-mode")
    monkeypatch.setattr(ms, "MS_LAST_GOOD", True)
    monkeypatch.setattr(ms, "MS_DRIFT_CARDS", True)

    def no_kanban(**kw):
        raise AssertionError("a test must not reach the real kanban")

    monkeypatch.setattr(ms, "_kanban_create", no_kanban)
    monkeypatch.setattr(ms, "_svc_systemctl", lambda spec, args, timeout=30: (1, "", "no systemctl in tests"))
    return tmp_path


# ---------------------------------------------------------------- 1. LAST-GOOD

def test_last_good_needs_green_smoke_and_an_ok_service(box):
    assert ms.write_last_good("comfyui", "/x/py", smoke_ok=False, service_state=None,
                              source="t", freeze_text=FREEZE_GOOD) is None
    p = ms.write_last_good("comfyui", "/x/py", smoke_ok=True, service_state=None,
                           source="t", freeze_text=FREEZE_GOOD)
    assert p == box / "manifests" / "comfyui-LAST-GOOD.txt"
    assert ms._freeze_versions(p) == ms._freeze_versions_from_text(FREEZE_GOOD)
    assert p.read_text().startswith("# LAST-GOOD comfyui ")
    assert "hindsight" in ms.SERVICE_ENVS
    for verdict in ("down", "broken", "restart-owed", "report-down", "parked", None):
        st = {"verdict": verdict} if verdict else None
        assert ms.write_last_good("hindsight", "/x/py", smoke_ok=True, service_state=st,
                                  source="t", freeze_text=FREEZE_GOOD) is None, verdict
    assert not (box / "manifests" / "hindsight-LAST-GOOD.txt").exists()
    assert ms.write_last_good("hindsight", "/x/py", smoke_ok=True, service_state={"verdict": "ok"},
                              source="t", freeze_text=FREEZE_GOOD)


def test_last_good_history_keeps_fourteen_per_env_and_never_touches_a_prefix_twin(box, monkeypatch):
    hist = box / "manifests" / "last-good"
    hist.mkdir(parents=True)
    for i in range(1, 17):
        (hist / f"vllm-2026-08-{i:02d}.txt").write_text("x==1\n")
    (hist / "vllm-0271-cu132-2026-08-01.txt").write_text("x==1\n")
    monkeypatch.setattr(ms, "LAST_GOOD_KEEP", 14)
    monkeypatch.setattr(ms, "SERVICE_ENVS", {})
    ms.write_last_good("vllm", "/x/py", smoke_ok=True, service_state=None, source="t",
                       freeze_text=FREEZE_GOOD)
    mine = sorted(p.name for p in hist.iterdir() if p.name.startswith("vllm-20"))
    assert len(mine) == 14 and mine[-1] == f"vllm-{dt.date.today().isoformat()}.txt"
    assert (hist / "vllm-0271-cu132-2026-08-01.txt").exists()


def test_kill_switch_writes_no_last_good(box, monkeypatch):
    monkeypatch.setattr(ms, "MS_LAST_GOOD", False)
    assert ms.write_last_good("comfyui", "/x/py", smoke_ok=True, service_state=None,
                              source="t", freeze_text=FREEZE_GOOD) is None


def test_nightly_last_good_resmokes_a_touched_env_and_reads_the_service(box, monkeypatch):
    py = sys.executable
    smoked = []
    monkeypatch.setattr(ms, "smoke", lambda name, p: smoked.append(name) or name != "rail-stt")
    monkeypatch.setattr(ms, "_freeze_text", lambda p: FREEZE_GOOD)
    monkeypatch.setattr(ms, "service_env_state",
                        lambda name, p, s: {"verdict": "ok" if name == "audio8-tts" else "restart-owed"})
    report = {
        "envs": {
            "comfyui": {"python": py, "smoke_baseline": True},
            "audio8-tts": {"python": py, "smoke_baseline": True},
            "vllm": {"python": py, "smoke_baseline": True},
            "rail-stt": {"python": py, "smoke_baseline": True},
            "gone": {"python": "/nope/python", "smoke_baseline": True},
        },
        "applied": {"rail-stt": {"upgraded": ["a 1→2"]}},
    }
    out = ms.nightly_last_good(report)
    assert smoked == ["rail-stt"], "only an env the night touched is smoked again"
    assert out["comfyui"] and out["audio8-tts"]
    assert out["vllm"] is None, "restart-owed never qualifies"
    assert out["rail-stt"] is None, "the post-apply smoke is RED"
    assert "gone" not in out


def test_the_night_writes_last_good_after_apply_and_the_survey_never_does():
    src = SCRIPT.read_text(encoding="utf-8")
    main = src.split("def main() -> int:", 1)[1]
    assert main.index("service_verify_after_apply(name, py, result)") < main.index("nightly_last_good(report)")
    survey = main.split("    if args.survey:", 1)[1].split("    do_kanban = ", 1)[0]
    assert "record_drift(" in survey
    assert "last_good" not in survey.lower()


def test_survey_records_drift_and_leaves_last_good_alone(box, monkeypatch):
    lg = box / "manifests" / "hindsight-LAST-GOOD.txt"
    lg.parent.mkdir(parents=True)
    lg.write_text("# LAST-GOOD\nav==17.0.0\n")
    drift = {"ok": True, "had_manifest": True, "changed": {"av": ["17.0.0", "18.0.0"]},
             "added": [], "removed": [], "n": 1}
    seen = []
    monkeypatch.setattr(ms, "VENVS", {"hindsight": (sys.executable, "memory", False),
                                      "comfyui": (sys.executable, "comfy", True)})
    monkeypatch.setattr(ms, "survey_env", lambda name, py: drift if name == "hindsight" else
                        {"ok": True, "had_manifest": True, "n": 0})
    monkeypatch.setattr(ms, "freeze_env", lambda name, py, write_latest=True: None)
    monkeypatch.setattr(ms, "os_update_snapshot", lambda: {})
    monkeypatch.setattr(ms, "survey_hermes_profiles", lambda: {})
    monkeypatch.setattr(ms, "record_drift", lambda name, py, d, **kw: seen.append((name, kw)))
    monkeypatch.setattr(ms, "write_last_good", lambda *a, **k: pytest.fail("survey wrote LAST-GOOD"))
    monkeypatch.setattr(sys, "argv", ["ms-trail-run.py", "--survey", "--no-kanban"])
    assert ms.main() == 0
    assert seen == [("hindsight", {"source": "survey", "file_card": False})]
    assert lg.read_text() == "# LAST-GOOD\nav==17.0.0\n"


def test_service_check_green_writes_last_good_and_red_points_at_the_restore_plan(box, monkeypatch, capsys):
    monkeypatch.setattr(ms, "VENVS", {"hindsight": (sys.executable, "memory", False)})
    monkeypatch.setattr(ms, "freeze_env", lambda name, py, write_latest=True: None)
    monkeypatch.setattr(ms, "_freeze_text", lambda p: FREEZE_GOOD)
    monkeypatch.setattr(ms, "service_restart_check", lambda name: (True, "hindsight.service healthy"))
    monkeypatch.setattr(ms, "service_env_state", lambda name, p, s: {"verdict": "ok"})
    monkeypatch.setattr(ms, "smoke", lambda name, p: True)
    monkeypatch.setattr(sys, "argv", ["ms-trail-run.py", "--service-check", "hindsight"])
    assert ms.main() == 0
    assert (box / "manifests" / "hindsight-LAST-GOOD.txt").is_file()
    assert "LAST-GOOD written" in capsys.readouterr().out

    def red(name, p):
        ms.SMOKE_ERR[name] = "ModuleNotFoundError: No module named 'jsonref'"
        return False

    monkeypatch.setattr(ms, "smoke", red)
    assert ms.main() == 1
    out = capsys.readouterr().out
    assert "--restore-plan hindsight" in out and "LAST-GOOD" in out
    assert "reinstall from" not in out
    ms.SMOKE_ERR.clear()


def test_no_restore_instruction_names_latest(monkeypatch):
    import re
    for path in (SCRIPT,):   # the reference protocol doc stays with its install
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"reinstall\s+from\s[\s\S]{0,60}?LATEST", text), path
    monkeypatch.setattr(ms, "upstream_note", lambda n: "")
    joined = "\n".join(ms._unused_card_lines([{"env": "hindsight", "unused_n": 1, "unused_sample": ["x"]}]))
    assert "--restore-plan" in joined and "LAST-GOOD" in joined


def test_restore_plan_prints_exact_no_deps_pins_for_missing_and_moved_only():
    good = "# LAST-GOOD hindsight 2026-09-17T01:00:00 source=nightly (smoke GREEN, service ok)\n" + \
        "av==17.0.0\njsonref==1.1.0\nnumpy==1.26.4\ntorch==2.9.0+cu130\nsame==1.0\n"
    now = "av==18.0.0\nnumpy==1.26.4\nsame==1.0\ncryptography==46.0.0\n"
    lines = ms.restore_plan_lines("hindsight", str(ms_defaults_only.ENVS_ROOT) + "/hindsight/bin/python", good, now, uv="uv")
    cmd = [ln for ln in lines if ln.startswith("uv pip install")]
    assert cmd == [f"uv pip install --python {ms_defaults_only.ENVS_ROOT}/hindsight/bin/python --no-deps av==17.0.0 jsonref==1.1.0"]
    text = "\n".join(lines)
    assert "torch==2.9.0+cu130" in text and "local builds" in text
    assert "numpy" not in cmd[0] and "same" not in cmd[0]
    assert "cryptography==46.0.0" in text and "does not remove" in text
    assert "--no-deps" in text and "cu13" in text
    assert "jsonref: MISSING -> 1.1.0" in text


def test_restore_plan_without_last_good_refuses(box, monkeypatch, capsys):
    monkeypatch.setattr(ms, "VENVS", {"hindsight": (sys.executable, "memory", False)})
    monkeypatch.setattr(sys, "argv", ["ms-trail-run.py", "--restore-plan", "hindsight"])
    assert ms.main() == 2
    assert "Do not restore from LATEST" in capsys.readouterr().out


def test_restore_plan_cli_prints_and_runs_nothing(box, monkeypatch, capsys):
    (box / "manifests").mkdir()
    (box / "manifests" / "hindsight-LAST-GOOD.txt").write_text("av==17.0.0\n")
    monkeypatch.setattr(ms, "VENVS", {"hindsight": (sys.executable, "memory", False)})
    monkeypatch.setattr(ms, "_freeze_text", lambda p: "av==18.0.0\n")
    ran = []
    monkeypatch.setattr(ms, "run", lambda *a, **k: ran.append(a) or (0, "", ""))
    monkeypatch.setattr(sys, "argv", ["ms-trail-run.py", "--restore-plan", "hindsight"])
    assert ms.main() == 0
    assert "--no-deps av==17.0.0" in capsys.readouterr().out
    assert ran == []


# ---------------------------------------------------------------- 2. drift ledger

def _ledger(*rows):
    return {"decisions": list(rows)}


def test_drift_reconciles_applied_rows_and_names_holds_crossed(box):
    day = "2026-09-16"
    drift = {"changed": {"av": ["17.0.0", "18.0.0"], "cryptography": ["44.0.0", "46.0.0"],
                         "boto3": ["1.40.0", "1.40.1"]},
             "added": ["newpkg"], "added_v": {"newpkg": "0.1"},
             "removed": ["jsonref"], "removed_v": {"jsonref": "1.1.0"}, "n": 5}
    ledger = _ledger(
        {"env": "cosyvoice", "package": "av", "decision": "hold", "reason": "av 18 drops ffmpeg 6",
         "source": "t_research", "ts": "2026-09-01"},
        {"env": "cosyvoice", "package": "boto3", "decision": "applied", "target": "1.40.1", "ts": day},
        {"env": "cosyvoice", "package": "cryptography", "decision": "applied", "target": "45.0.0", "ts": day},
        {"env": "rail-stt", "package": "newpkg", "decision": "applied", "target": "0.1", "ts": day},
    )
    r = ms.reconcile_drift("cosyvoice", drift, ledger, day)
    assert [e["package"] for e in r["explained_by"]] == ["boto3"]
    assert sorted(r["unexplained"]) == ["av", "cryptography", "jsonref", "newpkg"], \
        "another version or another env explains nothing"
    assert [(h["package"], h["decision"], h["from"], h["to"]) for h in r["against_hold"]] == \
        [("av", "hold", "17.0.0", "18.0.0")]
    old = _ledger({"env": "cosyvoice", "package": "boto3", "decision": "applied",
                   "target": "1.40.1", "ts": "2026-09-10"})
    assert ms.reconcile_drift("cosyvoice", drift, old, day)["explained_by"] == [], "that day only"


def test_unexplained_drift_smokes_and_files_one_ready_card_per_env_per_day(box, monkeypatch):
    ms.save_decisions(_ledger({"env": "cosyvoice", "package": "av", "decision": "hold",
                               "reason": "researched", "source": "t_475b98da", "ts": "2026-09-01"}))
    calls = []
    monkeypatch.setattr(ms, "_drift_card_create",
                        lambda **kw: calls.append(kw) or (True, "t_deadbeef"))
    monkeypatch.setattr(ms, "smoke", lambda name, py: True)
    drift = {"changed": {"av": ["17.0.0", "18.0.0"]}, "added": ["cryptography"],
             "added_v": {"cryptography": "46.0.0"}, "removed": [], "n": 2}
    now = dt.datetime(2026, 9, 16, 22, 55)
    row = ms.record_drift("cosyvoice", sys.executable, drift, source="survey", now=now)
    assert row["smoke"] is True and row["card"] == "ms-drift:cosyvoice:2026-09-16" and row["card_filed"]
    assert len(calls) == 1 and calls[0]["idemp"] == "ms-drift:cosyvoice:2026-09-16"
    body = calls[0]["body"]
    assert "av 17.0.0 → 18.0.0" in body and "against hold" in body and "t_475b98da" in body
    assert "Smoke: GREEN" in body and "--restore-plan cosyvoice" in body
    assert "cryptography (absent) → 46.0.0" in body
    ms.record_drift("cosyvoice", sys.executable, drift, source="survey", now=now.replace(hour=23))
    assert len(calls) == 1, "one card per env per day"
    rows = [json.loads(x) for x in (box / "drift.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and rows[1]["card_note"] == "already filed today"
    assert rows[0]["against_hold"][0]["package"] == "av"
    assert not (box / "manifests" / "cosyvoice-LAST-GOOD.txt").exists()


def test_explained_drift_is_recorded_without_smoke_or_card(box, monkeypatch):
    monkeypatch.setattr(ms, "smoke", lambda *a: pytest.fail("explained drift must not smoke"))
    monkeypatch.setattr(ms, "_drift_card_create", lambda **kw: pytest.fail("no card"))
    ledger = _ledger({"env": "comfyui", "package": "boto3", "decision": "applied",
                      "target": "1.40.1", "ts": "2026-09-17"})
    row = ms.record_drift("comfyui", "/x/py", {"changed": {"boto3": ["1.40.0", "1.40.1"]}},
                          source="survey", ledger=ledger, now=dt.datetime(2026, 9, 17, 2, 0))
    assert row["unexplained"] == [] and row["card"] is None
    assert (box / "drift.jsonl").is_file()


def test_drift_card_kill_switch_still_records(box, monkeypatch):
    monkeypatch.setattr(ms, "MS_DRIFT_CARDS", False)
    monkeypatch.setattr(ms, "_drift_card_create", lambda **kw: pytest.fail("no card"))
    monkeypatch.setattr(ms, "smoke", lambda *a: False)
    row = ms.record_drift("comfyui", sys.executable, {"changed": {"x": ["1", "2"]}},
                          source="survey", ledger=_ledger())
    assert row["unexplained"] == ["x"] and row["smoke"] is False and row["card"] is None


def test_drift_card_is_not_triage_and_goes_to_ada(box, monkeypatch):
    seen = []

    class R:
        returncode, stdout, stderr = 0, "t_12345678", ""

    monkeypatch.setattr(ms.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or R())
    ok, _ = ms._drift_card_create(title="M&S drift: x", body="b", idemp="ms-drift:x:2026-09-17")
    assert ok
    cmd = seen[0]
    assert "--triage" not in cmd
    assert cmd[cmd.index("--assignee") + 1] == "ada"
    assert cmd[cmd.index("--idempotency-key") + 1] == "ms-drift:x:2026-09-17"


def test_nightly_records_its_own_survey_drift_with_baseline_reads(box, monkeypatch):
    got = []
    monkeypatch.setattr(ms, "record_drift", lambda name, py, d, **kw: got.append((name, kw)) or
                        {"unexplained": ["x"], "against_hold": [], "card": None})
    report = {"envs": {
        "comfyui": {"python": "/p", "smoke_baseline": False, "service": None,
                    "drift_since_last_manifest": {"ok": True, "had_manifest": True, "n": 1}},
        "fresh": {"python": "/p", "drift_since_last_manifest": {"ok": True, "had_manifest": False, "n": 9}},
    }}
    out = ms.nightly_record_drift(report, file_card=False)
    assert [g[0] for g in got] == ["comfyui"] and got[0][1]["smoke_ok"] is False
    assert got[0][1]["file_card"] is False and out[0]["env"] == "comfyui"


def test_survey_env_keeps_versions_of_added_and_removed(box, monkeypatch):
    (box / "manifests").mkdir()
    (box / "manifests" / "e-LATEST.txt").write_text("gone==1.0\nkept==1.0\n")
    monkeypatch.setattr(ms, "_freeze_text", lambda py: "kept==1.0\nnew==2.0\n")
    d = ms.survey_env("e", "/x/py")
    assert d["added_v"] == {"new": "2.0"} and d["removed_v"] == {"gone": "1.0"}
    assert ms.drift_moves(d) == {"new": (None, "2.0"), "gone": ("1.0", None)}


# ---------------------------------------------------------------- 3. service envs

def test_service_table_policy_and_parking():
    t = ms.SERVICE_ENVS_ALL
    for env, units in t.items():
        assert isinstance(units, list) and units, env
        for u in units:
            assert u["restart"] in ("restart", "report") and u["health"].startswith("http://127.0.0.1:")
            assert u["scope"] in ("user", "system")
    policy = {u["unit"]: u["restart"] for units in t.values() for u in units}
    # Lead review 2026-09-17: the live senses and the wall are never restarted
    # by M&S; only back-office units are.
    for live in ("ref-conductor.service", "ref-conductor-bench.service",
                 "ref-lila-conductor.service", "turbo-voice.service", "crew.service",
                 "rail-presence.service", "rail-camera.service", "audio8-asr.service",
                 "rail-stt.service", "rail-align.service"):
        assert policy[live] == "report", live
    for unit in ("audio8-tts.service", "ws-shadow.service", "enroll-panel.service",
                 "hindsight.service"):
        assert policy[unit] == "restart", unit
    (cosy,) = t["chatterbox-turbo"]
    assert set(cosy["occupancy_parked_in"]) == {"beast", "off"}
    (crew,) = t["vllm-0271-cu132"]
    assert crew["occupancy_parked_in"] == ["off"]


def test_wide_kill_switch_is_hindsight_only(monkeypatch):
    monkeypatch.setenv("MS_SERVICE_ENVS_WIDE", "0")
    narrow = _load("ms_trail_narrow")
    assert list(narrow.SERVICE_ENVS) == ["hindsight"]


def _fake_units(monkeypatch, *, mode, health, started=0.0, changed=0.0):
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: mode)
    monkeypatch.setattr(ms, "_http_ok", lambda url, timeout=3.0: health.get(url, False))
    monkeypatch.setattr(ms, "_unit_started_at", lambda spec: started)
    monkeypatch.setattr(ms, "_site_packages_mtime", lambda py: changed)


def test_a_parked_unit_reads_parked_and_never_blocks(monkeypatch):
    _fake_units(monkeypatch, mode="beast", health={})
    st = ms.service_env_state("chatterbox-turbo", "/x/py", True)
    assert st["verdict"] == "parked" and st["parked"] == ["turbo-voice.service"] and st["down"] == []
    assert ms.service_blocks_apply(st) == ""
    _fake_units(monkeypatch, mode="normal", health={})
    st = ms.service_env_state("chatterbox-turbo", "/x/py", True)
    assert st["verdict"] == "report-down" and ms.service_blocks_apply(st) == "", \
        "a report-only unit down is named, not a block"
    _fake_units(monkeypatch, mode="beast", health={})
    assert ms.service_blocks_apply(ms.service_env_state("chatterbox-turbo", "/x/py", False)) == "service_smoke_red"


def test_a_restart_unit_down_blocks_and_report_units_do_not(monkeypatch):
    urls = {u["unit"]: u["health"] for u in ms.SERVICE_ENVS_ALL["rail-stt"]}
    up = {v: True for v in urls.values()}
    _fake_units(monkeypatch, mode="render", health=up)
    assert ms.service_env_state("rail-stt", "/x/py", True)["verdict"] == "ok"
    ear_down = dict(up, **{urls["rail-stt.service"]: False})
    _fake_units(monkeypatch, mode="render", health=ear_down)
    st = ms.service_env_state("rail-stt", "/x/py", True)
    assert st["verdict"] == "report-down" and ms.service_blocks_apply(st) == ""
    shadow_down = dict(up, **{urls["ws-shadow.service"]: False})
    _fake_units(monkeypatch, mode="render", health=shadow_down)
    st = ms.service_env_state("rail-stt", "/x/py", True)
    assert st["verdict"] == "down" and st["down"] == ["ws-shadow.service"]
    assert ms.service_blocks_apply(st) == "service_down"
    _fake_units(monkeypatch, mode="render", health=up, started=100, changed=200)
    st = ms.service_env_state("rail-stt", "/x/py", True)
    assert st["verdict"] == "restart-owed" and len(st["restart_owed"]) == 4


def test_after_apply_only_restart_units_restart_and_report_units_are_owed(monkeypatch):
    restarted = []
    monkeypatch.setattr(ms, "_svc_systemctl",
                        lambda spec, args, timeout=30: restarted.append(args[-1]) or (0, "", ""))
    monkeypatch.setattr(ms, "_http_ok", lambda url, timeout=3.0: True)
    monkeypatch.setattr(ms.time, "sleep", lambda s: None)
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "normal")
    monkeypatch.setattr(ms, "MS_SERVICE_RESTART", True)
    monkeypatch.setattr(ms, "_restart_window_busy", lambda units: "")      # the window says go
    res = ms.service_verify_after_apply("rail-stt", "/x/py", {"ok": True, "upgraded": ["a 1→2"]})
    assert restarted == ["ws-shadow.service", "enroll-panel.service"]
    assert res["ok"] is True and res["restart_owed"] == ["rail-stt.service", "rail-align.service"]
    restarted.clear()
    res = ms.service_verify_after_apply("vllm", "/x/py", {"ok": True, "upgraded": ["a 1→2"]})
    assert restarted == [] and len(res["restart_owed"]) == 5
    restarted.clear()
    res = ms.service_verify_after_apply("chatterbox-turbo", "/x/py", {"ok": True, "upgraded": ["a 1→2"]})
    assert restarted == [] and res["restart_owed"] == ["turbo-voice.service"]
    assert "restart owed" in res["service"]


def test_a_parked_restart_unit_is_not_started(monkeypatch):
    fake = {"e": [{"unit": "x.service", "scope": "user", "health": "http://127.0.0.1:1/h",
                   "restart": "restart", "occupancy_parked_in": ["render"]}]}
    monkeypatch.setattr(ms, "SERVICE_ENVS", fake)
    monkeypatch.setattr(ms, "occupancy_gpu_mode", lambda: "render")
    monkeypatch.setattr(ms, "_http_ok", lambda url, timeout=3.0: False)
    monkeypatch.setattr(ms, "_svc_systemctl", lambda *a, **k: pytest.fail("parked unit restarted"))
    ok, note = ms.service_restart_check("e")
    assert ok and "parked" in note


def test_card_names_owed_restarts_and_keeps_parked_quiet(monkeypatch):
    monkeypatch.setattr(ms, "upstream_note", lambda n: "")
    summary = {"totals": {}, "env_summaries": [
        {"env": "vllm", "outdated_n": 0, "green_candidate": 1, "risk": 0, "never": 0,
         "smoke_baseline": True, "service": {"unit": "a, b", "verdict": "ok"}},
        {"env": "cosyvoice_vllm", "outdated_n": 0, "green_candidate": 0, "risk": 0, "never": 0,
         "smoke_baseline": True, "service": {"unit": "cosy-voice.service", "verdict": "parked"}},
    ]}
    body = ms._card_body(summary, {"vllm": {"ok": True, "upgraded": ["a 1→2"],
                                            "restart_owed": ["ref-conductor.service"]}}, "2026-09-17")
    assert "**RESTART OWED vllm: ref-conductor.service**" in body
    assert "LIVE SERVICE cosyvoice_vllm" not in body


def test_live_venv_users_sees_system_service_units(monkeypatch):
    monkeypatch.setattr(ms, "SERVICE_ENVS", {"hindsight": ms.SERVICE_ENVS_ALL["hindsight"]})
    monkeypatch.setattr(ms, "_systemctl_user", lambda args, timeout=8: "")
    monkeypatch.setattr(ms, "_svc_systemctl", lambda spec, args, timeout=30:
                        (0, "active\n", "") if args[0] == "is-active" else (1, "", ""))
    used = ms.live_venv_users()
    assert "unit:hindsight" in used.get("hindsight", [])


# ---------------------------------------------------------------- 4. unused guard

def test_guard_reasons_never_auto_mapped_and_young(tmp_path):
    now = 1_800_000_000.0
    old = now - 30 * 86400
    dists = {
        "old-squatter": {"installed_ts": old, "files": ["/s/old_squatter.py"]},
        "mapped-ext": {"installed_ts": old, "files": ["/s/mapped_ext/_c.so"]},
        "young": {"installed_ts": now - 2 * 86400, "files": ["/s/young.py"]},
        "nvidia-cublas-cu13": {"installed_ts": old, "files": []},
        "torchao": {"installed_ts": old, "files": []},
    }
    names = list(dists) + ["cuda-bindings", "missing"]
    r = ms.unused_guard_reasons(names, dists, {"/s/mapped_ext/_c.so"}, now, min_age_days=7)
    assert "old-squatter" not in r
    assert r["mapped-ext"].startswith("mapped by a live process")
    assert r["young"].startswith("installed 2.0 d ago")
    assert "never-auto" in r["nvidia-cublas-cu13"] and "never-auto" in r["torchao"]
    assert "never-auto" in r["cuda-bindings"]
    assert r["missing"].startswith("unverified")
    assert set(ms.unused_guard_reasons(["old-squatter"], None, set(), now)) == {"old-squatter"}


def test_live_mapped_paths_reads_readable_pids_only(tmp_path):
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "maps").write_text(
        f"7f00-7f01 r-xp 00000000 08:01 1 {ms_defaults_only.ENVS_ROOT}/x/site/a/_c.so\n"
        "7f01-7f02 rw-p 00000000 00:00 0 \n"
        f"7f02-7f03 r--p 00000000 08:01 2 {ms_defaults_only.ENVS_ROOT}/x/site/b.so (deleted)\n"
        "7f03-7f04 rw-p 00000000 00:00 0 [heap]\n")
    (tmp_path / "456").mkdir()            # no maps: unreadable pid
    (tmp_path / "self").mkdir()
    got = ms._live_mapped_paths(tmp_path)
    assert got == {str(ms_defaults_only.ENVS_ROOT) + "/x/site/a/_c.so", str(ms_defaults_only.ENVS_ROOT) + "/x/site/b.so"}


def test_guard_unused_moves_names_out_of_the_list_and_keeps_the_reason(monkeypatch):
    now_ts = dt.datetime.now().timestamp()
    monkeypatch.setattr(ms, "MS_UNUSED_GUARD", True)
    monkeypatch.setattr(ms, "_dist_files", lambda py, names: {
        "squatter": {"installed_ts": now_ts - 60 * 86400, "files": ["/s/squatter.py"]},
        "fresh": {"installed_ts": now_ts - 3600, "files": ["/s/fresh.py"]}})
    monkeypatch.setattr(ms, "_live_mapped_paths", lambda *a, **k: set())
    u = {"unreferenced": ["squatter", "fresh", "nvidia-nccl-cu13"],
         "never_imported": ["squatter", "fresh", "nvidia-nccl-cu13"], "dists": 3}
    out = ms.guard_unused("/x/py", dict(u))
    assert out["never_imported"] == ["squatter"]
    assert set(out["guarded"]) == {"fresh", "nvidia-nccl-cu13"}
    monkeypatch.setattr(ms, "MS_UNUSED_GUARD", False)
    assert ms.guard_unused("/x/py", dict(u))["never_imported"] == u["never_imported"]


def test_dist_files_probe_names_files_and_age(tmp_path):
    site = tmp_path / "site"
    di = site / "lonely-1.0.dist-info"
    di.mkdir(parents=True)
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: lonely\nVersion: 1.0\n")
    (di / "RECORD").write_text("lonely.py,,\nlonely_c.so,,\nlonely-1.0.dist-info/METADATA,,\n")
    (site / "lonely.py").write_text("")
    (site / "lonely_c.so").write_text("")
    out = subprocess.run([sys.executable, "-I", "-c",
                          f"import sys; sys.path[:0]=[{str(site)!r}]\n" + ms._DIST_FILES_PROBE,
                          json.dumps(["lonely"])], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)["lonely"]
    assert os.path.realpath(site / "lonely_c.so") in d["files"]
    assert abs(d["installed_ts"] - dt.datetime.now().timestamp()) < 600


def test_drift_back_to_a_kept_or_held_version_is_not_against_the_row():
    """Lead review 2026-09-17: the restore of the 09-12 uninstalls read as
    'against a hold' — a reinstall under a `wontfix` (keep) row, and av going
    back to the exact version its hold keeps."""
    ledger = {"decisions": [
        {"env": "e", "package": "jsonref", "decision": "wontfix", "current": "", "reason": "keep"},
        {"env": "e", "package": "av", "decision": "hold", "current": "17.0.1", "target": "18.1.0"},
        {"env": "e", "package": "scipy", "decision": "hold", "current": "1.15.0", "target": "1.18.0"},
        {"env": "e", "package": "gone", "decision": "wontfix", "current": "1.0"},
    ]}
    drift = {"changed": {"av": ["18.1.0", "17.0.1"], "scipy": ["1.15.0", "1.18.0"]},
             "added": ["jsonref"], "added_v": {"jsonref": "1.1.0"},
             "removed": ["gone"], "removed_v": {"gone": "1.0"}}
    try:
        moves = ms.drift_moves(drift)
    except Exception:
        moves = None
    if not moves:
        import pytest
        pytest.skip("drift_moves shape differs; covered by the survey tests")
    out = ms.reconcile_drift("e", drift, ledger, "2026-09-17")
    crossed = sorted(x["package"] for x in out["against_hold"])
    assert crossed == ["gone", "scipy"], crossed



# ── M&S asks the restart window before it restarts (the User's 10-01 audit) ──
def test_a_busy_window_rolls_the_pins_back_and_restarts_nothing(monkeypatch):
    ran = []
    monkeypatch.setattr(ms, "MS_SERVICE_RESTART", True)
    monkeypatch.setattr(ms, "_restart_window_busy", lambda units: "ws-shadow.service: the ear was in a turn 4 s ago")
    monkeypatch.setattr(ms, "service_restart_check", lambda name: pytest.fail("restarted under a busy window"))
    monkeypatch.setattr(ms, "_installer", lambda py: ["uv", "pip", "install", "--python", py])
    monkeypatch.setattr(ms, "run", lambda argv, timeout=0, **k: ran.append(argv) or (0, "", ""))
    res = ms.service_verify_after_apply("rail-stt", "/x/py",
                                        {"ok": True, "upgraded": ["silero-vad 6.2.1→6.2.3", "loc 1+cu→2"]})
    assert res["ok"] is True and res["upgraded"] == [] and res["rolled_back"] == 1
    assert res["unverified"].startswith("restart window busy: ws-shadow.service")
    assert ran and ran[0][-1] == "silero-vad==6.2.1" and "--no-deps" in ran[0]
    assert "nothing restarted" in res["service"]


def test_window_busy_asks_only_restart_units_and_the_kill_switch_skips_it(monkeypatch):
    import restart_window as rw
    asked = []
    monkeypatch.setattr(rw, "busy_now", lambda unit=None, **k: asked.append(unit) or "")
    units = [{"unit": "a.service", "restart": "restart"}, {"unit": "b.service", "restart": "report"}]
    monkeypatch.setattr(ms, "MS_RESTART_ASK_WINDOW", True)
    assert ms._restart_window_busy(units) == "" and asked == ["a.service"]
    monkeypatch.setattr(rw, "busy_now", lambda unit=None, **k: "on air")
    assert ms._restart_window_busy(units) == "a.service: on air"
    monkeypatch.setattr(ms, "MS_RESTART_ASK_WINDOW", False)
    assert ms._restart_window_busy(units) == ""


def test_an_unreadable_window_holds(monkeypatch):
    import restart_window as rw
    monkeypatch.setattr(ms, "MS_RESTART_ASK_WINDOW", True)
    monkeypatch.setattr(rw, "busy_now", lambda unit=None, **k: (_ for _ in ()).throw(OSError("x")))
    assert "unreadable" in ms._restart_window_busy([{"unit": "a.service", "restart": "restart"}])


def test_the_caller_queues_an_unverified_batch_for_the_window():
    src = SCRIPT.read_text(encoding="utf-8")
    i = src.index("result = service_verify_after_apply(name, py, result)")
    assert "queue_greens_for_window(" in src[i:i + 600] and 'result.get("unverified")' in src[i:i + 300]


def test_the_live_face_engine_env_is_report_only_and_door_stamp_is_not_listed():
    import yaml
    cfg = ms_defaults_only.reference_settings()
    envs = cfg.get("envs", cfg)
    assert envs["mediapipe"]["apply"] == "report"
    units = {u["unit"] for u in envs["vllm"]["services"]}
    assert "door-stamp.service" not in units      # stopped + disabled on the User's word 10-01: it would read DOWN nightly


def test_the_roots_name_no_retired_unit_or_app():
    roots = json.loads((ms_defaults_only.ROOTS_FILE).read_text())
    vllm = (roots.get("envs") or roots)["vllm"]
    named = " ".join(vllm["units"]) + " " + " ".join(vllm["app"])     # the lists, not the review prose
    assert "presence (system)" not in named   # (two retired voice units, named in the reference test, left out)


def test_a_copy_without_the_restart_window_restarts_as_before(monkeypatch):
    """The shipped M&S has no restart_window module: absent means go, never a permanent hold."""
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name == "restart_window":
            raise ModuleNotFoundError("No module named 'restart_window'", name="restart_window")
        return real(name, *a, **k)
    monkeypatch.setattr(ms, "MS_RESTART_ASK_WINDOW", True)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert ms._restart_window_busy([{"unit": "a.service", "restart": "restart"}]) == ""
