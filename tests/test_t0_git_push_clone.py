"""T0: the crew does not push, and does not move the upstream hermes clone off main (2026-09-28).

The owner: "prevent accidental git push ... fix T0 if that fix will not disable other functions".
Pins, on the REAL policy through the real gate, with the crew's own recorded commands: every real
push and every real branch move in ~/projects/hermes-agent is refused even from a shadow caller;
the workflows the crew uses there every day (stash push/pop, checkout -- <path>, commit, reads,
worktree add) and `push --dry-run` stay free; each rule has its own kill; `default` is free.
"""
from __future__ import annotations

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
CLONE = "/home/crew/projects/hermes-agent"

PUSHES = [
    "cd /tmp/crypto-test-49 && git push fork test/cryptography-49.0.0-upgrade 2>&1",
    "git -C /home/crew/projects/crew-rails push origin main 2>&1 | tail -8",
    "git log --oneline -1 && git push -q origin test-aiohttp-3.14.2 2>&1 | tail -2",
    "cd ~/hermes-worktrees/t_1 && git -c user.name=bash push --force origin HEAD",
    "echo x | git push origin main",
    'subprocess.run(["git", "push", "origin", "main"], check=True)',
]
PUSH_FREE = [
    "git push --dry-run origin main",
    "git status && git log --oneline -3",
    "grep -nE 'not pushed|git push|branch' /tmp/log.txt",
    "echo 'remember: nothing is pushed until the owner publishes'",
    "git remote get-url --push origin",
]
MOVES = [
    f"cd {CLONE} && git checkout -b kanban/crash-status-class",
    f"cd {CLONE} && git checkout -q 36d4d9ee6d && git status --porcelain | head -8",
    f"cd {CLONE} && git stash -q; git checkout -q HEAD~1; ls",
    f"cd {CLONE} && git switch -c fix/x",
    f"git -C {CLONE} reset --hard origin/main",
    f"cd ~/projects/hermes-agent && git rebase origin/main",
    f"git -C {CLONE} branch -D kanban/old",
]
CLONE_FREE = [
    f"cd {CLONE} && git stash push -m vera-review -- hermes_cli/kanban_db.py && /home/crew/projects/crew-rails/scripts/kanban-env-clean .venv/bin/pytest -q; git stash pop",
    f"cd {CLONE} && git checkout -- hermes_cli/kanban_db.py",
    f"cd {CLONE} && git checkout HEAD~1 -- tests/hermes_cli/test_kanban_db.py",
    f'cd {CLONE} && git commit -m "kanban: a fix"',
    f"cd {CLONE} && git merge-base HEAD origin/main && git log --oneline -3 && git diff --stat",
    f"git -C {CLONE} worktree add ~/hermes-worktrees/t_1 -b t_1 origin/main",
    f"git -C {CLONE} reset --soft HEAD~1",
    "cd ~/hermes-worktrees/t_1 && git checkout -b feature && git rebase origin/main",
    "cd /home/crew/projects/hermes-agent-fork && git checkout -b other",
]


def _gate(tmp_path, cmd, who=WORKER, extra=None, mode="shadow", tool="terminal", field="command"):
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_GIT_PUSH",
                        "KREW_T0_HERMES_CLONE", "KREW_T0_GIT_TREE",
                        "HERMES_KANBAN_TASK", "HERMES_PROFILE", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"),
               KREW_T0_MODE=mode, KREW_USER_ID="alex", **(who or {}), **(extra or {}))
    payload = {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": {field: cmd}}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


def _rule(env_off):
    rules = [r for r in t0_policy.filled_rules() if r.get("env_off") == env_off]
    assert len(rules) == 1
    return rules[0]


def test_both_rules_ship_enforcing_with_their_own_kill():
    assert _rule("KREW_T0_GIT_PUSH")["mode"] == "enforce" and _rule("KREW_T0_GIT_PUSH")["route"] == "push"
    assert _rule("KREW_T0_HERMES_CLONE")["mode"] == "enforce" and _rule("KREW_T0_HERMES_CLONE")["route"] == "clone"
    assert all(_rule(k)["scope"] == "crew" for k in ("KREW_T0_GIT_PUSH", "KREW_T0_HERMES_CLONE"))


@pytest.mark.parametrize("cmd", PUSHES)
def test_a_crew_push_is_refused_anywhere(tmp_path, cmd):
    tool, field = ("execute_code", "code") if cmd.startswith("subprocess") else ("terminal", "command")
    out = _gate(tmp_path, cmd, tool=tool, field=field)
    assert out.get("decision") == "block", cmd              # the crew-rails git rules name push too
    assert "the owner publishes" in out["reason"] or "crew-rails" in out["reason"] or "does not say where" in out["reason"]


@pytest.mark.parametrize("cmd", PUSH_FREE)
def test_dry_runs_reads_and_prose_are_free(tmp_path, cmd):
    assert _gate(tmp_path, cmd, mode="enforce") == {}, cmd


@pytest.mark.parametrize("cmd", MOVES)
def test_a_branch_move_in_the_hermes_clone_is_refused(tmp_path, cmd):
    out = _gate(tmp_path, cmd)
    assert out.get("decision") == "block", cmd
    assert "worktree add" in out["reason"]


@pytest.mark.parametrize("cmd", CLONE_FREE)
def test_the_clone_workflows_stay_free(tmp_path, cmd):
    assert _gate(tmp_path, cmd, mode="enforce") == {}, cmd


def test_a_push_from_a_worktree_or_tmp_names_this_rule(tmp_path):
    for cmd in (PUSHES[0], PUSHES[3]):
        assert "the owner publishes" in _gate(tmp_path, cmd)["reason"], cmd


def test_each_kill_and_the_user_are_free(tmp_path):
    assert _gate(tmp_path, PUSHES[0], extra={"KREW_T0_GIT_PUSH": "0"}) == {}
    assert _gate(tmp_path, MOVES[0], extra={"KREW_T0_HERMES_CLONE": "0"}) == {}
    for cmd in (PUSHES[0], MOVES[0]):
        assert _gate(tmp_path, cmd, who={"HERMES_PROFILE": "default"}) == {}
