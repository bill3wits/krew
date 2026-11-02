"""Render keeps the kanban lane — and All OS Off still takes everything.

The Off half matters as much as the Render half: both used to share one
roster, so thinning Render for the lane would have quietly stopped Off from
killing Ada/Lila/Vera/Bash too. No GPU, no systemd.
"""
from __future__ import annotations

import json
import time
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_mode_sandbox  # noqa: E402
hm = krew_mode_sandbox.load("krew_mode_lane", "crew/scripts/krew-mode")   # never the passwd tree

LANE = ("ada", "bash", "lila", "vera", "steward")







def test_off_roster_still_takes_ada_and_the_whole_lane():
    assert "hermes-ada-gateway" in hm.OFF_SLEEP_GATEWAYS
    for who in ("ada", "bash", "lila", "vera", "steward", "crissa"):
        assert who in hm.OFF_SLEEP_PROFILES, who


def test_off_and_render_rosters_are_not_the_same_object():
    assert hm.OFF_SLEEP_PROFILES != hm.RENDER_SLEEP_PROFILES
    assert hm.OFF_SLEEP_GATEWAYS != hm.RENDER_SLEEP_GATEWAYS


def test_cmd_off_uses_the_off_roster_not_renders():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    off = src[src.find("def cmd_off") : src.find("def units_to_wake")]
    assert "sleep_off_rest()" in off
    assert "sleep_render_rest()" not in off, "Off must not follow Render's thinner roster"


def test_wake_covers_both_rosters():
    """Leaving Off comes through wake_render_rest; Ada must come back."""
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    wake = src[src.find("def wake_render_rest") : src.find("def keep_render_ops_down")]
    assert "OFF_SLEEP_GATEWAYS" in wake and "RENDER_SLEEP_GATEWAYS" in wake


# ------------------------------------------------------------------ dispatch


def test_render_is_three_workers_one_each():
    assert hm.ADA_DISPATCH["render"] == (3, 1)
    assert hm.ADA_DISPATCH["normal"] == (3, 2), "one switch back = full speed"



def test_vision_aux_is_repinned_not_left_on_a_parked_engine():
    """fallback_model covers the MAIN model only; aux slots inherit nothing."""
    text = (
        "model:\n  base_url: http://127.0.0.1:8005/v1\n  model: crew\n"
        "auxiliary:\n  vision:\n    base_url: http://127.0.0.1:8005/v1\n    model: crew\n"
    )
    new, n = hm.swap_crew_pins_to_ops(text)
    assert n == 4
    assert "8005" not in new
    assert new.count("http://127.0.0.1:8000/v1") == 2
    assert new.count("model: ops") == 2





def test_write_flag_pins_the_lane_before_it_rewrites_caps():
    """Snapshot must predate the cap edit, or leaving Render restores (1, 1)."""
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    body = src[src.find("def write_flag") : src.find("def write_mode")]
    assert body.index("apply_render_lane") < body.index("apply_ada_dispatch")



def _comfy():
    from importlib.util import module_from_spec as mfs, spec_from_file_location as sffl

    sp = sffl("hc_lane", ROOT / "plugins" / "krew-comfy" / "__init__.py")
    m = mfs(sp)
    sp.loader.exec_module(m)
    return m


def test_comfy_refuses_while_the_studio_holds_the_seat(tmp_path, monkeypatch):
    hc = _comfy()
    state = tmp_path / "render-studio.json"
    state.write_text(json.dumps({"busy": "drawing"}))
    monkeypatch.setattr(hc, "STUDIO_STATE", state)
    monkeypatch.setattr(hc, "gpu_mode", lambda: "render")
    monkeypatch.setattr(hc, "comfy_up", lambda: True)
    monkeypatch.setattr(hc, "_free_vram_gib", lambda: 40.0)
    why = hc._refuse(20)
    assert "studio is drawing" in why
    assert "max-num-seqs 1" in why
    # ...and does not tell the worker to escape by switching occupancy.
    assert "krew_gpu_mode" not in why

    state.write_text(json.dumps({"busy": ""}))
    assert hc._refuse(20) == ""


