"""TurboKrew: Vera+Steward share ops for 4h. Ada stays crew. No occupancy."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_mode_sandbox  # noqa: E402
import pytest  # noqa: E402

tk = krew_mode_sandbox.load("turbo_krew", "crew/scripts/turbo-krew.py")


@pytest.fixture(autouse=True)
def _never_the_passwd_tree(monkeypatch):
    """turbo-krew reads its tree at call time (KREW_HERMES, else the passwd home's): every test here names its own."""
    for k, v in krew_mode_sandbox.ENV.items():
        monkeypatch.setenv(k, v)

SAMPLE = """\
model:
  default: crew
  provider: custom:crew
  base_url: http://127.0.0.1:8005/v1
  context_length: 102400
custom_providers:
- name: crew
  base_url: http://127.0.0.1:8005/v1
  model: crew
  api_key: sk-local-noauth
auxiliary:
  vision:
    provider: custom
    base_url: http://127.0.0.1:8005/v1
    model: crew
fallback_model:
  provider: custom
  model: ops
  base_url: http://127.0.0.1:8000/v1
kanban:
  default_assignee: steward
"""

ADA_SAMPLE = """\
model:
  default: crew
  provider: custom:crew
  base_url: http://127.0.0.1:8005/v1
custom_providers:
- name: crew
  base_url: http://127.0.0.1:8005/v1
  model: crew
- name: ops
  base_url: http://127.0.0.1:8000/v1
  model: ops
"""


def _tree(tmp_path: Path, mode: str = "normal") -> Path:
    house = tmp_path / "hermes"
    for name, body in (
        ("vera", SAMPLE),
        ("steward", SAMPLE),
        ("ada", ADA_SAMPLE),
    ):
        d = house / "profiles" / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(body, encoding="utf-8")
    (house / "krew-gpu-mode").write_text(mode + "\n", encoding="utf-8")
    return house


def test_set_top_model_leaves_aux_and_fallback():
    new, changed = tk.pin_to_ops(SAMPLE)
    assert changed
    top = tk.read_top_model(new)
    assert top["default"] == "ops"
    assert top["provider"] == "custom:ops"
    assert top["base_url"] == tk.OPS_URL
    assert "auxiliary:\n  vision:\n    provider: custom\n    base_url: http://127.0.0.1:8005/v1" in new
    assert "fallback_model:\n  provider: custom\n  model: ops\n  base_url: http://127.0.0.1:8000/v1" in new
    assert "- name: ops" in new
    assert tk.pin_kind(new) == "ops"
    assert re.search(r"^  extra_body:\n(?:.*\n)*?    priority: 50\n", new, re.M)
    assert "priority: 50" in new.split("custom_providers:")[1].split("auxiliary:")[0]
    back, _ = tk.pin_to_crew(new)
    assert tk.read_top_model(back)["default"] == "crew"
    assert tk.read_top_model(back)["base_url"] == tk.CREW_URL


def test_default_ttl_is_four_hours():
    assert tk.TTL_DEFAULT_S == 14400
    assert tk.ttl_s() == 14400


def test_on_pins_vera_steward_not_ada(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "8")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    out = tk.turn_on(house=house, ttl=60, reason="test")
    assert out["ok"] is True
    assert out.get("ops_seqs") == 8
    assert out["on"] is True
    pins = out["pins"]
    assert pins["vera"] == "ops"
    assert pins["steward"] == "ops"
    assert pins["ada"] == "crew"
    ada = (house / "profiles" / "ada" / "config.yaml").read_text(encoding="utf-8")
    assert tk.read_top_model(ada)["default"] == "crew"
    vera = (house / "profiles" / "vera" / "config.yaml").read_text(encoding="utf-8")
    assert "base_url: http://127.0.0.1:8005/v1" in vera  # aux + crew provider stay
    assert tk.read_top_model(vera)["base_url"] == tk.OPS_URL
    bak = house / "profiles" / "vera" / "config.yaml.pre-turbo-krew"
    assert bak.is_file()
    assert tk.pin_kind(bak.read_text(encoding="utf-8")) == "crew"


def test_off_does_not_redisable_allowlisted_skill(tmp_path, monkeypatch):
    """A snapshot taken before an allowlist change must not roll the skill back."""
    house = _tree(tmp_path)
    vera = house / "profiles" / "vera" / "config.yaml"
    vera.write_text(SAMPLE + "skills:\n  disabled:\n  - sdlc-review\n", encoding="utf-8")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    tk.turn_on(house=house, ttl=60, reason="test")
    live = vera.read_text(encoding="utf-8").replace("  - sdlc-review\n", "")
    vera.write_text(live, encoding="utf-8")
    assert "sdlc-review" not in vera.read_text(encoding="utf-8")
    tk.turn_off(house=house, reason="test", quiet=True)
    after = vera.read_text(encoding="utf-8")
    assert "sdlc-review" not in after
    assert tk.pin_kind(after) == "crew"


def test_off_restores_backup(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    tk.turn_on(house=house, ttl=60, reason="test")
    out = tk.turn_off(house=house, reason="test", quiet=True)
    assert out["ok"] is True
    assert out["on"] is False
    pins = tk.profile_pins(house)
    assert pins["vera"] == "crew"
    assert pins["steward"] == "crew"
    assert pins["ada"] == "crew"
    assert not (house / "profiles" / "vera" / "config.yaml.pre-turbo-krew").exists()
    assert not (house / "turbo-krew.json").exists()


def test_refuse_when_not_standard(tmp_path, monkeypatch):
    house = _tree(tmp_path, mode="render")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    out = tk.turn_on(house=house, ttl=60)
    assert out["ok"] is False
    assert "Standard only" in out["error"]
    assert tk.profile_pins(house)["vera"] == "crew"


def test_refuse_when_ops_seqs_too_low(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    out = tk.turn_on(house=house, ttl=60)
    assert out["ok"] is False
    assert "max-num-seqs 1" in out["error"]
    assert tk.profile_pins(house)["vera"] == "crew"


def test_on_accepts_eight_seqs(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "8")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    out = tk.turn_on(house=house, ttl=60, reason="test")
    assert out["ok"] is True
    vera = (house / "profiles" / "vera" / "config.yaml").read_text(encoding="utf-8")
    assert "priority: 50" in vera
    ada = (house / "profiles" / "ada" / "config.yaml").read_text(encoding="utf-8")
    assert "priority: 50" not in ada


def test_refuse_when_ops_down(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "0")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    out = tk.turn_on(house=house, ttl=60)
    assert out["ok"] is False
    assert "ops" in out["error"]
    assert tk.profile_pins(house)["vera"] == "crew"


def test_toggle_on_then_off(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    a = tk.toggle(house=house)
    assert a["on"] is True
    b = tk.toggle(house=house)
    assert b["on"] is False
    assert tk.profile_pins(house)["steward"] == "crew"


def test_leaving_standard_parks_pins_keeps_hour(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "8")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    tk.turn_on(house=house, ttl=600, reason="test")
    assert tk.profile_pins(house)["vera"] == "ops"
    until = tk.status_snapshot(house)["until"]
    (house / "krew-gpu-mode").write_text("render\n", encoding="utf-8")
    out = tk.apply_occupancy("render", house=house, quiet=True)
    assert out["on"] is True
    assert out["parked"] is True
    assert out["sharing"] is False
    assert tk.profile_pins(house)["vera"] == "crew"
    assert tk.profile_pins(house)["ada"] == "crew"
    assert (house / "profiles" / "vera" / "config.yaml.pre-turbo-krew").is_file()
    assert (house / "turbo-krew.json").is_file()
    assert abs(tk.status_snapshot(house)["until"] - until) < 1e-6


def test_return_to_standard_resumes_ops_pins(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "8")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    tk.turn_on(house=house, ttl=600, reason="test")
    (house / "krew-gpu-mode").write_text("render\n", encoding="utf-8")
    tk.apply_occupancy("render", house=house, quiet=True)
    assert tk.profile_pins(house)["vera"] == "crew"
    (house / "krew-gpu-mode").write_text("normal\n", encoding="utf-8")
    out = tk.apply_occupancy("normal", house=house, quiet=True)
    assert out["on"] is True
    assert out["parked"] is False
    assert out["sharing"] is True
    assert tk.profile_pins(house)["vera"] == "ops"
    assert tk.profile_pins(house)["steward"] == "ops"
    assert tk.profile_pins(house)["ada"] == "crew"


def test_resume_stays_crew_until_ops_up(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "8")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    tk.turn_on(house=house, ttl=600, reason="test")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "0")
    (house / "krew-gpu-mode").write_text("normal\n", encoding="utf-8")
    out = tk.apply_occupancy("normal", house=house, quiet=True)
    assert out["on"] is True
    assert tk.profile_pins(house)["vera"] == "crew"


def test_sharing_is_the_pin_not_the_lease(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "8")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    tk.turn_on(house=house, ttl=600, reason="test")
    snap = tk.status_snapshot(house)
    assert snap["on"] is True
    assert snap["sharing"] is True
    (house / "krew-gpu-mode").write_text("render\n", encoding="utf-8")
    parked = tk.apply_occupancy("render", house=house, quiet=True)
    assert parked["on"] is True
    assert parked["sharing"] is False
    assert parked["pins"]["vera"] == "crew"


def test_expired_orphan_ops_pins_heal_on_status(tmp_path, monkeypatch):
    """A dead lease that left YAML on ops must not read as off-and-invisible."""
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "8")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    tk.turn_on(house=house, ttl=600, reason="test")
    st = json.loads((house / "turbo-krew.json").read_text())
    st["until"] = 1.0
    (house / "turbo-krew.json").write_text(json.dumps(st), encoding="utf-8")
    assert tk.profile_pins(house)["vera"] == "ops"
    snap = tk.status_snapshot(house)
    assert snap["on"] is False
    assert snap["sharing"] is False
    assert snap["pins"]["vera"] == "crew"
    assert snap["pins"]["steward"] == "crew"
    assert not (house / "turbo-krew.json").exists()


def test_status_is_cheap_json(tmp_path, monkeypatch):
    house = _tree(tmp_path)
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    snap = tk.status_snapshot(house)
    assert snap["on"] is False
    assert snap["remaining_s"] == 0
    assert snap["pins"]["ada"] == "crew"


# --- 2026-09-18 (the owner "the whole point is to double throughput"): the lease sets Ada's caps ----

def test_turbo_caps_match_krew_mode_table_and_set_ada_caps_rewrites_only_the_pair():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text()
    m = re.search(r'"turbo":\s*\((\d+),\s*(\d+)\)', src)
    assert m and (int(m.group(1)), int(m.group(2))) == tk.TURBO_ADA_CAPS
    m = re.search(r'"normal":\s*\((\d+),\s*(\d+)\)', src)
    assert m and (int(m.group(1)), int(m.group(2))) == tk.NORMAL_ADA_CAPS
    text = "model:\n  model: crew\nkanban:\n  dispatch_interval_seconds: 120\n  max_in_progress: 3\n  max_in_progress_per_profile: 2\n  review_dispatch: true\nskills:\n  x: 1\n"
    new, changed = tk.set_ada_caps(text, 6, 3)
    assert changed and "  max_in_progress: 6\n" in new and "  max_in_progress_per_profile: 3\n" in new
    assert "dispatch_interval_seconds: 120" in new and "review_dispatch: true" in new and "skills:\n  x: 1" in new
    assert tk.set_ada_caps(new, 6, 3) == (new, False)
