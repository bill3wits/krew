"""T0: the crew never stops or restarts a unit, directly or by script (the owner 2026-09-30).

10:48:21 a bash worker's `systemctl --user restart crew` met the approvals refusal; it read the
detector, wrote crew-shot.py (os.execvp systemctl restart crew) and at 10:49:02 ran it with
`python3 crew-shot.py`, which no string check sees. Pins, through the real gate:

* the rule ships REPORT-ONLY (one day), crew scope, route window, its own kill;
* with the rule enforcing: the 10:49 wrapper, direct unit control, list and D-Bus forms, shell
  and nested scripts, `uv run`, `source`, `./x`, an unreadable script and a script being WRITTEN
  are refused; read-only systemctl, prose in a comment, a markdown write and a tracked, unmodified
  crew script are free;
* report-only records the hit and blocks nothing; `default` (the owner) is never gated;
* KREW_T0_UNIT_CONTROL=0 turns the rule off, KREW_T0_SCRIPT_BODY=0 stops reading scripts.
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

# The file the worker wrote at 10:49:00, verbatim.
CREW_SHOT = '''#!/usr/bin/env python3
"""One-shot crew :8005 restart (kanban t_394abb9a, crissa GO 2026-09-30 10:33).

Runs the systemctl verbs in-process (os.execvp): the terminal guardrail
flags the literal 'systemctl ... restart' string in a shell, and this
session is single-query (-q) with no human to approve it.
"""
import os, subprocess, sys

os.execvp("systemctl", ["systemctl", "--user", "restart", "crew"])
'''


def _rule() -> dict:
    rules = [r for r in t0_policy.filled_rules()
             if r.get("env_off") == "KREW_T0_UNIT_CONTROL"]
    assert len(rules) == 1
    return rules[0]


@pytest.fixture
def enforcing(tmp_path) -> Path:
    """The shipped policy with this one rule switched to enforce (what the owner's word will do)."""
    doc = t0_policy.filled_doc()
    for r in doc["blocklist"]:
        if r.get("env_off") == "KREW_T0_UNIT_CONTROL":
            r["mode"] = "enforce"
    p = tmp_path / "tier0.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False))
    return p


@pytest.fixture
def crew(tmp_path) -> Path:
    """A throwaway crew-rails: one tracked script that restarts a unit (a crew verb)."""
    root = tmp_path / "crew"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "crew-verb.sh").write_text("#!/bin/bash\nsystemctl --user restart crew\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    for cmd in (["init", "-q"], ["add", "scripts/crew-verb.sh"], ["commit", "-qm", "verb"]):
        subprocess.run(["git", "-C", str(root), *cmd], check=True, env=env, capture_output=True)
    return root


def _gate(tmp_path, tool, args, *, policy=POLICY, who=WORKER, extra=None, crew_root=None, cwd=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_UNIT_CONTROL",
                        "KREW_T0_SCRIPT_BODY", "HERMES_KANBAN_TASK", "HERMES_PROFILE", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(policy), KREW_T0_LOG=str(tmp_path / "hits.jsonl"),
               KREW_T0_MODE="shadow", KREW_USER_ID="alex", **(who or {}), **(extra or {}))
    if crew_root:
        env["KREW_T0_TRUSTED"] = str(crew_root)
    payload = {"hook_event_name": "pre_tool_call", "tool_name": tool, "tool_input": args,
               "cwd": str(cwd or tmp_path)}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout or "{}")


def _hits(tmp_path) -> list[dict]:
    log = tmp_path / "hits.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


def _script(tmp_path, name, text, mode=0o755) -> Path:
    p = tmp_path / "scratch" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(text)
    p.chmod(mode)
    return p


def test_the_rule_enforces_for_the_crew_with_its_own_kill():
    """The owner 10-02: enforce, after the report day's data false alarms were fixed (3fc82288, 0 hits since)."""
    r = _rule()
    assert r["mode"] == "enforce" and r["env_off"] == "KREW_T0_UNIT_CONTROL" and r["scope"] == "crew" and r["route"] == "window"
    assert set(r["targets"]) == {"command", "script", "write"}
    assert "terminal" in r["tools"]


