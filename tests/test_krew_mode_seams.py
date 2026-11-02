"""The seams cut into the reference mode switch on its way into krew (2026-10-10): what krew-mode reads from krew.yaml
`gpu:` and $KREW_HOME/services.json, how the Wall is reached, which kill switches it kept, and the two ends it has to
agree with (engines/start-engine.sh, which parks on its flag and holds, and the crew install, which puts it where
every krew caller looks and tells the Wall where the studio is). Every load is sandboxed (tests/krew_mode_sandbox.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_mode_sandbox as sb  # noqa: E402

ROOT = sb.ROOT
MODE = "crew/scripts/krew-mode"


def _home(tmp_path, *, yaml: str = "", services: list | None = None) -> dict:
    kh = tmp_path / "krew"
    kh.mkdir(parents=True)
    if yaml:
        (kh / "krew.yaml").write_text(yaml, encoding="utf-8")
    env = {"KREW_HOME": str(kh), "KREW_CONFIG": str(kh / "krew.yaml"), "KREW_SERVICES": str(kh / "services.json"),
           "KREW_HERMES": "", "KREW_TRAIN_HOLD": "", "KREW_GPU_MODE_FILE": ""}
    if services is not None:
        (kh / "services.json").write_text(json.dumps({"schema": "krew-services/v1", "services": services}))
    return env


def test_the_reference_values_are_the_defaults(tmp_path):
    hm = sb.load("km_defaults", MODE, **_home(tmp_path, services=[]))
    assert hm.ADA_DISPATCH == {"normal": (3, 2), "turbo": (6, 3), "render": (3, 1)}
    assert hm.RENDER_BASH_SPAWN == (1, 1) and hm.CRISSA_SPAWN == {"normal": (2, 1)}
    assert str(hm.CREW_HOLD) == "/tmp/krew-train-hold"
    assert hm.MODE_FILE == hm.HOUSE / "krew-gpu-mode"
    import pwd
    assert hm.HOUSE == Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes"   # read, never written by this test
    # no services.json row: krew's own engine units (engines/krew-engine@.service); the voice row's reference name
    assert hm.OPS_UNIT == hm.KREW_ENGINE_UNIT.format("ops") and hm.CREW_UNIT == hm.KREW_ENGINE_UNIT.format("crew")
    assert hm.VOICE_UNIT == "voice.service" and hm.COSY_UNIT == hm.VOICE_UNIT
    assert hm.OPS_URL == "http://127.0.0.1:8000" and hm.CREW_URL == "http://127.0.0.1:8005"


def test_turbo_krew_and_the_switch_agree_on_the_caps():
    hm = sb.load("km_caps", MODE)
    tk = sb.load("tk_caps", "crew/scripts/turbo-krew.py")
    assert tk.TURBO_ADA_CAPS == hm.ADA_DISPATCH["turbo"] and tk.NORMAL_ADA_CAPS == hm.ADA_DISPATCH["normal"]


def test_krew_yaml_gpu_moves_the_tree_the_flag_the_hold_and_the_caps(tmp_path):
    y = (f"gpu:\n  state_dir: {tmp_path / 'tree'}\n  hold_file: {tmp_path / 'hold'}\n"
         "  caps: {render: [2, 1], normal: [4, 2], junk: [1]}\n  bash_spawn: {render: [2, 1]}\n"
         "  crissa_spawn: {normal: [3, 1]}\n")
    hm = sb.load("km_yaml", MODE, **_home(tmp_path, yaml=y))
    assert hm.HOUSE == tmp_path / "tree" and hm.MODE_FILE == tmp_path / "tree" / "krew-gpu-mode"
    assert hm.CREW_HOLD == tmp_path / "hold"
    assert hm.ADA_DISPATCH == {"normal": (4, 2), "turbo": (6, 3), "render": (2, 1)}, "a bad row is ignored"
    assert hm.RENDER_BASH_SPAWN == (2, 1) and hm.CRISSA_SPAWN == {"normal": (3, 1)}
    y2 = f"gpu:\n  gpu_mode_file: {tmp_path / 'flag'}\n"
    hm2 = sb.load("km_yaml2", MODE, **_home(tmp_path / "b", yaml=y2))
    assert hm2.MODE_FILE == tmp_path / "flag"
    # the environment still wins over the file (the reference install's switches, renamed)
    hm3 = sb.load("km_env", MODE, **{**_home(tmp_path / "c", yaml=y), "KREW_HERMES": str(tmp_path / "env-tree")})
    assert hm3.HOUSE == tmp_path / "env-tree"


def test_services_rows_name_the_units_and_their_ports(tmp_path):
    rows = [{"name": "ops", "unit": "ops-brain.service", "port": 9000, "approved": True},
            {"name": "crew", "unit": "crew-brain.service", "port": 9005, "approved": True},
            {"name": "voice", "unit": "my-voice.service", "port": 9789, "approved": True}]
    hm = sb.load("km_rows", MODE, **_home(tmp_path, services=rows))
    assert (hm.OPS_UNIT, hm.CREW_UNIT, hm.VOICE_UNIT) == ("ops-brain.service", "crew-brain.service", "my-voice.service")
    assert (hm.OPS_URL, hm.CREW_URL, hm.COSY_URL) == ("http://127.0.0.1:9000", "http://127.0.0.1:9005",
                                                      "http://127.0.0.1:9789")
    assert hm.OPS_BEAST_DROPIN.parent.name == "ops-brain.service.d"
    # an unapproved row is listed, never acted on
    rows[0]["approved"] = False
    hm2 = sb.load("km_rows2", MODE, **_home(tmp_path / "b", services=rows))
    assert hm2.OPS_UNIT == hm2.KREW_ENGINE_UNIT.format("ops")


def test_the_wall_is_reached_through_its_player(tmp_path):
    hm = sb.load("km_wall", MODE, CRISSA_PLAYER="http://127.0.0.1:18100/", KREW_PRESENCE_URL="",
                 KREW_CONDUCTOR_URL="")
    assert hm.CONDUCTOR_URL == "http://127.0.0.1:18100"
    assert hm.PRESENCE_URL == "http://127.0.0.1:18100/v1/rail/presence"
    assert hm.CRISSA_FEED == "http://127.0.0.1:8099"
    # each Wall address appears once in the code, as its variable's default (the pack's port table numbers)
    for line_of, addr in (("CRISSA_PLAYER =", "127.0.0.1:8100"), ("CRISSA_FEED =", "127.0.0.1:8099")):
        hits = [ln for ln in (ROOT / MODE).read_text(encoding="utf-8").splitlines() if addr in ln]
        assert len(hits) == 1 and hits[0].startswith(line_of), hits
    assert "127.0.0.1:8191" not in (ROOT / MODE).read_text(encoding="utf-8")


@pytest.mark.parametrize("switch,attr", [
    ("KREW_ADA_CAPS_BOUNCE", "ADA_CAPS_BOUNCE_ON"), ("KREW_SWEEP_COMFY_LEAK", "SWEEP_COMFY_LEAK"),
    ("KREW_SWEEP_WAY_BACK", "SWEEP_WAY_BACK"), ("KREW_SWEEP_OCCUPANCY", "SWEEP_OCCUPANCY"),
    ("KREW_SWEEP_FLAG_HOME", "SWEEP_FLAG_HOME"), ("KREW_FLAG_HOME_COMFY_BUSY", "FLAG_HOME_COMFY_BUSY"),
    ("KREW_OCCUPANCY_CARD_FROM_CWD", "OCCUPANCY_CARD_FROM_CWD"), ("KREW_CRISSA_CREW_IN_RENDER", "CRISSA_CREW_IN_RENDER"),
    ("KREW_COSY_RENDER_PARK", "COSY_RENDER_PARK"), ("KREW_RENDER_LILA_HOST", "RENDER_LILA_HOST")])
def test_every_reference_kill_switch_is_kept_under_its_krew_name(switch, attr):
    on = sb.load("km_on_" + attr, MODE, **{switch: ""})
    off = sb.load("km_off_" + attr, MODE, **{switch: "0"})
    assert getattr(on, attr) is True and getattr(off, attr) is False


def test_heavy_render_is_dark_until_switched_on():
    hm = sb.load("km_heavy_off", MODE, KREW_HEAVY_RENDER="")
    assert hm.heavy_enabled() is False
    os.environ["KREW_HEAVY_RENDER"] = "1"
    try:
        assert hm.heavy_enabled() is True
    finally:
        os.environ.pop("KREW_HEAVY_RENDER", None)


def test_heavy_render_and_render_still_honour_krew_mode_bin(tmp_path):
    hr = sb.load("hr_bin", "crew/scripts/heavy-render.py", KREW_MODE_BIN=str(tmp_path / "stub"))
    assert hr.KREW_MODE == str(tmp_path / "stub")
    hr2 = sb.load("hr_bin2", "crew/scripts/heavy-render.py", KREW_MODE_BIN="")
    assert hr2.KREW_MODE == str(ROOT / "crew" / "scripts" / "krew-mode")


def _engine(tmp_path, env_extra: dict) -> subprocess.CompletedProcess:
    kh = tmp_path / "krew"
    kh.mkdir(exist_ok=True)
    (kh / "engines.env").write_text("OPS_KV_BYTES=0\nCREW_KV_BYTES=0\n")
    env = {"HOME": str(tmp_path), "KREW_HOME": str(kh), "PATH": "/usr/bin:/bin",
           "KREW_VLLM_ENV": str(tmp_path / "none"), "KREW_TRAIN_HOLD": str(tmp_path / "hold"), **env_extra}
    return subprocess.run(["bash", str(ROOT / "engines" / "start-engine.sh"), env_extra.pop("_name", "ops")],
                          capture_output=True, text=True, env=env, timeout=30)


def test_the_engines_park_on_the_flag_the_switch_writes(tmp_path):
    hm = sb.load("km_flag", MODE)
    flag = tmp_path / ".hermes" / "krew-gpu-mode"           # the switch's default, under the engine's own HOME
    flag.parent.mkdir()
    assert hm.MODE_FILE.name == flag.name
    flag.write_text("render\n")
    r = _engine(tmp_path, {})
    assert r.returncode == 0 and "parked: gpu-mode=render" in r.stdout, r.stderr
    flag.write_text("normal\n")
    r = _engine(tmp_path, {})
    assert "parked" not in r.stdout                          # goes on to the (missing) engine and stops there


def test_the_engines_park_on_the_switchs_hold_line(tmp_path):
    hm = sb.load("km_hold", MODE, **_home(tmp_path, services=[]))
    hm.CREW_HOLD = tmp_path / "hold"
    hm.hold_units(hm.OPS_UNIT, card="t_fake0001")            # the switch's own writer, the ops engine's unit
    assert hm.KREW_ENGINE_UNIT.format("ops") in (tmp_path / "hold").read_text()
    r = _engine(tmp_path, {})
    assert r.returncode == 0 and "parked: hold lists ops" in r.stdout, (r.stdout, r.stderr)
    r = _engine(tmp_path, {"_name": "crew"})
    assert "parked" not in r.stdout


def test_the_crew_install_links_the_switch_and_points_the_wall_at_the_studio(tmp_path):
    kh, hh, zh = tmp_path / "k", tmp_path / "h", tmp_path / "z"
    hh.mkdir()
    zh.mkdir()
    env = {**sb.env(), "CRISSA_HOME": str(zh), "KREW_GPU_MODE_FILE": str(tmp_path / "flag"), "HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, str(ROOT / "crew" / "scripts" / "krew-crew-install.py"), "--krew-home", str(kh),
                        "--hermes-home", str(hh)], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    link = kh / "bin" / "krew-mode"
    assert link.is_symlink() and link.resolve() == (kh / "crew" / "scripts" / "krew-mode").resolve()
    ptr = json.loads((zh / "krew-render.json").read_text())
    assert ptr["lib"] == str(kh / "crew" / "lib") and (Path(ptr["lib"]) / "render_studio.py").is_file()
    assert ptr["mode_bin"] == str(link) and ptr["gpu_mode_file"] == str(tmp_path / "flag")
    # a krew-mode of your own is kept; no Wall state home = no pointer, and it says so
    link.unlink()
    link.write_text("#!/bin/sh\n")
    r = subprocess.run([sys.executable, str(ROOT / "crew" / "scripts" / "krew-crew-install.py"), "--krew-home", str(kh),
                        "--hermes-home", str(hh)], env={**env, "CRISSA_HOME": str(tmp_path / "none")},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and not link.is_symlink() and "kept" in r.stdout and "Crissa not installed" in r.stdout
