"""krew-mode park policy + flag parse. No GPU, no systemd."""
from __future__ import annotations

import inspect
import os
import subprocess
import pytest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_mode_sandbox  # noqa: E402
# every import-time path in a sandbox, never the passwd tree (tests/krew_mode_sandbox.py)
hm = krew_mode_sandbox.load("krew_mode", "crew/scripts/krew-mode")


# NEVER THE LIVE HOUSE (2026-09-17). This file drives krew-mode, and krew-mode's
# job is to start and stop units. On 09-14 a run executed another agent's hunks for
# real (docker rm crew, wake Crissa); on 09-17 a glob over "every test that reads the
# panel page" ran it in Render and STARTED Crissa's parked gateway and rewrote
# /tmp/krew-train-hold. Every test here now runs with a hold file of its own, and a
# systemctl / docker / podman call that would change something raises instead of
# running. A test that wants the call patches it itself, as the careful ones do.
_MUTATING = {"start", "stop", "restart", "try-restart", "reload", "kill", "enable", "disable",
             "mask", "unmask", "reset-failed", "rm", "run", "exec", "daemon-reload", "set-property"}


@pytest.fixture(autouse=True)
def _never_the_live_house(monkeypatch, tmp_path):
    import subprocess as _sp
    real_run, real_popen = _sp.run, _sp.Popen

    def _argv(cmd):
        return [str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else str(cmd).split())]

    def _refuse(cmd):
        argv = _argv(cmd)
        if argv and Path(argv[0]).name in ("systemctl", "docker", "podman", "sudo") and _MUTATING & set(argv):
            raise AssertionError("test_krew_mode reached a LIVE unit: " + " ".join(argv))
        # Since the 09-27 one-host-gateway cutover a profile's stop/start is `hermes -p <p> gateway
        # stop|start|restart` (lib/hermes_topology park/unpark) - no systemctl in the argv at all.
        if argv and Path(argv[0]).name == "hermes" and "gateway" in argv and _MUTATING & set(argv):
            raise AssertionError("test_krew_mode reached a LIVE gateway: " + " ".join(argv))

    def run(cmd, *a, **k):
        _refuse(cmd)
        return real_run(cmd, *a, **k)

    class Popen(real_popen):
        def __init__(self, cmd, *a, **k):
            _refuse(cmd)
            super().__init__(cmd, *a, **k)

    real_kill, real_killpg = os.kill, os.killpg

    def _own_child(pid):
        try:
            stat = Path(f"/proc/{int(pid)}/stat").read_text()
            return int(stat.rsplit(")", 1)[1].split()[1]) == os.getpid()
        except (OSError, ValueError, IndexError):
            return False

    # Render sleep stops a profile's WORKERS by argv (profile_worker_pids -> SIGTERM): a signal to
    # anything but this pytest's own child is a live process.
    def kill(pid, sig):
        if sig and not _own_child(pid):
            raise AssertionError(f"test_krew_mode signalled a LIVE pid {pid} ({sig})")
        return real_kill(pid, sig)

    def killpg(pgid, sig):
        if sig and not _own_child(pgid):
            raise AssertionError(f"test_krew_mode signalled a LIVE group {pgid} ({sig})")
        return real_killpg(pgid, sig)

    monkeypatch.setattr(_sp, "run", run)
    monkeypatch.setattr(_sp, "Popen", Popen)
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "killpg", killpg)
    if hasattr(hm, "CREW_HOLD"):
        monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "krew-train-hold")
    # ...and the flag: write_mode() on the passwd ~/.hermes/krew-gpu-mode IS entering Render.
    if hasattr(hm, "MODE_FILE"):
        monkeypatch.setattr(hm, "MODE_FILE", tmp_path / "krew-gpu-mode")


def test_free_vram_parses_mib():
    assert abs(hm.free_vram_gib("2705\n") - 2705 / 1024.0) < 1e-6


def test_voice_on_off():
    assert hm.voice_is_on('{"voice": "on"}') is True
    assert hm.voice_is_on('{"voice": "off"}') is False
    assert hm.voice_is_on("{") is False


def test_read_mode_plain_and_missing():
    # Heavy was deleted 2026-09-05. A flag file left saying "heavy" must
    # degrade to normal, not raise and not stick -- that fallback is what makes
    # the removal safe without a migration step.
    assert hm.read_mode("heavy\n") == "normal"
    assert "heavy" not in hm.MODES
    assert hm.read_mode("render\n") == "render"
    assert hm.read_mode("beast\n") == "beast"
    assert hm.read_mode("") == "normal"
    assert hm.read_mode("normal\n") == "normal"
    assert hm.read_mode('{"mode": "user_auth_only"}') == "normal"


def test_hermes_home_profile_does_not_own_occupancy_flag(tmp_path):
    """Kanban workers set HERMES_HOME to ~/.hermes/profiles/<name>.

    Occupancy must still read/write the house flag. Otherwise Render parks
    the real engines and image_generate still sees normal (Lila 2026-09-08).
    """
    house = tmp_path / "house"
    house.mkdir()
    (house / "krew-gpu-mode").write_text("render\n")
    profile = tmp_path / "lila"
    profile.mkdir()
    (profile / "krew-gpu-mode").write_text("normal\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"KREW_GPU_MODE_FILE", "HERMES_HOME", "KREW_HERMES"}
    }
    env["HERMES_HOME"] = str(profile)
    env["KREW_HERMES"] = str(house)
    r = subprocess.run(
        ["python3", str(ROOT / "crew" / "scripts" / "krew-mode"), "status"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "mode     render" in r.stdout, r.stdout
    assert str(house / "krew-gpu-mode") in r.stdout
    assert str(profile / "krew-gpu-mode") not in r.stdout


def test_occupancy_switch_guests_the_user():
    """the owner 2026-09-08: krew-mode flip reauths Secure (live keep / token FF)."""
    assert "drop_presence_to_guest" in inspect.getsource(hm.write_flag)
    assert "secure_reauth" in inspect.getsource(hm.drop_presence_to_guest)


def test_a_noop_mode_write_does_not_drop_to_guest(monkeypatch):
    """2026-10-08 13:15: normal→normal still called secure_reauth and presence
    refused every later door match."""
    dropped: list[str] = []
    monkeypatch.setattr(hm, "drop_presence_to_guest", dropped.append)
    for name in (
        "apply_turbo_krew_occupancy", "apply_render_lane", "apply_ada_decomposer",
        "apply_dispatch_pause", "apply_ada_dispatch", "apply_crissa_spawn",
    ):
        monkeypatch.setattr(hm, name, lambda *a, **k: "x (already)")
    monkeypatch.setattr(hm, "ada_caps_for_mode", lambda mode: None)
    monkeypatch.setattr(hm, "_FLAG_MOVED", False)
    hm.MODE_FILE.write_text("normal\n", encoding="utf-8")
    hm.write_flag("normal")
    assert dropped == []
    hm.write_flag("render")
    assert dropped == ["occupancy:render"]


def test_write_flag_parks_turbo_krew_before_lane_when_leaving_standard():
    """TurboKrew ops pins must not be snapshotted as Render SoT.

    The hour keeps running; occupancy only moves Vera/Steward onto crew.
    """
    src = inspect.getsource(hm.write_flag)
    assert "apply_turbo_krew_occupancy" in src
    assert src.index("apply_turbo_krew_occupancy") < src.index("apply_render_lane")
    apply = inspect.getsource(hm.apply_turbo_krew_occupancy)
    assert "apply_occupancy" in apply
    assert "turn_off" not in apply
    assert "Ada stays on crew" in apply


def test_ops_recipe_never_writes_beast(tmp_path, monkeypatch):
    rec = tmp_path / "krew-ops-recipe"
    rec.write_text("beast\n")
    monkeypatch.setattr(hm, "OPS_RECIPE_FILE", rec)
    assert hm.read_ops_recipe() == "daily"
    hm.write_ops_recipe("beast")
    assert not rec.exists()
    hm.write_ops_recipe("daily")
    assert not rec.exists()
    assert hm.read_ops_recipe() == "daily"


def test_park_ops_first_when_short():
    plan = hm.park_plan(20.0, 2.6, voice_on=True)
    assert plan["ok"] is True
    assert plan["park"] == ["ops"]


def test_max_fits_after_ops():
    plan = hm.park_plan(36.0, 2.6, voice_on=True)
    assert plan["ok"] is True
    assert plan["park"] == ["ops"]


def test_voice_on_parks_ops_refuses_crew():
    # Need more than ops can free (2.6 + 39.4 = 42.0 < 60).
    plan = hm.park_plan(60.0, 2.6, voice_on=True)
    assert plan["ok"] is False
    assert plan["park"] == ["ops"]
    assert "Voice ON" in plan["reason"]
    assert "Cosy stays" in plan["reason"]
    assert "crew stays" in plan["reason"]


def test_voice_off_may_park_ops():
    plan = hm.park_plan(60.0, 2.6, voice_on=False)
    assert plan["ok"] is True
    assert plan["park"] == ["ops", "crew"]


def test_enough_free_parks_nothing():
    plan = hm.park_plan(20.0, 51.0, voice_on=True)
    assert plan["ok"] is True
    assert plan["park"] == []


def test_never_park_list():
    assert "audio8-asr" in hm.NEVER_PARK
    assert "live-ear" in hm.NEVER_PARK
    assert "rail-stt" in hm.NEVER_PARK
    assert "rail-presence" in hm.NEVER_PARK


def test_recipes_match_krew_comfy_gates():
    assert hm.RECIPES["turbo"]["need_gib"] == 20.0
    assert hm.RECIPES["wan"]["need_gib"] == 22.0
    assert hm.RECIPES["max"]["need_gib"] == 36.0
    assert hm.RECIPES["klein"]["need_gib"] == 10.0
    assert hm.RECIPES["qwen"]["need_gib"] == 30.0


def test_crew_hold_helpers(tmp_path, monkeypatch):
    hold = tmp_path / "krew-train-hold"
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    hm.hold_crew_park()
    text = hold.read_text()
    assert "crew.service" in text
    assert "# card=occupancy" in text
    hm.release_crew_hold()
    assert not hold.exists() or "crew.service" not in hold.read_text()


def test_beast_hold_keeps_ops_and_cosy_off_card(tmp_path, monkeypatch):
    hold = tmp_path / "krew-train-hold"
    flag = tmp_path / "beast-hold"
    ops_d = tmp_path / "ops.d" / "beast-hold.conf"
    cosy_d = tmp_path / "cosy.d" / "beast-hold.conf"
    crew_d = tmp_path / "crew.d" / "beast-hold.conf"
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.setattr(hm, "BEAST_HOLD", flag)
    monkeypatch.setattr(hm, "OPS_BEAST_DROPIN", ops_d)
    monkeypatch.setattr(hm, "COSY_BEAST_DROPIN", cosy_d)
    monkeypatch.setattr(hm, "CREW_BEAST_DROPIN", crew_d)
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: None)
    hm.hold_crew_park()
    hm.hold_beast_park()
    lines = hold.read_text().splitlines()
    assert "crew.service" not in lines
    assert "ops.service" in lines
    assert "cosy-voice.service" not in lines
    assert flag.is_file()
    # Runtime Restart=no drop-ins are gone — unit files pin ConditionPathExists.
    assert not ops_d.exists()
    assert not cosy_d.exists()
    assert not crew_d.exists()
    hm.release_beast_park()
    assert not flag.exists()
    assert "ops.service" not in (hold.read_text().splitlines() if hold.exists() else [])


def test_beast_clears_legacy_runtime_dropins(tmp_path, monkeypatch):
    flag = tmp_path / "beast-hold"
    ops_d = tmp_path / "ops.d"
    ops_d.mkdir()
    drop = ops_d / "beast-hold.conf"
    drop.write_text("Restart=no\n")
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "krew-train-hold")
    monkeypatch.setattr(hm, "BEAST_HOLD", flag)
    monkeypatch.setattr(hm, "OPS_BEAST_DROPIN", drop)
    monkeypatch.setattr(hm, "COSY_BEAST_DROPIN", tmp_path / "cosy.d" / "beast-hold.conf")
    monkeypatch.setattr(hm, "CREW_BEAST_DROPIN", tmp_path / "crew.d" / "beast-hold.conf")
    reloads = []
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: reloads.append(list(args)))
    hm.hold_beast_park()
    assert not drop.exists()
    assert reloads == [["daemon-reload"]]


_LIVE_DECOMP = """\
auxiliary:
  vision:
    model: crew
    base_url: http://127.0.0.1:8005/v1
  kanban_decomposer:
    provider: custom
    model: ops
    base_url: http://127.0.0.1:8000/v1
    timeout: 300
  goal_judge:
    model: crew
    base_url: http://127.0.0.1:8005/v1
"""

_PACK_DECOMP = """\
auxiliary:
  kanban_decomposer:
    provider: custom
    base_url: http://127.0.0.1:8000/v1
    model: ops
    extra_body:
      priority: 100
"""


def test_decomposer_for_mode():
    assert hm.decomposer_for_mode("beast") == hm.DECOMPOSER_CREW
    assert hm.decomposer_for_mode("normal") == hm.DECOMPOSER_CREW
    assert hm.decomposer_for_mode("heavy") == hm.DECOMPOSER_CREW
    # Canonical Render keeps crew; decomposer stays.
    assert hm.decomposer_for_mode("render") == hm.DECOMPOSER_CREW
    assert hm.DECOMPOSER_CREW == ("crew", "http://127.0.0.1:8005/v1")
    assert hm.DECOMPOSER_OPS == ("ops", "http://127.0.0.1:8000/v1")


