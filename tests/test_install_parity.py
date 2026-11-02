"""A clean `krew-install.py init` lays the crew down the way the reference install runs it (the User 2026-10-10:
"if I clean-install the packs, I could not tell the difference").

One real init into a scratch HOME, Hermes home, krew home and unit folder; `hermes` is a stand-in that only logs
its argv. Then every file it wrote is read back against the reference shape: each profile's hooks (scripts, order,
matchers, `fail_closed` on the T0 gate, `hooks_auto_accept`), toolsets on every platform, agent caps, plugins,
skills, profile.yaml's Board class description (equal to the SOUL's line), the SOULs with their placeholders filled,
the T0 policy in place with every rule, the cron jobs asked for, and a pass of the four checks `hermes hooks doctor`
makes (script resolvable, allowlisted, unchanged since approval, valid JSON on the doctor's synthetic payload).
The real doctor runs too when KREW_HERMES_PIN_TREE + KREW_HERMES_PYTHON name a pinned checkout (never the live
tree: a scratch HERMES_HOME there once rewrote the live launcher).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "crew" / "lib"))
import krew_yaml  # noqa: E402

KIT = json.loads((ROOT / "crew" / "config" / "profile-kit.json").read_text(encoding="utf-8"))
CREW = ("crissa", "ada", "bash", "steward", "vera", "lila", "scout", "judge")

# The reference install's pre_tool_call list per profile, by script, in its order (read off its configs 2026-10-10).
REF_HOOKS = {
    "ada": ["krew-close-clean.py", "krew-review-route.py", "tier0_gate.py", "crissa-tool-callout.py", "krew-tool-count.py"],
    "vera": ["krew-close-clean.py", "krew-review-route.py", "tier0_gate.py", "krew_test_sandbox.py",
             "crissa-tool-callout.py", "krew-tool-count.py"],
    "lila": ["krew-close-clean.py", "krew-review-route.py", "tier0_gate.py", "crissa-tool-callout.py", "krew-tool-count.py"],
    "steward": ["krew-close-clean.py", "krew-review-route.py", "tier0_gate.py", "krew_test_sandbox.py",
                "crissa-tool-callout.py", "krew-tool-count.py"],
    "bash": ["krew-close-clean.py", "krew-review-route.py", "tier0_gate.py", "krew_test_sandbox.py",
             "crissa-tool-callout.py", "krew-tool-count.py"],
    "scout": ["krew-close-clean.py", "tier0_gate.py"],
    "judge": ["krew-close-clean.py", "krew-review-route.py", "tier0_gate.py", "crissa-tool-callout.py"],
    "crissa": ["krew-review-route.py", "tier0_gate.py", "crissa-tool-callout.py", "krew-tool-count.py"],
}
REF_T0_MATCHER = {"lila": "terminal|execute_code|write_file|patch|browser_navigate",
                  "crissa": "terminal|execute_code|write_file|patch|browser"}
# The reference's toolsets (platform `cron` / `api_server`; cli adds docs) and caps.
REF_TOOLSETS = {
    "ada": ["kanban", "clarify", "file", "memory", "session_search", "team", "todo", "skills", "delegation", "cronjob"],
    "vera": ["clarify", "file", "terminal", "kanban_lean", "web", "search", "vision", "video", "memory", "team", "todo",
             "skills", "delegation"],
    "lila": ["clarify", "file", "browser", "web", "vision", "video", "memory", "team", "skills", "kanban_lean",
             "delegation", "image_gen", "video_gen"],
    "steward": ["clarify", "file", "terminal", "code_execution", "memory", "team", "skills", "kanban_lean", "delegation",
                "cronjob", "search"],
    "bash": ["clarify", "file", "terminal", "code_execution", "memory", "team", "skills", "kanban_lean", "delegation",
             "web", "search", "vision", "video", "image_gen", "video_gen"],
    "scout": ["file", "web", "search", "skills", "memory", "session_search"],
    "judge": ["kanban_lean", "file", "skills"],
}
REF_CAPS = {"ada": (60, "high"), "vera": (40, "high"), "lila": (45, "high"), "steward": (80, "low"),
            "bash": (80, "high"), "scout": (80, "medium"), "judge": (8, "low")}
# The reference's plugins per profile, in krew's names (team-crissa's board tools ride krew-board; Cosy -> krew-tts).
REF_PLUGINS = {
    "ada": {"team-crissa", "krew-board", "krew-tts", "krew-kitting", "krew-panel", "krew-docs"},
    "vera": {"team-crissa", "krew-board", "krew-tts", "krew-kitting", "krew-panel", "krew-kanban-lean", "krew-vlm",
             "krew-docs", "krew-render"},
    "lila": {"team-crissa", "krew-board", "krew-tts", "krew-comfy", "krew-kitting", "krew-panel", "krew-kanban-lean",
             "krew-vlm", "krew-docs", "krew-render"},
    "steward": {"team-crissa", "krew-board", "krew-tts", "krew-kitting", "krew-panel", "krew-kanban-lean", "krew-docs"},
    "bash": {"team-crissa", "krew-board", "krew-tts", "krew-comfy", "krew-kitting", "krew-panel", "krew-kanban-lean",
             "krew-vlm", "krew-docs", "krew-render"},
    "scout": {"krew-kitting", "krew-docs"},
    "judge": {"krew-kitting", "krew-kanban-lean", "krew-docs"},
}
DOCTOR_PAYLOAD = {"tool_name": "terminal", "args": {"command": "echo hello"}, "session_id": "test-session",
                  "task_id": "test-task", "tool_call_id": "test-call"}


def _load_yaml(p: Path) -> dict:
    return krew_yaml.safe_load(p.read_text(encoding="utf-8")) or {}


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("clean-install")
    home, hh, kh, units = tmp / "home", tmp / "home" / ".hermes", tmp / "home" / ".krew", tmp / "units"
    home.mkdir()
    log = tmp / "hermes-calls.jsonl"
    fake = tmp / "bin" / "hermes"
    fake.parent.mkdir()
    fake.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                    f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n", encoding="utf-8")
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KREW_", "HERMES_"))}
    env.update(HOME=str(home), KREW_HOME=str(kh), HERMES_HOME=str(hh), KREW_HERMES_BIN=str(fake),
               CRISSA_HOME=str(tmp / "no-crissa"), CRISSA_PLAYER="http://127.0.0.1:9", CRISSA_EAR="off",
               PATH=str(fake.parent) + os.pathsep + env.get("PATH", ""))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-install.py"), "init", "--hermes-home", str(hh),
                        "--krew-home", str(kh), "--unit-dir", str(units), "--no-hermes", "--no-discover", "--yes",
                        "--endpoint", "http://127.0.0.1:1234/v1", "--model", "m", "--owner", "Pat",
                        "--deliverables", str(home / "Desktop" / "crew-out")],
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return {"home": home, "hh": hh, "kh": kh, "units": units, "out": r.stdout, "env": env, "calls": calls}


def _cfg(inst, p) -> dict:
    return _load_yaml(inst["hh"] / "profiles" / p / "config.yaml")


def _pre(cfg) -> list:
    return (cfg.get("hooks") or {}).get("pre_tool_call") or []


# ---- hooks -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("p", CREW)
def test_every_profile_runs_the_reference_hooks_in_the_reference_order(installed, p):
    pre = _pre(_cfg(installed, p))
    scripts = [os.path.basename(next(x for x in h["command"].split() if x.endswith(".py"))) for h in pre]
    assert scripts == REF_HOOKS[p], (p, scripts)
    t0 = next(h for h in pre if "tier0_gate.py" in h["command"])
    assert t0["fail_closed"] is True and t0["timeout"] == 10
    assert t0["matcher"] == REF_T0_MATCHER.get(p, "terminal|execute_code|write_file|patch")
    assert t0["command"] == f"python3 {installed['kh'] / 'rails' / 'tier0_gate.py'}"     # never behind the runner
    callout = [h for h in pre if "crissa-tool-callout.py" in h["command"]]
    if callout:
        assert ("^hands$" in callout[0]["matcher"]) == (p == "crissa")
        assert p == "crissa" or callout[0]["matcher"] == "kanban_"
    assert _cfg(installed, p).get("hooks_auto_accept") is True


@pytest.mark.parametrize("p", CREW)
def test_hooks_doctor_reads_clean_on_a_fresh_install(installed, p):
    """The four checks of `hermes hooks doctor`, as it makes them (hermes_cli/hooks.py _doctor_one)."""
    pdir = installed["hh"] / "profiles" / p
    approvals = json.loads((pdir / "shell-hooks-allowlist.json").read_text())["approvals"]
    assert oct((pdir / "shell-hooks-allowlist.json").stat().st_mode)[-3:] == "600"
    for h in _pre(_cfg(installed, p)):
        cmd = h["command"]
        parts = cmd.split()
        script = next((x for x in parts if x.endswith((".py", ".sh"))), None) or next(x for x in parts if "/" in x)
        assert os.path.isfile(script) and os.access(script, os.R_OK), (p, cmd)          # 1. resolvable
        entry = next((e for e in approvals if e["event"] == "pre_tool_call" and e["command"] == cmd), None)
        assert entry, (p, "not allowlisted", cmd)                                         # 2. allowlisted
        from datetime import datetime, timezone
        now = datetime.fromtimestamp(os.path.getmtime(script), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        assert entry["script_mtime_at_approval"] == now, (p, cmd)                         # 3. unchanged
        stdin = json.dumps({"hook_event_name": "pre_tool_call", "tool_name": "terminal",
                            "tool_input": DOCTOR_PAYLOAD["args"], "session_id": "test-session", "cwd": str(pdir),
                            "profile": p, "extra": {"task_id": "test-task", "tool_call_id": "test-call"}})
        env = dict(installed["env"], HERMES_HOME=str(pdir), HERMES_PROFILE=p)
        r = subprocess.run(parts, input=stdin, capture_output=True, text=True, env=env, timeout=h["timeout"] + 20)
        out = r.stdout.strip()
        if out:                                                                           # 4. empty or JSON
            json.loads(out)


def test_a_second_init_approves_nothing_twice_and_adds_no_hook(installed):
    env = installed["env"]
    before = {p: (installed["hh"] / "profiles" / p / "shell-hooks-allowlist.json").read_text() for p in CREW}
    r = subprocess.run([sys.executable, str(ROOT / "crew" / "scripts" / "krew-crew-install.py"), "--hermes-home",
                        str(installed["hh"]), "--krew-home", str(installed["kh"]), "--systemd-dir", str(installed["units"]),
                        "--profiles", ",".join(CREW), "--approve-hooks"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "every hook already approved" in r.stdout
    for p in CREW:
        assert (installed["hh"] / "profiles" / p / "shell-hooks-allowlist.json").read_text() == before[p]
        assert [os.path.basename(h["command"].split()[-1]) for h in _pre(_cfg(installed, p))] == \
            [s for s in REF_HOOKS[p]], p


# ---- the profile's own settings ----------------------------------------------------------------------------------
@pytest.mark.parametrize("p", sorted(REF_TOOLSETS))
def test_each_crew_profile_has_the_reference_toolsets_caps_and_plugins(installed, p):
    c = _cfg(installed, p)
    pt = c["platform_toolsets"]
    assert pt["cron"] == REF_TOOLSETS[p] and pt["api_server"] == REF_TOOLSETS[p]
    assert pt["cli"] == REF_TOOLSETS[p] + ["docs"]
    assert (c["agent"]["max_turns"], c["agent"]["reasoning_effort"]) == REF_CAPS[p]
    # skills.disabled is krew-kitting's allowlist, laid on by init's pinned writes (krew-config-restore after Hermes,
    # 2026-10-30): whatever bundled skill the allowlist does not keep (none here: the stand-in hermes ships none)
    assert {k: v for k, v in c["skills"].items() if k != "disabled"} == {"guard_agent_created": True, "write_approval": True}
    assert isinstance(c["skills"].get("disabled", []), list)
    assert set(c["plugins"]["enabled"]) >= REF_PLUGINS[p], (p, set(REF_PLUGINS[p]) - set(c["plugins"]["enabled"]))
    assert c["memory"]["memory_enabled"] is (p != "judge")
    assert c["kanban"]["dispatch_in_gateway"] is False
    for n in c["plugins"]["enabled"]:
        assert (installed["hh"] / "profiles" / p / "plugins" / n).exists() or n == "hindsight", (p, n)


def test_judge_is_one_call_one_verb(installed):
    """The reference's judge pins (house test_judge_profile, lifted): the belt, 8 turns, low effort, no children,
    no memory, no voice, the T0 gate failing closed, a description that forbids routing work to it."""
    c = _cfg(installed, "judge")
    assert set(c["platform_toolsets"]["cron"]) == {"kanban_lean", "file", "skills"}
    assert c["agent"]["max_turns"] <= 8 and c["agent"]["reasoning_effort"] == "low"
    assert c["delegation"]["max_concurrent_children"] == 0 and c["delegation"]["orchestrator_enabled"] is False
    assert c["memory"]["memory_enabled"] is False and "tts" not in c
    assert any("tier0_gate" in h["command"] and h["fail_closed"] for h in _pre(c))
    d = _load_yaml(installed["hh"] / "profiles" / "judge" / "profile.yaml")["description"].lower()
    assert "never assign work here" in d and "only kanban-loop and the board gate" in d
    assert (installed["hh"] / "profiles" / "judge" / "skills" / "krew" / "krew-judge" / "SKILL.md").is_file()
    assert (installed["hh"] / "profiles" / "ada" / "skills" / "krew" / "krew-judge" / "SKILL.md").is_file()


def test_ada_runs_the_reference_tool_search_and_her_sandbox_waits_for_docker():
    sys.path.insert(0, str(ROOT / "scripts"))
    import krew_install_init as init
    t = (ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8")
    vals = dict(init.soul_values(), hermes_home="/nonexistent-h", live_tree=str(ROOT))
    with_docker = krew_yaml.safe_load(init.profile_config(t, "ada", "", "", "K", api_key=False, values=vals, docker=True))
    without = krew_yaml.safe_load(init.profile_config(t, "ada", "", "", "K", api_key=False, values=vals, docker=False))
    assert with_docker["terminal"]["backend"] == "docker"
    assert with_docker["terminal"]["docker_volumes"] == [f"{ROOT}:/repo:ro"]          # missing host folders left out
    assert "terminal" not in without
    assert without["tools"]["tool_search"]["eager"] == ["team_board", "krew_docs"]
    assert "computer_use" in without["tools"]["tool_search"]["defer"]
    assert krew_yaml.safe_load(init.profile_config(t, "scout", "", "", "K", api_key=False))["tools"]["tool_search"] == \
        {"enabled": False}


# ---- profile.yaml, SOUL, skills ----------------------------------------------------------------------------------
@pytest.mark.parametrize("p", CREW)
def test_profile_yaml_carries_the_board_class_the_soul_says(installed, p):
    pdir = installed["hh"] / "profiles" / p
    py = _load_yaml(pdir / "profile.yaml")
    assert py["name"] == p and py["description_auto"] is False
    soul = (pdir / "SOUL.md").read_text(encoding="utf-8")
    assert "{{" not in soul, p
    m = re.search(r"\*\*Board class\*\*[^:]*:\s*(.*?)(?=\n\n|\n[A-Z#*-]|\Z)", soul, re.S)
    assert m and " ".join(m.group(1).split()) == " ".join(py["description"].split()), p


def test_the_souls_name_the_owner_and_the_folder_given(installed):
    z = (installed["hh"] / "profiles" / "crissa" / "SOUL.md").read_text()
    assert "Pat" in z and str(installed["home"] / "Desktop" / "crew-out") in z
    main = (installed["hh"] / "SOUL.md").read_text()
    assert "{{" not in main


@pytest.mark.parametrize("p", CREW)
def test_each_profile_carries_its_kit_skills(installed, p):
    want = sorted(n for n, profs in KIT["skills"].items() if not n.startswith("_") and p in profs)
    have = sorted(d.name for d in (installed["hh"] / "profiles" / p / "skills" / "krew").iterdir()) if want else []
    assert have == want, p


# ---- policy, crons, gateway --------------------------------------------------------------------------------------
def test_the_t0_policy_is_in_place_with_every_rule(installed):
    pol = _load_yaml(installed["kh"] / "tier0.yaml")
    ex = _load_yaml(ROOT / "config" / "tier0.example.yaml")
    assert len(pol["blocklist"]) == len(ex["blocklist"]) >= 55
    assert [r.get("route") for r in pol["blocklist"]] == [r.get("route") for r in ex["blocklist"]]
    assert pol["why_before_fix"]["scope"] == "crew"
    conf = (installed["units"] / "hermes-gateway.service.d" / "krew.conf").read_text()
    assert "KREW_T0_MODE=enforce" in conf


def test_every_cron_the_reference_runs_is_asked_for(installed):
    made = {(c[c.index("-p") + 1], c[c.index("--name") + 1]) for c in installed["calls"] if "cron" in c and "create" in c}
    crew = json.loads((ROOT / "crew" / "config" / "crew-crons.json").read_text())["jobs"]
    want = {(j["profile"], j["name"]) for j in crew}
    want |= {(j["profile"], n) for n, j in KIT["crons"].items() if not n.startswith("_")}
    assert want <= made, sorted(want - made)
    assert ("ada", "kanban-heartbeat") in made and ("crissa", "needs-crissa-watch") in made
    beat = next(c for c in installed["calls"] if "kanban-heartbeat" in c)
    assert beat[beat.index("create") + 1] == "every 60m" and beat[beat.index("--deliver") + 1] == "origin"


def test_nothing_was_written_outside_the_scratch_folders(installed):
    out = installed["out"]
    for real in (str(Path.home()) + "/.hermes", str(Path.home()) + "/.krew", str(Path.home()) + "/.config/systemd"):
        assert real not in out, real
    assert "daemon-reload: ok" not in out


# ---- krew's own units: written by init, the dated one-shots never enabled unnamed (the User 2026-10-10) -------------
ONE_SHOTS = ("krew-pyjwt-trail-confirm.timer", "krew-hands-step-cap-remeasure.timer")


def test_init_writes_krews_own_units_and_keeps_the_one_shots_opt_in(installed):
    for n in ("krew-window-tick.timer", "krew-ms-survey.timer", "krew-restart-window.timer", *ONE_SHOTS):
        assert (installed["units"] / n).is_file(), n
        assert "{krew_home}" not in (installed["units"] / n).read_text()
    for n in ONE_SHOTS + ("krew-ms-nightly.timer",):
        assert "# krew: opt-in" in (installed["units"] / n).read_text(), n
    line = next(x for x in installed["out"].splitlines() if x.startswith("units    opt-in"))
    assert all(n in line for n in ONE_SHOTS), line
    assert "enable   " not in installed["out"]                     # no --enable: nothing was turned on


def test_enable_all_never_turns_on_a_one_shot_and_naming_one_does(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import krew_install_init as init
    monkeypatch.setenv("HOME", str(tmp_path))
    user_dir = tmp_path / ".config" / "systemd" / "user"
    calls, said = [], []
    run = lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")  # noqa: E731
    got = init.enable_own_units("all", user_dir, said.append, dry=False, run=run)
    assert "krew-window-tick.timer" in got and "krew-ms-survey.timer" in got
    assert not set(got) & set(ONE_SHOTS + ("krew-ms-nightly.timer",)), got
    assert not any("@." in n for n in got), got                   # never a template: no model engine from --enable all
    if calls:                                                       # a box with systemctl: only the chosen, --now
        assert {c[-1] for c in calls} == set(got) and all(c[:4] == ["systemctl", "--user", "enable", "--now"] for c in calls)
    assert not any(n in c for c in calls for n in ONE_SHOTS)
    calls.clear()
    got = init.enable_own_units("pyjwt-trail-confirm", user_dir, said.append, dry=False, run=run)
    assert got == ["krew-pyjwt-trail-confirm.timer"]
    assert init.enable_own_units("", user_dir, said.append, dry=False, run=run) == []
    calls.clear()
    got = init.enable_own_units("all", tmp_path / "scratch-units", said.append, dry=False, run=run)
    assert got and calls == [] and "is not your user systemd folder" in said[-1]   # elsewhere: said, never run


def test_unit_python_reads_a_shell_launchers_exec_line(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("krewtest_install_main", ROOT / "scripts" / "krew-install.py")
    ki = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec.loader.exec_module(ki)
    py = tmp_path / "tools" / "python3"
    py.parent.mkdir()
    py.write_text("")
    launcher = tmp_path / "launcher"
    launcher.write_text(f"#!/bin/sh\nexec {py} -I -c 'import sys' \"$@\"\n")
    shim = tmp_path / "hermes"
    shim.write_text(f"#!/bin/sh\nexec {launcher} \"$@\"\n")
    assert ki.unit_python(str(shim)) == str(py)                  # shim -> launcher -> its python
    assert ki.unit_python(str(launcher)) == str(py)
    plain = tmp_path / "plain"
    plain.write_text(f"#!{py}\nprint(1)\n")
    assert ki.unit_python(str(plain)) == str(py)
    assert ki.unit_python(str(tmp_path / "missing")) == sys.executable


# ---- the real doctor, against a pinned checkout only --------------------------------------------------------------
TREE, PY = os.environ.get("KREW_HERMES_PIN_TREE"), os.environ.get("KREW_HERMES_PYTHON")


@pytest.mark.skipif(not (TREE and PY), reason="set KREW_HERMES_PIN_TREE and KREW_HERMES_PYTHON (a pinned checkout)")
@pytest.mark.parametrize("p", CREW)
def test_the_real_hooks_doctor_finds_no_issue(installed, p):
    env = dict(installed["env"], HERMES_HOME=str(installed["hh"] / "profiles" / p))
    r = subprocess.run([PY, "-c", "import sys; from hermes_cli.hooks import _cmd_doctor; _cmd_doctor(None)"],
                       cwd=TREE, capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    assert "All shell hooks look healthy." in r.stdout, r.stdout[-3000:]
