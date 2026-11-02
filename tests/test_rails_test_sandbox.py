"""A crew test run cannot open the live board (2026-09-27; lifted with rails/krew_test_sandbox.py).

The last test RUNS the wrapped command for real against a stand-in board under tmp_path: a
write aimed at the "live" file lands in the scratch and the file is untouched. No live path
is used by any test here.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CREW_WITH_A_SHELL = ("bash", "steward", "vera")


@pytest.fixture
def sb(monkeypatch, tmp_path):
    for k in ("KREW_TEST_SANDBOX", "HERMES_HOME", "HERMES_PROFILE", "HERMES_KANBAN_TASK"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("krew_test_sandbox_under_test",
                                                  ROOT / "rails" / "krew_test_sandbox.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    board = tmp_path / "hermes" / "kanban"
    (board / "boards" / "daily-x").mkdir(parents=True)
    (board / "current").write_text("daily-x\n")
    (board / "boards" / "daily-x" / "kanban.db").write_text("LIVE")
    mod.kw = {"board_dir": board, "root": tmp_path / "scratch", "which": lambda n: "/usr/bin/bwrap"}
    return mod


def _call(sb, command, profile="bash", tool="terminal", **extra):
    return sb.decide({"tool_name": tool, "profile": profile, "args": {"command": command, **extra}}, **sb.kw)


@pytest.mark.parametrize("command", [
    "cd ~/projects/hermes-agent && .venv/bin/python -m pytest tests/hermes_cli/ -q -k 'kanban' 2>&1 | tail -8",
    "cd /home/crew/projects/hermes-agent && uv run --with pytest pytest tests/hermes_cli/ -q -k kanban 2>&1 | tail -8",
    "timeout 300 python3 -m pytest tests/test_x.py -q",
    "pytest -q",
    "git stash -q && uv run pytest tests/a.py; git stash pop -q",
    "python -m unittest discover",
    "bash -c 'cd x && pytest -q'",
])
def test_a_test_run_is_sandboxed_and_the_command_is_kept_whole(sb, command):
    answer, row = _call(sb, command, timeout=600, background=True)
    assert answer["action"] == "modify" and row["did"] == "sandboxed"
    new = answer["args"]["command"]
    assert new.startswith("/usr/bin/bwrap --dev-bind / / --bind ") and new.endswith(sb.shlex.quote(command))
    assert f" {sb.kw['board_dir']} -- /bin/bash -c " in new
    assert answer["args"]["timeout"] == 600 and answer["args"]["background"] is True


@pytest.mark.parametrize("command", [
    "grep -i 'randomly\\|pytest' pyproject.toml",
    "ls tests/ && cat tests/conftest.py | head",
    "git diff HEAD -- tests/hermes_cli/test_kanban_blocked_sticky.py",
    "python3 -c \"import pytest_helper\"",
    "hermes -p steward kanban list",
    "cat docs/pytest-notes.md",
])
def test_a_command_that_runs_no_test_is_not_touched_and_not_logged(sb, command):
    assert _call(sb, command) == ({}, {})


def test_the_users_own_profile_and_other_tools_are_left_alone(sb):
    answer, row = _call(sb, "pytest -q", profile="default")
    assert answer == {} and row["why"] == "the default profile is the owner's"
    assert _call(sb, "pytest -q", tool="execute_code") == ({}, {})
    assert sb.decide({"tool_name": "terminal", "profile": "bash", "args": "pytest"}, **sb.kw) == ({}, {})


def test_it_never_wraps_twice_and_says_when_it_cannot(sb):
    first, _ = _call(sb, "pytest -q")
    again, row = _call(sb, first["args"]["command"])
    assert again == {} and row["why"] == "already sandboxed"
    sb.kw["which"] = lambda n: None
    answer, row = _call(sb, "pytest -q")
    assert answer == {} and "bwrap is not installed" in row["why"]


def test_the_kill_switch_and_report_only(sb, monkeypatch):
    monkeypatch.setenv("KREW_TEST_SANDBOX", "0")
    assert _call(sb, "pytest -q") == ({}, {})
    monkeypatch.setenv("KREW_TEST_SANDBOX", "report")
    answer, row = _call(sb, "pytest -q")
    assert answer == {} and row["did"] == "would sandbox"


def test_the_profile_is_the_served_one(sb, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "/home/x/.hermes/profiles/steward")
    assert sb.profile_of({}) == "steward" and sb.profile_of({"profile": "vera"}) == "vera"
    monkeypatch.setenv("HERMES_HOME", "/home/x/.hermes")
    assert sb.profile_of({}) == "default"


def test_the_hook_never_fails_a_call(sb, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sb, "LOG", tmp_path / "log.jsonl")
    monkeypatch.setattr(sb.sys, "stdin", type("S", (), {"read": lambda self: "not json"})())
    assert sb.main() == 0 and capsys.readouterr().out.strip() == "{}"
    assert "JSONDecodeError" in (tmp_path / "log.jsonl").read_text()


def _bwrap_works() -> bool:
    """bwrap is installed AND may create a namespace here (inside another sandbox, or on a kernel without
    unprivileged user namespaces, it is installed and refuses: that is the host, not the hook)."""
    if not shutil.which("bwrap"):
        return False
    try:
        return subprocess.run([shutil.which("bwrap"), "--dev-bind", "/", "/", "true"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _bwrap_works(), reason="bwrap is not installed or may not create a namespace here")
def test_inside_the_sandbox_the_live_board_cannot_be_written(sb):
    sb.kw["which"] = shutil.which
    db = sb.kw["board_dir"] / "boards" / "daily-x" / "kanban.db"
    # the word pytest makes it a test run; the command then writes where the live board lives
    command = f": pytest; cat {db} 2>/dev/null; echo FAKE > {db}; cat {sb.kw['board_dir']}/current"
    answer, _ = _call(sb, f"python3 -m pytest --version >/dev/null 2>&1; {command}")
    out = subprocess.run(["/bin/bash", "-c", answer["args"]["command"]], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "LIVE" not in out.stdout and out.stdout.strip().endswith("daily-x")   # it still learns WHICH board
    assert db.read_text() == "LIVE"                                              # the live file is untouched
    assert (sb.kw["root"] / "bash" / "kanban" / "boards" / "daily-x" / "kanban.db").read_text().strip() == "FAKE"


# (the crew's own profile check, every shell profile runs the sandbox after the gate, is krew_hooks' job here:
#  tests/test_rails_install.py::test_the_sandbox_runs_after_the_gate_on_the_shell_profiles)