def test_park_unit_holds_ops_so_keeper_cannot_resurrect_it():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    body = src[src.find("def park_unit") : src.find("def thaw_unit")]
    assert 'hold_units(OPS_UNIT)' in body
    assert "hold_crew_park()" in body
    write = src[src.find("def write_mode") : src.find("def decomposer_for_mode")]
    assert "maybe_sleep_ada_for_exclusive" in write
    render = src[src.find("def cmd_render") : src.find("def cmd_beast")]
    after_comfy = render[render.find("start_comfy") :]
    assert "keep_render_ops_down()" in after_comfy


def test_decomposer_for_occupancy_follows_the_live_brain(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "hold")
    hold = tmp_path / "hold"
    hold.write_text("crew.service\n")
    assert hm.decomposer_for_occupancy("render") == hm.DECOMPOSER_OPS
    hold.write_text("ops.service\n")
    assert hm.decomposer_for_occupancy("render") == hm.DECOMPOSER_CREW
    hold.write_text("ops.service\ncrew.service\n")
    assert hm.decomposer_for_occupancy("render") == hm.DECOMPOSER_CREW
    hold.unlink()
    assert hm.decomposer_for_occupancy("render") == hm.DECOMPOSER_CREW
    assert hm.decomposer_for_occupancy("normal") == hm.DECOMPOSER_CREW


def test_set_kanban_decomposer_live_key_order():
    crew, changed = hm.set_kanban_decomposer(_LIVE_DECOMP, "crew", "http://127.0.0.1:8005/v1")
    assert changed is True
    assert hm.read_kanban_decomposer(crew) == ("crew", "http://127.0.0.1:8005/v1")
    assert "model: crew" in crew
    assert "base_url: http://127.0.0.1:8005/v1" in crew
    # sibling aux slots stay on crew; only decomposer moved
    assert crew.count("model: crew") == 3
    again, changed2 = hm.set_kanban_decomposer(crew, "crew", "http://127.0.0.1:8005/v1")
    assert changed2 is False
    back, changed3 = hm.set_kanban_decomposer(crew, "ops", "http://127.0.0.1:8000/v1")
    assert changed3 is True
    assert hm.read_kanban_decomposer(back) == ("ops", "http://127.0.0.1:8000/v1")
    assert "model: ops" in back
    assert "http://127.0.0.1:8000/v1" in back


def test_set_kanban_decomposer_pack_key_order():
    crew, changed = hm.set_kanban_decomposer(_PACK_DECOMP, "crew", "http://127.0.0.1:8005/v1")
    assert changed is True
    assert hm.read_kanban_decomposer(crew) == ("crew", "http://127.0.0.1:8005/v1")
    assert "priority: 100" in crew
    back, _ = hm.set_kanban_decomposer(crew, "ops", "http://127.0.0.1:8000/v1")
    assert hm.read_kanban_decomposer(back) == ("ops", "http://127.0.0.1:8000/v1")


def test_apply_ada_decomposer_crew_everywhere_but_render(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "no-hold")
    cfg = tmp_path / "ada-config.yaml"
    cfg.write_text(_LIVE_DECOMP)
    assert hm.apply_ada_decomposer("beast", path=cfg) == "crew"
    assert hm.read_kanban_decomposer(cfg.read_text()) == ("crew", "http://127.0.0.1:8005/v1")
    assert hm.apply_ada_decomposer("beast", path=cfg) == "crew (already)"
    assert hm.apply_ada_decomposer("normal", path=cfg) == "crew (already)"
    assert hm.apply_ada_decomposer("heavy", path=cfg) == "crew (already)"
    assert hm.apply_ada_decomposer("render", path=cfg) == "crew (already)"
    assert hm.read_kanban_decomposer(cfg.read_text()) == ("crew", "http://127.0.0.1:8005/v1")
    assert hm.apply_ada_decomposer("normal", path=cfg) == "crew (already)"


_CRISSA_OPS = """\
model:
  default: ops
  provider: custom:ops
  base_url: http://127.0.0.1:8000/v1
auxiliary:
  vision:
    model: ops
    base_url: http://127.0.0.1:8000/v1
  web_extract:
    model: crew
    base_url: http://127.0.0.1:8005/v1
custom_providers:
  - name: ops
    base_url: http://127.0.0.1:8000/v1
    models:
      - ops
    model: ops
"""


def test_swap_ops_pins_to_crew_spares_native_crew_and_limits():
    raw = (
        _CRISSA_OPS
        + "web:\n  extract_char_limit: 8000\n"
        + "# DENY curl 127.0.0.1 ports 8000/8005\n"
    )
    new, n = hm.swap_ops_pins_to_crew(raw)
    assert n >= 6
    assert "default: crew" in new
    assert "provider: custom:crew" in new
    assert "http://127.0.0.1:8000/v1" not in new
    assert new.count("http://127.0.0.1:8005/v1") >= 4
    assert "extract_char_limit: 8000" in new
    assert "ports 8000/8005" in new
    assert "name: ops" in new
    assert "- crew" in new
    assert hm.file_has_ops_pin(new) is False
    # native crew slot still crew
    assert "web_extract:\n    model: crew" in new


def test_apply_ops_occupancy_roundtrip(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"
    crissa = hermes / "profiles" / "crissa" / "config.yaml"
    ada = hermes / "profiles" / "ada" / "config.yaml"
    crissa.parent.mkdir(parents=True)
    ada.parent.mkdir(parents=True)
    crissa.write_text(_CRISSA_OPS)
    ada.write_text(_LIVE_DECOMP)
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "BRIDGE_CONFIG", hermes / "missing-bridge.yaml")
    monkeypatch.setattr(hm, "ADA_CONFIG", ada)
    monkeypatch.setattr(hm, "BEAST_OPS_PIN", tmp_path / "pin")
    restarts: list[list[str]] = []
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: restarts.append(list(args)))
    assert hm.apply_ops_occupancy("beast", restart=True).startswith("crew")
    assert "http://127.0.0.1:8000/v1" not in crissa.read_text()
    assert hm.read_kanban_decomposer(ada.read_text())[0] == "ops"
    assert (tmp_path / "pin" / "profile-crissa.config.yaml").is_file()
    assert (tmp_path / "pin" / "profile-ada.config.yaml").is_file() is False
    assert restarts == [
        ["restart", "hermes-crissa-gateway.service"],
    ]
    assert not any("ada" in a[-1] for a in restarts)
    assert hm.apply_ops_occupancy("normal").startswith("ops")
    assert "default: ops" in crissa.read_text()
    assert hm.read_kanban_decomposer(ada.read_text())[0] == "ops"
    assert not (tmp_path / "pin").exists()


def test_pytest_cannot_write_live_occupancy():
    """The 10:46 invert: writers used HOUSE while tests patched HERMES."""
    import pytest

    # krew: only the GUARD is called on a copy that resolves the live tree. The reference test called
    # apply_ops_occupancy("beast") on the live tree and trusted the guard to raise first; under a scrubbed HOME
    # (kanban-env-clean) the guard compared the wrong home, did not raise, and the writer re-pinned a live profile
    # (2026-10-10, the first run of this lift). The guard now knows the passwd tree; this test never risks a writer.
    import pwd
    bare_loader = SourceFileLoader("krew_mode_bare", str(ROOT / "crew" / "scripts" / "krew-mode"))
    bare = module_from_spec(spec_from_loader(bare_loader.name, bare_loader))
    bare_loader.exec_module(bare)
    assert bare.HOUSE.resolve() == (Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes").resolve()
    with pytest.raises(RuntimeError, match="live ~/.hermes"):
        bare._refuse_live_under_pytest()
    # and the sandboxed copy every other test drives is not the live tree
    assert hm.HOUSE.resolve() != bare.HOUSE.resolve()


def test_ops_pin_recycles_crissa_gateway():
    assert hm.OPS_PIN_PROFILES == ("crissa",)
    assert "ada" not in hm.OPS_PIN_PROFILES
    assert hm.OPS_PIN_GATEWAYS == ("hermes-crissa-gateway.service",)
    assert "hermes-ada-gateway.service" not in hm.OPS_PIN_GATEWAYS


def test_write_mode_pins_ops_clients(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"
    crissa = hermes / "profiles" / "crissa" / "config.yaml"
    crissa.parent.mkdir(parents=True)
    crissa.write_text(_CRISSA_OPS)
    modef = tmp_path / "krew-gpu-mode"
    restarts: list[list[str]] = []
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "BRIDGE_CONFIG", hermes / "missing-bridge.yaml")
    monkeypatch.setattr(hm, "ADA_CONFIG", hermes / "profiles" / "ada" / "missing.yaml")
    monkeypatch.setattr(hm, "BEAST_OPS_PIN", tmp_path / "pin")
    monkeypatch.setattr(hm, "RENDER_LANE_PIN", tmp_path / "lane-pin")
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "hold")
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: restarts.append(list(args)))
    hm.write_mode("beast")
    assert modef.read_text() == "beast\n"
    assert "default: crew" in crissa.read_text()
    assert ["restart", "hermes-crissa-gateway.service"] in restarts
    restarts.clear()
    hm.write_mode("normal")
    assert modef.read_text() == "normal\n"
    assert "default: ops" in crissa.read_text()
    assert ["restart", "hermes-crissa-gateway.service"] in restarts


def test_nh_flip_restarts_crissa_without_beast_snapshot(tmp_path, monkeypatch):
    """Plain Normal↔Heavy must bounce Crissa so spawn caps load (not only Beast restore)."""
    hermes = tmp_path / "hermes"
    crissa = hermes / "profiles" / "crissa" / "config.yaml"
    crissa.parent.mkdir(parents=True)
    crissa.write_text(
        "model:\n  default: ops\n"
        "delegation:\n  max_concurrent_children: 1\n  max_spawn_depth: 1\n"
    )
    modef = tmp_path / "krew-gpu-mode"
    modef.write_text("normal\n")
    restarts: list[list[str]] = []
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "BRIDGE_CONFIG", hermes / "missing-bridge.yaml")
    monkeypatch.setattr(hm, "ADA_CONFIG", hermes / "profiles" / "ada" / "missing.yaml")
    monkeypatch.setattr(hm, "BEAST_OPS_PIN", tmp_path / "pin")
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "hold")
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: restarts.append(list(args)))
    hm.write_mode("normal")
    assert ["restart", "hermes-crissa-gateway.service"] in restarts
    assert "max_concurrent_children: 2\n" in crissa.read_text()



def test_crissa_spawn_is_two_subs(tmp_path):
    z = tmp_path / "crissa.yaml"
    z.write_text(
        "delegation:\n"
        "  max_concurrent_children: 1\n"
        "  max_spawn_depth: 1\n"
        "  orchestrator_enabled: true\n"
    )
    new, ch = hm.set_crissa_spawn(z.read_text(), 2, 1)
    assert ch is True
    assert "max_concurrent_children: 2\n" in new
    assert hm.CRISSA_SPAWN["normal"] == (2, 1)
    assert "heavy" not in hm.CRISSA_SPAWN
    assert hm.apply_crissa_spawn("normal", path=z) == "2/1"



def test_already_render_is_documented_in_cmd():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text()
    assert "already render" in src
    assert "pin_render_welcome" in src
    assert "kiosk_panel" in src
    assert "pin_kiosk_wall" in src
    assert "leave_render()" in src
    assert "listen_set(True)" not in src.split("def pin_kiosk_wall")[1].split("def cmd_render")[0]
    assert "cmd_beast" in src
    assert "already beast" in src
    assert "start-beast.sh" in src
    assert "hold_beast_park" in src
    assert "Unsloth Qwen3.8-27B" in src
    assert "Cosy + Audio8 stay" in src
    assert "Audio8 STT stays on CPU" in src
    assert "apply_ada_decomposer" in src
    assert "apply_ops_occupancy" in src
    assert "DECOMPOSER_CREW" in src
    assert "hermes-crissa-gateway.service" in src
    assert "sleep_render_rest" in src
    assert "wake_render_rest" in src
    stop = src.split("def stop_unit")[1].split("def start_unit")[0]
    assert "reset-failed" in stop





def _janitor(name, monkeypatch, topology):
    """Load krew-janitor under an EXPLICIT gateway topology: it rewrites its roster at import
    (apply_gateway_topology, 5ed52cd3), so an unpinned load reads the live krew-topology.env."""
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", topology)
    loader = SourceFileLoader(name, str(ROOT / "janitor" / "krew-janitor.py"))
    spec = spec_from_loader(loader.name, loader)
    jan = module_from_spec(spec)
    loader.exec_module(jan)
    return jan




def test_thaw_after_comfy_wakes_a_sleeper(monkeypatch):
    monkeypatch.setattr(hm, "crew_is_sleeping", lambda: True)
    assert hm.thaw_after_comfy() == "wake"
    monkeypatch.setattr(hm, "crew_is_sleeping", lambda: False)
    assert hm.thaw_after_comfy() == "restart"


def test_host_ram_gate_for_crew_l1():
    # 40 GiB available → 32+8 fits. 20 GiB does not.
    forty = "MemAvailable: 41943040 kB\n"
    twenty = "MemAvailable: 20971520 kB\n"
    assert hm.host_ram_available_gib(forty) == 40.0
    assert hm.host_ram_available_gib(twenty) == 20.0