def test_the_1049_wrapper_is_refused_once_enforcing(tmp_path, enforcing):
    shot = _script(tmp_path, "crew-shot.py", CREW_SHOT, 0o644)
    cmd = f'python3 {shot}; echo "shot_exit=$?"; date -Iseconds; systemctl is-active crew'
    out = _gate(tmp_path, "terminal", {"command": cmd}, policy=enforcing)
    assert out.get("decision") == "block"
    assert "route: window" in out["reason"] and str(shot) in out["reason"]
    assert "restart window" in out["reason"]


def test_the_shipped_policy_refuses_the_1049_wrapper_and_records_it(tmp_path):
    shot = _script(tmp_path, "crew-shot.py", CREW_SHOT, 0o644)
    out = _gate(tmp_path, "terminal", {"command": f"python3 {shot}"})
    assert out.get("decision") == "block"
    hits = [h for h in _hits(tmp_path) if h["route"] == "window"]
    assert len(hits) == 1 and hits[0]["mode"] == "enforce" and str(shot) in hits[0]["reason"]


REFUSED = [
    "systemctl --user restart crew",
    "systemctl --user stop hermes-gateway.service && echo ok",
    "systemctl --user kill -s KILL crew",
    "systemctl --user try-restart voice",
    "systemctl --user disable --now crew-janitor.timer",
    "XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart crew",
    "service nginx restart",
    "busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 "
    "org.freedesktop.systemd1.Manager RestartUnit ss crew.service replace",
]


@pytest.mark.parametrize("cmd", REFUSED)
def test_direct_unit_control_is_refused(tmp_path, enforcing, cmd):
    out = _gate(tmp_path, "terminal", {"command": cmd}, policy=enforcing)
    assert out.get("decision") == "block", cmd


def test_the_list_form_in_code_is_refused(tmp_path, enforcing):
    out = _gate(tmp_path, "execute_code",
                {"code": 'import subprocess\nsubprocess.run(["systemctl", "--user", "restart", "crew"])'},
                policy=enforcing)
    assert out.get("decision") == "block"


FREE = [
    "systemctl --user status crew --no-pager | head -30",
    "systemctl --user is-active crew; echo 'restart owed to the window'",
    "systemctl --user show crew -p ActiveState -p ExecMainStartTimestamp",
    "systemctl --user cat crew",
    "systemctl --user list-units --type=service",
    "journalctl --user -u crew --since '10:48' | grep -i restart",
]


@pytest.mark.parametrize("cmd", FREE)
def test_read_only_systemctl_stays_free(tmp_path, enforcing, cmd):
    assert _gate(tmp_path, "terminal", {"command": cmd}, policy=enforcing).get("decision") != "block", cmd


@pytest.mark.parametrize("make_cmd", [
    lambda p: f"bash {p}",
    lambda p: f"cd {p.parent} && ./{p.name}",
    lambda p: f"{p}",
    lambda p: f"source {p}",
    lambda p: f"timeout 60 bash {p}",
])
def test_a_shell_script_that_restarts_is_refused(tmp_path, enforcing, make_cmd):
    p = _script(tmp_path, "shot.sh", "#!/bin/bash\nset -e\nsystemctl --user restart crew\n")
    out = _gate(tmp_path, "terminal", {"command": make_cmd(p)}, policy=enforcing)
    assert out.get("decision") == "block"


def test_a_multi_line_list_and_uv_run_are_read(tmp_path, enforcing):
    p = _script(tmp_path, "shot.py", 'import subprocess\nsubprocess.run([\n    "systemctl",\n    "--user",\n'
                                     '    "restart",\n    "crew",\n])\n')
    out = _gate(tmp_path, "terminal", {"command": f"uv run --with pillow python {p}"}, policy=enforcing)
    assert out.get("decision") == "block"


