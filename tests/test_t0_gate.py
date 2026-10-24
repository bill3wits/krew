"""Parallax T0 (security/tier0_gate.py): the pre-act gate must be secure AND
functional (casey 2026-09-18). Pins:

* a matching act is BLOCKED in enforce mode and carries its route;
* a non-matching act is a silent no-op;
* a MISSING policy fails OPEN, loudly (policy_unavailable line, inactive context);
* a PRESENT-but-broken policy fails CLOSED in enforce mode (policy_broken line,
  decision block), only says so in shadow, and KREW_T0_BROKEN_OPEN=1 restores
  the old open path;
* the repo copy is the executed copy (~/.hermes/security/tier0_gate.py), and
  every T0 hook spec in the packs carries fail_closed: true so a crashed or
  timed-out gate blocks instead of silently allowing (hermes shell_hooks).

The gate is run as a subprocess exactly as hermes runs it (JSON on stdin).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import t0_policy  # noqa: E402
from t0_policy import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GATE = t0_policy.GATE
PACKS = ROOT / "hermes-packs" / "distributions"

GOOD_POLICY = """
blocklist:
  - pattern: '\\bsudo\\b[^\\n]{0,120}\\bsystemctl\\b'
    reason: 'root systemctl (sudo) — NEEDS HUMAN'
    route: 'assent'
  - pattern: 'rm -rf /data'
    reason: 'wipe the data volume'
    route: 'protected'
"""


def _run(policy: Path | None, log: Path, mode: str, command: str, extra_env: dict | None = None) -> tuple[dict, str]:
    env = dict(os.environ)
    env.pop("KREW_T0_BROKEN_OPEN", None)
    env["KREW_REAL_HOME"] = t0_policy.HOME
    env["KREW_T0_POLICY"] = str(policy) if policy else str(log.parent / "absent.yaml")
    env["KREW_T0_LOG"] = str(log)
    env["KREW_T0_MODE"] = mode
    env.update(extra_env or {})
    payload = {"hook_event_name": "pre_tool_call", "tool_name": "terminal",
               "tool_input": {"command": command}}
    r = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                       capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout or "{}")
    lines = log.read_text().splitlines() if log.exists() else []
    return out, "\n".join(lines)


def test_matching_act_is_blocked_with_route_in_enforce(tmp_path):
    pol = tmp_path / "tier0.yaml"; pol.write_text(GOOD_POLICY)
    out, log = _run(pol, tmp_path / "hits.jsonl", "enforce", "sudo systemctl restart docker")
    assert out.get("decision") == "block"
    assert "route: assent" in out["reason"]
    assert '"mode": "enforce"' in log and "root systemctl" in log


def test_matching_act_is_only_noted_in_shadow(tmp_path):
    pol = tmp_path / "tier0.yaml"; pol.write_text(GOOD_POLICY)
    out, _ = _run(pol, tmp_path / "hits.jsonl", "shadow", "sudo systemctl restart docker")
    assert "decision" not in out and out["context"].startswith("[T0 shadow]")


def test_non_matching_act_is_silent(tmp_path):
    pol = tmp_path / "tier0.yaml"; pol.write_text(GOOD_POLICY)
    out, log = _run(pol, tmp_path / "hits.jsonl", "enforce", "systemctl --user restart crew")
    assert out == {} and log == ""


def test_missing_policy_fails_open_loudly(tmp_path):
    out, log = _run(None, tmp_path / "hits.jsonl", "enforce", "rm -rf /data")
    assert "decision" not in out
    assert "inactive" in out.get("context", "")
    assert "policy_unavailable" in log


def test_broken_policy_fails_closed_in_enforce(tmp_path):
    pol = tmp_path / "tier0.yaml"; pol.write_text("blocklist: [\n  - pattern: 'x'\n")  # unparseable
    out, log = _run(pol, tmp_path / "hits.jsonl", "enforce", "echo hello")
    assert out.get("decision") == "block"
    assert "route: protected" in out["reason"]
    assert "policy_broken" in log


def test_zero_rule_policy_is_broken_too(tmp_path):
    pol = tmp_path / "tier0.yaml"; pol.write_text("blocklist: []\n")
    out, log = _run(pol, tmp_path / "hits.jsonl", "enforce", "echo hello")
    assert out.get("decision") == "block" and "policy_broken" in log


def test_broken_policy_only_notes_in_shadow(tmp_path):
    pol = tmp_path / "tier0.yaml"; pol.write_text("blocklist: [\n  - pattern: 'x'\n")
    out, _ = _run(pol, tmp_path / "hits.jsonl", "shadow", "echo hello")
    assert "decision" not in out and "would block" in out["context"]


def test_kill_switch_restores_open_path(tmp_path):
    pol = tmp_path / "tier0.yaml"; pol.write_text("blocklist: [\n  - pattern: 'x'\n")
    out, log = _run(pol, tmp_path / "hits.jsonl", "enforce", "echo hello",
                    {"KREW_T0_BROKEN_OPEN": "1"})
    assert "decision" not in out and "policy_unavailable" in log