def test_park_unit_crew_sleeps_when_ram_and_endpoints_exist(monkeypatch):
    posts: list[str] = []
    stops: list[str] = []
    monkeypatch.setattr(hm, "hold_crew_park", lambda: None)
    monkeypatch.setattr(hm, "sleep_available", lambda base: base == hm.CREW_URL)
    monkeypatch.setattr(hm, "ram_ok_for_crew_l1", lambda: True)
    monkeypatch.setattr(hm, "_post", lambda url, timeout=180: posts.append(url) or True)
    monkeypatch.setattr(hm, "stop_unit", lambda name: stops.append(name))
    assert hm.park_unit("crew") == "sleep"
    assert posts == [f"{hm.CREW_URL}/sleep?level=1"]
    assert stops == []


def test_park_unit_crew_stops_when_ram_is_tight(monkeypatch):
    stops: list[str] = []
    monkeypatch.setattr(hm, "hold_crew_park", lambda: None)
    monkeypatch.setattr(hm, "sleep_available", lambda base: True)
    monkeypatch.setattr(hm, "ram_ok_for_crew_l1", lambda: False)
    monkeypatch.setattr(hm, "stop_unit", lambda name: stops.append(name))
    assert hm.park_unit("crew") == "stop"
    assert stops == ["crew"]


def test_thaw_unit_crew_wakes_from_ram(monkeypatch):
    posts: list[str] = []
    starts: list[str] = []
    monkeypatch.setattr(hm, "engine_is_sleeping", lambda base: base == hm.CREW_URL)
    monkeypatch.setattr(hm, "_post", lambda url, timeout=240: posts.append(url) or True)
    monkeypatch.setattr(hm, "release_crew_hold", lambda: None)
    monkeypatch.setattr(hm, "start_unit", lambda name: starts.append(name))
    assert hm.thaw_unit("crew") == "wake"
    assert posts == [f"{hm.CREW_URL}/wake_up"]
    assert starts == []


def test_keep_render_crew_down_does_not_stop_a_sleeper(monkeypatch):
    stops: list[str] = []
    monkeypatch.setattr(hm, "hold_crew_park", lambda: None)
    monkeypatch.setattr(hm, "crew_is_sleeping", lambda: True)
    monkeypatch.setattr(hm, "stop_unit", lambda name: stops.append(name))
    monkeypatch.setattr(
        hm.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})()
    )
    hm.keep_render_crew_down()
    assert stops == []



def test_leaving_render_keeps_hold_until_thaw():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    chunk = src[src.find("def cmd_restore") :]
    leave = chunk[chunk.find("if leaving_render and not was_beast") :]
    leave = leave[: leave.find("if was_beast")]
    assert "force_restore=True" in leave
    assert "release_crew_hold()" not in leave


def test_units_to_wake_empty_parked_is_flag_only():
    assert hm.units_to_wake({"parked": []}, was_beast=False) == []
    assert hm.units_to_wake({"parked": ["crew"]}, was_beast=False) == ["crew"]
    assert hm.units_to_wake({"parked": ["ops", "crew"]}, was_beast=False) == ["ops", "crew"]
    assert hm.units_to_wake({}, was_beast=False) == ["crew"]
    assert hm.units_to_wake({"parked": []}, was_beast=True) == ["crew"]


def test_beast_restore_waits_for_cosy_health():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    chunk = src[src.find("def cmd_restore") :]
    beast = chunk[chunk.find("leaving beast") :]
    assert 'COSY_URL' in src
    assert "/health" in src
    assert 'label="cosy :7789"' in src
    assert "ensure_cosy_up(" in src
    assert "was_beast = beast_park_active()" in chunk
    assert beast.find("write_flag(mode)") < beast.find("ensure_cosy_up(")
    assert beast.find("ensure_cosy_up(") < beast.find('start_unit("ops")')
    assert 'apply_ops_occupancy(mode, restart=True)' in chunk
    assert beast.find("wait_http(f\"{OPS_URL}/v1/models\"") < beast.find(
        'apply_ops_occupancy(mode, restart=True)'
    )


def test_nothing_starts_cosy_except_ensure_cosy_up():
    """The twin came from five call sites, not from one bad line.

    2026-09-05: krew-mode had five `start_unit("cosy-voice")` calls, each
    guarded only by `if not http_ok(health)`. A switch that caught a live but
    still-loading Cosy started a SECOND server on the same port and card. The
    fix is that exactly one function may start her, and it checks for a running
    cosy_server.py by process before it does. Pin the absence, because the
    regression is someone adding a sixth site, not editing the one that exists.
    """
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    assert 'start_unit("cosy-voice")' not in src
    assert 'park_unit("cosy-voice")' not in src
    assert "def ensure_cosy_up(" in src
    assert "def park_cosy(" in src
    # ensure_cosy_up must consult the process, not only the health endpoint --
    # the loading window is exactly when a twin used to appear.
    body = src[src.find("def ensure_cosy_up(") : src.find("def park_cosy(")]
    assert "cosy_server_alive()" in body


def test_cosy_scope_is_user_everywhere():
    """Cosy is a USER unit. Saying "system" in a print is how the twin started.

    The 08-07 purge moved every lane to a single user owner; the system copy
    lingered until 2026-09-05 and `sudo systemctl start cosy-voice` woke a
    server with none of the eight drop-ins -- no vLLM stage, no voice pin. It
    answered /health and was the wrong voice. Keep the scope stated one way.
    """
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    body = src[src.find("def _systemctl_cosy(") :]
    body = body[: body.find("\ndef ", 1)]
    assert "_systemctl_user(" in body
    assert "sudo" not in body
    # No line may call Cosy a system unit.
    for i, line in enumerate(src.splitlines(), 1):
        low = line.lower()
        if "cosy" in low and "system unit" in low:
            raise AssertionError(f"krew-mode:{i} calls Cosy a system unit: {line.strip()}")



def test_occupancy_pending_roundtrip(tmp_path, monkeypatch):
    pend = tmp_path / "krew-gpu-pending"
    monkeypatch.setattr(hm, "PENDING_FILE", pend)
    assert hm.read_pending() == ""
    with hm.occupancy_pending("render"):
        raw = pend.read_text()
        assert raw.splitlines()[0] == "render"
        assert hm.read_pending() == "render"
        assert str(os.getpid()) in raw
    assert hm.read_pending() == ""
    assert not pend.exists()
    pend.write_text("beast\n1\n")  # PID 1 is not this writer; treat dead-or-other as not ours
    # PID 1 is usually alive (init). Write a definitely-dead pid.
    pend.write_text("beast\n99999999\n")
    assert hm.read_pending() == ""
    assert not pend.exists()
    pend.write_text("beast\n")
    assert hm.read_pending() == ""
    assert not pend.exists()
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    assert "render_sleep_hold_active" in src
    keep = src[src.find("def keep_render_ops_down") : src.find("def keep_render_crew_down")]
    assert "stop_unit(\"ops\")" in keep
    assert "--enable-sleep-mode" not in keep
    rest = src[src.find("def cmd_restore") :]
    assert "need_wake_sleepers" in rest
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    assert "occupancy_pending" in src
    assert "with occupancy_pending" in src
    assert "args.dry_run" in src[src.find("def main") :]


def test_beast_park_active_hold_or_flag(tmp_path, monkeypatch):
    hold = tmp_path / "beast-hold"
    mode = tmp_path / "krew-gpu-mode"
    monkeypatch.setattr(hm, "BEAST_HOLD", hold)
    monkeypatch.setattr(hm, "MODE_FILE", mode)
    mode.write_text("normal\n")
    assert hm.beast_park_active() is False
    hold.write_text("beast\n")
    assert hm.beast_park_active() is True
    hold.unlink()
    mode.write_text("beast\n")
    assert hm.beast_park_active() is True
    mode.write_text("render\n")
    hold.write_text("beast\n")
    assert hm.beast_park_active() is True


def test_dry_run_parses_after_the_recipe():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    assert 'a != "--dry-run"' in src
    assert "dry_anywhere" in src


def test_render_sleeps_the_mouth_not_the_lane():
    """User 2026-08-28: the kanban lane keeps working while Lila draws."""
    assert hm.RENDER_SLEEP_GATEWAYS == ("hermes-crissa-gateway",)
    assert "hermes-ada-gateway" not in hm.RENDER_SLEEP_GATEWAYS, "Ada dispatches"
    assert hm.RENDER_SLEEP_PROFILES == ("steward", "crissa")
    for who in ("lila", "vera", "bash", "ada"):
        assert who not in hm.RENDER_SLEEP_PROFILES, who
    assert "ops" not in hm.RENDER_SLEEP_GATEWAYS
    assert "crew" not in hm.RENDER_SLEEP_GATEWAYS
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    # workers are killed by argv (never the gateway) — see test_profile_worker_pids_*
    assert "_kill_profile_workers(name)" in src
    assert 'pkill", "-f", f"hermes -p {name} "' not in src
    cycle = (ROOT / "crew" / "scripts" / "render-cycle.py").read_text(encoding="utf-8")
    assert "KREW_TRAIN_HOLD" not in cycle
    assert "url=OPS" not in cycle
    assert "url=CREW" in cycle
    assert "def _engine(" in cycle
    keeper = (ROOT / "crew" / "scripts" / "krew-keeper.py").read_text(encoding="utf-8")
    assert "RENDER_NO_HEAL" in keeper
    assert "lane_down_render_sleep" in keeper


def test_write_mode_render_moves_crissa_to_crew_and_bounces_her(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"
    crissa = hermes / "profiles" / "crissa" / "config.yaml"
    crissa.parent.mkdir(parents=True)
    crissa.write_text(_CRISSA_OPS)
    modef = tmp_path / "krew-gpu-mode"
    restarts: list[list[str]] = []
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "BRIDGE_CONFIG", hermes / "missing-bridge.yaml")
    monkeypatch.setattr(hm, "ADA_CONFIG", hermes / "profiles" / "ada" / "missing.yaml")
    monkeypatch.setattr(hm, "BEAST_OPS_PIN", tmp_path / "pin")
    monkeypatch.setattr(hm, "RENDER_LANE_PIN", tmp_path / "lane-pin")

    class _Host:
        HOST_UNIT = "hermes-gateway.service"

    monkeypatch.setattr(hm, "_gateway_topology", lambda: _Host())
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: restarts.append(list(args)))
    hm.write_mode("render")
    assert modef.read_text() == "render\n"
    flat = [u for call in restarts for u in call]
    # 2026-09-22, the User: "use her as catchall ... wake her from the sleeping in Render."
    # She used to be slept here, so bouncing her was pure churn. Now she stays up as the
    # board's ruler and rides crew :8005 — ops is the 39 GB the render has to evict — and a
    # gateway that is not restarted keeps the brain it cached at start, i.e. the ops that
    # just parked. So the bounce is now the whole point. Kill KREW_CRISSA_CREW_IN_RENDER=0.
    assert "hermes-crissa-gateway.service" in flat
    assert "ops" not in crissa.read_text().split("base_url:")[1].split("\n")[0]
    assert ":8005" in crissa.read_text()
    # The dispatcher IS bounced: caps are read once at the shared gateway's boot.
    # The masked per-profile unit is not the process that loads them.
    assert "hermes-gateway.service" in flat
    assert "hermes-ada-gateway.service" not in flat


def test_cmd_render_keeps_ops_parked_when_leaving_beast():
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    chunk = src[src.find("def cmd_render") : src.find("def cmd_beast")]
    assert "leaving_beast = beast_park_active()" in chunk
    assert "keep ops parked" in chunk
    assert 'apply_ops_occupancy("render", restart=True)' not in chunk
    bounce = chunk.find('start_unit("crew")')
    comfy = chunk.find("start_comfy()")
    keep = chunk.find("keep_render_ops_down()")
    assert 0 < bounce < comfy < keep







def test_is_krew_comfy_skips_bash_wrapper():
    py = f"/opt/comfy/venv/bin/python {hm.COMFY_MAIN} --port 8188"
    bash = f"/bin/bash -c pgrep -f {hm.COMFY_MAIN}"
    assert hm._is_krew_comfy(10, py) is True
    assert hm._is_krew_comfy(11, bash) is False
    assert hm._is_krew_comfy(1, py) is False
    assert hm._is_krew_comfy(10, "sleep 1") is False


def test_live_comfy_pids_pgrep_not_stale_file(tmp_path, monkeypatch):
    pidf = tmp_path / "krew-comfy.pid"
    pidf.write_text("999999\n")
    monkeypatch.setattr(hm, "COMFY_PID", pidf)
    cmds = {
        3015610: f"/opt/comfy/venv/bin/python {hm.COMFY_MAIN} --port 8188",
        999999: "",
        8: f"/bin/bash -c pkill -f {hm.COMFY_MAIN}",
    }
    monkeypatch.setattr(hm, "_proc_cmdline", lambda pid: cmds.get(pid, ""))
    monkeypatch.setattr(
        hm.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, stdout="8\n3015610\n", stderr=""
        ),
    )
    monkeypatch.setattr(hm, "_pids_listening_port", lambda port: [])
    assert hm.live_comfy_pids() == [3015610]


