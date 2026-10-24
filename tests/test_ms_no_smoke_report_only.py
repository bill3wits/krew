"""An env with no declared smoke test is report-only and never applied (the User 09-29).

`heavy` was only ever a label; the fallback smoke is `import sys`, which proves
nothing, so an env M&S had never been told how to test got the nightly greens
and a LAST-GOOD rewritten from them (mediapipe and mfa on 09-28). Kill:
MS_REQUIRE_SMOKE=0.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_SRC = Path(os.environ.get("MS_TRAIL_UNDER_TEST") or ROOT / "ms" / "scripts" / "ms-trail-run.py")
_spec = importlib.util.spec_from_file_location("ms_no_smoke", _SRC)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

BATCHES = [("green", [{"package": "numpy"}, {"package": "opencv-python"}]),
           ("pending-major", [{"package": "protobuf"}])]


def test_env_without_a_smoke_test_applies_nothing(monkeypatch):
    monkeypatch.setattr(ms, "MS_REQUIRE_SMOKE", True)
    assert not ms.has_declared_smoke("mediapipe-x")
    report = {"envs": {"mediapipe-x": {}}, "applied": {}}
    assert ms.smoke_gate_batches("mediapipe-x", list(BATCHES), report) == []
    a = report["applied"]["mediapipe-x"]
    assert a == {"ok": True, "upgraded": [], "skipped": "no_declared_smoke",
                 "changed_n": 0, "held_n": 3}
    assert report["envs"]["mediapipe-x"]["report_only"] == "no_declared_smoke"
    # nothing attempted, not a rollback, and the cell says why
    t = ms.apply_tally(report["applied"])["envs"]["mediapipe-x"]
    assert t["attempted"] == 0 and t["landed"] == 0
    assert "no smoke test" in ms.applied_label(a, considered_apply=True, green_n=3)


def test_env_with_a_declared_smoke_passes_through(monkeypatch):
    monkeypatch.setattr(ms, "MS_REQUIRE_SMOKE", True)
    assert ms.has_declared_smoke("hindsight")
    report = {"envs": {"hindsight": {}}, "applied": {}}
    b = list(BATCHES)
    assert ms.smoke_gate_batches("hindsight", b, report) is b
    assert report["applied"] == {} and "report_only" not in report["envs"]["hindsight"]


def test_nothing_to_apply_still_marks_the_env_report_only(monkeypatch):
    monkeypatch.setattr(ms, "MS_REQUIRE_SMOKE", True)
    report = {"envs": {"mfa-x": {}}, "applied": {}}
    assert ms.smoke_gate_batches("mfa-x", [], report) == []
    assert report["applied"] == {} and report["envs"]["mfa-x"]["report_only"] == "no_declared_smoke"


def test_kill_switch_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setattr(ms, "MS_REQUIRE_SMOKE", False)
    report = {"envs": {"mfa-x": {}}, "applied": {}}
    b = list(BATCHES)
    assert ms.smoke_gate_batches("mfa-x", b, report) is b


def test_the_gate_runs_on_every_apply_batch_after_the_hold():
    """Greens, pending majors and board GO rows all reach apply_green through
    `batches`; the gate sits right after the hold and before anything is tried."""
    src = _SRC.read_text()
    main = src[src.index("batches = hold_env_batches(name, batches, report)"):]
    assert main.index("batches = smoke_gate_batches(name, batches, report)") < main.index("apply_green(name, py, rows)")