def test_the_studio_draws_through_its_own_seat(tmp_path, monkeypatch):
    """the owner 2026-09-16: "we have no lila voice in Render mode now". _lila_turn
    sets busy "drawing" and then loads this plugin IN ITS OWN PROCESS to draw;
    the seat refused its owner, every drawing turn raised, and she went silent.
    The seat still holds against any other process (a kanban card)."""
    import os as _os
    hc = _comfy()
    state = tmp_path / "render-studio.json"
    monkeypatch.setattr(hc, "STUDIO_STATE", state)
    monkeypatch.setattr(hc, "gpu_mode", lambda: "render")
    monkeypatch.setattr(hc, "comfy_up", lambda: True)
    monkeypatch.setattr(hc, "_free_vram_gib", lambda: 40.0)
    state.write_text(json.dumps({"busy": "drawing", "busy_pid": _os.getpid()}))
    assert hc._refuse(20) == "", "the studio's own draw"
    state.write_text(json.dumps({"busy": "drawing", "busy_pid": _os.getpid() + 1}))
    assert "studio is drawing" in hc._refuse(20), "a card beside the studio still waits"
    src = (ROOT / "crew" / "lib" / "render_studio.py").read_text(encoding="utf-8")
    setb = src[src.find("def _set_busy"): src.find("def still_basename")]
    assert 'st["busy_pid"] = os.getpid()' in setb
    turn = src[src.find("def lila_turn"): src.find("def _lila_turn")]
    assert 'speak_lila("I dropped the charcoal.")' in turn.split("except Exception:")[1].split("raise")[0], \
        "a failed turn is heard, not only shown on the panel"


def test_listen_latch_alone_does_not_hold_the_seat(tmp_path, monkeypatch):
    """Armed Mic lasts the whole Render window. Gating on it means a card
    can never draw unattended — the point of the lane (2026-08-28).

    busy: "listening" is a real in-turn listen and MUST still hold the seat.
    """
    hc = _comfy()
    state = tmp_path / "render-studio.json"
    state.write_text(json.dumps({"busy": ""}))
    latch = tmp_path / "render-studio-listen"
    latch.write_text("render-studio\n")
    monkeypatch.setattr(hc, "STUDIO_STATE", state)
    monkeypatch.setattr(hc, "gpu_mode", lambda: "render")
    monkeypatch.setattr(hc, "comfy_up", lambda: True)
    monkeypatch.setattr(hc, "_free_vram_gib", lambda: 40.0)
    assert hc._refuse(20) == "", "the occupancy-duration latch is not a held seat"

    state.write_text(json.dumps({"busy": "listening"}))
    why = hc._refuse(20)
    assert "listening" in why
    assert "krew_gpu_mode" not in why


def test_lane_marker_is_dropped_even_when_the_graph_raises(tmp_path, monkeypatch):
    hc = _comfy()
    monkeypatch.setattr(hc, "LANE_BUSY", tmp_path / "render-lane-busy")
    try:
        with hc.lane_busy("drawing"):
            assert hc.LANE_BUSY.is_file()
            raise RuntimeError("Comfy fell over")
    except RuntimeError:
        pass
    assert not hc.LANE_BUSY.exists(), "a dead card must not wedge the studio"



# A crew-pinned profile config, the shape the reference install's lane profiles carry (model on crew :8005, vision on
# crew, the fallback on ops, the dispatcher's kanban block). The reference test copied the install's own pack files;
# krew's profiles are made by krew-install init, so the lane tests bring their own.
LANE_CONFIG = """\
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
  max_in_progress: 3
  max_in_progress_per_profile: 2
"""


def _lane_hermes(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"
    originals = {}
    for prof in hm.RENDER_LANE_PROFILES:
        d = hermes / "profiles" / prof
        d.mkdir(parents=True)
        raw = LANE_CONFIG
        (d / "config.yaml").write_text(raw)
        originals[prof] = raw
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "RENDER_LANE_PIN", tmp_path / "pin")
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "hold")
    return hermes, originals


def test_lane_brain_never_names_a_parked_engine(tmp_path, monkeypatch):
    hermes, _ = _lane_hermes(tmp_path, monkeypatch)
    hold = tmp_path / "hold"
    hold.write_text("crew.service\n")
    assert hm.lane_brain("render") == "ops"
    hold.write_text("ops.service\n")
    assert hm.lane_brain("render") == "crew"
    hold.write_text("ops.service\ncrew.service\n")
    assert hm.lane_brain("render") == "none"
    hold.unlink()
    assert hm.lane_brain("render") == "crew"
    assert hm.lane_brain("normal") == "crew"


