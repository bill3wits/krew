"""krew's tiles in the Crissa Control Panel: the add-on manifest, where it goes, and krew-install's step.

Crissa owns the one Control Panel, in the Wall (the owner 2026-10-06). krew adds only its few tiles to the panel's
bottom section through a manifest in the panel's add-on folder; without it the panel shows "Hermes krew plugin not
installed". Every test writes under tmp_path only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

TILES = ROOT / "panel" / "krew-panel-tiles.py"
HOOK = ROOT / "scripts" / "krew_install_panel_tiles.py"
ENV = ("CRISSA_HOME", "KREW_JANITOR_SNAPSHOT", "KREW_JANITOR_CONFIG")
UNIT = re.compile(r"^[A-Za-z0-9@._:-]{1,120}$")   # the panel's own unit check (lib/crissa_panel_addons.py)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for v in ENV:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def T():
    return _load("krewtest_panel_tiles", TILES)


def test_defaults_are_the_gateways_the_dashboard_crew_kanban_and_the_janitor(T):
    m = T.manifest({})
    assert m["schema"] == "crissa-panel-addon/v1" and m["id"] == "krew" and m["title"] == "Krew"
    t = [x for x in m["tiles"] if not x.get("section")]      # the "krew services" section comes after (2026-10-08)
    assert [x.get("unit") or x.get("match") for x in t] == [
        "hermes-gateway.service", "hermes-*-gateway.service", "hermes-dashboard.service",
        "krew-kanban-loop.timer", "krew-janitor.timer"]
    assert m["sections"] == ["krew services", "modes"]
    assert len(m["tiles"]) == 5 + len(T.KREW_LOOPS) + len(T.DEFAULTS["engines"]) + 1    # + the modes section
    assert t[0]["port"] == 8642 and t[2]["port"] == 9119 and t[2]["href"] == "http://127.0.0.1:9119/"
    assert "{unit}" in t[1]["label"]
    kh = Path(os.environ["KREW_HOME"])
    assert t[3]["status"] == str(kh / "run-status" / "krew-kanban-loop.service.json") and t[3]["stale_s"] == 3600
    assert "kanban" in t[3]["label"].lower()
    assert t[4]["status"] == str(kh / "janitor" / "metrics" / "janitor-latest.json")
    assert t[4]["stale_s"] == 900
    for x in t:
        if x.get("unit"):
            assert UNIT.match(x["unit"]), x


def test_janitor_report_follows_janitor_yaml_and_the_environment(T, tmp_path, monkeypatch):
    (Path(os.environ["KREW_HOME"]) / "janitor.yaml").write_text(f"paths:\n  metrics_dir: {tmp_path}/m\n")
    assert T.manifest({})["tiles"][4]["status"] == f"{tmp_path}/m/janitor-latest.json"
    monkeypatch.setenv("KREW_JANITOR_SNAPSHOT", "/x/snap.json")
    assert T.manifest({})["tiles"][4]["status"] == "/x/snap.json"
    assert T.manifest({"janitor_report": "/y/j.json"})["tiles"][4]["status"] == "/y/j.json"


def test_kanban_tile_settings(T, tmp_path, monkeypatch):
    t = T.manifest({"kanban": "my-loop.timer", "kanban_stale_s": 120})["tiles"][3]
    assert t["unit"] == "my-loop.timer" and t["stale_s"] == 120
    assert t["status"] == str(Path(os.environ["KREW_HOME"]) / "run-status" / "my-loop.service.json")
    assert T.manifest({"kanban_status": "/r/k.json"})["tiles"][3]["status"] == "/r/k.json"
    monkeypatch.setenv("KREW_HOME", str(tmp_path / "kh2"))
    assert T.manifest({})["tiles"][3]["status"] == str(tmp_path / "kh2" / "run-status" / "krew-kanban-loop.service.json")
    assert [x.get("unit") for x in T.manifest({"kanban": ""})["tiles"] if not x.get("section")][-1] == "krew-janitor.timer"


# The panel's add-on contract (crissa-cp-wiring-20261006): a tile is unit | match | href | status (+ unit). A unit
# tile pages its journal backward with a cut marker, restarts with a post-check and names a failure; a `.timer` unit's
# key is "Run now" (one run of the service, failure named) and its tape is timer + service; a status tile's unit is
# discovered and allowed, so a click opens that tape and that key. These pin what krew hands the panel for that.
KINDS = ("unit", "match", "href", "status")


def test_every_tile_is_one_the_panel_reads(T):
    for x in T.manifest({})["tiles"]:
        assert any(x.get(k) for k in KINDS) and isinstance(x.get("label"), str) and x["label"], x
        assert not set(x) - {"unit", "match", "href", "status", "stale_s", "label", "port", "scope",
                             "id", "section", "run", "off"}, x        # the last four: docs/PANEL-CONTRACT.md (v1 ignores them)
        if x.get("section") == "krew services":
            assert x["run"] == {"unit": x["unit"]} and set(x["off"]) in ({"file"}, {"unit"}), x
        elif x.get("section") == "modes":
            assert x.get("unit") and x["id"].startswith(("krew-engine-", "krew-comfy")), x
        if "status" in x:
            assert os.path.isabs(x["status"]) and isinstance(x["stale_s"], int) and x["stale_s"] > 0, x


def test_crew_kanban_and_the_janitor_get_run_now_logs_and_a_named_failure(T):
    t = {x.get("unit"): x for x in T.manifest({})["tiles"]}
    for unit in ("krew-kanban-loop.timer", "krew-janitor.timer"):
        x = t[unit]
        assert unit.endswith(".timer") and x.get("status"), x   # the panel labels a .timer's key Run now
        assert (ROOT / "crew" / "units" / unit).is_file() or (ROOT / "systemd" / "user" / unit).is_file(), unit
    # the loop's service writes the very report the tile reads: how the run ended, with a named reason
    svc = (ROOT / "crew" / "units" / "krew-kanban-loop.service").read_text()
    timer = (ROOT / "crew" / "units" / "krew-kanban-loop.timer").read_text()
    assert "krew-kanban-loop.service" in timer or "Unit=" not in timer
    post = [ln for ln in svc.splitlines() if ln.startswith("ExecStopPost=")]
    assert len(post) == 1 and post[0].startswith("ExecStopPost=-")       # a status write never fails the loop
    assert "krew-run-status.py %n --home %h/.krew" in post[0] and "kanban-loop.off" in post[0]


def test_tiles_can_be_switched_off_and_extras_added(T):
    m = T.manifest({"gateway": "", "profile_gateways": "", "dashboard": "", "kanban": "", "janitor": False,
                    "krew_section": False, "services": False, "modes": False, "extra": [
        {"unit": "searxng.service", "scope": "system", "label": "Search", "port": 8080},
        {"href": "http://127.0.0.1:8898/", "label": "List"},
        {"label": "no unit, no link: dropped"},
        "not a mapping"]})
    assert m["tiles"] == [{"unit": "searxng.service", "scope": "system", "label": "Search", "port": 8080},
                          {"href": "http://127.0.0.1:8898/", "label": "List"}]


def test_settings_come_from_panel_tiles_in_krew_yaml(T):
    (Path(os.environ["KREW_HOME"]) / "krew.yaml").write_text(
        "panel:\n  tiles:\n    title: Crew\n    dashboard_url: http://127.0.0.1:1/d\n    gateway_port: 18642\n")
    m = T.manifest()
    assert m["title"] == "Crew" and m["tiles"][0]["port"] == 18642 and m["tiles"][2]["href"] == "http://127.0.0.1:1/d"


def test_target_dir_order(T, tmp_path):
    d, why = T.target_dir({}, env={})
    assert d is None and "no Crissa Control Panel found" in why
    (tmp_path / "home" / ".crissa").mkdir(parents=True)
    assert T.target_dir({}, env={})[0] == tmp_path / "home" / ".crissa" / "panel.d"
    assert T.target_dir({}, env={"CRISSA_HOME": str(tmp_path / "z")})[0] == tmp_path / "z" / "panel.d"
    assert T.target_dir({"dir": str(tmp_path / "pd")}, env={"CRISSA_HOME": str(tmp_path / "z")}) == (
        tmp_path / "pd", "panel.tiles.dir in krew.yaml")


def test_write_then_remove(T, tmp_path):
    cfg = {"dir": str(tmp_path / "pd")}
    r = T.write(cfg)
    f = tmp_path / "pd" / "krew.json"
    assert r["ok"] and r["written"] == str(f) and r["tiles"] == 5 + len(T.KREW_LOOPS) + len(T.DEFAULTS["engines"]) + 1
    assert json.loads(f.read_text()) == T.manifest(cfg) and not list((tmp_path / "pd").glob("*.tmp"))
    assert T.remove(cfg) == {"ok": True, "removed": str(f)} and not f.exists()
    assert T.remove(cfg)["removed"] is None
    assert T.write({}, env={})["ok"] is False   # no panel found: nothing written anywhere


def test_cli_show_writes_nothing(tmp_path):
    env = {**os.environ, "CRISSA_HOME": str(tmp_path / "z")}
    r = subprocess.run([sys.executable, str(TILES), "show"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["manifest"]["id"] == "krew" and out["dir"] == str(tmp_path / "z" / "panel.d")
    assert not (tmp_path / "z").exists()


def test_settings_copies_are_identical():
    ref = (ROOT / "plugins" / "krew-board" / "krew_settings.py").read_bytes()
    for p in (ROOT / "panel" / "krew_settings.py", ROOT / "plugins" / "krew-panel" / "krew_settings.py"):
        assert p.read_bytes() == ref, p


def run_install(tmp_path, *extra, crissa=True):
    hh, kh = tmp_path / "hermes-home", tmp_path / "krew-home"
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    env.pop("CRISSA_HOME", None)
    if crissa:
        (tmp_path / "crissa").mkdir(exist_ok=True)
        env["CRISSA_HOME"] = str(tmp_path / "crissa")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-install.py"), "--hermes-home", str(hh),
                        "--krew-home", str(kh), "--no-rails", *extra], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r, hh, kh


def test_install_places_the_tools_and_writes_the_tiles(tmp_path):
    r, hh, kh = run_install(tmp_path)
    f = tmp_path / "crissa" / "panel.d" / "krew.json"
    m = json.loads(f.read_text())
    assert m["id"] == "krew" and m["tiles"][4]["status"] == str(kh / "janitor" / "metrics" / "janitor-latest.json")
    assert m["tiles"][3]["status"] == str(kh / "run-status" / "krew-kanban-loop.service.json")
    assert os.access(kh / "panel" / "krew-panel-tiles.py", os.X_OK) and (kh / "panel" / "krew_settings.py").is_file()
    assert (hh / "plugins" / "krew-panel" / "plugin.yaml").is_file() and (hh / "skills" / "krew-panel" / "SKILL.md").is_file()
    assert "Control Panel's bottom section" in r.stdout and "krew-panel-tiles.py remove" in r.stdout
    assert not (kh / "panel" / "crissa-decide-list.py").exists()


def test_install_without_a_panel_writes_nothing_and_says_how(tmp_path):
    r, _, kh = run_install(tmp_path, crissa=False)
    assert "no Crissa Control Panel found" in r.stdout and f"{kh}/panel/krew-panel-tiles.py write" in r.stdout
    assert not list(tmp_path.rglob("krew.json"))


def test_install_dry_run_writes_nothing(tmp_path):
    r, _, _ = run_install(tmp_path, "--dry-run")
    assert "crissa/panel.d/krew.json" in r.stdout and not (tmp_path / "crissa" / "panel.d").exists()