def test_start_comfy_records_live_pid_when_already_up(tmp_path, monkeypatch):
    pidf = tmp_path / "krew-comfy.pid"
    monkeypatch.setattr(hm, "COMFY_PID", pidf)
    monkeypatch.setattr(hm, "comfy_up", lambda: True)
    monkeypatch.setattr(hm, "live_comfy_pids", lambda: [3015610])
    assert hm.start_comfy() == 3015610
    assert pidf.read_text() == "3015610\n"


def test_start_comfy_runs_in_its_own_scope_not_the_callers_cgroup(tmp_path, monkeypatch):
    """the owner 2026-09-16: "she is non responsive in render" / "no render in CP at
    all now". The CP's Render button runs krew-mode inside voiceonly-home, so
    Comfy shared that cgroup and a :8099 restart SIGKILLed it silently."""
    monkeypatch.setattr(hm, "COMFY_PID", tmp_path / "krew-comfy.pid")
    monkeypatch.setattr(hm, "comfy_up", lambda: False)
    monkeypatch.setenv("KREW_COMFY_LOG", str(tmp_path / "comfy.log"))
    monkeypatch.delenv("KREW_COMFY_SCOPE", raising=False)
    seen = []

    class P:
        pid = 4242

    monkeypatch.setattr(hm.subprocess, "Popen", lambda cmd, **k: seen.append(cmd) or P())
    monkeypatch.setattr(hm.shutil, "which", lambda n: "/usr/bin/systemd-run")
    assert hm.start_comfy() == 4242
    cmd = seen[-1]
    assert cmd[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
    assert hm.COMFY_MAIN in cmd and cmd.index("--unit") < cmd.index(hm.COMFY_MAIN)
    monkeypatch.setenv("KREW_COMFY_SCOPE", "0")
    hm.start_comfy()
    assert seen[-1][0] == os.environ.get("KREW_COMFY_PYTHON") or os.path.join(os.path.expanduser(os.environ.get("KREW_COMFY_ROOT") or "~/ComfyUI"), "venv", "bin", "python"), "the kill switch is the old launch"


def test_start_comfy_env_drops_the_worker_python(tmp_path, monkeypatch):
    """A Hermes worker's PYTHONPATH is the py3.14 tree. Comfy is py3.12."""
    monkeypatch.setattr(hm, "COMFY_PID", tmp_path / "krew-comfy.pid")
    monkeypatch.setattr(hm, "comfy_up", lambda: False)
    monkeypatch.setenv("KREW_COMFY_LOG", str(tmp_path / "comfy.log"))
    monkeypatch.setenv("KREW_COMFY_SCOPE", "0")
    monkeypatch.setenv("PYTHONPATH", str(Path.home() / ".hermes" / "hermes-agent"))
    monkeypatch.setenv("PYTHONHOME", "/tmp/pyhome")
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/venv")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_worker")
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "daily")
    monkeypatch.setenv("HERMES_KANBAN_DB", "/tmp/kanban.db")
    seen: dict = {}

    class P:
        pid = 4242

    def popen(cmd, **k):
        seen["env"] = k.get("env") or {}
        return P()

    monkeypatch.setattr(hm.subprocess, "Popen", popen)
    assert hm.start_comfy() == 4242
    env = seen["env"]
    for key in (
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
        "HERMES_KANBAN_TASK", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_DB",
    ):
        assert key not in env, key


def test_stop_comfy_unlinks_pid_only_when_down(tmp_path, monkeypatch):
    pidf = tmp_path / "krew-comfy.pid"
    pidf.write_text("10\n")
    monkeypatch.setattr(hm, "COMFY_PID", pidf)
    monkeypatch.setattr(hm, "live_comfy_pids", lambda: [10])
    killed: list[int] = []
    monkeypatch.setattr(hm, "_kill_pid", killed.append)
    monkeypatch.setattr(
        hm.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0)
    )
    monkeypatch.setenv("KREW_COMFY_KILL_SETTLE_SEC", "0")
    n = {"t": 0}

    def now() -> float:
        n["t"] += 20
        return float(n["t"])

    monkeypatch.setattr(hm.time, "time", now)
    monkeypatch.setattr(hm.time, "sleep", lambda s: None)
    monkeypatch.setattr(hm, "comfy_up", lambda: True)
    assert hm.stop_comfy() is False
    assert pidf.exists()
    assert 10 in killed
    killed.clear()
    n["t"] = 0
    monkeypatch.setattr(hm, "comfy_up", lambda: False)
    assert hm.stop_comfy() is True
    assert not pidf.exists()


def test_cmd_restore_aborts_before_thaw_if_comfy_up(monkeypatch):
    thawed: list[str] = []
    monkeypatch.setattr(hm, "stop_comfy", lambda: False)
    monkeypatch.setattr(hm, "read_mode", lambda: "render")
    monkeypatch.setattr(hm, "beast_park_active", lambda: False)
    monkeypatch.setattr(hm, "off_park_active", lambda: False)
    monkeypatch.setattr(hm, "render_sleep_hold_active", lambda: True)
    monkeypatch.setattr(hm, "read_state", lambda: {"parked": ["crew"]})
    monkeypatch.setattr(hm, "thaw_unit", lambda u: thawed.append(u))
    monkeypatch.setattr(hm, "pin_kiosk_wall", lambda: (_ for _ in ()).throw(AssertionError("kiosk")))
    # cmd_restore wakes the render rest before it looks at Comfy; unmocked, this
    # test started Crissa's PARKED gateway for real and rewrote the live hold file
    # (2026-09-17, in Render). The wake is recorded, never run.
    woke: list[str] = []
    monkeypatch.setattr(hm, "wake_render_rest", lambda: woke.append("wake"))
    rc = hm.cmd_restore("normal")
    assert rc == 1
    assert thawed == []
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    rest = src[src.find("def cmd_restore") :]
    assert rest.find("if not stop_comfy()") < rest.find("thaw_unit(unit)")
    beast = src[src.find("def cmd_beast") : src.find("def cmd_off")]
    off = src[src.find("def cmd_off") : src.find("def units_to_wake")]
    assert "if not stop_comfy()" in beast
    assert "if not stop_comfy()" in off


def test_ops_fallback_ready(monkeypatch):
    monkeypatch.setattr(
        hm, "http_ok", lambda url, timeout=2.0: url.rstrip("/").endswith(":8000") or "/v1/models" in url
    )
    monkeypatch.setattr(hm, "count_live_ops_pins", lambda: 1)
    assert hm.ops_fallback_ready() is True
    monkeypatch.setattr(hm, "count_live_ops_pins", lambda: 0)
    assert hm.ops_fallback_ready() is False
    monkeypatch.setattr(hm, "count_live_ops_pins", lambda: 1)
    monkeypatch.setattr(hm, "http_ok", lambda url, timeout=2.0: False)
    assert hm.ops_fallback_ready() is False


def test_crew_wait_failure_wakes_only_with_ops_fallback(monkeypatch):
    woke: list[str] = []
    monkeypatch.setattr(hm, "wake_render_rest", lambda: woke.append("wake"))
    monkeypatch.setattr(hm, "ops_fallback_ready", lambda: True)
    monkeypatch.setattr(hm, "comfy_up", lambda: False)
    assert (
        hm.maybe_wake_sleepers_after_crew_fail(
            leaving_render=True, need_wake_sleepers=True
        )
        is True
    )
    assert woke == ["wake"]
    woke.clear()
    monkeypatch.setattr(hm, "ops_fallback_ready", lambda: False)
    assert (
        hm.maybe_wake_sleepers_after_crew_fail(
            leaving_render=True, need_wake_sleepers=True
        )
        is False
    )
    assert woke == []
    assert (
        hm.maybe_wake_sleepers_after_crew_fail(
            leaving_render=False, need_wake_sleepers=False
        )
        is False
    )
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text(encoding="utf-8")
    rest = src[src.find("def cmd_restore") :]
    fail = rest.find('crew :8005 did not return /v1/models')
    cond = rest.find("maybe_wake_sleepers_after_crew_fail")
    write = rest.find("write_mode(mode)", fail)
    assert 0 <= fail < cond < write
    assert "release_occupancy_hold" in rest
    assert "extend=90" in rest[rest.find("wait_vram") :]


def test_each_recipe_cmd_render_dry_run_parks_ops(monkeypatch, capsys):
    """Every krew-mode software option is a known recipe that parks ops."""
    monkeypatch.setattr(hm, "free_vram_gib", lambda: 2.6)
    monkeypatch.setattr(hm, "voice_is_on", lambda: True)
    monkeypatch.setattr(hm, "cosy_server_alive", lambda: True)
    monkeypatch.setattr(hm, "COSY_RENDER_PARK", False)
    # krew: both brains up, Comfy down, by mock - the reference test read the live engines' health on :8000/:8005
    monkeypatch.setattr(hm, "http_ok", lambda url, timeout=2.0: url.startswith((hm.OPS_URL, hm.CREW_URL)))
    monkeypatch.setattr(hm, "comfy_up", lambda: False)
    want = (
        "turbo", "klein", "sdxl", "flux1", "qwen", "qwen-edit",
        "wan", "max", "flux2-dev",
        "qwen21",  # Qwen-Image 2.1 int8 (09-29), krew-only
    )
    assert tuple(sorted(hm.RECIPES)) == tuple(sorted(want))
    for recipe in want:
        rc = hm.cmd_render(recipe, dry_run=True)
        plan = hm.park_plan(hm.RECIPES[recipe]["need_gib"], 2.6, True)
        assert rc == 0, recipe
        assert plan["ok"] is True, recipe
        assert plan["park"] == ["ops"], recipe
    out = capsys.readouterr().out
    for recipe in want:
        assert f"render {recipe} " in out


def test_park_unit_sleep_failure_falls_back_to_stop(monkeypatch, tmp_path):
    hold = tmp_path / "hold"
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.setattr(hm, "sleep_available", lambda base: True)
    monkeypatch.setattr(hm, "ram_ok_for_crew_l1", lambda: True)
    monkeypatch.setattr(hm, "_post", lambda url, timeout=180: False)
    stops: list[str] = []
    monkeypatch.setattr(hm, "stop_unit", lambda name: stops.append(name))
    assert hm.park_unit("crew") == "stop"
    assert stops == ["crew"]
    text = hold.read_text()
    assert "crew.service" in text
    assert "# card=occupancy" in text


def test_thaw_unit_wake_failure_falls_back_to_restart(monkeypatch, tmp_path):
    hold = tmp_path / "hold"
    hold.write_text("# card=occupancy\ncrew.service\n")
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.setattr(hm, "engine_is_sleeping", lambda base: True)
    monkeypatch.setattr(hm, "_post", lambda url, timeout=240: False)
    calls: list[list[str]] = []
    held_at_restart: list[bool] = []

    def sysctl(args):
        args = list(args)
        calls.append(args)
        if args[:2] == ["restart", "crew.service"]:
            still = hold.is_file() and "crew.service" in hold.read_text()
            held_at_restart.append(still)
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(hm, "_systemctl_user", sysctl)
    assert hm.thaw_unit("crew") == "restart"
    assert ["restart", "crew.service"] in calls
    assert held_at_restart == [False], "start-crew.sh no-ops if hold still lists crew"
    assert not hold.exists() or "crew.service" not in hold.read_text()


def test_release_occupancy_hold_clears_unstamped_render_hold(tmp_path, monkeypatch):
    hold = tmp_path / "hold"
    hold.write_text("crew.service\nhermes-crissa-gateway.service\n")
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    hm.release_occupancy_hold()
    assert not hold.exists()


def test_release_occupancy_hold_spares_human_other_units(tmp_path, monkeypatch):
    hold = tmp_path / "hold"
    hold.write_text("ops.service\nrail-presence.service\n")
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    hm.release_occupancy_hold()
    assert hold.exists()
    text = hold.read_text()
    assert "rail-presence.service" in text
    assert "ops.service" not in text


def _stub_restore_side_effects(monkeypatch, tmp_path):
    modef = tmp_path / "krew-gpu-mode"
    statef = tmp_path / "krew-gpu-state.json"
    hold = tmp_path / "hold"
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "STATE_FILE", statef)
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.setattr(hm, "COMFY_SETTLE_SEC", 0)
    monkeypatch.setattr(hm, "stop_comfy", lambda: True)
    monkeypatch.setattr(hm, "beast_park_active", lambda: False)
    monkeypatch.setattr(hm, "off_park_active", lambda: False)
    monkeypatch.setattr(hm, "pin_kiosk_wall", lambda: None)
    monkeypatch.setattr(hm, "write_ops_recipe", lambda x: None)
    monkeypatch.setattr(hm, "wait_vram", lambda *a, **k: 50.0)
    monkeypatch.setattr(hm, "wait_http", lambda *a, **k: True)
    monkeypatch.setattr(hm, "http_ok", lambda *a, **k: "/8188" not in str(a[0]))
    monkeypatch.setattr(hm, "comfy_up", lambda: False)
    monkeypatch.setattr(hm, "crew_is_sleeping", lambda: False)
    monkeypatch.setattr(hm, "apply_render_lane", lambda *a, **k: "ok")
    monkeypatch.setattr(hm, "apply_ada_decomposer", lambda *a, **k: None)
    monkeypatch.setattr(hm, "apply_ada_dispatch", lambda *a, **k: None)
    monkeypatch.setattr(hm, "apply_crissa_spawn", lambda *a, **k: None)
    monkeypatch.setattr(hm, "apply_ops_occupancy", lambda *a, **k: None)
    monkeypatch.setattr(hm, "maybe_sleep_ada_for_exclusive", lambda *a, **k: None)
    monkeypatch.setattr(hm, "maybe_restart_ada_gateway", lambda *a, **k: None)
    monkeypatch.setattr(hm, "maybe_restart_crissa_gateway", lambda *a, **k: None)
    woke: list[str] = []
    monkeypatch.setattr(hm, "wake_render_rest", lambda: woke.append("wake"))
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    return modef, statef, hold, woke


