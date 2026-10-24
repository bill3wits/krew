"""T0: the crew does not move the live tree (2026-09-24).

09-23 17:53 a bash worker ran `git stash push && git checkout -b … main && git cherry-pick`
in ~/projects/crew-rails, and every patrol (which execs from that folder) failed for 7 h.
The rules were report-only until a real bash agent fired both (09-24 02:16:48/49); they
ENFORCE now, route `worktree`. Pins, on the REAL policy through the real gate (JSON on stdin):

* last night's command and each tree-moving verb is REFUSED for a crew caller even when the
  caller carries KREW_T0_MODE=shadow (a crew run from a timer or the CLI does) — which is
  why the switch is `enforce`, never deleting the field;
* a move that names no folder (a shell already sitting in the repo) is its own rule;
* the refusal says where the work goes (a worktree, a pathspec commit), not "ask Zoey";
* `mode: report` still records without blocking (kept for the next new rule);
* reads, pathspec commits, worktrees and other repos stay free;
* a report-only rule never masks an enforcing rule after it;
* KREW_T0_GIT_TREE=0 turns just this rule off; `default` (the owner) is never gated.
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
REPO = "/home/crew/projects/crew-rails"

LAST_NIGHT = (f'cd {REPO} && git stash push -m "pre-rebase-sweep-ram-guard-$(date +%H%M)" && '
              f"git checkout -b bash/render-classifier-t_1904a089 main && git cherry-pick ff64e9ad && git log --oneline -2")

MOVES = [
    LAST_NIGHT,
    f"cd {REPO} && git reset --hard HEAD~1",
    f"cd {REPO}\ngit reset -q",
    f"git -C {REPO} switch main",
    f"cd {REPO} && git stash",
    f"cd {REPO} && git rebase main",
    f'cd "{REPO}" && git clean -fd',
    f"cd {REPO} && git checkout -- docs/STEWARD-MS-REPORT.md",
    f'git -C {REPO} branch -f main "$NOTETIP"',
    f"cd {REPO} && git branch -D old",
    f"cd {REPO} && git push origin main",
    f"cd {REPO} && git push https://github.com/example-org/crew-rails.git main",
    f"cd {REPO} && git remote set-url --push origin https://github.com/example-org/crew-rails.git",
    f"git -C {REPO} config remote.origin.pushurl https://github.com/example-org/crew-rails.git",
]

BARE = [
    "git checkout -b bash/render-classifier-t_1904a089 main",
    "git stash push -m x && git cherry-pick ff64e9ad",
    "git checkout --detach f6abd1280dc813edfbdbf59dc297668bf8279a4c",
    "git checkout -- scripts/voiceonly-home-server.py 2>&1",
    "git remote set-url --push origin https://github.com/example-org/crew-rails.git",
    "echo ok; git reset --hard",
]

FREE = [
    f"cd {REPO} && git status --short && git log --oneline -3 && git diff --stat",
    f'cd {REPO} && git add scripts/x.py && git commit -m "checkout the reset path" -- scripts/x.py',
    f"cd {REPO} && git stash list && git stash show -p stash@{{0}}",
    f"cd {REPO} && git branch --show-current && git branch -a && git branch -d merged",
    f"cd {REPO} && git push --dry-run origin main",
    f"cd {REPO} && git worktree add .worktrees/t_1 -b bash/t_1 main",
    f"cd {REPO}/.worktrees/t_1 && git checkout -b other && git rebase main",
    f"cd {REPO}-verify-a32ca71d && git checkout x",
    f"cd {REPO} && git stash list; cd /home/crew/projects/hermes-agent && git stash",
    "cd /tmp/crypto && git checkout -b y",
    "rollback = git checkout HEAD -- unit + reload + restart",
    "grep -nE '2 failed|not pushed|git push|branch|origin/' /tmp/x.log",
    "git status && git log --oneline -3 && git stash list",
    '{"body": "Rollback (git checkout HEAD -- unit); then git push"}',
]


def _policy(tmp_path, mode="live") -> Path:
    """live = the repo file as shipped; report = the git rules set back to report-only;
    drop = the git rules with no mode field at all (they follow KREW_T0_MODE)."""
    if mode == "live":
        return POLICY
    doc = t0_policy.filled_doc()
    for r in doc["blocklist"]:
        if r.get("env_off") == "KREW_T0_GIT_TREE":
            if mode == "drop":
                r.pop("mode", None)
            else:
                r["mode"] = mode
    p = tmp_path / f"tier0-{mode}.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def _gate(tmp_path, text, who=WORKER, extra=None, policy=None, mode="enforce") -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_GIT_TREE", "KREW_T0_GIT_PUSH",
                        "HERMES_KANBAN_TASK", "HERMES_PROFILE", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(policy or POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"),
               KREW_T0_MODE=mode, KREW_USER_ID="alex", **(who or {}), **(extra or {}))
    payload = {"hook_event_name": "pre_tool_call", "tool_name": "terminal", "tool_input": {"command": text}}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


def _hits(tmp_path) -> list[dict]:
    p = tmp_path / "hits.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_the_rules_ship_enforcing_with_their_own_kill():
    rules = [r for r in t0_policy.filled_rules() if r.get("env_off") == "KREW_T0_GIT_TREE"]
    assert len(rules) == 2
    assert all(r["mode"] == "enforce" and r["scope"] == "crew" and r["route"] == "worktree" for r in rules)
    # (the crew pinned its repo copy == the live policy here; krew ships one policy file)


@pytest.mark.parametrize("cmd", MOVES)
def test_report_mode_records_a_move_without_blocking(tmp_path, cmd):
    # KREW_T0_GIT_PUSH=0: since 09-28 a crew push is refused by its own enforce rule; this pins the
    # git-tree rules' report mode alone.
    out = _gate(tmp_path, cmd, policy=_policy(tmp_path, "report"), extra={"KREW_T0_GIT_PUSH": "0"})
    assert out.get("decision") != "block", out
    assert "[T0 report] would refuse" in out.get("context", ""), out
    (hit,) = _hits(tmp_path)
    assert hit["mode"] == "report" and hit["route"] == "worktree"


@pytest.mark.parametrize("cmd", MOVES)
def test_the_live_policy_refuses_it_even_from_a_shadow_caller(tmp_path, cmd):
    out = _gate(tmp_path, cmd, mode="shadow")
    assert out.get("decision") == "block", out
    assert "live tree" in out["reason"] and "route: worktree" in out["reason"]
    assert "worktree add .worktrees/<card>" in out["reason"] and "commit by path" in out["reason"]
    assert "Ask Zoey" not in out["reason"]


@pytest.mark.parametrize("cmd", BARE)
def test_a_move_that_names_no_folder_is_its_own_rule(tmp_path, cmd):
    out = _gate(tmp_path, cmd, policy=_policy(tmp_path, "report"))
    assert out.get("decision") != "block" and "does not say where it runs" in out.get("context", ""), out
    out = _gate(tmp_path, cmd, mode="shadow")
    assert out.get("decision") == "block" and "name the folder" in out["reason"], out


def test_deleting_the_field_is_not_enforcing_for_a_shadow_caller(tmp_path):
    """The trap the foreman found: with the line removed, a timer-launched worker
    (KREW_T0_MODE unset -> shadow) only gets a note and the branch still moves."""
    out = _gate(tmp_path, LAST_NIGHT, policy=_policy(tmp_path, "drop"), mode="shadow")
    assert out.get("decision") != "block" and "[T0 shadow]" in out.get("context", "")


@pytest.mark.parametrize("cmd", FREE)
def test_reads_commits_worktrees_and_other_repos_stay_free(tmp_path, cmd):
    out = _gate(tmp_path, cmd, mode="shadow")
    assert out.get("decision") != "block", (cmd, out)
    assert "would refuse" not in out.get("context", "")


def test_a_report_rule_never_masks_an_enforcing_rule_after_it(tmp_path):
    # an enforcing rule placed AFTER the report-only one still blocks the same command
    doc = yaml.safe_load(_policy(tmp_path, "report").read_text())
    doc["blocklist"].append({"pattern": r"\bMASKCHECK\b", "reason": "a later rule", "route": "zoey"})
    p = tmp_path / "tier0-later.yaml"
    p.write_text(yaml.safe_dump(doc))
    out = _gate(tmp_path, f"cd {REPO} && git stash && echo MASKCHECK", policy=p)
    assert out.get("decision") == "block", out
    assert "a later rule" in out["reason"]
    assert [h["mode"] for h in _hits(tmp_path)] == ["report", "enforce"]


def test_its_own_kill_and_the_user_are_free(tmp_path):
    for cmd in (LAST_NIGHT, BARE[0]):
        assert _gate(tmp_path, cmd, extra={"KREW_T0_GIT_TREE": "0"}) == {}
        assert _gate(tmp_path, cmd, who={}) == {}