def test_ops_hold_pins_the_lane_to_crew(tmp_path, monkeypatch):
    """Park-ops Render: workers ride the live 27B, not the parked one."""
    hermes, _ = _lane_hermes(tmp_path, monkeypatch)
    (tmp_path / "hold").write_text("# card=t_test\nops.service\n")
    assert hm.apply_render_lane("render").startswith("crew")
    bash = yaml.safe_load((hermes / "profiles" / "bash" / "config.yaml").read_text())
    assert "8005" in str((bash.get("model") or {}).get("base_url") or "")
    ada = yaml.safe_load((hermes / "profiles" / "ada" / "config.yaml").read_text())
    assert ada["kanban"]["default_assignee"] == "lila", "Render cards still go to Lila"


def test_dual_park_does_not_pin_workers_to_a_corpse(tmp_path, monkeypatch):
    """2026-09-05 stall: hold named both 27Bs, YAML still said :8000."""
    hermes, originals = _lane_hermes(tmp_path, monkeypatch)
    hm.apply_render_lane("render")
    lila_crew = (hermes / "profiles" / "lila" / "config.yaml").read_text()
    bash_stay = (hermes / "profiles" / "bash" / "config.yaml").read_text()
    assert "127.0.0.1:8005" in lila_crew
    assert "127.0.0.1:8005" in bash_stay, "canonical Render keeps the team on crew"
    (tmp_path / "hold").write_text("ops.service\ncrew.service\n")
    assert hm.apply_render_lane("render") == "none (both 27Bs parked)"
    # Leave the files alone — rewriting toward either dead port is the stall.
    assert (hermes / "profiles" / "lila" / "config.yaml").read_text() == lila_crew
    assert (hermes / "profiles" / "bash" / "config.yaml").read_text() == bash_stay


# --- Crissa survives Render (the User, 2026-09-22) -----------------------------------------------
# "Use her as catchall. Wake her from the sleeping in Render." She is the reason the board ever
# needs him: lib/kanban_liveness defines needs_user as `reader == "crissa" and not crissa_up`, and
# the only thing that puts her down is Render. She is not slept for VRAM — write_state parks
# ["ops"] alone and her gateway is ~250 MB of RAM and zero VRAM — she is slept because her BRAIN
# is ops, and ops is the 39 GB a 20-36 GB recipe must evict. Crew :8005 stays up all through.

def test_render_no_longer_sleeps_crissa_but_still_sleeps_steward(monkeypatch):
    monkeypatch.setattr(hm, "CRISSA_CREW_IN_RENDER", True)
    assert "hermes-crissa-gateway" not in hm.render_sleep_gateways()
    assert "crissa" not in hm.render_sleep_profiles()
    assert "steward" in hm.render_sleep_profiles()      # he is a separate decision


def test_the_kill_switch_restores_the_old_sleep_exactly(monkeypatch):
    monkeypatch.setattr(hm, "CRISSA_CREW_IN_RENDER", False)
    assert hm.render_sleep_gateways() == hm.RENDER_SLEEP_GATEWAYS
    assert hm.render_sleep_profiles() == hm.RENDER_SLEEP_PROFILES


def test_the_release_side_always_knows_her_gateway(monkeypatch):
    """The hold WRITTEN by an older render (or with the switch off) still names her. A release
    that stopped recognising the name would read that file as a human hold and leave it
    forever — a permanent latch of exactly the kind 09-21/09-22 spent two days removing.
    Releasing an absent hold is a no-op, so the full roster is safe both ways."""
    for flag in (True, False):
        monkeypatch.setattr(hm, "CRISSA_CREW_IN_RENDER", flag)
        assert "hermes-crissa-gateway.service" in hm.render_sleep_hold_units()


def test_crissa_rides_crew_in_beast_and_render_only(monkeypatch):
    monkeypatch.setattr(hm, "CRISSA_CREW_IN_RENDER", True)
    assert hm.crissa_rides_crew("beast") is True
    assert hm.crissa_rides_crew("render") is True
    for mode in ("normal", "turbo", "off", ""):
        assert hm.crissa_rides_crew(mode) is False
    monkeypatch.setattr(hm, "CRISSA_CREW_IN_RENDER", False)
    assert hm.crissa_rides_crew("render") is False       # back to sleeping her
    assert hm.crissa_rides_crew("beast") is True         # Beast is unconditional


# Two more tests read the reference install's own profile pack files (Lila's config.yaml as shipped) and are
# not here: test_studio_yields_to_a_card_but_not_to_a_stale_marker, test_her_brain_round_trips_ops_to_crew_and_back