def test_cmd_restore_matches_pre_render_snapshot(tmp_path, monkeypatch):
    import json
    modef, statef, hold, woke = _stub_restore_side_effects(monkeypatch, tmp_path)
    modef.write_text("render\n")
    hold.write_text("# card=occupancy\nops.service\nhermes-crissa-gateway.service\n")
    statef.write_text(
        json.dumps(
            {
                "mode": "render",
                "parked": ["ops"],
                "pre_render": {
                    "mode": "normal",
                    "hold": [],
                    "hold_cards": [],
                    "parked": [],
                },
            }
        )
    )
    starts: list[str] = []
    monkeypatch.setattr(hm, "engine_is_sleeping", lambda base: False)
    monkeypatch.setattr(hm, "start_unit", lambda name: starts.append(name))
    rc = hm.cmd_restore("normal")
    assert rc == 0
    assert modef.read_text().strip() == "normal"
    assert starts == ["ops"]
    assert woke == ["wake"]
    st = json.loads(statef.read_text())
    assert st["mode"] == "normal"
    assert st.get("parked") == []
    assert "pre_render" not in st
    assert not hold.exists() or "ops.service" not in hold.read_text()


def test_cmd_restore_wake_fail_still_leaves_normal(tmp_path, monkeypatch):
    import json
    modef, statef, hold, woke = _stub_restore_side_effects(monkeypatch, tmp_path)
    modef.write_text("render\n")
    hold.write_text("# card=occupancy\ncrew.service\n")
    statef.write_text(json.dumps({"mode": "render", "parked": ["crew"],
                                  "pre_render": {"mode": "normal", "hold": [],
                                                 "hold_cards": [], "parked": []}}))
    monkeypatch.setattr(hm, "engine_is_sleeping", lambda base: True)
    monkeypatch.setattr(hm, "_post", lambda url, timeout=240: False)
    calls: list[list[str]] = []
    held_at_restart: list[bool] = []

    def sysctl(args):
        args = list(args)
        calls.append(args)
        if args[:2] == ["restart", "crew.service"]:
            still = hold.is_file() and "crew.service" in hold.read_text()
            held_at_restart.append(still)
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(hm, "_systemctl_user", sysctl)
    rc = hm.cmd_restore("normal")
    assert rc == 0
    assert ["restart", "crew.service"] in calls
    assert held_at_restart == [False], "start-crew.sh no-ops if hold still lists crew"
    assert modef.read_text().strip() == "normal"
    assert not hold.exists() or "crew.service" not in hold.read_text()


def test_cmd_restore_crew_wait_fail_still_clears_render_flag(tmp_path, monkeypatch):
    import json
    modef, statef, hold, woke = _stub_restore_side_effects(monkeypatch, tmp_path)
    modef.write_text("render\n")
    hold.write_text("ops.service\nhermes-crissa-gateway.service\n")
    statef.write_text(json.dumps({"mode": "render", "parked": ["ops"]}))
    monkeypatch.setattr(hm, "engine_is_sleeping", lambda base: False)
    monkeypatch.setattr(hm, "start_unit", lambda name: None)
    monkeypatch.setattr(hm, "wait_http", lambda *a, **k: False)
    monkeypatch.setattr(hm, "ops_fallback_ready", lambda: True)
    rc = hm.cmd_restore("normal")
    assert rc == 1
    assert modef.read_text().strip() == "normal"
    assert not hold.exists()


def test_capture_pre_render_snapshot_first_write(tmp_path, monkeypatch):
    import json
    statef = tmp_path / "state.json"
    modef = tmp_path / "mode"
    hold = tmp_path / "hold"
    modef.write_text("normal\n")
    hold.write_text("ops.service\n")
    statef.write_text(json.dumps({"mode": "normal", "parked": []}))
    monkeypatch.setattr(hm, "STATE_FILE", statef)
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    snap = hm.capture_pre_render_snapshot()
    assert snap["mode"] == "normal"
    assert snap["hold"] == ["ops.service"]
    again = hm.capture_pre_render_snapshot()
    assert again == snap


# --- 2026-09-12: only a live worker holds; normal returns everything else ------

def _rule(monkeypatch, status, pid_alive, pending=False, mode="normal", comfy=False):
    monkeypatch.setattr(hm, "card_status", lambda c: status)
    monkeypatch.setattr(hm, "card_worker_pid", lambda c: 4242)
    monkeypatch.setattr(hm, "pid_alive", lambda p: pid_alive)
    pend = {"t_1"} if pending else set()
    return hm.hold_card_live("t_1", pending=pend, mode=mode, comfy=comfy)


def test_only_a_running_card_with_a_live_worker_holds(monkeypatch):
    assert _rule(monkeypatch, "running", True)[0] is True
    assert _rule(monkeypatch, "running", False)[0] is False          # worker gone
    assert _rule(monkeypatch, "blocked", True)[0] is False           # blocked releases
    assert _rule(monkeypatch, "archived", False)[0] is False
    assert _rule(monkeypatch, "ready", False)[0] is False
    assert _rule(monkeypatch, None, False)[0] is True                # no board knows it: keep (2026-09-14, Grok —
                                                                     # 12:48:02 released "init-wizard" as unknown)


def test_render_pending_holds_only_while_comfy_serves(monkeypatch):
    assert _rule(monkeypatch, "blocked", False, pending=True, mode="normal", comfy=False)[0] is False
    assert _rule(monkeypatch, "blocked", False, pending=True, mode="normal", comfy=True)[0] is True
    assert _rule(monkeypatch, "blocked", False, pending=True, mode="render", comfy=False)[0] is True
    assert _rule(monkeypatch, "running", False, pending=True, mode="render")[0] is True


def _crissa_start_argv(topology):
    """How the house wakes Crissa's gateway. The hold keeps the per-profile unit NAME (its vocabulary
    from before the cutover); under the 09-27 one host gateway the one door to systemd
    (`_host_gateway_call`) turns a start into `hermes -p crissa gateway start` (lib/hermes_topology
    unpark) - the masked unit is never started."""
    import hermes_topology

    if topology == "host":
        return [hermes_topology.HERMES_BIN, "-p", "crissa", "gateway", "start"]
    return ["systemctl", "--user", "start", "hermes-crissa-gateway.service"]


@pytest.mark.parametrize("topology", ["host", "units"])
def test_hold_sweep_releases_the_stale_card_and_starts_what_it_parked(tmp_path, monkeypatch, topology):
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", topology)
    hold = tmp_path / "krew-train-hold"
    hold.write_text("# card=t_dead\n# card=t_live\nops.service\nhermes-crissa-gateway.service\n")
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.setattr(hm, "card_status", lambda c: "running" if c == "t_live" else "blocked")
    monkeypatch.setattr(hm, "card_worker_pid", lambda c: 4242 if c == "t_live" else None)
    monkeypatch.setattr(hm, "pid_alive", lambda p: p == 4242)
    monkeypatch.setattr(hm, "_render_pending_cards", lambda **k: set())
    monkeypatch.setattr(hm, "read_mode", lambda text=None: "normal")
    monkeypatch.setattr(hm, "_hold_log", lambda e: None)
    monkeypatch.setattr(hm, "sweep_comfy_leak", lambda **k: True)   # Comfy down; leak + way back tested apart
    monkeypatch.setattr(hm, "way_back_units", lambda **k: [])
    calls = []
    monkeypatch.setattr(hm.subprocess, "run", lambda argv, **kw: calls.append(argv) or type("R", (), {"returncode": 0, "stdout": "inactive", "stderr": ""})())
    monkeypatch.setattr(hm, "HOUSE", tmp_path)
    assert hm.cmd_hold_sweep() == 0
    assert "t_dead" in hold.read_text() and "t_live" in hold.read_text()   # first not-live tick: hysteresis
    assert not [c for c in calls if c[-2:] == ["start", "ops.service"]]
    assert hm.cmd_hold_sweep() == 0
    assert "t_dead" not in hold.read_text() and "t_live" in hold.read_text()   # second tick releases t_dead
    assert not [c for c in calls if c[-2:] == ["start", "ops.service"]]     # still held by t_live
    hold.write_text("# card=t_dead\nops.service\nhermes-crissa-gateway.service\n")
    calls.clear()
    monkeypatch.setattr(hm, "_comfy_idle_or_stop", lambda: None)
    assert hm.cmd_hold_sweep() == 0  # hysteresis 1
    assert hold.exists()
    assert hm.cmd_hold_sweep() == 0  # hysteresis 2 → release
    assert not hold.exists()
    assert ["systemctl", "--user", "start", "ops.service"] in calls
    assert _crissa_start_argv(topology) in calls


def test_normal_return_calls_the_release():
    # heavy-render H2 (09-29): cmd_restore wraps _cmd_restore (the heavy-render prelude/cleanup);
    # the release lives in the inner body every `normal` still runs
    assert "return _cmd_restore(mode, dry_run=dry_run)" in inspect.getsource(hm.cmd_restore)
    src = inspect.getsource(hm._cmd_restore)
    assert 'release_stale_card_holds(by="normal"' in src


def test_krew_tree_ignores_a_sandboxed_home(tmp_path):
    """2026-09-13: kanban workers run with HOME=~/.hermes/profiles/<p>/home;
    Path.home() followed it, the voice lock was missing there, and `render`
    parked Cosy despite Voice ON. The house tree is the passwd home."""
    import os
    import pwd
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = ("import runpy,sys; sys.argv=['krew-mode']; "
            "g=runpy.run_path(%r, run_name='not_main'); print(g['HOUSE'])" % str(root / "crew" / "scripts" / "krew-mode"))
    env = dict(os.environ, HOME=str(tmp_path))
    env.pop("KREW_HERMES", None)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    want = str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes")
    assert out.stdout.strip().splitlines()[-1] == want
    assert str(tmp_path) not in out.stdout


# --- 2026-09-13: the render way back (docs/sessions/2026-09/REVIEW-20260913-RENDER-WAY-BACK.md)

def _jobs(tmp_path, monkeypatch, rows):
    import json
    house = tmp_path / "house"
    house.mkdir()
    (house / "render-jobs.json").write_text(json.dumps(rows, indent=1))
    monkeypatch.setattr(hm, "HOUSE", house)
    monkeypatch.setattr(hm, "_hold_log", lambda e: None)
    return house / "render-jobs.json"


def _stamp(age_s: float) -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - age_s))


def test_preparing_dead_pid_is_ghost_even_when_young(tmp_path, monkeypatch):
    import json, os
    jobs = _jobs(tmp_path, monkeypatch, [
        {"card": "t_dead", "name": "sit-down", "take": 1, "status": "preparing",
         "submitted": _stamp(30), "pid": 1, "pid_starttime": "nope", "alive_at": 0},
        {"card": "t_live", "name": "sit-down", "take": 2, "status": "preparing",
         "submitted": _stamp(500), "pid": os.getpid(),
         "pid_starttime": hm._pid_starttime(os.getpid()), "alive_at": __import__("time").time()},
        {"card": "t_old", "name": "sit-down", "take": 3, "status": "preparing",
         "submitted": _stamp(60)},
    ])
    live = hm._render_pending_cards()
    rows = {(r["card"], r["take"]): r for r in json.loads(jobs.read_text())}
    assert "t_dead" not in live and rows[("t_dead", 1)]["status"] == "failed"
    assert "t_live" in live and rows[("t_live", 2)]["status"] == "preparing"
    assert "t_old" in live and rows[("t_old", 3)]["status"] == "preparing"  # no pid, age 60 < 900


def test_preparing_ghost_older_than_ttl_is_marked_failed(tmp_path, monkeypatch):
    import json
    jobs = _jobs(tmp_path, monkeypatch, [
        {"card": "t_ghost", "name": "sit-down", "take": 10, "status": "preparing", "submitted": _stamp(4 * 3600)},
        {"card": "t_fresh", "name": "sit-down", "take": 11, "status": "preparing", "submitted": _stamp(60)},
        {"card": "t_on", "name": "listen", "take": 9, "status": "pending", "prompt_id": "abc", "submitted": _stamp(600)},
        {"card": "t_nopid", "name": "listen", "take": 3, "status": "pending", "submitted": _stamp(600)},
    ])
    live = hm._render_pending_cards()
    assert live == {"t_fresh", "t_on"}
    rows = {(r["card"], r["take"]): r for r in json.loads(jobs.read_text())}
    assert rows[("t_ghost", 10)]["status"] == "failed" and "ghost" in rows[("t_ghost", 10)]["error"]
    assert rows[("t_fresh", 11)]["status"] == "preparing"
    assert hm._render_pending_cards() == {"t_fresh", "t_on"}           # idempotent
    jobs.write_text(json.dumps([{"card": "t_ghost", "name": "sit-down", "take": 10, "status": "preparing",
                                 "submitted": _stamp(4 * 3600)}]))
    assert hm._render_pending_cards(dry_run=True) == set()             # dry run: not live, not rewritten
    assert json.loads(jobs.read_text())[0]["status"] == "preparing"


