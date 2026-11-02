"""Heavy-render H0 (the User 09-29): the house learns the `heavy-render` mode and gets an
ops fallback BEFORE anything can enter the mode. heavy-render = crew off the card on
purpose, ops up (Crissa on ops), Comfy in high-VRAM. Nothing here enters it.

Pure functions only: every module is loaded from its file, every live path is a tmp
path, and subprocess is refused - a test never touches a unit, the board or ~/.hermes.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    # every import-time path in a sandbox, never the passwd tree (tests/krew_mode_sandbox.py)
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import krew_mode_sandbox
    return krew_mode_sandbox.load(name, rel)


@pytest.fixture
def hm(tmp_path, monkeypatch):
    m = _load("krew_mode_h0", "crew/scripts/krew-mode")
    monkeypatch.setattr(m, "MODE_FILE", tmp_path / "krew-gpu-mode")
    monkeypatch.setattr(m, "HEAVY_STATE", tmp_path / "heavy-render.json")
    monkeypatch.setattr(m, "FLAG_HOME_STATE", tmp_path / "flag-home.json")
    monkeypatch.setattr(m, "read_pending", lambda: None)

    def no_subprocess(*a, **k):
        raise AssertionError(f"H0 tests never run a subprocess: {a[:1]}")
    monkeypatch.setattr(m.subprocess, "run", no_subprocess)
    return m


def _state(hm, pid, deadline):
    hm.HEAVY_STATE.write_text(json.dumps({"pid": pid, "deadline": deadline}))


# ------------------------------------------------------------- krew-mode learns the word
def test_heavy_is_a_mode_and_is_read_back(hm):
    assert "heavy-render" in hm.MODES
    hm.MODE_FILE.write_text("heavy-render\n")
    assert hm.read_mode() == "heavy-render"
    assert hm.read_mode("heavy-render") == "heavy-render" and hm.read_mode("bogus") == "normal"
    # the OLD "heavy" (deleted 2026-09-05) still degrades to normal: a leftover flag stays harmless
    assert hm.read_mode("heavy\n") == "normal" and "heavy" not in hm.MODES


def test_runner_alive_only_with_a_live_pid_inside_its_deadline(hm):
    assert hm.heavy_runner_alive() == (False, "heavy: no runner state")
    _state(hm, os.getpid(), time.time() + 60)
    assert hm.heavy_runner_alive()[0] is True
    _state(hm, 2 ** 22 + 12345, time.time() + 60)  # no such pid
    assert hm.heavy_runner_alive()[1].startswith("heavy: runner pid")
    _state(hm, os.getpid(), time.time() - hm.HEAVY_GRACE_S - 5)
    alive, why = hm.heavy_runner_alive()
    assert alive is False and "past the deadline" in why
    hm.HEAVY_STATE.write_text("{not json")
    assert hm.heavy_runner_alive()[0] is False


def test_the_sweep_sends_a_dead_heavy_window_home(hm):
    hm.MODE_FILE.write_text("heavy-render\n")
    _state(hm, os.getpid(), time.time() + 60)
    assert hm.flag_home_due(pending=set(), comfy=True)[0] is False  # the runner is in charge
    assert hm.restore_due() is False
    _state(hm, 2 ** 22 + 12345, time.time() + 60)
    due, why = hm.flag_home_due(pending=set(), comfy=True)
    assert due is True and "gone" in why
    assert hm.restore_due() is True


def test_one_tick_launches_krew_mode_normal_for_heavy(hm, monkeypatch):
    hm.MODE_FILE.write_text("heavy-render\n")
    _state(hm, 2 ** 22 + 12345, time.time() + 60)
    calls, logs = [], []
    monkeypatch.setattr(hm.subprocess, "run", lambda argv, **k: calls.append(argv) or
                        subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(hm, "_hold_log", lambda ev: logs.append(ev))
    assert hm.maybe_bring_flag_home(pending=set(), comfy=True) is True  # one tick, not two
    argv = calls[0]
    assert argv[:2] == ["systemd-run", "--user"] and argv[-1] == "normal"
    assert "latched at heavy-render" in " ".join(argv)
    assert logs[0]["event"] == "flag_home_launched"


def test_render_still_needs_two_ticks(hm, monkeypatch):
    hm.MODE_FILE.write_text("render\n")
    monkeypatch.setattr(hm, "flag_home_due", lambda **k: (True, "no render pending"))
    assert hm.maybe_bring_flag_home(pending=set(), comfy=False) is False  # tick 1 of 2: no launch


def test_the_flag_home_kill_switch_covers_heavy(hm, monkeypatch):
    hm.MODE_FILE.write_text("heavy-render\n")
    monkeypatch.setattr(hm, "SWEEP_FLAG_HOME", False)
    assert hm.flag_home_due(pending=set(), comfy=True) == (False, "KREW_SWEEP_FLAG_HOME=0")


def test_the_sweep_never_bounces_ada_under_heavy():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text()
    assert 'if mode not in ("render", "heavy-render") and not dry_run:' in src


# ------------------------------------------------------------- start-ops.sh: ops RUNS under heavy


def test_janitor_knows_heavy(tmp_path, monkeypatch):
    j = _load("krew_janitor_h0", "janitor/krew-janitor.py")
    f = tmp_path / "krew-gpu-mode"
    f.write_text("heavy-render\n")
    monkeypatch.setattr(j, "GPU_MODE_FILE", f)
    monkeypatch.setattr(j, "TRAIN_HOLD_PATHS", [tmp_path / "no-hold"])
    assert j.read_gpu_mode() == "heavy-render"
    assert j.occupancy_parks_probe("crew", "heavy-render") is True
    assert j.occupancy_parks_probe("comfy", "heavy-render") is False  # Comfy is expected UP in heavy
    assert j.occupancy_parks_probe("comfy", "normal") is True
    assert j.occupancy_parks_unit("crew.service", "heavy-render") is True
    assert j.occupancy_parks_unit("ops.service", "heavy-render") is False


# ------------------------------------------------------------- TurboKrew rides ops under heavy
def test_turbo_krew_pins_ops_under_heavy(tmp_path, monkeypatch):
    tk = _load("turbo_krew_h0", "crew/scripts/turbo-krew.py")
    # the existing fixture tree, loaded by PATH: `import tests.…` resolves to hermes-agent's own
    # `tests` package (conftest puts ~/.hermes/hermes-agent first on sys.path; ours has no __init__)
    tkt = _load("tkt_h0", "tests/test_turbo_krew.py")
    house = tkt._tree(tmp_path)
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_OK", "1")
    monkeypatch.setenv("KREW_TURBO_KREW_OPS_SEQS", "8")
    monkeypatch.setenv("KREW_TURBO_KREW_NO_TIMER", "1")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", str(house / "krew-gpu-mode"))
    tk.turn_on(house=house, ttl=600, reason="test")
    (house / "krew-gpu-mode").write_text("heavy-render\n", encoding="utf-8")
    out = tk.apply_occupancy("heavy-render", house=house, quiet=True)
    assert tk.profile_pins(house)["vera"] == "ops" and tk.profile_pins(house)["steward"] == "ops"
    assert tk.profile_pins(house)["ada"] == "crew"  # Ada never moves
    assert out["parked"] is False and out["sharing"] is True
    assert "heavy window" in out["note"]
    (house / "krew-gpu-mode").write_text("render\n", encoding="utf-8")
    tk.apply_occupancy("render", house=house, quiet=True)
    assert tk.profile_pins(house)["vera"] == "crew"  # ordinary Render unchanged


# ------------------------------------------------------------- the panel

def _pc():
    return _load("pin_check_h0", "crew/scripts/krew-pin-check.py")





def test_the_window_reads_config_debt_beside_code_debt():
    src = (ROOT / "window" / "scripts" / "krew-restart-window.py").read_text()
    # code debt and config debt merged into one owed list (09-29: a third source,
    # rw.hermes_env_owed, follows them in the same call - the pin checks the two it owns)
    # 10-01 (a2670393): the merged list passes through split_switched_off before it is the facts' "owed"
    gather = " ".join(src.split("def gather(", 1)[1].split("\ndef ", 1)[0].split())
    assert "rw.split_switched_off(rw.merge_owed( rw.owed_units(debt), rw.config_owed(unit_started)" in gather
    assert '"owed": owed' in gather
    # 10-02: the start-time read is ONE copy in lib/restart_window (user manager, then system)
    assert '"--timestamp=unix"' in (ROOT / "window" / "lib" / "restart_window.py").read_text() and "rw._unit_started(unit)" in src


# The reference install's engine launcher, its Wall feed and its profile packs are
# not here: test_start_ops_parks_only_under_beast_and_render, test_the_panel_never_resurrects_crew_under_heavy, test_every_pack_config_falls_back_to_another_engine, test_the_host_pack_keeps_crew_primary_and_falls_back_to_ops


# The keeper, the pin check and the restart window are krew's own lifts (crew/scripts/krew-keeper.py, crew/scripts/krew-pin-check.py, window/lib/restart_window.py) with their own tests; H0's contract on them (heavy-render parks crew, check_fallbacks(mode_file=), the owed config row at the gateway's tier) is recorded in the parity report, not asserted here:
# not here: test_keeper_knows_heavy_and_never_heals_crew_under_it, test_pin_check_reds_a_fallback_that_is_the_primary, test_pin_check_skips_a_crew_ride_whose_ops_snapshot_exists, test_a_config_change_is_owed_until_the_unit_restarts
