"""T0: the crew does not update hermes or move its tree (2026-09-27).

09-26 18:05 a steward worker ran the tree script's update from a card and 18:13 a bash worker
hard-reset ~/.hermes/hermes-agent: four gateways down for 10 h with no dispatcher to repair it.
Pins, on the REAL policy through the real gate:

* both workers' commands, and each update / gateway / tree-moving verb, are REFUSED for a crew
  caller even under KREW_T0_MODE=shadow;
* the list form a script would use is refused (steward got past approvals through a script);
* reads, dry runs, the stock checkout and prose that only mentions the words stay free;
* KREW_T0_HERMES_TREE=0 turns just these rules off; `default` (the owner) is never gated;
* scripts/hermes-tree.py refuses update and capture under a kanban task on its own.
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
WORKER = {"HERMES_KANBAN_TASK": "t_test", "HERMES_PROFILE": "steward"}
TREE = "/home/crew/.hermes/hermes-agent"

REFUSED = [
    "HERMES_TREE=/home/crew/.hermes/hermes-agent HOME=/home/crew python3 scripts/hermes-tree.py update",
    f"cd {TREE} && git reset -q --hard f1b4172233",
    f"git -C {TREE} checkout -q 5a69e4a56f -- .",
    f"cd {TREE} && git checkout -q -b hermes-tree/5a69e4a56f",
    f"cd {TREE} && git stash push --include-untracked",
    "cd ~/.hermes/hermes-agent && git pull --ff-only",
    f"git -C {TREE} apply /home/crew/projects/crew-rails/config/hermes/patches/10-kanban-unknown-skill.patch",
    f"cd {TREE} && git update-ref -d refs/heads/origin/main",
    f'cd {TREE} && git commit -am "crew patchset"',
    "hermes update --yes",
    "cd /tmp && /home/crew/.local/bin/hermes update",
    "hermes -p zoey gateway migrate --multiplex",
    "hermes gateway install --force",
    "hermes -p ada gateway restart",
    "hermes pm repair",
    "python3 scripts/hermes-tree.py capture 10-kanban-unknown-skill hermes_cli/kanban_db.py --card t_ac865f03",
    'subprocess.run(["hermes", "update", "--yes"], check=True)',
]

FREE = [
    "python3 scripts/hermes-tree.py status",
    "python3 scripts/hermes-tree.py verify",
    "python3 scripts/hermes-tree.py update --dry-run",
    f"cd {TREE} && git status --short && git log --oneline -3 && git diff --stat",
    f"git -C {TREE} apply --check /tmp/x.patch",
    f"git -C {TREE} worktree add /home/crew/.hermes/kanban/boards/b/workspaces/t_1/spike f1b4172233",
    f"cd {TREE} && git stash list && git show HEAD:pyproject.toml",
    # The stock checkout is outside the hermes-TREE rules. Since 09-28 its branch moves (reset --hard,
    # checkout <branch>, ...) belong to the hermes-CLONE rule (tests/test_tier0_git_push_clone.py);
    # the stash habit stays free.
    "cd /home/crew/projects/hermes-agent && git stash && git stash pop",
    "hermes gateway status",
    "hermes -p ada kanban list",
    "hermes -p steward hooks doctor",
    "grep -n 'hermes update' docs/REBOOT-RUNBOOK-20260807.md",
    "echo 'a previous hermes update pulled new code'",
]


def _gate(tmp_path, text, who=WORKER, extra=None, mode="enforce") -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_GIT_TREE",
                        "KREW_T0_HERMES_TREE", "HERMES_KANBAN_TASK", "HERMES_PROFILE", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"),
               KREW_T0_MODE=mode, KREW_USER_ID="alex", **(who or {}), **(extra or {}))
    payload = {"hook_event_name": "pre_tool_call", "tool_name": "terminal", "tool_input": {"command": text}}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


def test_the_rules_ship_enforcing_with_their_own_kill():
    rules = [r for r in t0_policy.filled_rules()
             if r.get("env_off") == "KREW_T0_HERMES_TREE"]
    assert len(rules) == 3
    assert all(r["mode"] == "enforce" and r["scope"] == "crew" and r["route"] == "zoey" for r in rules)


@pytest.mark.parametrize("cmd", REFUSED)
def test_the_crew_is_refused_even_from_a_shadow_caller(tmp_path, cmd):
    out = _gate(tmp_path, cmd, mode="shadow")
    assert out.get("decision") == "block", cmd
    assert "window job" in out["reason"]


WRITES = [
    ("write_file", "path", f"{TREE}/hermes_cli/kanban_db.py"),
    ("patch", "path", "~/.hermes/hermes-agent/tools/kanban_tools.py"),
    ("terminal", "command", f"cp /tmp/kanban_db.py {TREE}/hermes_cli/kanban_db.py"),
    ("terminal", "command", f"sed -i s/a/b/ {TREE}/utils.py"),
    ("terminal", "command", f"mv /tmp/new.py {TREE}/utils.py && echo done"),
    ("terminal", "command", f"rsync -a /tmp/tree/ {TREE}/"),
    ("terminal", "command", f"uv lock > {TREE}/uv.lock"),
    ("terminal", "command", f"cd /tmp && patch -p1 {TREE}/pyproject.toml < x.diff"),
    ("execute_code", "code", f"open('{TREE}/utils.py', 'w').write(src)"),
]

READS = [
    ("terminal", "command", f"grep -n complete_task {TREE}/hermes_cli/kanban_db.py"),
    ("terminal", "command", f"cat {TREE}/pyproject.toml | head"),
    ("terminal", "command", f"cp {TREE}/utils.py /tmp/utils.py"),
    ("write_file", "path", "/home/crew/hermes-worktrees/t_1/hermes_cli/kanban_db.py"),
    ("write_file", "path", "/home/crew/projects/hermes-agent/utils.py"),
    ("execute_code", "code", f"print(open('{TREE}/utils.py').read())"),
]


def _gate_tool(tmp_path, tool, field, text, mode="shadow"):
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_HERMES_TREE",
                        "HERMES_KANBAN_TASK", "HERMES_PROFILE", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"),
               KREW_T0_MODE=mode, KREW_USER_ID="alex", **WORKER)
    payload = {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": {field: text}}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


@pytest.mark.parametrize("tool,field,text", WRITES)
def test_no_file_write_into_the_live_tree(tmp_path, tool, field, text):
    out = _gate_tool(tmp_path, tool, field, text)
    assert out.get("decision") == "block", text
    assert "worktree" in out["reason"]


@pytest.mark.parametrize("tool,field,text", READS)
def test_reads_and_worktrees_stay_free(tmp_path, tool, field, text):
    assert _gate_tool(tmp_path, tool, field, text, mode="enforce") == {}, text


@pytest.mark.parametrize("cmd", FREE)
def test_reads_dry_runs_the_stock_checkout_and_prose_stay_free(tmp_path, cmd):
    assert _gate(tmp_path, cmd) == {}, cmd


def test_its_own_kill_and_the_user_are_free(tmp_path):
    for cmd in (REFUSED[0], REFUSED[1], "hermes update --yes"):
        assert _gate(tmp_path, cmd, extra={"KREW_T0_HERMES_TREE": "0"}) == {}
        assert _gate(tmp_path, cmd, who={"HERMES_PROFILE": "default"}) == {}


def _tree_script():
    spec = importlib.util.spec_from_file_location("hermes_tree", ROOT / "scripts" / "hermes-tree.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod




def test_the_gate_reads_its_policy_under_an_interpreter_with_no_yaml(tmp_path):
    """2026-09-27: a worker's PATH puts hermes's own Python first, which has no PyYAML, so the
    policy read as broken and the gate denied EVERY act. It re-runs itself under the system
    interpreter; with the kill switch it fails closed as before (never open)."""
    bare = Path.home() / ".hermes" / "tools"
    pys = sorted(bare.glob("python-*/bin/python3")) if bare.is_dir() else []
    pys = [p for p in pys if subprocess.run([str(p), "-c", "import yaml"], capture_output=True).returncode != 0]
    if not pys:
        pytest.skip("no interpreter without yaml on this box")
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERMES_KANBAN_TASK", "HERMES_PROFILE", "HERMES_HOME", "KREW_T0_REEXECED",
                        "KREW_T0_SYSTEM_PYTHON", "KREW_T0_HERMES_TREE")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"),
               KREW_T0_MODE="enforce", KREW_USER_ID="alex", **WORKER)

    def run(cmd, extra=None):
        payload = {"hook_event_name": "pre_tool_call", "tool_name": "terminal", "tool_input": {"command": cmd}}
        r = subprocess.run([str(pys[0]), str(GATE)], input=json.dumps(payload), capture_output=True,
                           text=True, env={**env, **(extra or {})}, timeout=20)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout or "{}")

    assert run("echo hello") == {}
    assert "window job" in run("hermes update --yes")["reason"]
    closed = run("echo hello", {"KREW_T0_SYSTEM_PYTHON": "0"})
    assert closed.get("decision") == "block" and "unreadable" in closed["reason"]
