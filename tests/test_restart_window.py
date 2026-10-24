"""The restart window in krew: OFF until restart-window.json says enabled, every unit list read from that file
(empty by default), the reference install's values kept only as an example, reboot intent under $KREW_HOME, and
fleet-bootcheck quiet without a config. Nothing here calls systemctl or restarts anything."""
from __future__ import annotations

import datetime as dt
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "window" / "lib"))
EXAMPLE = json.loads((ROOT / "config" / "restart-window.example.json").read_text(encoding="utf-8"))


@pytest.fixture
def kh(tmp_path, monkeypatch):
    home = tmp_path / ".krew"
    home.mkdir()
    monkeypatch.setenv("KREW_HOME", str(home))
    monkeypatch.delenv("KREW_CONFIG", raising=False)
    monkeypatch.delenv("KREW_RESTART_WINDOW_SETTINGS", raising=False)
    monkeypatch.delenv("KREW_RESTART_WINDOW", raising=False)
    return home


def _rw():
    import krew_window_settings
    importlib.reload(krew_window_settings)
    import restart_window
    return importlib.reload(restart_window)


def _script(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "window" / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_no_settings_means_off_and_empty(kh):
    rw = _rw()
    assert not rw.enabled()
    assert rw.TIER_OF == {} and not (rw.CREW_UNITS or rw.ZOEY_UNITS or rw.API_UNITS or rw.HERMES_UNITS)
    assert rw.ear_health() is None and rw.EAR_UNIT == ""
    assert str(rw.STATE_PATH).startswith(str(kh)) and str(rw.CONFIG_OWED).startswith(str(kh))
    assert "not set" in rw.api_runs()["error"]
    # the Hermes stack check is wired to krew's own hermes-tree.py and patch queue (tests/test_window_cuts.py runs it)
    assert rw.HERMES_TREE_SCRIPT == ROOT / "crew" / "scripts" / "hermes-tree.py" and rw.HERMES_TREE_SCRIPT.is_file()
    assert rw.HERMES_PATCH_QUEUE == ROOT / "patches"


def test_off_tick_says_so_and_files_nothing(kh, capsys):
    _rw()
    win = _script("krew-restart-window")
    assert win.tick() == 0
    log = (kh / "logs" / "restart-window.log").read_text()
    assert "restart window is OFF" in log and "restart-window.json" in log
    assert not (kh / "state" / "restart-window.json").exists()


def test_the_lists_come_from_the_settings_file(kh):
    (kh / "restart-window.json").write_text(json.dumps({
        "enabled": True, "tiers": {"a.service": 2, "door.service": 5}, "front_door_units": ["door.service"],
        "crew_units": ["crew.service"], "rulers": ["zoey", "me"], "hands_words": ["me"],
        "busy": {"hands_procs": ["hands.py"]}}))
    rw = _rw()
    assert rw.enabled() and rw.TIER_OF == {"a.service": 2, "door.service": 5}
    assert rw.ZOEY_UNITS == {"door.service"} and rw.CREW_UNITS == {"crew.service"} and rw.RULERS == {"zoey", "me"}
    assert rw.parse_ruling("me")[0] == "hands"
    (kh / "restart-window.off").write_text("")
    assert not rw.enabled()


def test_a_bad_settings_file_is_an_error_not_silently_empty(kh):
    (kh / "restart-window.json").write_text("{not json")
    with pytest.raises(ValueError):
        _rw()


def test_the_example_is_off_and_empty_and_the_reference_is_only_an_example(kh):
    assert EXAMPLE["enabled"] is False and EXAMPLE["tiers"] == {} and EXAMPLE["front_door_units"] == []
    ref = EXAMPLE["_reference_install"]
    assert ref["enabled"] is True and ref["tiers"] and ref["front_door_units"]
    (kh / "restart-window.json").write_text(json.dumps(EXAMPLE))
    rw = _rw()
    assert not rw.enabled() and rw.TIER_OF == {}, "_reference_install is never read"


def test_decide_holds_outside_off_hours(kh):
    rw = _rw()
    facts = {"hours": {"window": "22:00-06:00", "source": "init"}, "in_window": False, "window_key": "",
             "owed": [{"unit": "a.service", "tier": 2, "why": "x", "security": False}],
             "reboot": {"wanted": False, "pkgs": []}, "kanban": {"running": 0, "ready": 0},
             "occupancy": "", "occupancy_pending": "", "user_busy": False, "nightly_active": False}
    d = rw.decide({}, facts, None, dt.datetime(2026, 10, 6, 14, 0))
    assert d["action"] not in ("execute", "reboot", "file")


def test_reboot_intent_lives_under_krew_home(kh):
    st = _script("krew-reboot-stamp")
    assert str(st.STAMP_PATH).startswith(str(kh))
    st.stamp("kernel update")
    assert st.verify() == "intentional=kernel update"
    assert (kh / "metrics" / "reboot-intent.jsonl").is_file()
    import reboot_comment
    importlib.reload(reboot_comment)
    assert str(reboot_comment.AUDIT).startswith(str(kh))


def test_after_checks_read_krew_home_and_default_to_none(kh):
    rac = _script("restart-after-check")
    assert rac.CONFIG == kh / "restart-window-after.json" and str(rac.STATE).startswith(str(kh))
    after = json.loads((ROOT / "config" / "restart-window-after.example.json").read_text(encoding="utf-8"))
    assert after["units"] == {}


def test_fleet_bootcheck_without_config_or_breadcrumb_does_nothing(tmp_path):
    script = ROOT / "window" / "scripts" / "fleet-bootcheck"
    r = subprocess.run(["bash", str(script)], env={"PATH": "/usr/bin:/bin", "FLEET_BOOTCHECK_CONFIG": str(tmp_path / "none.json")},
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "no readable config" in r.stderr
    cfg = json.loads((ROOT / "config" / "fleet-bootcheck.example.json").read_text(encoding="utf-8"))
    cfg.update(logdir=str(tmp_path))
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    r = subprocess.run(["bash", str(script)], env={"PATH": "/usr/bin:/bin", "FLEET_BOOTCHECK_CONFIG": str(tmp_path / "c.json")},
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "ordinary boot" in r.stdout


def test_units_are_templates_and_fleet_is_a_system_unit_never_written(kh, tmp_path):
    svc = (ROOT / "units" / "krew-restart-window.service").read_text()
    assert "{python}" in svc and "{krew}" in svc and "krew-restart-window.py tick" in svc
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("krew_install", ROOT / "scripts" / "krew-install.py")
    ki = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ki)
    done = ki.write_units(tmp_path / "u", kh, "/usr/bin/python3", force=False, dry=False, say=lambda m: None)
    assert "krew-restart-window.timer" in done and "fleet-bootcheck.service" not in done
    assert not (tmp_path / "u" / "fleet-bootcheck.service").exists()