def test_ghost_row_does_not_hold_under_idle_comfy(tmp_path, monkeypatch):
    """The 441f8800 pin, flipped: blocked card + preparing-no-pid + flag normal
    + Comfy up kept ops and Crissa parked 00:25-04:10 on 2026-09-13."""
    _jobs(tmp_path, monkeypatch, [
        {"card": "t_1", "name": "sit-down", "take": 10, "status": "preparing", "submitted": _stamp(4 * 3600)},
    ])
    monkeypatch.setattr(hm, "card_status", lambda c: "blocked")
    monkeypatch.setattr(hm, "card_worker_pid", lambda c: None)
    live, why = hm.hold_card_live("t_1", mode="normal", comfy=True)
    assert live is False, why


def test_carried_archived_card_with_render_on_comfy_keeps_hold(tmp_path, monkeypatch):
    """krew-board-carry left the old id in the body; the render is real."""
    _jobs(tmp_path, monkeypatch, [
        {"card": "t_old", "name": "sit-up", "take": 3, "status": "pending", "prompt_id": "6abb", "submitted": _stamp(120)},
    ])
    monkeypatch.setattr(hm, "card_status", lambda c: "archived")
    monkeypatch.setattr(hm, "card_worker_pid", lambda c: None)
    live, why = hm.hold_card_live("t_old", mode="normal", comfy=True)
    assert live is True and "Comfy serving" in why
    live, why = hm.hold_card_live("t_old", mode="normal", comfy=False)
    assert live is False


def _way_back_stubs(monkeypatch, tmp_path, *, hold_text=None, mode="normal", pending="", comfy=False,
                    ops_http=False, active=(), budget=()):
    hold = tmp_path / "hold"
    if hold_text is not None:
        hold.write_text(hold_text)
    else:
        hold.unlink(missing_ok=True)
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.setattr(hm, "read_mode", lambda text=None: mode)
    monkeypatch.setattr(hm, "read_pending", lambda: pending)
    monkeypatch.setattr(hm, "beast_park_active", lambda: False)
    monkeypatch.setattr(hm, "off_park_active", lambda: False)
    monkeypatch.setattr(hm, "comfy_up", lambda: comfy)
    monkeypatch.setattr(hm, "http_ok", lambda url, timeout=2.0: ops_http)
    monkeypatch.setattr(hm, "_unit_active", lambda u: u in active)
    monkeypatch.setattr(hm, "keeper_budget_spent", lambda u: u in budget)
    return hold


def test_way_back_starts_ops_when_flag_normal_and_silicon_down(tmp_path, monkeypatch):
    _way_back_stubs(monkeypatch, tmp_path)
    assert hm.way_back_units() == ["ops.service", "hermes-crissa-gateway.service"]
    _way_back_stubs(monkeypatch, tmp_path, active=("hermes-crissa-gateway.service",))
    assert hm.way_back_units() == ["ops.service"]
    _way_back_stubs(monkeypatch, tmp_path, ops_http=True, active=("ops.service", "hermes-crissa-gateway.service"))
    assert hm.way_back_units() == []


def test_way_back_gates(tmp_path, monkeypatch):
    _way_back_stubs(monkeypatch, tmp_path, hold_text="ops.service\n")            # human hold: rail
    assert hm.way_back_units() == []
    _way_back_stubs(monkeypatch, tmp_path, hold_text="# card=t_live\nops.service\n")   # live owner
    assert hm.way_back_units() == []
    _way_back_stubs(monkeypatch, tmp_path, mode="render")
    assert hm.way_back_units() == []
    _way_back_stubs(monkeypatch, tmp_path, pending="render")                    # switch in flight
    assert hm.way_back_units() == []
    _way_back_stubs(monkeypatch, tmp_path, comfy=True)                          # a render, or a leak: not ours
    assert hm.way_back_units() == []
    _way_back_stubs(monkeypatch, tmp_path, budget=("ops.service",))             # keeper said "needs a human"
    assert hm.way_back_units() == ["hermes-crissa-gateway.service"]


@pytest.mark.parametrize("topology", ["host", "units"])
def test_hold_sweep_runs_the_way_back_with_no_card_hold(tmp_path, monkeypatch, topology):
    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", topology)
    _jobs(tmp_path, monkeypatch, [])
    _way_back_stubs(monkeypatch, tmp_path)
    monkeypatch.setattr(hm, "sweep_comfy_leak", lambda **k: True)
    calls = []
    monkeypatch.setattr(hm.subprocess, "run", lambda argv, **kw: calls.append(list(argv)) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    assert hm.cmd_hold_sweep() == 0
    assert ["systemctl", "--user", "start", "ops.service"] in calls
    assert _crissa_start_argv(topology) in calls
    calls.clear()
    assert hm.cmd_hold_sweep(dry_run=True) == 0
    assert not calls


def test_sweep_comfy_leak_stops_idle_comfy_in_normal(monkeypatch):
    stopped = []
    monkeypatch.setattr(hm, "comfy_up", lambda: True)
    monkeypatch.setattr(hm, "read_pending", lambda: "")
    monkeypatch.setattr(hm, "_comfy_queue_idle", lambda: True)
    monkeypatch.setattr(hm, "stop_comfy", lambda: stopped.append(1) or True)
    monkeypatch.setattr(hm, "_hold_log", lambda e: None)
    assert hm.sweep_comfy_leak(by="t", pending=set(), mode="normal") is True and stopped == [1]
    stopped.clear()
    assert hm.sweep_comfy_leak(by="t", pending={"t_1"}, mode="normal") is False and not stopped   # a brief
    assert hm.sweep_comfy_leak(by="t", pending=set(), mode="render") is False and not stopped    # Render
    monkeypatch.setattr(hm, "_comfy_queue_idle", lambda: False)
    assert hm.sweep_comfy_leak(by="t", pending=set(), mode="normal") is False and not stopped    # busy
    monkeypatch.setattr(hm, "_comfy_queue_idle", lambda: None)
    assert hm.sweep_comfy_leak(by="t", pending=set(), mode="normal") is False and not stopped    # unknown
    monkeypatch.setattr(hm, "_comfy_queue_idle", lambda: True)
    assert hm.sweep_comfy_leak(by="t", pending=set(), mode="normal", dry_run=True) is False and not stopped


def test_restore_due_is_hold_and_state_not_flag_only(tmp_path, monkeypatch):
    import json
    hold = tmp_path / "hold"
    statef = tmp_path / "state.json"
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.setattr(hm, "STATE_FILE", statef)
    monkeypatch.setattr(hm, "read_mode", lambda text=None: "normal")
    statef.write_text(json.dumps({"mode": "normal", "parked": []}))
    assert hm.restore_due() is False                                   # flag normal + nothing: not owed
    hold.write_text("ops.service\n")                                   # human hold: never
    assert hm.restore_due() is False
    hold.write_text("# card=t_15268ff5\nops.service\nhermes-crissa-gateway.service\n")
    assert hm.restore_due() is True                                    # a card still names ops
    hold.unlink()
    statef.write_text(json.dumps({"mode": "normal", "parked": ["ops"]}))
    assert hm.restore_due() is True
    statef.write_text(json.dumps({"mode": "normal", "parked": []}))
    monkeypatch.setattr(hm, "read_mode", lambda text=None: "render")
    assert hm.restore_due() is True
    # Its caller, the krew-gpu-hold plugin, was retired 09-27 (6d38b58d: it never ran - it rebound
    # kanban_tools after hermes had registered the handlers); the hold sweep is the way back now.


def test_cmd_restore_flag_normal_wakes_ops_named_by_released_hold(tmp_path, monkeypatch):
    """A worker wrote parked[] to a sandboxed HOME; the house state says [] while
    the hold in /tmp names ops. `normal` must wake what the hold named."""
    import json
    modef, statef, hold, woke = _stub_restore_side_effects(monkeypatch, tmp_path)
    modef.write_text("normal\n")
    statef.write_text(json.dumps({"mode": "normal", "parked": []}))
    hold.write_text("# card=t_15268ff5\nops.service\nhermes-crissa-gateway.service\n")
    monkeypatch.setattr(hm, "card_status", lambda c: "archived")
    monkeypatch.setattr(hm, "card_worker_pid", lambda c: None)
    monkeypatch.setattr(hm, "_render_pending_cards", lambda **k: set())
    monkeypatch.setattr(hm, "_hold_log", lambda e: None)
    monkeypatch.setattr(hm, "_comfy_idle_or_stop", lambda: True)
    monkeypatch.setattr(hm, "read_pending", lambda: "normal")
    monkeypatch.setattr(hm, "_unit_active", lambda u: False)
    monkeypatch.setattr(hm, "keeper_budget_spent", lambda u: False)
    monkeypatch.setattr(hm, "write_flag", lambda *a, **k: False)
    monkeypatch.setattr(hm, "write_mode", lambda *a, **k: None)
    monkeypatch.setattr(hm.subprocess, "run", lambda argv, **kw: type("R", (), {"returncode": 0, "stdout": "inactive", "stderr": ""})())
    thawed = []
    monkeypatch.setattr(hm, "thaw_unit", lambda name: thawed.append(name) or "start")
    rc = hm.cmd_restore("normal")
    assert rc == 0
    assert "ops" in thawed
    assert not hold.exists()


def test_cmd_restore_comfy_lingers_still_wakes_crissa(tmp_path, monkeypatch):
    import json
    modef, statef, hold, woke = _stub_restore_side_effects(monkeypatch, tmp_path)
    modef.write_text("render\n")
    hold.write_text("# card=occupancy\nhermes-crissa-gateway.service\n")
    statef.write_text(json.dumps({"mode": "render", "parked": ["ops"]}))
    monkeypatch.setattr(hm, "stop_comfy", lambda: False)
    rc = hm.cmd_restore("normal")
    assert rc == 1
    assert woke == ["wake"]


def test_park_plan_does_not_credit_a_parked_ops():
    legacy = hm.park_plan(22.0, 17.0, True)
    assert legacy["ok"] and legacy["park"] == ["ops"]                  # callers that do not probe: old credit
    plan = hm.park_plan(22.0, 17.0, True, ops_up=False, comfy_resident=True)
    assert plan["ok"] is False and plan["park"] == [] and "Comfy resident" in plan["reason"]
    plan = hm.park_plan(22.0, 17.0, False, ops_up=False, crew_up=True)
    assert plan["ok"] is True and plan["park"] == ["crew"]
    plan = hm.park_plan(22.0, 17.0, False, ops_up=False, crew_up=False)
    assert plan["ok"] is False and plan["park"] == []
    src = inspect.getsource(hm.cmd_render)
    assert "ops_up=ops_up" in src and src.index("park_plan(") < src.index("park_unit(")



def test_render_hold_blocks_entering_render_not_leaving(tmp_path, monkeypatch):
    f = tmp_path / "krew-render-hold"
    f.write_text("hold\nFable morning\n")
    monkeypatch.setattr(hm, "RENDER_HOLD_FILE", f)
    assert hm.render_hold_blocks("render") is True
    assert hm.render_hold_blocks("normal") is False
    assert "Fable morning" in hm.render_hold_reason()


def test_write_mode_noop_does_not_bounce_gateways(tmp_path, monkeypatch):
    hermes = tmp_path / "hermes"
    crissa = hermes / "profiles" / "crissa" / "config.yaml"
    crissa.parent.mkdir(parents=True)
    crissa.write_text(
        "model:\n  default: ops\n"
        "delegation:\n  max_concurrent_children: 2\n  max_spawn_depth: 1\n"
    )
    modef = tmp_path / "krew-gpu-mode"
    modef.write_text("normal\n")
    restarts: list[list[str]] = []
    monkeypatch.setattr(hm, "_FLAG_MOVED", False)
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "BRIDGE_CONFIG", hermes / "missing-bridge.yaml")
    monkeypatch.setattr(hm, "ADA_CONFIG", hermes / "profiles" / "ada" / "missing.yaml")
    monkeypatch.setattr(hm, "BEAST_OPS_PIN", tmp_path / "pin")
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "hold")
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: restarts.append(list(args)))
    hm.write_mode("normal")
    assert restarts == []                                              # nothing moved: no bounce
    modef.write_text("render\n")
    hm.write_mode("normal")
    assert ["restart", "hermes-crissa-gateway.service"] in restarts     # leftover leave: Crissa was slept
    assert ["restart", "hermes-ada-gateway.service"] not in restarts  # flag only — don't dirty-restart the dispatcher
    assert ["restart", "hermes-gateway.service"] not in restarts


