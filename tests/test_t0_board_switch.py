"""The crew does not switch the active kanban board (2026-09-27).

A probe card (t_4602219a) ran `hermes kanban boards create … --switch` ten times from 16:58, so
`kanban/current` named a one-card scratch board and every crew reader that follows it worked an
empty board for 90 minutes while 113 cards sat on the real one. Pins, on the REAL policy through
the real gate: the probe's own commands are REFUSED for a crew caller even from a shadow caller;
a scratch board made without --switch and named per call stays free; KREW_T0_BOARD_SWITCH=0 and
`default` (the owner) are free. And the board gate puts `current` back from `crew-today`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t0_policy  # noqa: E402
from t0_policy import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GATE = t0_policy.GATE
POLICY = t0_policy.POLICY
WORKER = {"HERMES_KANBAN_TASK": "t_test", "HERMES_PROFILE": "bash"}

REFUSED = [
    # the probe scripts, verbatim shapes (workspace t_6bbd2a21)
    'run "$HERMES" kanban boards switch "$BOARD_SLUG" > board-switch.log 2>&1',
    'run "$HERMES" kanban boards create "$SLUG" --name "Needs-Input Probe Scratch" --switch \\',
    'hermes kanban boards create "$SLUG" \\\n  --name x \\\n  --switch',
    'r = run_cli("boards", "switch", os.environ["BOARD_SLUG"])',
    'run_cli("boards", "create", slug, "--name", "Crash PID-Dead Probe Scratch", "--switch")',
    "hermes -p bash kanban boards use crash-scratch-1",
    "hermes kanban --board daily-2026-09-16 boards switch scratch",
    "echo crash-scratch-1 > ~/.hermes/kanban/current",
    "printf x | tee /home/crew/.hermes/kanban/current",
    "open('/home/crew/.hermes/kanban/current', 'w').write('x')",
]

FREE = [
    'hermes kanban boards create "$SLUG" --name "Probe Scratch"',
    "hermes kanban --board crash-scratch-1 create 'probe' --assignee bash",
    "HERMES_KANBAN_BOARD=crash-scratch-1 hermes kanban list",
    "hermes kanban boards show",
    "hermes kanban boards list",
    "cat ~/.hermes/kanban/current",
    "grep -n 'boards switch' docs/KANBAN-LOOPS.md",
]


def _gate(tmp_path, cmd, who=WORKER, extra=None, mode="shadow", tool="terminal", field="command"):
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_BOARD_SWITCH",
                        "HERMES_KANBAN_TASK", "HERMES_PROFILE", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"),
               KREW_T0_MODE=mode, KREW_USER_ID="alex", **(who or {}), **(extra or {}))
    payload = {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": {field: cmd}}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


def test_the_rule_ships_enforcing_with_its_own_kill():
    rules = [r for r in t0_policy.filled_rules()
             if r.get("env_off") == "KREW_T0_BOARD_SWITCH"]
    assert len(rules) == 1
    assert rules[0]["mode"] == "enforce" and rules[0]["scope"] == "crew" and rules[0]["route"] == "board"


@pytest.mark.parametrize("cmd", REFUSED)
def test_the_crew_is_refused_even_from_a_shadow_caller(tmp_path, cmd):
    out = _gate(tmp_path, cmd)
    assert out.get("decision") == "block", cmd
    assert "--board <slug>" in out["reason"]


def test_a_path_write_to_current_is_refused(tmp_path):
    out = _gate(tmp_path, "/home/crew/.hermes/kanban/current", tool="write_file", field="path")
    assert out.get("decision") == "block"


@pytest.mark.parametrize("cmd", FREE)
def test_a_scratch_board_named_per_call_stays_free(tmp_path, cmd):
    assert _gate(tmp_path, cmd, mode="enforce") == {}, cmd


def test_its_own_kill_and_the_user_are_free(tmp_path):
    for cmd in REFUSED[:2]:
        assert _gate(tmp_path, cmd, extra={"KREW_T0_BOARD_SWITCH": "0"}) == {}
        assert _gate(tmp_path, cmd, who={"HERMES_PROFILE": "default"}) == {}


def _board_gate(monkeypatch, home):
    spec = importlib.util.spec_from_file_location("kanban_board_gate_pin", ROOT / "scripts" / "kanban-board-gate.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    monkeypatch.setattr(g, "HERMES", home)
    monkeypatch.setattr(g, "LOG", home / "gate.log")
    return g


def _home(tmp_path, pin="daily-2026-09-16", current="crash-scratch-20260927-183411"):
    k = tmp_path / "kanban"
    for slug in ("daily-2026-09-16", "crash-scratch-20260927-183411"):
        (k / "boards" / slug).mkdir(parents=True)
        (k / "boards" / slug / "kanban.db").write_bytes(b"")
    (k / "crew-today").write_text(pin + "\n")
    (k / "current").write_text(current)
    return tmp_path






# The test fixtures that filed `t1` on the live board: a crew pytest in a hermes tree with the
# worker's HERMES_KANBAN_DB still set. ENFORCING since 09-27 ~23:15: the same runs dispatched real cards
# with the fixture's fake pid 12345 and crashed them (135 spawns, 59 real cards).
TEST_RUNS = [
    "cd /home/crew/projects/hermes-agent && .venv/bin/python -m pytest tests/hermes_cli/ -q -k 'kanban' 2>&1 | tail -8",
    "cd ~/hermes-worktrees/t_062e4737 && uv run pytest tests/hermes_cli -q",
    "cd /home/crew/projects/hermes-agent && timeout 60 uv run pytest tests/hermes_cli/test_kanban_core_functionality.py -q",
    "cd /home/crew/projects/crew-rails && /home/crew/.hermes/hermes-agent/venv/bin/python -m pytest tests/test_run_kanban_task.py -q",
    # env -u is not enough (23:36-23:41): only the wrapper's own HOME seals the live board off
    "cd /home/crew/projects/hermes-agent && env -u HERMES_KANBAN_DB -u HERMES_KANBAN_BOARD uv run pytest tests/hermes_cli -q",
    "cd ~/.hermes/profiles/steward/cache/scratch/wt-t_6bbd2a21 && UV_PROJECT_ENVIRONMENT=/home/crew/.hermes/hermes-agent/.venv uv run --no-sync pytest tests/hermes_cli -q",
]
TEST_RUNS_CLEAN = [
    "cd /home/crew/projects/hermes-agent && /home/crew/projects/crew-rails/scripts/kanban-env-clean .venv/bin/python -m pytest tests/hermes_cli -q",
    "cd /home/crew/projects/crew-rails && .venv/bin/python -m pytest tests/test_tier0_board_switch.py -q",
    "/home/crew/.hermes/hermes-agent/venv/bin/python3 -m pip install pytest -q",
    "cd /home/crew/projects/crew-rails && KREW_PROSE_LINT_ROOT=/home/crew/.hermes/hermes-agent .venv/bin/python -m pytest tests/test_lint_prose_phantom_refs.py -q",
]


def test_the_test_rule_ships_enforcing():
    rules = [r for r in t0_policy.filled_rules()
             if r.get("env_off") == "KREW_T0_KANBAN_ENV_TESTS"]
    assert len(rules) == 1 and rules[0]["mode"] == "enforce" and rules[0]["route"] == "kanban-env"


@pytest.mark.parametrize("cmd", TEST_RUNS)
def test_a_hermes_test_run_with_the_board_env_is_refused_with_the_fix(tmp_path, cmd):
    out = _gate(tmp_path, cmd)
    assert out.get("decision") == "block", cmd
    assert "kanban-env-clean" in out["reason"], cmd


def test_the_test_rule_has_its_own_kill(tmp_path):
    assert _gate(tmp_path, TEST_RUNS[0], extra={"KREW_T0_KANBAN_ENV_TESTS": "0"}) == {}
    assert _gate(tmp_path, TEST_RUNS[0], who={"HERMES_PROFILE": "default"}) == {}


@pytest.mark.parametrize("cmd", TEST_RUNS_CLEAN)
def test_a_clean_or_unrelated_test_run_hears_nothing(tmp_path, cmd):
    assert _gate(tmp_path, cmd, mode="enforce") == {}, cmd


