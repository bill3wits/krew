"""krew security rails: the install step, the hook writer, prodgate, prod-guard, and the shipped policy.

Everything runs in tmp_path: a fake Hermes home with profile configs, a fake $KREW_HOME. Nothing live is read.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from t0_policy import yaml  # noqa: E402


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str((ROOT / rel).parent))
    spec.loader.exec_module(mod)
    return mod


hooks = _load("krew_hooks_under_test", "rails/krew_hooks.py")

CONFIG = """# my profile
model:
  default: some-model
toolsets: [terminal, file]
hooks:
  pre_tool_call:
    - matcher: ^browser$
      command: python3 /opt/mine.py
      timeout: 3
  post_tool_call:
    - matcher: .*
      command: python3 /opt/after.py
"""


def _home(tmp_path, profiles=("zoey", "bash", "ada"), text=CONFIG):
    hh = tmp_path / "hermes"
    (hh).mkdir()
    (hh / "config.yaml").write_text("model:\n  default: x\n")
    for p in profiles:
        (hh / "profiles" / p).mkdir(parents=True)
        (hh / "profiles" / p / "config.yaml").write_text(text)
    return hh


def _say(lines):
    return lambda s: lines.append(s)


def test_the_hook_block_lands_in_every_profile_and_keeps_theirs(tmp_path):
    hh = _home(tmp_path)
    rails = tmp_path / "krew" / "rails"
    for name, path in hooks.profiles(hh).items():
        assert hooks.install_one(name, path, rails, auto_accept=True, dry=False, say=_say([]), cfg={})
    for name in ("zoey", "bash", "ada"):
        text = (hh / "profiles" / name / "config.yaml").read_text()
        doc = yaml.safe_load(text)
        pre = doc["hooks"]["pre_tool_call"]
        cmds = [h["command"] for h in pre]
        assert f"python3 {rails / 'tier0_gate.py'}" in cmds and "python3 /opt/mine.py" in cmds
        gate = next(h for h in pre if "tier0_gate.py" in h["command"])
        assert gate["fail_closed"] is True and gate["timeout"] == 10
        assert doc["hooks"]["post_tool_call"][0]["command"] == "python3 /opt/after.py"
        assert doc["hooks_auto_accept"] is True and text.startswith("# my profile")
        assert list((hh / "profiles" / name).glob("config.yaml.krew-bak-*")), "backed up first"
    zoey = yaml.safe_load((hh / "profiles" / "zoey" / "config.yaml").read_text())["hooks"]["pre_tool_call"]
    assert any(h["matcher"] == "terminal|execute_code|write_file|patch|browser" for h in zoey)
    default = yaml.safe_load((hh / "config.yaml").read_text())
    assert default["hooks"]["pre_tool_call"][0]["matcher"].endswith("|computer_use")


def test_the_sandbox_runs_after_the_gate_on_the_shell_profiles(tmp_path):
    hh = _home(tmp_path, profiles=("bash", "steward", "vera", "lila"))
    rails = tmp_path / "rails"
    for name, path in hooks.profiles(hh).items():
        hooks.install_one(name, path, rails, auto_accept=True, dry=False, say=_say([]), cfg={})
    for name in ("bash", "steward", "vera"):
        text = (hh / "profiles" / name / "config.yaml").read_text()
        assert text.index("tier0_gate.py") < text.index("krew_test_sandbox.py"), f"{name}: the gate judges first"
    lila = (hh / "profiles" / "lila" / "config.yaml").read_text()
    assert "krew_test_sandbox.py" not in lila and "browser_navigate" in lila


def test_a_second_install_changes_nothing(tmp_path):
    hh = _home(tmp_path, profiles=("bash",))
    rails = tmp_path / "rails"
    path = hh / "profiles" / "bash" / "config.yaml"
    hooks.install_one("bash", path, rails, auto_accept=True, dry=False, say=_say([]), cfg={})
    first = path.read_text()
    said = []
    hooks.install_one("bash", path, rails, auto_accept=True, dry=False, say=_say(said), cfg={})
    assert path.read_text() == first
    assert any("kept existing tier0_gate.py" in s for s in said)


def test_an_existing_gate_elsewhere_is_kept_not_doubled(tmp_path):
    text = "hooks:\n  pre_tool_call:\n  - matcher: terminal\n    command: python3 /srv/security/tier0_gate.py\n    timeout: 10\n"
    hh = _home(tmp_path, profiles=("ada",), text=text)
    path = hh / "profiles" / "ada" / "config.yaml"
    hooks.install_one("ada", path, tmp_path / "rails", auto_accept=False, dry=False, say=_say([]), cfg={})
    doc = yaml.safe_load(path.read_text())
    assert [h["command"] for h in doc["hooks"]["pre_tool_call"]] == ["python3 /srv/security/tier0_gate.py"]


def test_no_hooks_key_and_an_empty_mapping_both_work(tmp_path):
    for text in ("model: {default: x}\n", "model: {default: x}\nhooks: {}\n", "hooks:\n  post_tool_call: []\n"):
        hh = tmp_path / str(abs(hash(text)))
        (hh / "profiles" / "bash").mkdir(parents=True)
        path = hh / "profiles" / "bash" / "config.yaml"
        path.write_text(text)
        assert hooks.install_one("bash", path, tmp_path / "rails", auto_accept=True, dry=False, say=_say([]), cfg={})
        doc = yaml.safe_load(path.read_text())
        assert {h["command"].rsplit("/", 1)[1] for h in doc["hooks"]["pre_tool_call"]} == {
            "tier0_gate.py", "krew_test_sandbox.py"}, text


def test_an_unreadable_config_is_left_alone_and_the_block_printed(tmp_path):
    hh = _home(tmp_path, profiles=("bash",), text="hooks: [unclosed\n")
    path = hh / "profiles" / "bash" / "config.yaml"
    said = []
    assert not hooks.install_one("bash", path, tmp_path / "rails", auto_accept=True, dry=False, say=_say(said), cfg={})
    assert path.read_text() == "hooks: [unclosed\n" and any("tier0_gate.py" in s for s in said)


def test_check_names_a_profile_without_the_gate(tmp_path):
    hh = _home(tmp_path, profiles=("bash",))
    rails = tmp_path / "rails"
    assert hooks.check_one("bash", hh / "profiles" / "bash" / "config.yaml", rails, cfg={})
    hooks.install_one("bash", hh / "profiles" / "bash" / "config.yaml", rails, auto_accept=True, dry=False,
                      say=_say([]), cfg={})
    assert hooks.check_one("bash", hh / "profiles" / "bash" / "config.yaml", rails, cfg={}) == []


def test_krew_install_places_rails_settings_units_and_hooks(tmp_path):
    hh = _home(tmp_path, profiles=("bash", "zoey"))
    kh = tmp_path / "krew"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-install.py"), "--hermes-home", str(hh),
                        "--krew-home", str(kh), "--hooks", "--no-skills", "--only", "krew-board"],
                       capture_output=True, text=True, env={**os.environ, "KREW_HOME": str(kh)}, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (kh / "rails" / "tier0_gate.py").is_file() and (kh / "janitor").is_dir()
    for f in ("tier0.yaml", "tier0.example.yaml", "rails.yaml", "rails.example.yaml"):
        assert (kh / f).is_file(), f
    assert "python3 " + str(kh / "rails" / "tier0_gate.py") in (hh / "profiles" / "bash" / "config.yaml").read_text()
    units = sorted(p.name for p in (kh / "systemd" / "user").iterdir())
    assert "krew-janitor.timer" in units and "krew-janitor.service" in units
    assert "@KREW_HOME@" not in (kh / "systemd" / "user" / "krew-janitor.service").read_text()
    lint = subprocess.run([sys.executable, str(kh / "rails" / "tier0_gate.py"), "--lint"], capture_output=True,
                          text=True, env={**os.environ, "KREW_HOME": str(kh)}, timeout=60)
    assert lint.returncode == 0 and ": ok, mode shadow" in lint.stdout, lint.stdout + lint.stderr
    (kh / "tier0.yaml").write_text("# mine\n")
    r2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-install.py"), "--hermes-home", str(hh),
                         "--krew-home", str(kh), "--no-skills", "--only", "krew-board"], capture_output=True, text=True,
                        env={**os.environ, "KREW_HOME": str(kh)}, timeout=120)
    assert r2.returncode == 0 and (kh / "tier0.yaml").read_text() == "# mine\n", "yours is never overwritten"


def test_a_moved_krew_home_is_written_into_the_seeded_settings(tmp_path):
    hh = _home(tmp_path, profiles=("bash",))
    kh = tmp_path / "elsewhere" / "krew"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-install.py"), "--hermes-home", str(hh),
                        "--krew-home", str(kh), "--no-skills", "--only", "krew-board"],
                       capture_output=True, text=True, env={**os.environ, "KREW_HOME": str(kh)}, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    for live in ("tier0.yaml", "rails.yaml", "janitor.yaml"):
        text = (kh / live).read_text()
        assert "~/.krew" not in text, (live, [ln for ln in text.splitlines() if "~/.krew" in ln])
        assert yaml.safe_load(text) is not None
    t0 = yaml.safe_load((kh / "tier0.yaml").read_text())
    assert t0["vars"]["KREW_HOME_DIR"] == str(kh.resolve())
    jan = yaml.safe_load((kh / "janitor.yaml").read_text())
    assert jan["paths"]["metrics_dir"] == str(kh.resolve() / "janitor" / "metrics")
    assert jan["paths"]["lib_dirs"] == [str(ROOT / "window" / "lib")], "the Janitor's helpers: the window lib"
    assert jan["paths"]["reboot_stamp"] == str(ROOT / "window" / "scripts" / "krew-reboot-stamp.py")
    assert "{krew}" not in (kh / "janitor.yaml").read_text()
    assert "~/.krew" in (kh / "janitor.example.yaml").read_text(), "the example copy stays krew's, byte for byte"


def test_the_shipped_policy_lints_and_says_what_waits_on_a_var(tmp_path):
    env = {**os.environ, "KREW_HOME": str(tmp_path), "KREW_T0_POLICY": str(ROOT / "config" / "tier0.example.yaml")}
    out = subprocess.run([sys.executable, str(ROOT / "rails" / "tier0_gate.py"), "--lint"], capture_output=True,
                         text=True, env=env, timeout=60).stdout
    assert ": ok, mode shadow," in out
    active = int(out.split(" active rule")[0].rsplit(" ", 1)[1])
    assert active >= 40, out
    for line in out.splitlines()[1:]:
        assert "INACTIVE (" in line or "PARTIAL" in line, line
        assert "bad regex" not in line


def test_the_shipped_policy_names_no_machine():
    text = (ROOT / "config" / "tier0.example.yaml").read_text()
    doc = yaml.safe_load(text)
    assert doc["mode"] == "shadow" and len(doc["blocklist"]) == 55
    assert all(str(r.get("env_off", "KREW_T0_")).startswith("KREW_T0_") for r in doc["blocklist"])
    assert all(v not in str(doc["vars"]) for v in ("LIVE_TREE", "PACKS_DIR")), "machine values stay commented"


# prodgate --------------------------------------------------------------------------------------------------------

def _pg(tmp_path, *args, ok=True, scratch=None):
    env = {**os.environ, "PRODGATE_ROOT": str(tmp_path / "gate"), "KREW_HOME": str(tmp_path / "krew"),
           "PRODGATE_SCRATCH_PREFIXES": scratch or str(tmp_path / "scratch") + "/"}
    r = subprocess.run([sys.executable, str(ROOT / "rails" / "krew-prodgate.py"), *args], capture_output=True,
                       text=True, env=env, timeout=60)
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def test_prodgate_stage_review_approve_apply_round_trip(tmp_path):
    live = tmp_path / "prod" / "tool.py"
    live.parent.mkdir()
    live.write_text("print('v1')\n")
    _pg(tmp_path, "stage", "t_1", str(live))
    staged = Path(_pg(tmp_path, "path", "t_1", str(live)).stdout.strip())
    staged.write_text("print('v2')\n")
    assert "v2" in _pg(tmp_path, "diff", "t_1").stdout
    assert _pg(tmp_path, "apply", "t_1", ok=False).returncode != 0, "nothing lands unapproved"
    assert live.read_text() == "print('v1')\n"
    _pg(tmp_path, "approve", "t_1", "--by", "vera", "--note", "reviewed")
    _pg(tmp_path, "apply", "t_1")
    assert live.read_text() == "print('v2')\n"


def test_prodgate_refuses_a_scratch_target(tmp_path):
    r = _pg(tmp_path, "stage", "t_2", "/tmp/somewhere/x.py", ok=False, scratch="/tmp/")
    assert r.returncode != 0 and "not a production" in (r.stderr + r.stdout)


def test_prodgate_ships_its_checklist():
    text = (ROOT / "rails" / "prodgate-checklist.md").read_text()
    assert "PRE-FLIGHT" in text.upper() and "4.4" in text


# prod-guard ------------------------------------------------------------------------------------------------------

def test_prod_guard_status_lists_the_set_and_what_is_missing(tmp_path):
    root = tmp_path / "repo"
    (root / "display").mkdir(parents=True)
    (root / "display" / "wall.html").write_text("<html/>")
    kh = tmp_path / "krew"
    kh.mkdir()
    (kh / "rails.yaml").write_text(f"prod_guard:\n  root: {root}\n  files: [display/wall.html, scripts/gone.py]\n")
    r = subprocess.run([sys.executable, str(ROOT / "rails" / "krew-prod-guard.py"), "status"], capture_output=True,
                       text=True, env={**os.environ, "KREW_HOME": str(kh)}, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "wall.html" in r.stdout and "writable" in r.stdout and "MISSING" in r.stdout and "0 of 1 protected" in r.stdout


def test_prod_guard_with_nothing_listed_says_so(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "rails" / "krew-prod-guard.py"), "status"], capture_output=True,
                       text=True, env={**os.environ, "KREW_HOME": str(tmp_path)}, timeout=60)
    assert "no files listed" in r.stdout


def test_krew_settings_copies_beside_the_rails_match_the_plugins():
    first = (ROOT / "plugins" / "krew-board" / "krew_settings.py").read_bytes()
    for d in ("rails", "janitor"):
        assert (ROOT / d / "krew_settings.py").read_bytes() == first, d


def test_every_script_with_a_shebang_is_executable():
    """A systemd drop-in (krew-unit-event-log.sh) and the crystal check run these by path: a lost exec bit is a
    silent no-op there (ExecStartPre=- swallows the failure)."""
    lost = [str(f.relative_to(ROOT)) for d in ("janitor", "rails", "scripts") for f in sorted((ROOT / d).iterdir())
            if f.is_file() and f.read_bytes()[:2] == b"#!" and not os.access(f, os.X_OK)]
    assert not lost, lost
