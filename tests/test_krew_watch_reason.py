"""A red unit's own reason (the User 2026-10-06: a failure is NAMED, never just a red tile): janitor/krew_watch.py
file_reason + check, and the janitor's snapshot carrying it. The house's test_house_watch.py, its reason-file half;
the list half is tests/test_janitor_watch.py (krew's list is the unit registry + unit-notes.json, not a hand list)."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401  (the janitor's settings, in a temp folder)
sys.path.insert(0, str(ROOT / "janitor"))
import krew_watch as hw  # noqa: E402


def _janitor():
    spec = importlib.util.spec_from_file_location("krew_janitor_watch_t", ROOT / "janitor" / "krew-janitor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def runner_for(table: dict, journal: str = ""):
    """systemctl show / journalctl stub: table[unit] = {prop: value}."""
    def run(cmd, **_kw):
        if cmd[0] == "journalctl":
            return SimpleNamespace(stdout=journal, returncode=0)
        unit = next(c for c in cmd if c.endswith((".service", ".timer")))
        kv = table.get(unit, {})
        return SimpleNamespace(stdout="".join(f"{k}={v}\n" for k, v in kv.items()), returncode=0)
    return run


def up(**kw):
    return {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "Result": "success", **kw}


FAILED = up(ActiveState="failed", SubState="failed", Result="exit-code")
ZS_10_05 = {"ok": False, "display": ":20", "checks": [
    {"check": "an X server answers on her display", "ok": True, "why": ""},
    {"check": "his pointer did not move", "ok": False,
     "why": "x:1920 y:960 screen:0 window:6291466 -> x:1932 y:942 screen:0 window:6291466"},
    {"check": "his focused window did not change", "ok": True, "why": "6291466 -> 6291466"}]}


def test_a_failed_unit_with_a_check_file_names_the_failing_check(tmp_path):
    f = tmp_path / "crissa-screen-check.json"
    f.write_text(json.dumps(ZS_10_05))
    r = hw.check({"unit": "krew-crissa-screen.service", "expect": "exited", "reason_file": str(f)},
                 runner=runner_for({"krew-crissa-screen.service": FAILED}))
    assert r["red"] and r["why"].startswith("failed (exit-code): failed check: his pointer did not move (x:1920")
    assert "crissa-screen-check.json" in r["reason"] and "focused window" not in r["why"]


def test_the_snapshot_red_row_and_headline_carry_the_check_name(tmp_path, monkeypatch):
    f = tmp_path / "crissa-screen-check.json"
    f.write_text(json.dumps(ZS_10_05))
    r = hw.check({"unit": "krew-crissa-screen.service", "expect": "exited", "reason_file": str(f)},
                 runner=runner_for({"krew-crissa-screen.service": FAILED}))
    j = _janitor()
    fake_hw = SimpleNamespace(read_snapshot=lambda: {}, gaps=lambda prev=None: [])
    snap = j.janitor_snapshot(time.time(), {}, {"ts": "t"}, [r], fake_hw)
    assert "his pointer did not move" in snap["reds"][0]["why"]
    assert "his pointer did not move" in snap["headline"]


def test_reason_shapes_error_key_plain_text_and_missing(tmp_path):
    j = tmp_path / "r.json"
    j.write_text(json.dumps({"ok": False, "error": "model weights missing"}))
    assert hw.file_reason({"reason_file": {"path": str(j)}}).startswith("model weights missing [r.json ")
    t = tmp_path / "r.log"
    t.write_text("start\nbuild rc=2: tsc failed\n\n")
    assert hw.file_reason({"reason_file": str(t)}).startswith("build rc=2: tsc failed [r.log ")
    assert hw.file_reason({"reason_file": str(tmp_path / "nope.json")}) == f"no reason file at {tmp_path / 'nope.json'}"
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"ok": True, "checks": [{"check": "a", "ok": True}]}))
    assert hw.file_reason({"reason_file": str(ok)}).startswith("its record says ok [ok.json ")
    assert hw.file_reason({}) == ""


def test_many_failing_checks_are_capped_and_counted(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"ok": False, "checks": [{"check": f"c{i}", "ok": False} for i in range(6)]}))
    out = hw.file_reason({"reason_file": str(f)})
    assert out.startswith("failed checks: c0; c1; c2; c3 +2 more [c.json ")


def test_a_reason_file_never_turns_a_green_unit_red(tmp_path):
    f = tmp_path / "crissa-screen-check.json"
    f.write_text(json.dumps(ZS_10_05))
    r = hw.check({"unit": "krew-crissa-screen.service", "expect": "exited", "reason_file": str(f)},
                 runner=runner_for({"krew-crissa-screen.service": up(SubState="exited")}))
    assert not r["red"] and r["why"] == "" and "reason" not in r
