"""An env being rebuilt takes no in-place change (the User 09-28, comfyui).

The hold file (`~/.hermes/ms-env-hold.json`, one row per env) stops the
night's apply for that env and every card that would lead to one - the
mitigation card, the land card and the Comfy render-SOTA card - while the
survey, the freeze and the report go on. Kill: MS_ENV_HOLD=0.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_SRC = Path(os.environ.get("MS_TRAIL_UNDER_TEST") or ROOT / "ms" / "scripts" / "ms-trail-run.py")
_spec = importlib.util.spec_from_file_location("ms_env_hold", _SRC)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


@pytest.fixture(autouse=True)
def _no_live_board(monkeypatch):
    """Nothing here reads the live board: the 14-day dedupe and the board
    listing both see an empty board."""
    monkeypatch.setattr(ms, "_kanban_board_db", lambda *a, **k: None)
    monkeypatch.setattr(ms, "recently_closed", lambda *a, **k: "")


def _hold(monkeypatch, tmp_path, rows) -> Path:
    p = tmp_path / "ms-env-hold.json"
    if rows is not None:
        p.write_text(rows if isinstance(rows, str) else json.dumps(rows))
    monkeypatch.setattr(ms, "MS_ENV_HOLD_FILE", p)
    monkeypatch.setattr(ms, "MS_ENV_HOLD", True)
    return p


def test_no_file_holds_nothing(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, None)
    assert ms.env_hold("comfyui") is None


def test_a_row_holds_only_its_env(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, {"comfyui": {"reason": "rebuild"}})
    assert ms.env_hold("comfyui") == {"reason": "rebuild"}
    assert ms.env_hold("hindsight") is None


def test_a_bare_string_row_is_its_reason(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, {"comfyui": "rebuild"})
    assert ms.env_hold("comfyui") == {"reason": "rebuild"}


def test_an_unreadable_file_holds_every_env(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, "{not json")
    for env in ("comfyui", "hindsight", "vllm"):
        assert "unreadable" in ms.env_hold(env)["reason"]
    _hold(monkeypatch, tmp_path, "[1, 2]")
    assert "unreadable" in ms.env_hold("vllm")["reason"]


def test_kill_switch(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, {"comfyui": {"reason": "rebuild"}})
    monkeypatch.setattr(ms, "MS_ENV_HOLD", False)
    assert ms.env_hold("comfyui") is None


def test_held_env_applies_nothing_and_is_not_a_failure(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, {"comfyui": {"reason": "rebuild"}})
    report = {"envs": {"comfyui": {}}, "applied": {}}
    batches = [("green", [{"package": "timm"}, {"package": "pyjwt"}]),
               ("pending-major", [{"package": "kornia-rs"}])]
    assert ms.hold_env_batches("comfyui", batches, report) == []
    a = report["applied"]["comfyui"]
    assert a["ok"] is True and a["skipped"] == "env_hold"
    assert a["changed_n"] == 0 and a["held_n"] == 3
    assert report["envs"]["comfyui"]["hold"] == {"reason": "rebuild"}
    # the tally reads it as nothing attempted, not a rollback
    t = ms.apply_tally(report["applied"])["envs"]["comfyui"]
    assert t == {"landed": 0, "rolled": [], "failed": [], "attempted": 0, "cause": ""}


def test_other_env_batches_pass_through(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, {"comfyui": {"reason": "rebuild"}})
    report = {"envs": {"hindsight": {}}, "applied": {}}
    batches = [("green", [{"package": "x"}])]
    assert ms.hold_env_batches("hindsight", batches, report) is batches
    assert report["applied"] == {} and "hold" not in report["envs"]["hindsight"]


def test_held_env_gets_no_land_card(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, {"comfyui": {"reason": "rebuild"}})
    spawns = [{"env": "comfyui", "parent_id": "t_a", "rows": [{"package": "kornia-rs"}]},
              {"env": "hindsight", "parent_id": "t_b", "rows": [{"package": "x"}]}]
    monkeypatch.setattr(ms, "plan_kanban_ms_land", lambda *a, **k: ({}, {}, spawns))
    made = []
    monkeypatch.setattr(ms, "_kanban_create_land_child",
                        lambda env, parent, rows: made.append(env) or f"t_{env}")
    by_env, land_ids = ms.ingest_kanban_ms_go("", cards=[])
    assert made == ["hindsight"]
    assert "comfyui" not in by_env and "comfyui" not in land_ids
    assert land_ids == {"hindsight": ["t_hindsight"]}


def _fake_hermes(monkeypatch, calls):
    def run(argv, *a, **k):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    monkeypatch.setattr(ms.subprocess, "run", run)


def test_held_env_gets_no_mitigation_card(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, {"comfyui": {"reason": "rebuild"}})
    calls: list = []
    _fake_hermes(monkeypatch, calls)
    monkeypatch.setattr(ms, "MS_FILE_MITIGATIONS", True)
    monkeypatch.setattr(ms, "DELIVERABLES_DIR", tmp_path / "deliv")
    monkeypatch.setattr(ms, "_load_decisions", lambda *a, **k: {"decisions": []})
    monkeypatch.setattr(ms, "kanban_board_cards", lambda *a, **k: [])
    risk = [{"package": "diffusers", "current": "0.38.0", "latest": "0.40.0", "bucket": "risk"}]
    report = {"envs": {"comfyui": {"role": "r", "details": {"risk": risk}}}}
    ms.file_mitigation_cards(report, "2026-09-29")
    assert not [c for c in calls if "create" in c]
    # the same risk in an env with no hold is filed - the fake would have seen it
    report = {"envs": {"hindsight": {"role": "r", "details": {"risk": risk}}}}
    ms.file_mitigation_cards(report, "2026-09-29")
    assert [c for c in calls if "create" in c]


def test_held_comfyui_gets_no_render_sota_card(monkeypatch, tmp_path):
    _hold(monkeypatch, tmp_path, {"comfyui": {"reason": "rebuild"}})
    calls: list = []
    _fake_hermes(monkeypatch, calls)
    monkeypatch.setattr(ms, "MS_FILE_MITIGATIONS", True)
    report = {"render": {"sota": {"behind": True, "local": "v0.37.4", "origin_latest": "v0.38.0"}}}
    ms.file_render_sota_card(report, "2026-09-29", "", cards=[])
    assert calls == []
