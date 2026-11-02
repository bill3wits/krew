"""crew/ (lane C, group 1: kanban loop and gates): it compiles, its launcher and installer do what they say, the hooks
answer like hooks, every unit runs a script that ships, every settings key is read by code, and no agent-facing
text is cut silently (coach, don't cap)."""
from __future__ import annotations

import json
import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CREW = ROOT / "crew"
RUN = CREW / "scripts" / "krew-crew-run"


def _py_files():
    out = []
    for p in sorted((CREW / "scripts").iterdir()) + sorted((CREW / "lib").glob("*.py")):
        if p.is_file() and (p.suffix == ".py" or "python" in p.read_text(encoding="utf-8").split("\n", 1)[0]):
            out.append(p)
    return out


@pytest.mark.parametrize("path", _py_files(), ids=lambda p: p.name)
def test_python_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "x.pyc"), doraise=True)


def _shell_files():
    return [p for p in sorted((CREW / "scripts").iterdir())
            if p.is_file() and p.read_text(encoding="utf-8").startswith(("#!/bin/bash", "#!/usr/bin/env bash", "#!/bin/sh"))]


@pytest.mark.parametrize("path", _shell_files(), ids=lambda p: p.name)
def test_shell_parses(path):
    assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0


def _krew_cut():
    import importlib.util
    spec = importlib.util.spec_from_file_location("krew_cut_t", CREW / "lib" / "krew_cut.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cut_says_so():
    cut = _krew_cut().cut
    assert cut("short", 10) == "short"
    s = cut("x" * 50, 10, "kanban_show has it")
    assert s.startswith("x" * 10) and "cut at 10 of 50 chars; kanban_show has it" in s
    assert cut(None, 5) == ""


def _env(tmp_path, **extra):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KREW_", "HERMES_"))}
    env.update(HOME=str(home), KREW_HOME=str(tmp_path / "krew"), **extra)
    return env


def test_launcher_exports_settings_and_lib(tmp_path):
    (tmp_path / "krew").mkdir()
    (tmp_path / "krew" / "crew.yaml").write_text("env:\n  KREW_RULERS: crissa,user\n  KREW_X: from-file\n")
    probe = CREW / "scripts" / "_probe_env.py"
    probe.write_text("import os, importlib\nimportlib.import_module('krew_cut')\nprint(os.environ['KREW_RULERS'], os.environ['KREW_X'])\n")
    try:
        r = subprocess.run([sys.executable, str(RUN), "_probe_env.py"], capture_output=True, text=True,
                           env=_env(tmp_path, KREW_X="from-env"))
    finally:
        probe.unlink()
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["crissa,user", "from-env"]   # the file fills in; the environment wins


def test_launcher_reports_a_bad_settings_file(tmp_path):
    (tmp_path / "krew").mkdir()
    (tmp_path / "krew" / "crew.yaml").write_text("env: [unclosed\n")
    r = subprocess.run([sys.executable, str(RUN), "krew-review-route.py"], input="{}", capture_output=True, text=True,
                       env=_env(tmp_path))
    assert r.stdout.strip() == "{}"
    try:
        pytest.importorskip("yaml")
    except pytest.skip.Exception:
        return
    assert "does not parse" in r.stderr


def _hook(tmp_path, script, payload, **env):
    return subprocess.run([sys.executable, str(RUN), script], input=json.dumps(payload), capture_output=True,
                          text=True, env=_env(tmp_path, **env), timeout=60)


def test_review_route_names_a_reviewer_who_is_not_the_builder(tmp_path):
    r = _hook(tmp_path, "krew-review-route.py",
              {"tool_name": "kanban_request_review", "profile": "vera", "tool_input": {"task_id": "t_1", "reviewer": "vera"}})
    assert json.loads(r.stdout) == {"action": "modify", "args": {"task_id": "t_1", "reviewer": "steward"}}
    r = _hook(tmp_path, "krew-review-route.py",
              {"tool_name": "kanban_request_review", "profile": "bash", "tool_input": {"task_id": "t_1"}})
    assert json.loads(r.stdout)["args"]["reviewer"] == "vera"
    r = _hook(tmp_path, "krew-review-route.py", {"tool_name": "terminal", "tool_input": {}})
    assert r.stdout.strip() == "{}"


def test_close_clean_is_off_by_default_and_fails_open(tmp_path):
    r = _hook(tmp_path, "krew-close-clean.py", {"tool_name": "kanban_complete", "session_id": "s1"})
    assert r.returncode == 0 and r.stdout.strip() == "{}"
    r = _hook(tmp_path, "krew-close-clean.py", {"tool_name": "kanban_complete", "session_id": "s1"},
              KREW_CLOSE_CLEAN="enforce", HERMES_PROFILE="bash")
    assert r.returncode == 0 and r.stdout.strip() == "{}"     # no live repos configured: checks nothing


def test_tool_count_hook_is_a_no_op_reply(tmp_path):
    r = _hook(tmp_path, "krew-tool-count.py", {"tool_name": "terminal", "session_id": "s1"}, HERMES_PROFILE="bash")
    assert r.returncode == 0 and r.stdout.strip() in ("{}", "")


def test_every_unit_runs_a_shipped_script():
    for u in sorted((CREW / "units").glob("*.service")):
        optin = "# krew: opt-in" in u.read_text()      # runs a dependency krew does not ship (e.g. Hindsight)
        for line in u.read_text().splitlines():
            if line.startswith("ExecStart") and not optin:
                m = re.search(r"krew-crew-run (\S+)", line)
                assert m, f"{u.name}: ExecStart does not go through krew-crew-run: {line}"
                assert (CREW / "scripts" / m.group(1)).is_file(), f"{u.name}: {m.group(1)} does not ship"


def test_every_timer_has_its_service():
    for t in sorted((CREW / "units").glob("*.timer")):
        assert t.with_suffix(".service").is_file(), t.name


def test_every_settings_key_is_read_by_code():
    keys = set(re.findall(r"^\s*#?\s*(?:ref: )?([A-Z][A-Z0-9_]+):", (ROOT / "config" / "crew.example.yaml").read_text(), re.M))
    trees = [CREW, *(ROOT / "plugins" / n for n in ("team-crissa", "krew-kitting", "krew-skill-preload"))]
    code = "".join(p.read_text(encoding="utf-8") for t in trees for p in t.rglob("*")
                   if p.is_file() and p.suffix not in (".md", ".pyc"))
    missing = sorted(k for k in keys if k not in code)
    assert not missing, f"crew.example.yaml names keys no crew code reads: {missing}"


SILENT_CUTS = {   # the hidden-limits audit's sites in these files; each must stay gone
    "kanban-loop.py": [r"body\[:5500\]", r"question\[:1500\]", r"q\[:1500\]", r"frame\[:3800\]", r"card\.body\[:2000\]",
                       r"text\[:400\]"],
    "kanban-board-gate.py": [r"\[:5500\]", r"LIMIT 12", r"result\[:600\]", r"summary\[:1200\]"],
    "crissa-hands.py": [r"text\[-max\(", r"out\[-1200:\]", r"got\[-500:\]", r"text\[:3500\]"],
    "steward-sweep": [r'cut -c1-"\$1"'],
}


def test_no_silent_cuts_come_back():
    bad = []
    for name, pats in SILENT_CUTS.items():
        text = (CREW / "scripts" / name).read_text(encoding="utf-8")
        bad += [f"{name}: {p}" for p in pats if re.search(p, text)]
    assert not bad, bad


def test_installer_copies_writes_units_and_hooks_once(tmp_path):
    kh, hh, sd = tmp_path / "krew", tmp_path / "hermes", tmp_path / "units"
    (hh / "profiles" / "bash").mkdir(parents=True)
    (hh / "profiles" / "bash" / "config.yaml").write_text("model: x\nhooks:\n  pre_tool_call: []\n")
    args = [sys.executable, str(CREW / "scripts" / "krew-crew-install.py"), "--krew-home", str(kh),
            "--hermes-home", str(hh), "--units", "--systemd-dir", str(sd), "--profiles", "bash,crissa"]
    env = {**_env(tmp_path), "PATH": "/nonexistent"}       # no systemctl: nothing is reloaded or started
    for _ in range(2):
        r = subprocess.run(args, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
    assert (kh / "crew" / "scripts" / "kanban-loop.py").is_file()
    assert (kh / "crew.example.yaml").is_file() and not (kh / "crew.yaml").exists()
    unit = (sd / "krew-kanban-loop.service").read_text()
    assert str(kh) in unit and "%h/.krew" not in unit
    yaml = pytest.importorskip("yaml")
    cfg = yaml.safe_load((hh / "profiles" / "bash" / "config.yaml").read_text())
    cmds = [h["command"] for h in cfg["hooks"]["pre_tool_call"]]
    # the reference's list for bash, in its order (profile-kit.json hooks._order; the test sandbox since 2026-10-10)
    assert sorted(c.split()[-1].rsplit("/", 1)[-1] for c in cmds) == sorted([
        "krew-close-clean.py", "krew-review-route.py", "tier0_gate.py", "krew_test_sandbox.py", "crissa-tool-callout.py",
        "krew-tool-count.py"])
    assert cfg["hooks_auto_accept"] is True
    gate = next(h for h in cfg["hooks"]["pre_tool_call"] if h["command"].endswith("tier0_gate.py"))
    assert gate["fail_closed"] is True and gate["command"] == f"python3 {kh / 'rails' / 'tier0_gate.py'}"
    assert cfg["model"] == "x"
    crissa = yaml.safe_load((hh / "profiles" / "crissa" / "config.yaml").read_text())
    assert sorted(h["command"].split()[-1].rsplit("/", 1)[-1] for h in crissa["hooks"]["pre_tool_call"]) == sorted([
        "krew-review-route.py", "krew-tool-count.py", "tier0_gate.py", "crissa-tool-callout.py"])
    zgate = next(h for h in crissa["hooks"]["pre_tool_call"] if h["command"].endswith("tier0_gate.py"))
    assert zgate["matcher"].endswith("|browser") and zgate["fail_closed"] is True


def test_installer_dry_run_changes_nothing(tmp_path):
    r = subprocess.run([sys.executable, str(CREW / "scripts" / "krew-crew-install.py"), "--krew-home", str(tmp_path / "k"),
                        "--dry-run", "--units", "--systemd-dir", str(tmp_path / "u")], capture_output=True, text=True,
                       env=_env(tmp_path))
    assert r.returncode == 0 and not (tmp_path / "k").exists() and not (tmp_path / "u").exists()


@pytest.mark.parametrize("script", ["kanban-loop.py", "kanban-board-gate.py", "kanban-day-close.py",
                                    "kanban-chatter-atomic.py", "kanban-blast-radius.py", "kanban-salad-tripwire.py"])
def test_report_only_entry_points_have_dry_run(script):
    assert "--dry-run" in (CREW / "scripts" / script).read_text(encoding="utf-8")


def test_krew_yaml_reads_and_writes_without_pyyaml(tmp_path):
    """The pinned Hermes venv has ruamel.yaml, not PyYAML: crew code goes through krew_yaml."""
    ruamel = pytest.importorskip("ruamel.yaml")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys; sys.modules['yaml'] = None; sys.modules['hermes_yaml'] = None\n"
        f"sys.path.insert(0, {str(CREW / 'lib')!r})\n"
        "import krew_yaml as y\n"
        "d = y.safe_load('hooks:\\n  pre_tool_call: [{command: x}]\\n')\n"
        "assert d == {'hooks': {'pre_tool_call': [{'command': 'x'}]}}, d\n"
        "assert y.safe_load(y.safe_dump(d)) == d\n"
        "print('ok')\n")
    out = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr


def test_launcher_prefers_the_hermes_venv_python(tmp_path):
    src = (CREW / "scripts" / "krew-crew-run").read_text()
    assert 'env.get("KREW_PYTHON") or env.get("KREW_HERMES_PYTHON")' in src
