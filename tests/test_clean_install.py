"""The clean install (the owner 2026-10-08): one services list every patrol reads, a config-driven keeper, the
guided init, opt-in loops, the panel's krew section, a commercial-OK render default, SOULs with the laws kept and
the house's own lines gone, the two optional patrols off by default."""
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

LIB = ROOT / "crew" / "lib"
EXAMPLE = ROOT / "config" / "services.example.json"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def S():
    return _load(LIB / "krew_services.py", "krewtest_services")


# ---- the one services list ------------------------------------------------------------------------------------
def test_the_example_is_the_reference_install_and_every_reader_derives_from_it(S):
    rows = S.load(EXAMPLE)
    names = {r["name"] for r in rows}
    assert {"ops", "crew", "voice", "zoey-gw", "dashboard", "ear-rail", "zoey-display", "face-shell"} <= names
    lanes = S.keeper_lanes(rows, "http://p.invalid")
    assert lanes["ops"] == ("http://127.0.0.1:8000/v1/models", "user", "ops.service")
    assert lanes["ear-rail"] == ("http://p.invalid/v1/ear?part=stt", None, None)      # observe through the player
    assert lanes["zoey-display"][1] is None                                           # never-restart = observe
    sets = S.keeper_sets(rows)
    assert {"ops.service", "crew.service", "voice.service"} <= sets["gpu"]
    assert sets["parked_in"]["beast"] == {"ops.service"} and "crew.service" in sets["parked_in"]["render"]
    assert {"ear-rail", "zoey-display", "face-shell", "presence", "camera"} <= set(sets["zoeyos_lanes"])
    j = S.janitor_lists(rows)
    assert "ops.service" in j["watch"] and "ops.service" in j["report_only"]           # a GPU row is never the janitor's to restart
    assert "zoey-display.service" in j["observe_only"] and j["probes_ports"]["ops"] == [8000, "/v1/models"]
    assert "ear-bridge.service" in j["zoeyos_units"]
    su = S.site_units(rows)
    assert su["health_units"]["ops"] == "ops.service" and "hermes-gateway.service" in su["steward_log_units"]
    rw = S.restart_window(rows)
    assert rw["tiers"]["hermes-gateway.service"] == 5 and "hermes-gateway.service" in rw["front_door_units"]
    assert rw["unit_ports"]["ops.service"] == 8000 and "zoey-display.service" in rw["wall_units"]
    bc = S.bootcheck_checks(rows)
    assert {"name": "ops", "kind": "user_unit", "unit": "ops.service", "url": "http://127.0.0.1:8000/v1/models",
            "tries": 24, "nap": 10} in bc
    assert all(c["name"] != "ear-rail" or c["kind"] != "user_unit" for c in bc)       # no unit, no local url: left out
    tiles = S.panel_tiles(rows)
    assert {"unit": "ops.service", "label": "Ops - the model behind the front door", "port": 8000} in tiles
    assert not any(t["unit"] == "face-shell.service" for t in tiles)                 # the Wall's rows are not krew's