def test_leftover_render_leave_does_not_bounce_ada(tmp_path, monkeypatch):
    """Janitor: render flag, no hold → krew-mode normal. Need ops/flag, not Ada."""
    hermes = tmp_path / "hermes"
    (hermes / "profiles" / "crissa").mkdir(parents=True)
    (hermes / "profiles" / "crissa" / "config.yaml").write_text(
        "model:\n  default: ops\n"
        "delegation:\n  max_concurrent_children: 2\n  max_spawn_depth: 1\n"
    )
    modef = tmp_path / "krew-gpu-mode"
    modef.write_text("render\n")
    hold = tmp_path / "no-hold"
    restarts: list[list[str]] = []
    monkeypatch.setattr(hm, "_FLAG_MOVED", False)
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.setattr(hm, "BRIDGE_CONFIG", hermes / "missing-bridge.yaml")
    monkeypatch.setattr(hm, "ADA_CONFIG", hermes / "profiles" / "ada" / "missing.yaml")
    monkeypatch.setattr(hm, "BEAST_OPS_PIN", tmp_path / "pin")
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: restarts.append(list(args)))
    hm.write_mode("normal")
    assert modef.read_text() == "normal\n"
    assert ["restart", "hermes-ada-gateway.service"] not in restarts
    assert ["restart", "hermes-gateway.service"] not in restarts
    assert ["restart", "hermes-crissa-gateway.service"] in restarts


def test_leave_render_with_hold_does_not_bounce_ada(tmp_path, monkeypatch):
    """Real leave (hold still there) must not dirty-restart Ada either."""
    hermes = tmp_path / "hermes"
    (hermes / "profiles" / "crissa").mkdir(parents=True)
    (hermes / "profiles" / "crissa" / "config.yaml").write_text(
        "model:\n  default: ops\n"
        "delegation:\n  max_concurrent_children: 2\n  max_spawn_depth: 1\n"
    )
    modef = tmp_path / "krew-gpu-mode"
    modef.write_text("render\n")
    hold = tmp_path / "hold"
    hold.write_text("# card=t_test\nops.service\n")
    restarts: list[list[str]] = []
    monkeypatch.setattr(hm, "_FLAG_MOVED", False)
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.setattr(hm, "BRIDGE_CONFIG", hermes / "missing-bridge.yaml")
    monkeypatch.setattr(hm, "ADA_CONFIG", hermes / "profiles" / "ada" / "missing.yaml")
    monkeypatch.setattr(hm, "BEAST_OPS_PIN", tmp_path / "pin")
    monkeypatch.setattr(hm, "_systemctl_user", lambda args: restarts.append(list(args)))
    hm.write_mode("normal")
    assert ["restart", "hermes-ada-gateway.service"] not in restarts
    assert ["restart", "hermes-gateway.service"] not in restarts
    assert ["restart", "hermes-crissa-gateway.service"] in restarts


def test_status_flags_a_sandbox_flag_that_disagrees(tmp_path, monkeypatch):
    house = tmp_path / "house"
    (house / "profiles" / "vera" / "home" / ".hermes").mkdir(parents=True)
    (house / "profiles" / "vera" / "home" / ".hermes" / "krew-gpu-mode").write_text("render\n")
    (house / "profiles" / "bash" / "home" / ".hermes").mkdir(parents=True)
    (house / "profiles" / "bash" / "home" / ".hermes" / "krew-gpu-mode").write_text("normal\n")
    monkeypatch.setattr(hm, "HOUSE", house)
    bad = hm.sandbox_flag_disagreements("normal")
    assert [(p.parts[-4], v) for p, v in bad] == [("vera", "render")]
    assert "RED" in inspect.getsource(hm.cmd_status)


@pytest.mark.skip(reason="retired: the krew-gpu-hold plugin was retired 09-27 (6d38b58d, moved to "
                         "hermes-packs/plugins-retired-20260927); krew-mode's own passwd-home rule is "
                         "test_krew_tree_ignores_a_sandboxed_home")
def test_plugin_mode_file_is_passwd_home_under_sandbox(tmp_path):
    import pwd
    import sys
    plug = ROOT / "hermes-packs" / "plugins" / "krew-gpu-hold" / "__init__.py"
    code = ("from importlib.machinery import SourceFileLoader as L; "
            "m=L('hgh', %r).load_module(); print(m._MODE_FILE); print(m._LOG)" % str(plug))
    env = dict(os.environ, HOME=str(tmp_path))
    for k in ("KREW_HERMES", "KREW_GPU_MODE_FILE", "KREW_GPU_HOLD_LOG"):
        env.pop(k, None)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-800:]
    want = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes"
    mode_line, log_line = out.stdout.strip().splitlines()[-2:]
    assert mode_line == str(want / "krew-gpu-mode") and log_line == str(want / "krew-gpu-hold.jsonl")
    assert str(tmp_path) not in out.stdout




def test_render_submit_prefers_the_live_worker_card_and_a_shorter_wall():
    src = (ROOT / "crew" / "scripts" / "render-submit.py").read_text()
    assert 'os.environ.get("HERMES_KANBAN_TASK")' in src and "a.card = live_card" in src
    assert 'KREW_RENDER_PARK_WALL_SEC", "420"' in src and "timeout=PARK_WALL_SEC" in src
    assert 'j.get("out_dir") == str(out_dir)' in src
    col = (ROOT / "crew" / "scripts" / "render-collect.py").read_text()
    assert 'in ("pending", "preparing")' in col


def _fake_render_studio(monkeypatch, seen, *, pinned=False):
    import sys
    import types

    fake = types.ModuleType("render_studio")
    fake.welcome_pin = lambda: seen.append("helm") or {"say": "Lila has the helm."}
    fake.leave_render = lambda: seen.append("leave")
    fake.panel_was_pinned = lambda: pinned
    monkeypatch.setitem(sys.modules, "render_studio", fake)


def test_render_lila_host_enter_is_hub_aware(monkeypatch):
    """the owner 2026-09-13: Lila takes Crissa's place on the glass. From the desk the
    dissolve plays and the curtain leave does not — crissa_intro → down played
    from `down` would be a hard cut."""
    calls = []
    monkeypatch.setattr(hm, "_post_json", lambda url, body, timeout=3.0: calls.append((url.rsplit("/v1/", 1)[1], body.get("key"))) or {"taken": True, "ok": True})
    monkeypatch.setattr(hm.time, "sleep", lambda s: None)
    monkeypatch.setattr(hm, "conductor_hub", lambda: "down")
    assert hm.render_host_enter() == ["lila-work"]
    assert ("play", "crissa-intro-leave") not in calls
    assert ("play", "lila-work") in calls
    calls.clear()
    monkeypatch.setattr(hm, "conductor_hub", lambda: "crissa_intro")
    assert hm.render_host_enter() == ["crissa-intro-leave", "lila-work"]
    calls.clear()
    monkeypatch.setattr(hm, "conductor_hub", lambda: "crissa_intro_hd")     # the HD curtain walks its own leave
    assert hm.render_host_enter() == ["crissa-intro-hd-leave", "lila-work"]
    calls.clear()
    monkeypatch.setattr(hm, "conductor_hub", lambda: "lila_listen")
    assert hm.render_host_enter() == ["lila-listen-leave", "lila-work"]


def test_render_welcome_puts_lila_up_and_opens_the_ear_not_the_panel(monkeypatch):
    seen, posts = [], []
    monkeypatch.setattr(hm, "RENDER_LILA_HOST", True)
    monkeypatch.setattr(hm, "wait_handoff_pid", lambda pid, cap=20.0: None)
    monkeypatch.setattr(hm, "render_host_enter", lambda: seen.append("enter") or [])
    monkeypatch.setattr(hm, "open_ear_session", lambda: seen.append("open"))
    monkeypatch.setattr(hm, "_post_json", lambda url, body, timeout=3.0: posts.append((url, body.get("action"))) or {})
    _fake_render_studio(monkeypatch, seen)
    hm.pin_render_welcome()
    assert seen == ["helm", "enter", "open"]
    # the owner 2026-09-16: "leave the user on Wall in render mode when the Krew are
    # the one switching … Navigate to CP to see the work" — nothing is pulled forward
    assert "raise_panel" not in [p[1] for p in posts]
    assert "kiosk_panel" not in [p[1] for p in posts]
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text()
    already = src.split('print(f"already render recipe=')[1].split("return 0")[0]
    assert "pin_render_welcome()" in already      # the already-render path enters too


def test_render_lila_host_off_keeps_the_panel_pin(monkeypatch):
    import json

    seen, posted = [], []
    monkeypatch.setattr(hm, "RENDER_LILA_HOST", False)
    monkeypatch.setattr(hm, "wait_handoff_pid", lambda pid, cap=20.0: None)
    monkeypatch.setattr(hm, "render_host_enter", lambda: (_ for _ in ()).throw(AssertionError("enter")))

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=0):
        posted.append(json.loads(req.data.decode()).get("action"))
        return _Resp()

    monkeypatch.setattr(hm.urllib.request, "urlopen", _urlopen)
    _fake_render_studio(monkeypatch, seen)
    hm.pin_render_welcome()
    assert posted == ["kiosk_panel"]


def test_leaving_render_sends_lila_home_and_skips_kiosk_wall_without_a_panel(monkeypatch):
    seen = []
    monkeypatch.setattr(hm, "RENDER_LILA_HOST", True)
    monkeypatch.setattr(hm, "conductor_hub", lambda: "lila_listen")
    monkeypatch.setattr(hm, "lila_on_glass", lambda: True)
    monkeypatch.setattr(hm, "render_host_leave", lambda: seen.append("home"))
    monkeypatch.setattr(hm.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kiosk_wall posted")))
    _fake_render_studio(monkeypatch, seen, pinned=False)
    hm.pin_kiosk_wall()
    assert seen == ["leave"]
    assert "home" not in seen  # home is last, after wake_render_rest


class _Ran:
    def __init__(self, out=""):
        self.stdout, self.stderr, self.returncode = out, "", 0


def test_each_unit_names_its_owner_and_a_card_release_frees_only_its_own(tmp_path, monkeypatch):
    """the owner + Grok 2026-09-14: "Tie each unit to its owner … the file format is the law"."""
    hold = tmp_path / "hold"
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    hm.hold_units("ops.service", "hermes-crissa-gateway.service", card="occupancy")
    hm.hold_units("hermes-ada-gateway.service")                          # no card: a human claim
    hm.hold_units("crew.service", "ops.service", card="t_render")
    assert set(hm._hold_cards()) == {"occupancy", "t_render"}
    assert hm._hold_owners()["ops.service"] == ["occupancy", "t_render"]
    assert hm.release_card("t_render") == ["crew.service"]               # ops is still the Mode button's
    assert set(hm._hold_list()) == {"ops.service", "hermes-crissa-gateway.service", "hermes-ada-gateway.service"}
    assert hm.release_card("occupancy") == ["ops.service", "hermes-crissa-gateway.service"]
    assert hm._hold_list() == ["hermes-ada-gateway.service"] and hm._hold_owners()["hermes-ada-gateway.service"] == ["human"]
    assert hm.release_card("t_nobody") == [] and hold.exists()
    lines = hold.read_text().splitlines()
    assert "hermes-ada-gateway.service" in lines                         # start-ops/start-crew match whole lines


def test_the_1248_release_cannot_take_the_mode_buttons_units(tmp_path, monkeypatch):
    """2026-09-14 12:48:02: releasing "init-wizard" unlinked the hold and started ops + Crissa mid-Render."""
    hold = tmp_path / "hold"
    hold.write_text("# card=occupancy\n# card=init-wizard\n# unit ops.service card=occupancy\n"
                    "# unit hermes-crissa-gateway.service card=occupancy\n# unit hermes-ada-gateway.service card=init-wizard\n"
                    "ops.service\nhermes-crissa-gateway.service\nhermes-ada-gateway.service\n")
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    assert hm.release_card("init-wizard") == ["hermes-ada-gateway.service"]
    assert set(hm._hold_list()) == {"ops.service", "hermes-crissa-gateway.service"} and hm._hold_cards() == ["occupancy"]


def test_a_legacy_hold_keeps_its_old_meaning(tmp_path, monkeypatch):
    hold = tmp_path / "hold"
    hold.write_text("# card=t_a\n# card=t_b\nops.service\n")
    monkeypatch.setattr(hm, "CREW_HOLD", hold)
    assert hm.release_card("t_a") == [] and hm._hold_list() == ["ops.service"]
    assert hm.release_card("t_b") == ["ops.service"] and not hold.exists()
    hold.write_text("ops.service\nrail-presence.service\n")               # unstamped: a human's
    assert hm._hold_owners() == {"ops.service": ["human"], "rail-presence.service": ["human"]}


def test_a_card_no_board_answers_for_keeps_its_hold(monkeypatch):
    monkeypatch.setattr(hm, "card_worker_pid", lambda c: None)
    for st in (None, "", "unknown"):
        monkeypatch.setattr(hm, "card_status", lambda c, st=st: st)
        live, why = hm.hold_card_live("t_gone", pending=set(), mode="normal", comfy=False)
        assert live is True and "unknown" in why
    monkeypatch.setattr(hm, "card_status", lambda c: "done")
    assert hm.hold_card_live("t_done", pending=set(), mode="normal", comfy=False)[0] is False


def test_nothing_freed_starts_ops_or_crissa_while_the_flag_says_render(tmp_path, monkeypatch):
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "none")
    monkeypatch.setattr(hm, "read_mode", lambda text=None: "render")
    monkeypatch.setattr(hm, "_hold_log", lambda e: None)
    monkeypatch.setattr(hm, "_comfy_idle_or_stop", lambda: True)
    calls = []
    monkeypatch.setattr(hm.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _Ran("inactive\n"))
    hm._start_freed(["ops.service", "hermes-crissa-gateway.service", "crew.service"], by="test")
    assert [c[-1] for c in calls if c[:3] == ["systemctl", "--user", "start"]] == ["crew.service"]


