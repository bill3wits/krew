"""T0: Crissa's hands keep to her kit (the owner 2026-10-01).

Hands named no toolsets and hermes loaded every one: terminal 39 and execute_code 3 calls in a week. The
launcher now names her kit and HERMES_WRITE_SAFE_ROOT limits her file tools; these rules are the belt behind
both, for profile crissa only: no terminal / execute_code / process, and write_file / patch only inside
~/.hermes/profiles/crissa and ~/Desktop/crew-deliverables, judged on the RESOLVED path (links, `..`).
Shipped report-only for one day; enforce on the owner's word.

Second ruling the same day: a BLOCK LIST, not an allow list. Her config disables the terminal and code_execution
toolsets; the write rule names blocked paths (system folders, his home outside her two folders, the repos, the live
hermes tree, kanban, credential files, her own controls, five /data roots). The safe root stays until enforce.
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
CRISSA = {"HERMES_PROFILE": "crissa"}


def _rules(doc=None):
    doc = doc or t0_policy.filled_doc()
    return [r for r in doc["blocklist"] if r.get("env_off") == "KREW_T0_CRISSA_KIT"]


def _policy(tmp_path, block=None) -> Path:
    """The shipped policy with these rules enforcing (the owner's word), optionally blocking test folders only."""
    doc = t0_policy.filled_doc()
    for r in _rules(doc):
        r["mode"] = "enforce"
        if block is not None and r.get("write_block"):
            r["write_block"] = [str(x) for x in block]
            r["write_except"], r["write_block_always"] = [], []
    p = tmp_path / "tier0.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return p


def _gate(tmp_path, tool, args, *, policy, who=CRISSA, extra=None, cwd=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_CRISSA_KIT", "HERMES_KANBAN_TASK",
                        "HERMES_PROFILE", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(policy), KREW_T0_LOG=str(tmp_path / "hits.jsonl"), KREW_T0_MODE="shadow",
               KREW_USER_ID="alex", **(who or {}), **(extra or {}))
    payload = {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": args, "cwd": str(cwd or tmp_path)}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


def _blocked(out) -> bool:
    return out.get("decision") == "block" and "crissa-kit" in out.get("reason", "")


def test_the_rules_enforce_for_crissa_with_their_kill():
    rules = _rules()
    assert len(rules) == 3
    assert all(r["profiles"] == ["crissa"] and r["route"] == "crissa-kit" for r in rules)
    assert all(r["mode"] == "enforce" for r in rules), "all three enforce (the owner 10-02 03:43, after the report day)"
    assert all(r["env_off"] == "KREW_T0_CRISSA_KIT" for r in rules)
    w = [r for r in rules if r.get("write_block")][0]
    assert not w.get("write_roots"), "a block list, not an allow list (10-01 second ruling)"
    assert w["write_except"] == ["/home/crew/.hermes/profiles/crissa", "/home/crew/Desktop/crew-deliverables"]
    for d in ("/stores/crew-security", "/stores/crew-envs", "/stores/crew-memory", "/stores/hindsight", "/stores/hf", "/home/crew"):
        assert d in w["write_block"], d
    # (the crew pinned its repo copy == the live policy here; krew ships one policy file)


@pytest.mark.parametrize("tool,args", [
    ("terminal", {"command": "sqlite3 ~/.hermes/kanban.db .tables"}),
    ("terminal", {"command": "ls"}),
    ("execute_code", {"code": "print(1)"}),
])
def test_no_shell_or_code_for_crissa(tmp_path, tool, args):
    assert _blocked(_gate(tmp_path, tool, args, policy=_policy(tmp_path)))


def test_other_profiles_and_the_user_are_not_these_rules(tmp_path):
    pol = _policy(tmp_path)
    for who in ({"HERMES_PROFILE": "bash", "HERMES_KANBAN_TASK": "t_x"}, {"HERMES_PROFILE": "default"}):
        assert not _blocked(_gate(tmp_path, "terminal", {"command": "ls"}, policy=pol, who=who))
        assert not _blocked(_gate(tmp_path, "write_file", {"path": "/home/crew/Desktop/x.txt"}, policy=pol, who=who))


def test_writes_inside_her_folders_are_free(tmp_path):
    pol = _policy(tmp_path)
    for path in ("/home/crew/.hermes/profiles/crissa/notes/today.md",
                 "/home/crew/.hermes/profiles/crissa",
                 "/home/crew/Desktop/crew-deliverables/2026-10-01-note.md"):
        assert not _blocked(_gate(tmp_path, "write_file", {"path": path, "content": "x"}, policy=pol)), path


@pytest.mark.parametrize("path", [
    "/home/crew/Desktop/notes.txt",
    "/home/crew/projects/crew-rails/CLAUDE.md",
    "/etc/hosts",
    "/home/crew/.hermes/.env",
    "/home/crew/.hermes/profiles/crissa/../../config.yaml",
    "/home/crew/.hermes/profiles/crissax/file",
    "/home/crew/.hermes/hermes-agent/run_agent.py",
    "/home/crew/.hermes/kanban/boards/daily-2026-09-16/kanban.db",
    "/tmp/scratch/kanban.db",
    "/tmp/x/.env",
    "/stores/crew-envs/rail-stt/lib/x.py",
    "/stores/crew-security/key",
    "/stores/crew-memory/identity/site.json",
    "/stores/hindsight/x",
    "/stores/hf/hub/x",
    "/usr/local/bin/steward-sweep",
    "/home/crew/.hermes/profiles/crissa/config.yaml",
    "/home/crew/.hermes/profiles/crissa/cua-manifest.yaml",
    "/home/crew/.hermes/profiles/crissa/.env",
    "/home/crew/.hermes/profiles/crissa/plugins/crew-hands/__init__.py",
    "/home/crew/.hermes/profiles/crissa/cron/jobs.json",
])
def test_writes_outside_are_refused(tmp_path, path):
    out = _gate(tmp_path, "write_file", {"path": path, "content": "x"}, policy=_policy(tmp_path))
    assert _blocked(out), path


def test_writes_outside_the_block_list_are_free(tmp_path):
    pol = _policy(tmp_path)
    for path in ("/tmp/crissa-draft.md", "/stores/models/notes.txt", "/stores/tmp/x.txt"):
        assert not _blocked(_gate(tmp_path, "write_file", {"path": path, "content": "x"}, policy=pol)), path


def test_a_link_that_points_into_a_blocked_folder_is_refused_and_relative_paths_resolve(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    (root / "escape").symlink_to(outside)
    pol = _policy(tmp_path, block=[outside])
    assert _blocked(_gate(tmp_path, "patch", {"path": str(root / "escape" / "f.txt")}, policy=pol))
    assert not _blocked(_gate(tmp_path, "write_file", {"path": "sub/f.txt"}, policy=pol, cwd=root))
    assert _blocked(_gate(tmp_path, "write_file", {"path": "../outside/f.txt"}, policy=pol, cwd=root))


def test_a_patch_that_names_no_single_path_is_refused(tmp_path):
    out = _gate(tmp_path, "patch", {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: /etc/hosts\n"},
                policy=_policy(tmp_path))
    assert _blocked(out) and "no single path" in out["reason"]


def test_the_shipped_policy_refuses_a_shell_and_records_it(tmp_path):
    out = _gate(tmp_path, "terminal", {"command": "ls"}, policy=POLICY)
    assert _blocked(out)
    hits = [json.loads(l) for l in (tmp_path / "hits.jsonl").read_text().splitlines()]
    assert any(h["route"] == "crissa-kit" and h["mode"] == "enforce" for h in hits)


def test_the_kill_switch(tmp_path):
    pol = _policy(tmp_path)
    assert not _blocked(_gate(tmp_path, "terminal", {"command": "ls"}, policy=pol, extra={"KREW_T0_CRISSA_KIT": "0"}))


@pytest.mark.parametrize("name", ["config.yaml", "cua-manifest.yaml", "SOUL.md", "profile.yaml", "hooks/x.py",
                                  "plugins/crew-hands/__init__.py", "scripts/x.py", "bin/uv", "cron/jobs.json"])
def test_her_own_controls_are_enforced_as_shipped(tmp_path, name):
    """The owner 10-01 ~04:20: writes to her own controls enforce (the rest followed 10-02 03:43)."""
    out = _gate(tmp_path, "write_file", {"path": f"/home/crew/.hermes/profiles/crissa/{name}", "content": "x"},
                policy=POLICY)
    assert _blocked(out), name


@pytest.mark.parametrize("path", ["/stores/crew-envs/x.py", "/home/crew/Desktop/x.txt", "/home/crew/.hermes/profiles/crissa/.env"])
def test_the_rest_of_the_block_list_enforces_as_shipped(tmp_path, path):
    """The owner 10-02 03:43: the block list enforces after the report day."""
    out = _gate(tmp_path, "write_file", {"path": path, "content": "x"}, policy=POLICY)
    assert _blocked(out), path
    assert (tmp_path / "hits.jsonl").exists()


def test_her_notes_in_her_profile_stay_free_as_shipped(tmp_path):
    out = _gate(tmp_path, "write_file", {"path": "/home/crew/.hermes/profiles/crissa/notes/today.md", "content": "x"},
                policy=POLICY)
    assert out.get("decision") != "block"