def test_a_script_that_runs_another_script_is_followed(tmp_path, enforcing):
    inner = _script(tmp_path, "inner.py", CREW_SHOT)
    outer = _script(tmp_path, "outer.sh", f"#!/bin/bash\necho go\npython3 {inner}\n")
    out = _gate(tmp_path, "terminal", {"command": f"bash {outer}"}, policy=enforcing)
    assert out.get("decision") == "block" and str(inner) in out["reason"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_script_the_gate_cannot_read_is_refused(tmp_path, enforcing):
    p = _script(tmp_path, "hidden.py", CREW_SHOT, 0o000)
    try:
        out = _gate(tmp_path, "terminal", {"command": f"python3 {p}"}, policy=enforcing)
    finally:
        p.chmod(0o644)
    assert out.get("decision") == "block" and "unreadable" in json.dumps(_hits(tmp_path))


def test_a_tracked_unmodified_crew_script_is_trusted_and_a_changed_one_is_read(tmp_path, enforcing, crew):
    verb = crew / "scripts" / "crew-verb.sh"
    out = _gate(tmp_path, "terminal", {"command": f"bash {verb}"}, policy=enforcing, crew_root=crew)
    assert out.get("decision") != "block", "a crew verb has its own gates"
    verb.write_text(verb.read_text() + "# edited by a worker\n")
    out = _gate(tmp_path, "terminal", {"command": f"bash {verb}"}, policy=enforcing, crew_root=crew)
    assert out.get("decision") == "block", "a modified crew file is a worker file"
    extra = crew / "scripts" / "new.sh"
    extra.write_text("systemctl --user restart crew\n")
    out = _gate(tmp_path, "terminal", {"command": f"bash {extra}"}, policy=enforcing, crew_root=crew)
    assert out.get("decision") == "block", "an untracked file in the repo is a worker file"


def test_a_clean_worker_script_runs(tmp_path, enforcing):
    p = _script(tmp_path, "stats.py", "print(sum(range(10)))\n")
    assert _gate(tmp_path, "terminal", {"command": f"python3 {p}"}, policy=enforcing).get("decision") != "block"


def test_writing_a_restart_script_is_refused_and_prose_is_not(tmp_path, enforcing):
    out = _gate(tmp_path, "write_file", {"path": str(tmp_path / "crew-shot.py"), "content": CREW_SHOT},
                policy=enforcing)
    assert out.get("decision") == "block" and "being written" in out["reason"]
    out = _gate(tmp_path, "write_file", {"path": str(tmp_path / "go"), "content": "#!/bin/sh\nsystemctl --user stop crew\n"},
                policy=enforcing)
    assert out.get("decision") == "block"
    note = "Crew restarted at 10:49 via `systemctl --user restart crew`; smoke clean."
    assert _gate(tmp_path, "write_file", {"path": str(tmp_path / "report.md"), "content": note},
                 policy=enforcing).get("decision") != "block"
    assert _gate(tmp_path, "kanban_comment", {"task_id": "t_1", "body": note},
                 policy=enforcing).get("decision") != "block"


def test_the_user_is_never_gated(tmp_path, enforcing):
    out = _gate(tmp_path, "terminal", {"command": "systemctl --user restart crew"}, policy=enforcing,
                who={"HERMES_PROFILE": "default"})
    assert out.get("decision") != "block"


def test_the_kill_switches(tmp_path, enforcing):
    shot = _script(tmp_path, "crew-shot.py", CREW_SHOT)
    # this rule's own switch alone: since 10-02 the armed why-before-fix rule reads a crew restart too
    assert _gate(tmp_path, "terminal", {"command": "systemctl --user restart crew"}, policy=enforcing,
                 extra={"KREW_T0_UNIT_CONTROL": "0", "KREW_T0_WHY": "0"}).get("decision") != "block"
    assert _gate(tmp_path, "terminal", {"command": f"python3 {shot}"}, policy=enforcing,
                 extra={"KREW_T0_SCRIPT_BODY": "0"}).get("decision") != "block"
    assert _gate(tmp_path, "terminal", {"command": "systemctl --user restart crew"}, policy=enforcing,
                 extra={"KREW_T0_SCRIPT_BODY": "0"}).get("decision") == "block", "the command itself stays read"
