"""krew-install.py: places plugins and skills in a Hermes home, seeds a SOUL only where none exists."""
from __future__ import annotations

import os
import subprocess
import sys

from conftest import ROOT

SCRIPT = ROOT / "scripts" / "krew-install.py"


def run(*a, env=None):
    # never the Wall on this machine: the marry step looks for Crissa (2026-10-06)
    env = dict(os.environ if env is None else env)
    env.setdefault("ZOEYOS_HOME", "/nonexistent/zoeyos-home")
    env.setdefault("ZOEYOS_PLAYER", "http://127.0.0.1:9")
    return subprocess.run([sys.executable, str(SCRIPT), *a], capture_output=True, text=True, env=env)


def test_install_into_a_fresh_home(tmp_path):
    hh, kh = tmp_path / "hermes", tmp_path / "krew"
    r = run("--hermes-home", str(hh), "--krew-home", str(kh), "--souls", "zoey,ada,bash:bash-2")
    assert r.returncode == 0, r.stderr
    for p in sorted((ROOT / "plugins").iterdir()):
        if (p / "plugin.yaml").exists():
            assert (hh / "plugins" / p.name / "plugin.yaml").exists()
            assert not list((hh / "plugins" / p.name).rglob("__pycache__"))
            assert f"hermes plugins enable {p.name}" in r.stdout
    assert (hh / "skills" / "adversarial-gate" / "SKILL.md").exists()
    assert (kh / "krew.yaml").exists()
    soul = (hh / "profiles" / "bash-2" / "SOUL.md").read_text()
    assert soul.startswith("<!-- TRUNK") and "You are **Bash**" in soul
    assert "You are **Ada**" in (hh / "profiles" / "ada" / "SOUL.md").read_text()


def test_never_overwrites_without_force(tmp_path):
    hh, kh = tmp_path / "hermes", tmp_path / "krew"
    (hh / "profiles" / "zoey").mkdir(parents=True)
    (hh / "profiles" / "zoey" / "SOUL.md").write_text("mine")
    (hh / "plugins" / "krew-board").mkdir(parents=True)
    (hh / "plugins" / "krew-board" / "marker").write_text("mine")
    kh.mkdir()
    (kh / "krew.yaml").write_text("mine: true\n")
    r = run("--hermes-home", str(hh), "--krew-home", str(kh), "--souls", "zoey")
    assert r.returncode == 0
    assert (hh / "profiles" / "zoey" / "SOUL.md").read_text() == "mine"
    assert (hh / "plugins" / "krew-board" / "marker").exists() and "skipped" in r.stdout
    assert (kh / "krew.yaml").read_text() == "mine: true\n"


def test_dry_run_writes_nothing(tmp_path):
    r = run("--hermes-home", str(tmp_path / "h"), "--krew-home", str(tmp_path / "k"), "--dry-run")
    assert r.returncode == 0 and not (tmp_path / "h").exists() and not (tmp_path / "k").exists()


def test_unknown_plugin_name_is_refused(tmp_path):
    r = run("--hermes-home", str(tmp_path / "h"), "--only", "nope")
    assert r.returncode != 0 and "no such plugin" in (r.stderr + r.stdout)


def test_bridge_soul_goes_to_the_default_profile(tmp_path):
    """`main` is Hermes's default profile: its SOUL is $HERMES_HOME/SOUL.md, written without the crew TRUNK, and an
    existing one is kept."""
    hh, kh = tmp_path / "hermes", tmp_path / "krew"
    r = run("--hermes-home", str(hh), "--krew-home", str(kh), "--souls", "main", "--no-skills")
    assert r.returncode == 0, r.stderr
    soul = (hh / "SOUL.md").read_text()
    assert soul.startswith("# Identity") and "**Bridge**" in soul and "TRUNK" not in soul
    assert not (hh / "profiles" / "main").exists()
    (hh / "SOUL.md").write_text("mine")
    run("--hermes-home", str(hh), "--krew-home", str(kh), "--souls", "main", "--no-skills")
    assert (hh / "SOUL.md").read_text() == "mine"