def test_no_list_means_no_lanes_and_it_says_so(S, tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_SERVICES", str(tmp_path / "none.json"))
    assert S.load() == [] and not S.present()
    assert "krew-install init" in S.NO_LIST
    monkeypatch.setenv("KREW_MEMORY_ROOT", str(tmp_path / "mem"))
    k = _load(ROOT / "crew" / "scripts" / "krew-keeper.py", "krewtest_keeper_nolist")
    assert k.LANES == {} and k.GPU_LANES == set() and k.NO_SERVICES == S.NO_LIST
    assert k.VOICE_WARM_URL == "" and k.GATEWAY_WAKE_UNITS == ()


def test_an_unapproved_row_is_listed_never_acted_on(S, tmp_path):
    f = tmp_path / "s.json"
    S.write([{"name": "x", "unit": "x.service", "port": 1, "role": "watch", "approved": False},
             {"name": "y", "unit": "y.service", "port": 2, "role": "watch"}], f)
    assert [r["name"] for r in S.load(f)] == ["y"]
    assert [r["name"] for r in S.load(f, approved_only=False)] == ["x", "y"]
    assert json.loads(f.read_text())["schema"] == "krew-services/v1"


def test_discovery_reads_systemd_and_the_ports_and_approves_nothing(S, monkeypatch):
    ports = {8000: {"proc": "python3", "pid": 11}, 8642: {"proc": "python3", "pid": 12}, 22: {"proc": "sshd", "pid": 1},
             45123: {"proc": "python3", "pid": 99}}     # an ephemeral-range scratch socket: never a row
    table = {
        ("user", "ops.service"): "ExecStart=/opt/x/vllm serve\nDescription=ops\nFragmentPath=/home/u/.config/systemd/user/ops.service\nMainPID=11\n",
        ("user", "krew-keeper.service"): "ExecStart=/usr/bin/python3 /home/u/.krew/crew/scripts/krew-crew-run krew-keeper.py\nDescription=keeper\nFragmentPath=/x\n",
        ("user", "gvfs-daemon.service"): "ExecStart=/usr/libexec/gvfsd\nDescription=gvfs\nFragmentPath=/usr/lib/systemd/user/gvfs-daemon.service\n",
    }
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setattr(S.Path, "home", classmethod(lambda cls: Path("/home/u")))

    def run(argv, timeout=30.0):
        if "list-units" in argv:
            scope = "user" if "--user" in argv else "system"
            return "".join(f"{n} loaded active running x\n" for (s, n) in table if s == scope)
        if "show" in argv:
            return table.get(("user" if "--user" in argv else "system", argv[-1]), "")
        return ""
    rows = S.discover(run, ports, markers=("~/.krew", "~/.hermes"))
    by = {r["name"]: r for r in rows}
    assert "gvfs-daemon" not in by and by["ops"]["gpu"] and by["ops"]["port"] == 8000 and by["ops"]["kind"] == "engine"
    assert by["krew-keeper"]["kind"] == "loop" and not by["krew-keeper"]["approved"]
    assert "port-8642" in by and by["port-8642"]["unit"] == "" and "port-22" not in by
    assert "port-45123" not in by, "an ephemeral-range listener with no unit is not a service"
    assert S.ephemeral_range("/nonexistent") == (32768, 60999)
    assert all(not r["approved"] and r["role"] == "observe" for r in rows)


# ---- the keeper is config-driven -----------------------------------------------------------------------------
def test_the_keeper_builds_its_lanes_and_sets_from_the_list(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_SERVICES", str(EXAMPLE))
    monkeypatch.setenv("KREW_MEMORY_ROOT", str(tmp_path / "mem"))
    monkeypatch.setenv("ZOEYOS_PLAYER", "http://p.invalid")
    k = _load(ROOT / "crew" / "scripts" / "krew-keeper.py", "krewtest_keeper_cfg")
    assert k.LANES["crew"] == ("http://127.0.0.1:8005/v1/models", "user", "crew.service")
    assert k.GPU_LANES == {"ops.service", "crew.service", "voice.service"}
    assert k.BEAST_HELD_UNITS == {"ops.service"} and "hermes-gateway.service" in k.BEAST_NO_HEAL
    assert k.VOICE_WARM_URL == "http://127.0.0.1:7789/warm"
    assert k.GATEWAY_WAKE_BRAIN == "http://127.0.0.1:8005/v1/models"
    assert k.PRESENCE_URL == "http://p.invalid/v1/rail/presence/v1/presence"
    monkeypatch.setattr(k, "_train_holds_gpu", lambda u: False)
    monkeypatch.setattr(k, "_crew_is_sleeping", lambda: False)
    assert k.occupancy_parks_gpu_lane("ops.service", "beast") and not k.occupancy_parks_gpu_lane("ops.service", "normal")
    assert k.occupancy_parks_gpu_lane("crew.service", "render") and not k.occupancy_parks_gpu_lane("voice.service", "render")
    src = (ROOT / "crew" / "scripts" / "krew-keeper.py").read_text()
    for gone in ("hermes-ada-gateway", "hermes-lila-gateway", "_local(8000", "_local(8005", '"ops.service"', '"crew.service"'):
        assert gone not in src, gone


# ---- the janitor and the window read the same list -------------------------------------------------------------
def test_the_janitor_merges_the_list_into_its_units(monkeypatch):
    import janitor_env  # noqa: F401
    monkeypatch.setenv("KREW_SERVICES", str(EXAMPLE))
    j = _load(ROOT / "janitor" / "krew-janitor.py", "krewtest_janitor_services")
    assert j._SV.get("present") and ("searxng.service", "system") in j.UNITS and "zoey-display.service" in j.OBSERVE_ONLY
    assert "ops.service" in j.GPU_TENANT_REPORT_ONLY and ("hindsight.service" in j.PROBE_UNITS.values())
    assert j.PROBES["dashboard"] == (9119, "/")


def test_the_restart_window_fills_its_lists_from_the_list(monkeypatch):
    monkeypatch.setenv("KREW_SERVICES", str(EXAMPLE))
    sys.path.insert(0, str(ROOT / "window" / "lib"))
    for m in ("restart_window", "krew_window_settings", "text_cut"):
        sys.modules.pop(m, None)
    rw = _load(ROOT / "window" / "lib" / "restart_window.py", "krewtest_restart_window_services")
    assert rw.TIER_OF["hermes-gateway.service"] == 5 and "hermes-gateway.service" in rw.ZOEY_UNITS
    assert "ops.service" in rw.CREW_UNITS


def test_the_boot_check_takes_the_list_when_its_config_has_no_checks(tmp_path):
    cfg = tmp_path / "fleet.json"
    cfg.write_text(json.dumps({"hermes": "", "notify": "echo", "logdir": str(tmp_path), "breadcrumb": "none.json",
                               "checks_live": [], "checks_legacy": []}))
    script = (ROOT / "window" / "scripts" / "fleet-bootcheck").read_text()
    assert "FLEET_BOOTCHECK_SERVICES" in script and "krew_services.bootcheck_checks" in script
    # the probe list, as the script derives it
    code = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    r = subprocess.run([sys.executable, "-", str(cfg), "checks_legacy", str(EXAMPLE), str(LIB)], input=code,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "ops\t24\t10\t" in r.stdout and "systemctl --user is-active --quiet ops.service" in r.stdout


# ---- the launcher's off switch, opt-in loops ------------------------------------------------------------------
def test_a_crew_script_is_not_run_while_its_off_file_exists(tmp_path, krew_home):
    (krew_home / "krew-keeper.off").write_text("")
    r = subprocess.run([sys.executable, str(ROOT / "crew" / "scripts" / "krew-crew-run"), "krew-keeper.py"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "off by its switch" in r.stdout


def test_enable_is_opt_in_per_loop_and_the_steward_sweep_is_off_unless_named():
    ci = _load(ROOT / "crew" / "scripts" / "krew-crew-install.py", "krewtest_crew_install_enable")
    said = []
    starts = ["krew-kanban-loop.timer", "krew-hold-sweep.timer"]
    optin = ["steward-sweep.timer", "krew-keeper.service", "krew-tool-watch.timer"]
    assert ci.choose_enable("", starts, optin, said.append) == []
    assert ci.choose_enable("all", starts, optin, said.append) == starts
    assert ci.choose_enable("kanban-loop,steward-sweep,tool-watch", starts, optin, said.append) == [
        "krew-kanban-loop.timer", "steward-sweep.timer", "krew-tool-watch.timer"]
    assert ci.choose_enable("nope", starts, optin, said.append) == [] and "no such unit" in said[-1]
    assert "# krew: opt-in" in (ROOT / "crew" / "units" / "steward-sweep.timer").read_text()
    for u in ("krew-tool-watch.timer", "krew-wall-tab-probe.timer", "krew-keeper.service"):
        assert "# krew: opt-in" in (ROOT / "crew" / "units" / u).read_text(), u


# ---- init -------------------------------------------------------------------------------------------------------
def test_init_dry_run_writes_profiles_services_and_nothing_else(tmp_path, monkeypatch):
    hh, kh = tmp_path / "h", tmp_path / "k"
    env = dict(os.environ, ZOEYOS_HOME=str(tmp_path / "no-zoeyos"), ZOEYOS_PLAYER="http://127.0.0.1:9")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-install.py"), "init", "--hermes-home", str(hh),
                        "--krew-home", str(kh), "--endpoint", "http://127.0.0.1:1234/v1", "--model", "m",
                        "--api-key", "not-a-real-key-value", "--no-hermes", "--no-discover", "--yes", "--dry-run"],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120)
    assert r.returncode == 0, r.stderr
    assert not hh.exists() and not kh.exists()
    for p in ("zoey", "ada", "bash", "steward", "vera", "lila", "scout", "judge"):
        assert f"config   {p} ->" in r.stdout and f"soul     souls/{p} ->" in r.stdout
    assert "config   default ->" in r.stdout and "KREW_MODEL_API_KEY ->" in r.stdout and "not-a-real-key-value" not in r.stdout


def test_init_writes_each_profile_its_own_toolsets_and_the_key_only_to_env(tmp_path):
    init = _load(ROOT / "scripts" / "krew_install_init.py", "krewtest_init")
    hh = tmp_path / "h"
    said = []
    init.write_profiles(hh, "http://127.0.0.1:1234/v1", "m", "MY_KEY", "secret-value", said.append, dry=False)
    zoey = (hh / "profiles" / "zoey" / "config.yaml").read_text()
    main = (hh / "config.yaml").read_text()
    assert "base_url: http://127.0.0.1:1234/v1" in zoey and "default: m" in zoey and "key_env: MY_KEY" in zoey
    assert "cli: [hands, skill_read, zoeyos]" in zoey and "dispatch_in_gateway: false" in zoey
    assert "dispatch_in_gateway: true" in main and "multiplex_profiles: true" in main
    assert "secret-value" not in zoey and "secret-value" not in main
    assert (hh / ".env").read_text() == "MY_KEY=secret-value\n" and oct((hh / ".env").stat().st_mode)[-3:] == "600"
    init.write_profiles(hh, "http://other/v1", "x", "MY_KEY", "secret-value", said.append, dry=False)
    assert "base_url: http://127.0.0.1:1234/v1" in (hh / "profiles" / "zoey" / "config.yaml").read_text()   # never overwritten
    assert (hh / ".env").read_text() == "MY_KEY=secret-value\n"


def test_init_approves_discovered_rows_only_with_yes(tmp_path, monkeypatch):
    init = _load(ROOT / "scripts" / "krew_install_init.py", "krewtest_init2")
    S = _load(LIB / "krew_services.py", "krewtest_services2")
    rows = [S.normalize({"name": "a", "unit": "a.service", "port": 1, "approved": False})]
    monkeypatch.setattr(init, "krew_services", S, raising=False)
    sys.modules["krew_services"] = S
    monkeypatch.setattr(S, "discover", lambda *a, **k: rows)
    kh = tmp_path / "k"
    said = []
    init.services(kh, said.append, dry=False, yes=False, tty=False)
    assert not S.load(kh / "services.json") and S.load(kh / "services.json", approved_only=False)
    (kh / "services.json").unlink()
    init.services(kh, said.append, dry=False, yes=True, tty=False)
    assert [r["name"] for r in S.load(kh / "services.json")] == ["a"]


# ---- the panel's krew section ---------------------------------------------------------------------------------
def test_the_panel_manifest_carries_the_krew_services_section_with_run_and_off(krew_home):
    T = _load(ROOT / "panel" / "krew-panel-tiles.py", "krewtest_panel_section")
    m = T.manifest({})
    assert m["sections"] == ["krew services", "modes"]
    sec = [t for t in m["tiles"] if t.get("section") == "krew services"]
    ids = [t["id"] for t in sec]
    for want in ("krew-keeper", "krew-hold-sweep", "krew-steward-sweep", "krew-restart-window", "krew-window-tick",
                 "krew-ms-nightly", "krew-dream-night", "krew-hindsight", "krew-docs-index", "krew-boot-check",
                 "krew-render-window"):
        assert want in ids, want
    for t in sec:
        assert t["run"]["unit"] == t["unit"] and ("file" in t["off"] or "unit" in t["off"])
    keeper = next(t for t in sec if t["id"] == "krew-keeper")
    assert keeper["off"] == {"file": str(krew_home / "krew-keeper.off")} and keeper["status"].endswith("keeper/status.json")
    assert next(t for t in sec if t["id"] == "krew-boot-check")["scope"] == "system"
    assert next(t for t in sec if t["id"] == "krew-hindsight")["off"] == {"unit": "krew-hindsight.service"}
    doc = (ROOT / "docs" / "PANEL-CONTRACT.md").read_text()
    for key in ("`sections`", "`section`", "`run`", "`off`", "krew not installed"):
        assert key in doc


def test_services_rows_become_panel_tiles(krew_home, monkeypatch):
    monkeypatch.setenv("KREW_SERVICES", str(EXAMPLE))
    T = _load(ROOT / "panel" / "krew-panel-tiles.py", "krewtest_panel_services")
    units = [t.get("unit") for t in T.manifest({})["tiles"]]
    assert "ops.service" in units and "searxng.service" in units and "face-shell.service" not in units
    assert units.count("hermes-gateway.service") == 1     # the gateway tile is not doubled by its services row


# ---- render: the default is commercial-OK, flux2 is opt-in ---------------------------------------------------------
def test_the_render_default_is_commercial_ok_and_flux2_is_opt_in(monkeypatch):
    monkeypatch.setenv("KREW_COMFY_WORKFLOWS", str(ROOT / "plugins" / "krew-comfy" / "workflows"))
    from conftest import load_plugin
    m = load_plugin("krew-comfy")
    assert m.default_image_model() == "qwen-image-2512" and m.HouseComfyImage().default_model() == "qwen-image-2512"
    assert m.HouseComfyImage().is_available()
    recs = {r["id"]: r for r in m.load_recipes()}
    for rid in ("flux2-turbo", "flux2-klein", "flux2-max", "flux2-dev", "ltx25-i2v", "ltx25-flf2v"):
        assert recs[rid].get("opt_in") and not recs[rid].get("studio") and not m.commercial_ok(recs[rid]), rid
    for rid in ("sdxl", "qwen-image-2512", "qwen-edit-2511", "wan22-i2v"):
        assert m.commercial_ok(recs[rid]) and m.recipe_wired(recs[rid]), rid
    shipped = {p.name for p in (ROOT / "plugins" / "krew-comfy" / "workflows").glob("*.json")}
    assert {"sdxl-txt2img.json", "qwen-image-2512-txt2img.json", "qwen-edit-2511.json", "wan22-i2v.json"} <= shipped
    assert not any("2.1" in n or "qwen21" in n for n in shipped)
    assert "flux2-turbo" not in (ROOT / "plugins" / "krew-comfy" / "__init__.py").read_text().split("def default_image_model")[1].split("def ")[0]


# ---- SOULs: the laws stay, the house's own lines are gone ------------------------------------------------------
def test_souls_keep_the_laws_and_drop_the_house_lines():
    souls = {p.parent.name: p.read_text(encoding="utf-8") for p in (ROOT / "souls").glob("*/SOUL.md")}
    z = souls["zoey"]
    assert "Default short; a summary is a few sentences, longer only when he asks" in " ".join(z.split())
    assert "# AUTH" in z and "**zoey-auth**" in z
    for s in ("zoey-wall", "zoey-map", "zoey-list", "zoey-marketing", "zoey-look", "zoey-needs", "zoey-spotcheck"):
        assert f"**{s}**" in z, s
    assert "self-report" in souls["bash"] and "self-report" in souls["steward"] and "self-report" in souls["vera"] and "self-report" in souls["lila"]
    assert "needs-zoey" in souls["ada"] and "needs-zoey" in souls["steward"]
    assert "land-only" in souls["vera"] and "land-only" in souls["steward"] and "unlock=" in souls["vera"]
    assert "spawn-only" in souls["vera"].lower() and "spawn-only" in souls["lila"].lower()
    for name, text in souls.items():
        for gone in (":8191", ":8653", ":8099", "projects/", "DEPS-PINNED", "Grok", "STEWARD-MS-REPORT"):
            assert gone not in text, (name, gone)
        assert "{{deliverables_dir}}" in text or name in ("judge", "main"), name
    for name in ("zoey", "lila", "ada", "bash", "steward", "vera", "scout", "judge"):
        shown = "Crissa" if name == "zoey" else name.capitalize()     # the front door is Crissa (2026-10-10)
        assert f"You are **{shown}**" in souls[name], name


def test_the_maps_name_only_skills_that_ship():
    shipped = {p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").is_file()}
    for m in ("bash-map", "lila-map", "vera-map"):
        y = (ROOT / "skills" / m / "map.yaml").read_text()
        assert f"name: {m}" in y
        assert set(re.findall(r"^\s*skill: ([a-z0-9-]+)", y, re.M)) <= shipped, m
        assert "house" not in y.lower()


# ---- the wording and the docker paths are gone; the patrols are off by default ---------------------------------
def test_no_docker_deliverables_path_and_no_old_ear_wording():
    from test_krew_packages import repo_files
    hits = []
    for rel in repo_files():
        if rel.startswith("tests/"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for pat in ("/deliverables/", "Parakeet", "Never Whisper", "restart rail-stt"):
            if pat in text:
                hits.append(f"{rel}: {pat}")
    assert not hits, hits


def test_the_patrols_probe_without_filing_until_armed(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_HOME", str(tmp_path))
    monkeypatch.delenv("KREW_WALL_TAB_PROBE_CARDS", raising=False)
    w = _load(ROOT / "crew" / "scripts" / "krew-wall-tab-probe.py", "krewtest_wall_tab_probe")
    assert w.probe("http://127.0.0.1:9")["installed"] is False
    dead = {"installed": True, "alive": False, "glasses": {"wall": {"fresh": False, "age_s": 99}}, "why": "stale"}
    alive = {"installed": True, "alive": True, "glasses": {"wall": {"fresh": True, "age_s": 0.3}}, "why": ""}
    filed = []
    k = lambda *a, **kw: (filed.append(a), (0, "t_00000001"))[1]  # noqa: E731
    for _ in range(w.DEAD_TICKS - 1):
        assert w.tick(reading=dead, kanban=k)["action"] == ""
    assert w.tick(reading=dead, kanban=k)["action"] == "would_card" and not filed
    assert w.tick(reading=alive, kanban=k)["action"] == "episode closed"
    t = _load(ROOT / "crew" / "scripts" / "krew-tool-watch.py", "krewtest_tool_watch")
    assert t.classify('{"success": false, "error": "Unknown action x"}') == "bad_args"
    assert t.classify("Traceback (most recent call last)") == "error" and t.classify("done, 3 files") == ""
    assert t.rates([{"ts": 100, "failed": True, "retry": False}] * 12, 200)["jump"] is True     # 12 of 12 failed, against an empty baseline
    assert t.rates([{"ts": 100, "failed": False, "retry": False}] * 12, 200)["jump"] is False
    assert not t.cards_on({}) and t.cards_on({"KREW_TOOL_WATCH_CARDS": "1"})


def test_ltx_is_community_licensed_opt_in_and_its_outputs_are_marked(tmp_path):
    """The owner 2026-10-08: every ltx recipe carries Lightricks' community licence (ltx23 was mislabelled Apache),
    is off by default and never downloaded by krew, and an LTX output is marked AI-generated."""
    recs = {r["id"]: r for r in json.loads((ROOT / "plugins" / "krew-comfy" / "recipes.json").read_text())["recipes"]}
    from conftest import submodule
    rc = submodule("krew-render", "render_cards")
    for rid, r in recs.items():
        if rid.startswith("ltx"):
            assert "community" in r["license"].lower() and r.get("opt_in") and not r.get("studio"), rid
            assert "Lightricks" in r.get("note", "") and "never downloaded" in r.get("note", ""), rid
        if rid.startswith("ltx23"):   # commercial under Lightricks' terms, never labelled "not commercial"
            assert "$10M" in r["license"] and rc.commercial(r).startswith("yes - under $10M"), rid
    for rel in ("skills/krew-render/SKILL.md", "skills/render-lane/SKILL.md", "plugins/team-zoey/__init__.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "ltx23-*" not in text.split("Never")[0] or "community" in text, rel
        assert "Apache 2.0 | **yes** | Text-to-video" not in text, rel
    rr = _load(ROOT / "plugins" / "krew-render" / "render_runner.py", "krewtest_render_runner_mark")
    out = tmp_path / "o"
    out.mkdir()
    mark = rr.ai_generated_mark({"id": "ro-1", "family": "ltx23", "workflow": "ltx23-nvfp4-t2v"}, out)
    assert mark == {"ai_generated": True, "licence": "Lightricks LTX community licence"}
    assert "AI-generated" in (out / "AI-GENERATED.txt").read_text()
    assert rr.ai_generated_mark({"id": "ro-2", "family": "wan22", "workflow": "wan22-i2v"}, out) == {}


def test_init_enables_each_profiles_plugins_and_writes_the_gateway_pins(tmp_path):
    """A fresh init used to leave every profile with no plugin enabled, no topology file and no T0 drop-in, so
    krew's own pin check was red on a new box (the owner 2026-10-08)."""
    init = _load(ROOT / "scripts" / "krew_install_init.py", "krewtest_init3")
    hh = tmp_path / "hermes"
    for n in ("krew-kitting", "krew-skill-preload", "team-zoey", "krew-board", "krew-render", "krew-comfy", "krew-vlm"):
        (hh / "plugins" / n).mkdir(parents=True)
    (hh / "config.yaml").write_text("model:\n  default: m\n")
    for p in ("zoey", "bash"):
        (hh / "profiles" / p).mkdir(parents=True)
    (hh / "profiles" / "zoey" / "config.yaml").write_text("model:\n  default: m\nplugins:\n  enabled:\n    - team-zoey\n")
    (hh / "profiles" / "bash" / "config.yaml").write_text("model:\n  default: m\n")
    said = []
    units = tmp_path / "units"
    init.plugins_and_gateway(hh, said.append, dry=False, unit_dir=units)
    zoey = (hh / "profiles" / "zoey" / "config.yaml").read_text()
    assert zoey.count("- team-zoey") == 1 and "- krew-skill-preload" in zoey and "- krew-kitting" in zoey
    assert "krew_preload:\n  skills: none" in zoey
    assert (hh / "profiles" / "zoey" / "plugins" / "krew-skill-preload").is_symlink()
    bash = (hh / "profiles" / "bash" / "config.yaml").read_text()
    assert "plugins:\n  enabled:\n" in bash and "- krew-render" in bash and "- krew-skill-preload" not in bash
    main = (hh / "config.yaml").read_text()
    assert "- team-zoey" in main and "- krew-board" in main and not (hh / "plugins" / "plugins").exists()
    assert (hh / "krew-topology.env").read_text().strip() == "KREW_GATEWAY_TOPOLOGY=host"
    assert "KREW_T0_MODE=enforce" in (units / "hermes-gateway.service.d" / "krew.conf").read_text()
    before = zoey
    init.plugins_and_gateway(hh, said.append, dry=False, unit_dir=units)      # idempotent
    assert (hh / "profiles" / "zoey" / "config.yaml").read_text() == before