# ── the way back bounces Ada when her caps are stale (2026-09-19) ─────────
def _ada_caps_house(tmp_path, monkeypatch, *, disk="3/2", record=None, start="Fri 2026-09-18 20:11:08 EDT",
                    mode="render", hold=False):
    hermes = tmp_path / "hermes"
    (hermes / "profiles" / "crissa").mkdir(parents=True)
    (hermes / "profiles" / "crissa" / "config.yaml").write_text(
        "model:\n  default: ops\n"
        "delegation:\n  max_concurrent_children: 2\n  max_spawn_depth: 1\n"
    )
    ada = hermes / "profiles" / "ada" / "config.yaml"
    ada.parent.mkdir(parents=True)
    t, per = disk.split("/")
    ada.write_text(f"kanban:\n  max_in_progress: {t}\n  max_in_progress_per_profile: {per}\n")
    modef = tmp_path / "krew-gpu-mode"
    modef.write_text(mode + "\n")
    holdf = tmp_path / ("hold" if hold else "no-hold")
    if hold:
        holdf.write_text("# card=t_test\nops.service\n")
    restarts: list[list[str]] = []
    monkeypatch.setattr(hm, "_FLAG_MOVED", False)
    monkeypatch.setattr(hm, "MODE_FILE", modef)
    monkeypatch.setattr(hm, "HOUSE", hermes)
    monkeypatch.setattr(hm, "HERMES", hermes)
    monkeypatch.setattr(hm, "CREW_HOLD", holdf)
    monkeypatch.setattr(hm, "BRIDGE_CONFIG", hermes / "missing-bridge.yaml")
    monkeypatch.setattr(hm, "ADA_CONFIG", ada)
    monkeypatch.setattr(hm, "ADA_CAPS_LIVE", hermes / "ada-dispatch-live.json")
    monkeypatch.setattr(hm, "ADA_CAPS_PENDING", hermes / "ada-caps-pending")
    monkeypatch.setattr(hm, "ADA_CAPS_BOUNCED", hermes / "ada-caps-bounced")
    monkeypatch.setattr(hm, "ADA_CAPS_BOUNCE_ON", True)
    monkeypatch.setattr(hm, "BEAST_OPS_PIN", tmp_path / "pin")
    monkeypatch.setattr(hm, "_ada_gateway_start", lambda: (start, 1789776668.0))
    monkeypatch.setattr(hm, "lane_brain", lambda mode=None: "crew")
    monkeypatch.setattr(hm, "_hold_log", lambda rec: None)
    # These cases are the units-topology contract: the dispatcher unit is still Ada's.
    # The host-gateway contract is test_render_enter_and_exit_write_and_bounce_the_host_gateway.
    monkeypatch.setattr(hm, "_gateway_topology", lambda: None)
    monkeypatch.setattr(hm, "_systemctl_user", lambda args, timeout=None: restarts.append(list(args)))
    if record is not None:
        import json as _json
        (hermes / "ada-dispatch-live.json").write_text(_json.dumps(record))
    return hermes, modef, restarts


def test_leave_render_bounces_ada_when_her_caps_are_stale(tmp_path, monkeypatch):
    """2026-09-18: a 3-min Render restarted her at (1, 1), the way back wrote
    (3, 2) and bounced nobody — one worker wide all night. The gate's record
    says what she loaded; disk says (3, 2); the leave restarts her."""
    hermes, modef, restarts = _ada_caps_house(
        tmp_path, monkeypatch, disk="3/2",
        record={"gateway_start": "Fri 2026-09-18 20:11:08 EDT", "caps": [1, 1]})
    hm.write_mode("normal")
    assert modef.read_text() == "normal\n"
    assert ["restart", "hermes-ada-gateway.service"] in restarts
    assert (hermes / "ada-caps-bounced").read_text().strip() == "Fri 2026-09-18 20:11:08 EDT"


def test_leave_render_leaves_ada_alone_when_caps_match(tmp_path, monkeypatch):
    hermes, modef, restarts = _ada_caps_house(
        tmp_path, monkeypatch, disk="3/2",
        record={"gateway_start": "Fri 2026-09-18 20:11:08 EDT", "caps": [3, 2]})
    hm.write_mode("normal")
    assert ["restart", "hermes-ada-gateway.service"] not in restarts
    assert ["restart", "hermes-crissa-gateway.service"] in restarts      # the leave still wakes Crissa


def test_stale_caps_bounce_once_per_gateway_start(tmp_path, monkeypatch):
    hermes, modef, restarts = _ada_caps_house(
        tmp_path, monkeypatch, disk="3/2", mode="normal",
        record={"gateway_start": "Fri 2026-09-18 20:11:08 EDT", "caps": [1, 1]})
    assert hm.maybe_bounce_ada_for_caps("normal", by="test") is True
    assert hm.maybe_bounce_ada_for_caps("normal", by="test") is False
    assert restarts.count(["restart", "hermes-ada-gateway.service"]) == 1


def test_stale_caps_by_mtime_when_the_gate_has_no_record(tmp_path, monkeypatch):
    """No gate record for this start: a config written after she booted is a
    cap she never read (hermes reads kanban.max_in_progress once at boot)."""
    import os
    hermes, modef, restarts = _ada_caps_house(tmp_path, monkeypatch, disk="3/2", mode="normal")
    ada = hermes / "profiles" / "ada" / "config.yaml"
    os.utime(ada, (1789776668.0 + 240, 1789776668.0 + 240))          # 4 min after the start
    stale, why = hm.ada_caps_stale()
    assert stale and "rewritten" in why
    os.utime(ada, (1789776668.0 - 600, 1789776668.0 - 600))          # written before she started
    stale, why = hm.ada_caps_stale()
    assert not stale


def test_hold_sweep_bounces_stale_ada_as_the_fallback(tmp_path, monkeypatch):
    """The 2-min sweep is the way back that actually runs; it catches a leave
    that went through any other door."""
    hermes, modef, restarts = _ada_caps_house(
        tmp_path, monkeypatch, disk="3/2", mode="normal",
        record={"gateway_start": "Fri 2026-09-18 20:11:08 EDT", "caps": [1, 1]})
    monkeypatch.setattr(hm, "_render_pending_cards", lambda dry_run=False: [])
    monkeypatch.setattr(hm, "sweep_comfy_leak", lambda **k: False)
    monkeypatch.setattr(hm, "_hold_cards", lambda: [])
    monkeypatch.setattr(hm, "way_back_units", lambda **k: [])
    assert hm.cmd_hold_sweep() == 0
    assert ["restart", "hermes-ada-gateway.service"] in restarts


def test_stale_caps_bounce_kill_switch_and_hold(tmp_path, monkeypatch):
    hermes, modef, restarts = _ada_caps_house(
        tmp_path, monkeypatch, disk="3/2", mode="normal", hold=True,
        record={"gateway_start": "Fri 2026-09-18 20:11:08 EDT", "caps": [1, 1]})
    monkeypatch.setattr(hm, "_hold_list", lambda: ["hermes-ada-gateway.service"])
    assert hm.maybe_bounce_ada_for_caps("normal") is False             # held: never
    monkeypatch.setattr(hm, "_hold_list", lambda: [])
    monkeypatch.setattr(hm, "ADA_CAPS_BOUNCE_ON", False)
    assert hm.maybe_bounce_ada_for_caps("normal") is False             # kill switch
    assert hm.maybe_bounce_ada_for_caps("render") is False             # never entering Render
    assert restarts == []


def test_leaving_render_under_a_live_turbo_lease_writes_turbo_caps(tmp_path, monkeypatch):
    """2026-09-19 04:40: render-collect's `krew-mode normal` wrote 3/2 while
    TurboKrew had four hours left; Ada came back 3/2 under a 6/3 lease."""
    class _Lease:
        def __init__(self, live): self.live = live
        def lease_live(self, house=None): return self.live
    monkeypatch.setattr(hm, "_turbo_krew_mod", lambda: _Lease(True))
    assert hm.ada_caps_for_mode("normal") == hm.ADA_DISPATCH["turbo"] == (6, 3)
    assert hm.ada_caps_for_mode("render") is None
    monkeypatch.setattr(hm, "_turbo_krew_mod", lambda: _Lease(False))
    assert hm.ada_caps_for_mode("normal") is None                    # the plain Standard row


def test_render_enter_and_exit_write_and_bounce_the_host_gateway(tmp_path, monkeypatch):
    """Render caps are written to the file the shared gateway reads, and both the
    enter restart and the stale-caps way back restart hermes-gateway.service.

    The Ada profile file is not that file. A 6/3 left there must survive enter
    (3/1) and exit (3/2)."""
    hermes, modef, restarts = _ada_caps_house(
        tmp_path, monkeypatch, disk="6/3", mode="normal",
        record={"gateway_start": "Fri 2026-09-18 20:11:08 EDT", "caps": [3, 2]})
    host = hermes / "config.yaml"
    host.write_text("kanban:\n  max_in_progress: 3\n  max_in_progress_per_profile: 2\n")
    ada = hermes / "profiles" / "ada" / "config.yaml"
    ada.write_text("kanban:\n  max_in_progress: 6\n  max_in_progress_per_profile: 3\n")

    class _Host:
        HOST_UNIT = "hermes-gateway.service"

    monkeypatch.setenv("KREW_GATEWAY_TOPOLOGY", "host")
    monkeypatch.setattr(hm, "_gateway_topology", lambda: _Host())
    monkeypatch.setattr(hm, "ADA_CONFIG", hm._dispatcher_config())
    assert hm.ADA_CONFIG == host
    reasons = []
    monkeypatch.setattr(hm, "drop_presence_to_guest", lambda reason: reasons.append(reason))

    hm.write_mode("render")
    host_text = host.read_text()
    assert "max_in_progress: 3\n" in host_text
    assert "max_in_progress_per_profile: 1\n" in host_text
    assert ada.read_text() == "kanban:\n  max_in_progress: 6\n  max_in_progress_per_profile: 3\n"
    assert ["restart", "hermes-gateway.service"] in restarts
    assert ["restart", "hermes-ada-gateway.service"] not in restarts
    assert reasons == ["occupancy:render"]
    assert modef.read_text() == "render\n"

    # The gate has seen the render boot (3/1). Leaving writes normal 3/2, which
    # the running process has not read, so the way back restarts the same unit.
    (hermes / "ada-dispatch-live.json").write_text(
        '{"gateway_start": "Fri 2026-09-18 20:11:08 EDT", "caps": [3, 1]}')
    restarts.clear()
    hm.write_mode("normal")
    host_text = host.read_text()
    assert "max_in_progress: 3\n" in host_text
    assert "max_in_progress_per_profile: 2\n" in host_text
    assert ada.read_text().split("max_in_progress: ")[1].startswith("6")
    assert ["restart", "hermes-gateway.service"] in restarts
    assert ["restart", "hermes-ada-gateway.service"] not in restarts
    assert reasons == ["occupancy:render", "occupancy:normal"]


def test_turbo_lease_read_failure_falls_back_to_standard(monkeypatch):
    def boom():
        raise RuntimeError("turbo krew missing")
    monkeypatch.setattr(hm, "_turbo_krew_mod", boom)
    assert hm.ada_caps_for_mode("normal") is None



def _fake_proc(tmp_path, procs):
    root = tmp_path / "proc"
    for pid, argv in procs.items():
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (root / "self").mkdir(parents=True)                    # non-numeric: skipped
    return root


def test_profile_worker_pids_never_match_the_gateway(tmp_path):
    """2026-09-19: `pkill -f "hermes -p steward "` SIGTERMed Steward's gateway
    on every Render entry (12 exit-1 + auto-restarts in one day)."""
    py = "~/.hermes/hermes-agent/venv/bin/python"
    h = "~/.hermes/hermes-agent/hermes"
    root = _fake_proc(tmp_path, {
        101: [py, h, "-p", "steward", "gateway", "run", "--replace"],     # the gateway: never
        102: [py, h, "-p", "steward", "--cli", "--accept-hooks", "-q", "t_x"],  # a worker: yes
        103: [py, h, "-p", "stewardess", "--cli"],                        # a longer name: no
        104: [py, h, "-p", "crissa", "--cli"],                              # another profile: no
        105: ["/usr/bin/bash", "-c", "grep 'hermes -p steward --cli'"],  # a shell mentioning it: no
    })
    assert hm.profile_worker_pids("steward", proc=root) == [102]
    assert hm.profile_worker_pids("crissa", proc=root) == [104]


def test_render_sleep_kills_workers_not_gateways(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(hm, "profile_worker_pids", lambda name, **k: {"steward": [7], "crissa": []}[name])
    monkeypatch.setattr(hm.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    hm.kill_render_sleep_workers()
    assert killed == [(7, hm.signal.SIGTERM)]


# The Janitor is krew's janitor/ (its own tests, tests/test_janitor_*.py) and the engine wait script is the reference install's; the mode switch's tests that drove them are
# not here: test_janitor_does_not_restart_gpu_occupancy, test_janitor_hold_parks_ops_probe_in_render, test_beast_does_not_stop_janitor, test_comfy_never_carries_a_cards_kanban_id


# Two more drove the Janitor's park tables (krew's janitor/ reads them from janitor.yaml gpu:, tested in tests/test_janitor_*.py)
# not here: test_janitor_render_sleeps_no_gateway_under_the_host, test_janitor_beast_parks_ops_cosy_not_red
