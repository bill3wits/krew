"""Crissa's kit (the parity build, 2026-10-09): the profile-kit lists her seventeen skills, her hooks (the T0 gate
fail-closed, the callout, the counter, the review route), her Decide watch cron with its monitor script and pinned
prompt, and her plugins; every piece it names ships; the installers carry them; no file of the kit spells a house
word (tests/test_scrub.py does the scan; this file pins the pieces).

Every test writes under tmp_path only. Nothing here reaches a Wall, a board or a Hermes."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

KIT = json.loads((ROOT / "crew" / "config" / "profile-kit.json").read_text(encoding="utf-8"))
SKILLS = ROOT / "skills"
PLUGINS = ROOT / "plugins"
SCRIPTS = ROOT / "crew" / "scripts"
CRISSA_SKILLS = ("krew-deliverable", "krew-panel", "krew-reframe", "crissa-auth", "crissa-big-push", "crissa-cua", "crissa-list",
               "crissa-look", "crissa-map", "crissa-marketing", "crissa-morning", "crissa-needs", "crissa-render-cycle",
               "crissa-report", "crissa-spotcheck", "crissa-surface-art", "crissa-her-wall")
CATALOG_PLUGINS = {"hindsight"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _kit(section: str) -> dict:
    return {k: v for k, v in KIT[section].items() if not k.startswith("_")}


# ---- skills -----------------------------------------------------------------------------------------------------
def test_the_profile_kit_gives_crissa_her_seventeen_skills_and_each_ships():
    hers = sorted(n for n, profs in _kit("skills").items() if "crissa" in profs)
    assert hers == sorted(CRISSA_SKILLS), hers
    for s in hers:
        text = (SKILLS / s / "SKILL.md").read_text(encoding="utf-8")
        assert re.search(rf"^name: {re.escape(s)}$", text, re.M), s


def test_the_installer_copies_every_profile_skill_it_names(tmp_path):
    inst = _load("krewtest_crew_install_zk", SCRIPTS / "krew-crew-install.py")
    hh = tmp_path / "hermes"
    (hh / "profiles" / "crissa").mkdir(parents=True)
    env = {**os.environ, "KREW_HOME": str(tmp_path / "kh")}
    r = subprocess.run([sys.executable, str(SCRIPTS / "krew-crew-install.py"), "--hermes-home", str(hh),
                        "--krew-home", str(tmp_path / "kh"), "--profiles", "crissa"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    for s in CRISSA_SKILLS:
        assert (hh / "profiles" / "crissa" / "skills" / "krew" / s / "SKILL.md").is_file(), s
    assert inst.kit(ROOT / "crew") == KIT


# ---- hooks ------------------------------------------------------------------------------------------------------
def test_crissa_runs_the_four_hooks_and_the_t0_gate_fails_closed():
    hooks = _kit("hooks")
    hers = {n for n, h in hooks.items() if "crissa" in h["profiles"]}
    assert hers == {"review-route", "tier0", "crissa-tool-callout", "tool-count"}, hers
    t0 = hooks["tier0"]
    assert t0["fail_closed"] is True and t0["path"] == "rails/tier0_gate.py"
    assert (ROOT / t0["path"]).is_file()
    assert t0["matcher_extra"]["crissa"] == "browser"
    for n, h in hooks.items():
        if h.get("script"):
            assert (SCRIPTS / h["script"]).is_file(), n
    # the same spec rails/krew_hooks.py writes: matcher, fail_closed, crissa's browser
    sys.path.insert(0, str(ROOT / "rails"))
    import krew_hooks
    want = krew_hooks.entries_for("crissa", Path("/kh/rails"), cfg={})[0]
    assert want["fail_closed"] is True and want["matcher"] == t0["matcher"] + "|" + t0["matcher_extra"]["crissa"]


def test_hook_entries_carry_fail_closed_and_dedupe_against_the_rails_installer():
    inst = _load("krewtest_crew_install_zk2", SCRIPTS / "krew-crew-install.py")
    kh = Path("/kh")
    entries = inst.hook_entries("crissa", kh, KIT)
    by = {e["command"].split()[-1].rsplit("/", 1)[-1]: e for e in entries}
    gate = by["tier0_gate.py"]
    assert gate["command"] == "python3 /kh/rails/tier0_gate.py" and gate["fail_closed"] is True
    assert gate["matcher"] == "terminal|execute_code|write_file|patch|browser"
    assert "krew-crew-run" not in gate["command"]                       # never behind the runner or an .off switch
    callout = by["crissa-tool-callout.py"]
    # the script by its absolute path (2026-10-10): `hermes hooks doctor` resolves the first *.py token, and a bare
    # name read as a missing script
    assert callout["command"] == "python3 /kh/crew/scripts/krew-crew-run /kh/crew/scripts/crissa-tool-callout.py"
    assert callout["timeout"] == 3
    assert "^hands$" in callout["matcher"] and "fail_closed" not in callout
    # a profile whose config already runs the gate (written by rails/krew_hooks.py, by its own path) gets no second one
    cfg = {"hooks": {"pre_tool_call": [{"matcher": "x", "command": "python3 /elsewhere/rails/tier0_gate.py",
                                        "timeout": 10, "fail_closed": True}]}}
    cfg, changed = inst.merge_hooks(cfg, entries)
    assert not any(c.endswith("tier0_gate.py") for c in changed)
    assert sum("tier0_gate.py" in h["command"] for h in cfg["hooks"]["pre_tool_call"]) == 1
    cfg, changed2 = inst.merge_hooks(cfg, entries)
    assert changed2 == []
    # bash has no callout? it does: every crew profile but scout speaks its tool starts (the reference placement)
    assert "crissa-tool-callout.py" in " ".join(e["command"] for e in inst.hook_entries("bash", kh, KIT))
    assert "crissa-tool-callout.py" not in " ".join(e["command"] for e in inst.hook_entries("scout", kh, KIT))


# ---- the Decide watch cron ----------------------------------------------------------------------------------------
def test_the_decide_watch_cron_is_in_the_kit_with_its_monitor_and_pinned_prompt(tmp_path):
    crons = _kit("crons")
    j = crons["needs-crissa-watch"]
    assert j["profile"] == "crissa" and j["schedule"] == "every 10m"
    mon = SCRIPTS / j["monitor_script"]
    assert mon.is_file() and j["prompt_from"] == j["monitor_script"]
    watch = _load("krewtest_needs_watch", mon)
    assert "skill_view crissa-needs" in watch.CRON_PROMPT and "team_board answer" in watch.CRON_PROMPT
    assert "done|archive" in watch.CRON_PROMPT and "frame: <why>" in watch.CRON_PROMPT
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": [{"name": "needs-crissa-watch", "prompt": "old", "skills": ["crissa-needs"]},
                                         {"name": "other", "prompt": "keep"}]}))
    assert watch.install_cron_prompt(jobs) is True
    d = json.loads(jobs.read_text())
    assert d["jobs"][0]["prompt"] == watch.CRON_PROMPT and d["jobs"][0]["skills"] == [] and d["jobs"][0]["skill"] is None
    assert d["jobs"][1]["prompt"] == "keep"
    assert watch.install_cron_prompt(jobs) is False                     # idempotent


def test_the_installer_copies_the_monitor_and_pins_the_prompt_without_running_hermes(tmp_path):
    hh, kh = tmp_path / "hermes", tmp_path / "kh"
    (hh / "profiles" / "crissa" / "cron").mkdir(parents=True)
    (hh / "profiles" / "crissa" / "cron" / "jobs.json").write_text(json.dumps(
        {"jobs": [{"name": "needs-crissa-watch", "prompt": "stale", "skills": []}]}))
    env = {**os.environ, "KREW_HOME": str(kh), "KREW_HERMES_BIN": str(tmp_path / "no-hermes")}
    r = subprocess.run([sys.executable, str(SCRIPTS / "krew-crew-install.py"), "--hermes-home", str(hh),
                        "--krew-home", str(kh), "--crons"], capture_output=True, text=True, env=env)
    # 2026-10-30 (finding 1): with no hermes the missing cron jobs fail LOUDLY and the install exits 1; the prompt of
    # the job that is there is still pinned without running hermes
    assert r.returncode == 1, r.stderr + r.stdout
    assert "cron     NOT CREATED: no `hermes` command" in r.stdout and "CRONS FAILED:" in r.stdout
    assert (hh / "profiles" / "crissa" / "scripts" / "crissa-needs-watch.py").is_file()
    assert "crissa/needs-crissa-watch: present, prompt pinned" in r.stdout
    d = json.loads((hh / "profiles" / "crissa" / "cron" / "jobs.json").read_text())
    assert "skill_view crissa-needs" in d["jobs"][0]["prompt"]
    # a missing job is only reported without --crons (nothing runs hermes)
    r2 = subprocess.run([sys.executable, str(SCRIPTS / "krew-crew-install.py"), "--hermes-home", str(tmp_path / "h2"),
                         "--krew-home", str(kh)], capture_output=True, text=True, env=env)
    assert r2.returncode == 0 and "crissa/needs-crissa-watch: missing (--crons creates it)" in r2.stdout


def test_the_watch_reads_the_board_read_only_and_beats_under_krew_home(tmp_path, monkeypatch):
    kh = tmp_path / "kh"
    hermes = tmp_path / "hermes"
    board = hermes / "kanban" / "boards" / "live" / "kanban.db"
    board.parent.mkdir(parents=True)
    (hermes / "kanban" / "current").write_text("live\n")
    con = sqlite3.connect(board)
    con.execute("CREATE TABLE tasks (id TEXT, assignee TEXT, title TEXT, status TEXT, block_kind TEXT, created_at REAL)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at REAL)")
    con.execute("INSERT INTO tasks VALUES ('t_1', 'ada', 'Which colour?', 'blocked', 'needs_input', 1.0)")
    con.execute("INSERT INTO tasks VALUES ('t_2', 'bash', 'a worker card', 'ready', NULL, 2.0)")
    con.execute("INSERT INTO tasks VALUES ('t_3', 'ada', 'parked question', 'scheduled', 'needs_input', 3.0)")
    con.commit(); con.close()
    monkeypatch.setenv("KREW_HOME", str(kh))
    monkeypatch.setenv("KREW_HERMES", str(hermes))
    monkeypatch.setenv("CRISSA_PLAYER", "http://127.0.0.1:9")
    monkeypatch.setenv("KREW_OWNER_NAME", "Pat")
    monkeypatch.setenv("KREW_OWNER_ID", "pat")
    r = subprocess.run([sys.executable, str(SCRIPTS / "crissa-needs-watch.py")], capture_output=True, text=True,
                       env={**os.environ, "KREW_HOME": str(kh), "KREW_HERMES": str(hermes), "KREW_OWNER_NAME": "Pat",
                            "KREW_OWNER_ID": "pat", "CRISSA_PLAYER": "http://127.0.0.1:9"})
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0] == "present=0" and lines[1].startswith("beat=")
    assert [ln.split("\t")[0] for ln in lines[2:]] == ["t_1", "t_3"]
    beat = json.loads((kh / "crissa-decide-watch.beat").read_text())
    assert beat == {"cards": 2}
    watch = _load("krewtest_needs_watch2", SCRIPTS / "crissa-needs-watch.py")
    assert watch.USER_NAME == "Pat" and "Hey Pat" in watch.CRON_PROMPT
    assert "/data/" not in (SCRIPTS / "crissa-needs-watch.py").read_text()


def test_the_watch_with_no_owner_record_says_a_short_hello(monkeypatch):
    for v in ("KREW_OWNER_NAME", "KREW_OWNER_ID", "KREW_CONFIG"):
        monkeypatch.delenv(v, raising=False)
    watch = _load("krewtest_needs_watch3", SCRIPTS / "crissa-needs-watch.py")
    assert watch.USER_NAME == "the owner" and "starting with a short hello" in watch.CRON_PROMPT


# ---- the spoken callout -------------------------------------------------------------------------------------------
def test_the_callout_speaks_only_the_english_lines_and_keeps_its_state_under_krew_home(tmp_path, monkeypatch):
    kh = tmp_path / "kh"
    monkeypatch.setenv("KREW_HOME", str(kh))
    monkeypatch.setenv("CRISSA_PLAYER", "http://127.0.0.1:9")
    co = _load("krewtest_callout", SCRIPTS / "crissa-tool-callout.py")
    assert co._phrase("web_search") == "Searching the web." and co._phrase("team_board") is None
    assert co._phrase("hands") is None and co._phrase("kanban_list") is None and co._phrase("terminal") is None
    assert co._phrase("vision_analyze") == "Looking at a picture."
    assert co._voice_on() and co._callout_info()                       # no voice.json = on, info
    assert co._claim("web_search", "Searching the web.") is True
    assert co._claim("web_search", "Searching the web.") is False       # the burst window
    assert (kh / "crissa-tool-callout.last").is_file()
    assert co._enqueue("Searching the web.", "crissa") is True
    assert (kh / "crissa-tool-callout.queue").read_text() == "crissa\tSearching the web.\n"
    (kh / "voice.json").write_text(json.dumps({"voice": "off"}))
    assert not co._voice_on()
    (kh / "voice.json").write_text(json.dumps({"voice": "on", "tool_callout": "off"}))
    assert co._voice_on() and not co._callout_info()
    assert co.PLAYER == "http://127.0.0.1:9" and co.TTS_GATE.name == "tts-gate.json"
    text = (SCRIPTS / "crissa-tool-callout.py").read_text()
    assert "/v1/announce" in text and "pgrep" not in text


def test_the_callout_hook_is_silent_inside_a_hands_child(tmp_path):
    env = {**os.environ, "KREW_HOME": str(tmp_path / "kh"), "KREW_HANDS_NESTED": "1", "CRISSA_PLAYER": "http://127.0.0.1:9"}
    r = subprocess.run([sys.executable, str(SCRIPTS / "crissa-tool-callout.py")], input=json.dumps({"tool_name": "web_search"}),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0 and not (tmp_path / "kh" / "crissa-tool-callout.queue").exists()


# ---- plugins ------------------------------------------------------------------------------------------------------
def test_crissas_plugins_are_listed_and_each_ships_or_is_the_pinned_catalog_one():
    plugins = _kit("plugins")
    hers = {n for n, profs in plugins.items() if "crissa" in profs}
    assert {"team-crissa", "krew-hands", "krew-kitting", "krew-panel", "krew-skill-preload", "krew-oracle", "krew-tts",
            "hindsight"} <= hers, hers
    for n in hers - CATALOG_PLUGINS:
        assert (PLUGINS / n / "plugin.yaml").is_file(), n
    pin = json.loads((ROOT / "crew" / "hindsight" / "plugin-pin.json").read_text())
    assert pin["repo"].endswith("vectorize-io/hindsight") and re.fullmatch(r"[0-9a-f]{40}", pin["sha"])
    cfg = json.loads((ROOT / "crew" / "hindsight" / "config.example.json").read_text())
    assert cfg["auto_recall"] is False and cfg["recall_tags_match"] == "any_strict"


def test_krew_oracle_reads_presence_through_the_player_and_says_when_the_camera_is_not_here(monkeypatch):
    from conftest import load_plugin
    monkeypatch.delenv("KREW_PRESENCE_URL", raising=False)
    mod = load_plugin("krew-oracle")
    assert mod.presence_base() == "http://127.0.0.1:9/v1/rail/presence" and mod.direct_base() == ""
    tools = {}

    class Ctx:
        def register_tool(self, name, **kw):
            tools[name] = kw
    mod.register(Ctx())
    assert set(tools) == {"oracle_visit", "oracle_presence", "oracle_chase_auth", "oracle_inspect", "oracle_embed",
                          "oracle_look_at", "oracle_name_face", "error-check"}
    for name, kw in tools.items():
        assert kw["schema"]["name"] == name and kw["schema"]["description"] and "parameters" in kw["schema"]
        assert kw["toolset"] == "team"
    assert "not on this install" in tools["oracle_look_at"]["handler"]({"station": "desk"})
    assert "not on this install" in tools["oracle_name_face"]["handler"]({"person_id": "x"})
    assert tools["oracle_presence"]["handler"]().startswith("presence down")
    monkeypatch.setenv("KREW_PRESENCE_URL", "http://127.0.0.1:9/")
    assert mod.direct_base() == "http://127.0.0.1:9" and mod.presence_base() == "http://127.0.0.1:9"


def test_krew_tts_walks_the_crissa_tiers_in_order_and_clips_at_a_sentence(monkeypatch):
    from conftest import load_plugin
    for v in ("KREW_TTS_URL", "CRISSA_PORT_TURBO", "CRISSA_PORT_OFFSET"):
        monkeypatch.delenv(v, raising=False)
    mod = load_plugin("krew-tts")
    tiers = mod.tier_urls(env={}, cfg={})
    assert [(n, u) for n, u, _ in tiers] == [("turbo", "http://127.0.0.1:8186"), ("audio8", "http://127.0.0.1:8187"),
                                             ("pocket", "http://127.0.0.1:8188"), ("kokoro", "http://127.0.0.1:8189")]
    assert [c for _, _, c in tiers] == ["stream", "speech", "stream", "speech"]
    assert mod.tier_urls(env={"CRISSA_PORT_OFFSET": "100"}, cfg={})[0][1] == "http://127.0.0.1:8286"
    assert mod.tier_urls(env={"CRISSA_PORT_TURBO": "9001"}, cfg={})[0][1] == "http://127.0.0.1:9001"
    assert mod.tier_urls(env={"KREW_TTS_URL": "http://127.0.0.1:7789/"}, cfg={}) == [("url", "http://127.0.0.1:7789", "stream")]
    assert [n for n, _, _ in mod.tier_urls(env={}, cfg={"tiers": ["pocket"]})] == ["pocket"]
    assert mod.first_live([("turbo", "http://127.0.0.1:9", "stream")]) == ()
    long = "One. Two. " * 60
    assert mod.clip_at_sentence(long, 100).endswith(".") and len(mod.clip_at_sentence(long, 100)) <= 100
    assert mod.clip_at_sentence("short", 0) == "short"
    assert mod.PROVIDER == "crissa"


# ---- her config: the hands' ceiling, voice in and out, the SOUL ----------------------------------------------------
def test_init_uncomments_the_front_doors_blocks_and_copies_her_hands_ceiling(tmp_path):
    init = _load("krewtest_init_zk", ROOT / "scripts" / "krew_install_init.py")
    template = (ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8")
    z = init.profile_config(template, "crissa", "http://127.0.0.1:8000/v1", "ops", "K", api_key=False)
    assert "\ntts:\n  provider: crissa\n" in z and "krew-stt-cli.py {input_path}" in z
    assert "disabled_toolsets: [terminal, code_execution, desktop_ui, project]" in z
    assert "capability_manifest: ~/.hermes/profiles/crissa/cua-manifest.yaml" in z
    assert "#crissa " not in z and "parakeet" not in z.lower()
    b = init.profile_config(template, "bash", "", "", "K", api_key=False)
    assert "\ntts:\n" not in b and "#crissa tts:" in b
    hh = tmp_path / "hermes"
    said = []
    init.write_profiles(hh, "", "", "K", "", said.append, dry=False)
    cua = hh / "profiles" / "crissa" / "cua-manifest.yaml"
    assert cua.is_file() and cua.read_text() == (ROOT / "crew" / "config" / "crissa-cua-manifest.yaml").read_text()
    assert not (hh / "profiles" / "bash" / "cua-manifest.yaml").exists()
    text = cua.read_text()
    assert "version: 3" in text and "- executable: /usr/bin/xfdesktop" in text and "/home/" not in text


def test_the_stt_cli_decodes_a_wav_and_names_the_asr_when_it_is_down(tmp_path):
    import wave
    cli = _load("krewtest_stt_cli", SCRIPTS / "krew-stt-cli.py")
    f = tmp_path / "a.wav"
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 1600)
    assert len(cli.pcm_of(f)) == 3200
    with pytest.raises(Exception):
        cli.transcribe(b"\0\0", base="http://127.0.0.1:9", timeout=0.5)
    r = subprocess.run([sys.executable, str(SCRIPTS / "krew-stt-cli.py"), str(f)], capture_output=True, text=True,
                       env={**os.environ, "CRISSA_ASR": "http://127.0.0.1:9", "KREW_STT_TIMEOUT_S": "1"})
    assert r.returncode == 3 and "ASR did not answer" in r.stderr


def test_the_soul_is_the_reference_soul_line_for_line():
    text = (ROOT / "souls" / "crissa" / "SOUL.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == 88
    assert lines[0] == "## crissaaffect" and "**Board class**" in text and "{{owner}}" in text and "{{deliverables_dir}}" in text
    assert "krew_docs lookup" in text and "**crissa-auth**" in text and "**crissa-spotcheck**" in text
    for gone in (":8191", ":8099", "/home/", "projects/"):
        assert gone not in text


# ---- the unit table -----------------------------------------------------------------------------------------------
def test_every_unit_file_is_in_the_engines_readme_table():
    readme = (ROOT / "engines" / "README.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([^`]+\.(?:service|timer|path))` \|", readme, re.M))
    files = set()
    for d in ("crew/units", "units", "systemd/user", "units/system"):
        for f in (ROOT / d).iterdir():
            if f.suffix in (".service", ".timer", ".path"):
                files.add(f.relative_to(ROOT).as_posix())
    assert files <= listed, sorted(files - listed)
    assert listed <= files, sorted(listed - files)
    assert len(files) >= 55


# ---- the panel's modes section -------------------------------------------------------------------------------------
def test_the_panel_manifest_carries_the_modes_section(monkeypatch):
    T = _load("krewtest_panel_tiles_zk", ROOT / "panel" / "krew-panel-tiles.py")
    monkeypatch.setenv("KREW_GPU_MODE_FILE", "/x/gpu-mode")
    m = T.manifest({})
    assert m["sections"] == ["krew services", "modes"]
    modes = [x for x in m["tiles"] if x.get("section") == "modes"]
    assert not [x for x in modes if x.get("set")]                      # the mode keys wait for a panel that honours `set`
    keyed = [x for x in T.manifest({"mode_keys": True})["tiles"] if x.get("set")]
    assert {x["set"]["value"] for x in keyed} == {"normal", "beast", "render", "off"}
    assert all(x["set"]["file"] == "/x/gpu-mode" for x in keyed)
    units = {x["unit"] for x in modes if x.get("unit")}
    assert units == {"krew-engine@" + "ops.service", "krew-engine@" + "crew.service", "comfyui.service"}
    comfy = next(x for x in modes if x.get("unit") == "comfyui.service")
    assert comfy["href"] == "http://127.0.0.1:8188" and comfy["port"] == 8188
    assert not [x for x in T.manifest({"modes": False})["tiles"] if x.get("section") == "modes"]
