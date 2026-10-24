"""Lifted from the reference install's test file 2026-10-10 (crew/scripts/zoey-hands.py).
No computer_use click on the owner's desktop (the owner 2026-10-02): until her own Bot Desktop
publishes an env, computer_use is out of every hands kit. 10-01 01:53 a foreground click landed on :0."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys  # noqa: E402
sys.path.insert(0, str(ROOT / "crew" / "lib"))      # krew_yaml: the script runs under krew-crew-run, which puts crew/lib on PYTHONPATH
SPEC = importlib.util.spec_from_file_location("zoey_hands_cua", ROOT / "crew" / "scripts" / "zoey-hands.py")
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("platform_toolsets:\n  cli: [file, browser, computer_use, team, hands]\n")
    return p


def test_no_private_screen_names_the_kit_without_computer_use(tmp_path, monkeypatch):
    monkeypatch.delenv("KREW_HANDS_CUA_GUARD", raising=False)
    env = tmp_path / "missing-env"
    assert h.guard_cua(None, config=cfg(tmp_path), env_file=env) == "file,browser,team"
    assert h.guard_cua("team,computer_use,web", env_file=env) == "team,web"


def test_a_private_display_lets_it_through(tmp_path):
    env = tmp_path / "env"
    env.write_text("DISPLAY=:21\nXAUTHORITY=/x\n")
    assert h.guard_cua(None, config=cfg(tmp_path), env_file=env) is None
    assert h.guard_cua("team,computer_use", env_file=env) == "team,computer_use"


def test_the_users_display_is_never_a_private_screen(tmp_path):
    env = tmp_path / "env"
    env.write_text("export DISPLAY=:0\n")
    assert not h.private_screen_ready(env)
    assert h.guard_cua("computer_use,team", env_file=env) == "team"


def test_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_HANDS_CUA_GUARD", "0")
    assert h.guard_cua(None, env_file=tmp_path / "none") is None


# dropped: test_a_cua_child_sees_her_screen_only (needs the screen env builder zoey_screen, which krew does not ship yet)


def test_a_child_without_cua_keeps_its_env(tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("DISPLAY=:21\n")
    base = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}
    assert h.pin_screen(dict(base), "team", env_file=env_file) == base
    assert h.pin_screen(dict(base), "team,computer_use", env_file=tmp_path / "missing") == base


# dropped: test_a_cua_child_gets_her_own_browser (needs the screen env builder zoey_screen, which krew does not ship yet)


# dropped: test_his_wayland_is_unreachable_not_merely_unnamed (needs the screen env builder zoey_screen, which krew does not ship yet)


def test_her_screen_routes_to_the_full_kit_only_with_the_switch(monkeypatch):
    ask = "On your own screen, list the windows that are open."
    monkeypatch.setenv("KREW_HANDS_CUA_ROUTE", "0")
    assert h.kit_for(ask, lean=True) == h.LEAN_TOOLSETS          # switched off: lean kit, no computer_use
    monkeypatch.setenv("KREW_HANDS_CUA_ROUTE", "1")
    assert h.kit_for(ask, lean=True) is None                     # the whole kit (the CUA guard still applies)
    assert h.kit_for("How many cards are open on the board?", lean=True) == h.LEAN_TOOLSETS
