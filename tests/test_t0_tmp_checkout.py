"""T0: comparison checkouts on disk, never in /tmp; test_solo_mode.py never in a bulk run (the owner 2026-10-01).

05:17 10-01: run 4795 (t_81ef59f0) checked crew-rails out twice into tmpfs /tmp and the OOM killer took git at
the 4 GB worker cap; the same run had run `pytest tests/` in crew-rails. Both rules ship REPORT-ONLY, crew scope.
"""
from __future__ import annotations

import json
import os
import re
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
RULES = {r["route"]: r for r in t0_policy.filled_rules()
         if r.get("route") in ("tmp-checkout", "solo-tests")}


def test_both_ship_report_only_for_the_crew_with_their_kills():
    assert set(RULES) == {"tmp-checkout", "solo-tests"}
    for r in RULES.values():
        assert r["mode"] == "report" and r["scope"] == "crew" and r["env_off"].startswith("KREW_T0_")
    # (the crew pinned its repo copy == the live policy here; krew ships one policy file)


@pytest.mark.parametrize("cmd,hit", [
    ("cd /home/crew/projects/crew-rails && git worktree add /tmp/crew-baseline 38bc6862", True),
    ("git -C /home/crew/projects/crew-rails worktree add /tmp/crew-mine aae55c13 2>&1 | tail -1", True),
    ("git worktree add -b cmp '/tmp/x' HEAD", True),
    ("git worktree add /home/crew/.hermes/kanban/boards/b/workspaces/t_1/baseline 38bc6862", False),
    ("git worktree remove --force /tmp/crew-baseline", False),
    ("git worktree list", False),
])
def test_tmp_checkout(cmd, hit):
    assert bool(re.search(RULES["tmp-checkout"]["pattern"], cmd, re.I)) is hit


@pytest.mark.parametrize("cmd,hit", [
    ("cd /home/crew/projects/crew-rails && /stores/crew-envs/vllm/bin/python -m pytest tests/ -q > /tmp/s.log 2>&1", True),
    ("cd /home/crew/projects/crew-rails && python -m pytest tests -q", True),
    ("pytest tests/test_crew_*.py -q", True),
    ("python -m pytest tests/test_solo_mode.py tests/test_crew_pin_check.py -q", True),
    ("python -m pytest tests/test_solo_mode.py -q", False),
    ("python -m pytest tests/test_crew_pin_check.py tests/test_tier0_gate.py -q", False),
    ("cd /home/crew/projects/crew-rails && python -m pytest tests/test_research_catchup_stale_jstat.py -q", False),
    ("cd /home/crew/projects/crew-rails && uv run --with pytest --with pyyaml pytest tests/ -q 2>&1 | tail -8", True),
    ("cd /home/crew/projects/crew-rails && nohup /stores/crew-envs/vllm/bin/python -m pytest tests/ -q > /tmp/s.log 2>&1 &", True),
    ('cd /home/crew/projects/crew-rails && for V in a b; do "$V" -m pytest tests/ -q; done', True),
    # false positive 1 (steward 10-01 05:55): a grep for the TEXT, with the | and && inside quotes
    ("cd /home/crew/projects/crew-rails && grep -rln 'git worktree add /tmp\\|pytest tests/' CLAUDE.md", False),
    ("grep -n 'pytest tests/' CLAUDE.md", False),
    ("echo 'never run pytest tests/ in crew-rails'", False),
    ('hermes kanban complete t_1 --result "ran the crew-rails suite with pytest tests/ - all green"', False),
])
def test_solo_mode_bulk(cmd, hit):
    assert bool(re.search(RULES["solo-tests"]["pattern"], cmd, re.I)) is hit


# false positive 2 (steward 10-01 05:55): a FILE that merely contains the text - a Python check script holding
# the old pattern and a sample command as strings. Written or run, it is not a pytest run.
CHECK_SCRIPT = """import re
PAT = r'crew-rails[^\\n]*?\\bpytest\\b[^\\n;&|]*?\\s(?:\\./)?tests/?'
print(re.search(PAT, 'cd /home/crew/projects/crew-rails && pytest tests/'))
"""


def _gate_write(tmp_path, path, content):
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_SOLO_TESTS", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"), KREW_T0_MODE="shadow",
               KREW_USER_ID="alex", **WORKER)
    payload = {"hook_event_name": "pre_tool_call", "tool_name": "write_file",
               "tool_input": {"path": str(path), "content": content}, "cwd": str(tmp_path)}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    log = tmp_path / "hits.jsonl"
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def test_a_file_that_only_contains_the_text_is_not_a_run(tmp_path):
    assert not re.search(RULES["solo-tests"]["pattern"], CHECK_SCRIPT, re.I)
    hits = _gate_write(tmp_path, tmp_path / "t0-false-pos-check.py", CHECK_SCRIPT)
    assert not any(h.get("route") == "solo-tests" for h in hits)


def test_a_script_that_really_runs_the_bulk_suite_still_reports(tmp_path):
    body = "#!/bin/bash\ncd /home/crew/projects/crew-rails\npython -m pytest tests/ -q\n"
    hits = _gate_write(tmp_path, tmp_path / "run-all.sh", body)
    assert any(h.get("route") == "solo-tests" and h.get("mode") == "report" for h in hits)


def _gate(tmp_path, cmd):
    env = {k: v for k, v in os.environ.items()
           if k not in ("KREW_T0_BROKEN_OPEN", "KREW_T0_CREW_RULES", "KREW_T0_TMP_CHECKOUT",
                        "KREW_T0_SOLO_TESTS", "HERMES_HOME")}
    env.update(KREW_REAL_HOME=t0_policy.HOME, KREW_T0_POLICY=str(POLICY), KREW_T0_LOG=str(tmp_path / "hits.jsonl"), KREW_T0_MODE="shadow",
               KREW_USER_ID="alex", **WORKER)
    payload = {"hook_event_name": "pre_tool_call", "tool_name": "terminal", "tool_input": {"command": cmd},
               "cwd": str(tmp_path)}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout or "{}")
    hits = [json.loads(l) for l in (tmp_path / "hits.jsonl").read_text().splitlines()] \
        if (tmp_path / "hits.jsonl").exists() else []
    return out, hits


def test_report_only_through_the_real_gate(tmp_path):
    out, hits = _gate(tmp_path, "cd /home/crew/projects/crew-rails && git worktree add /tmp/crew-baseline HEAD")
    assert out.get("decision") != "block"
    assert any(h.get("route") == "tmp-checkout" and h.get("mode") == "report" for h in hits)
    out, hits = _gate(tmp_path, "cd /home/crew/projects/crew-rails && python -m pytest tests/ -q")
    assert out.get("decision") != "block"
    assert any(h.get("route") == "solo-tests" for h in hits)
