"""T0 parity (the User 2026-10-10): krew's policy IS the reference install's tier0.yaml, rule for rule, and the gate
carries every symbol the reference gate has.

config/tier0.example.yaml was rebuilt from the reference policy's TEXT (its comments, order and history), with each
machine literal turned into krew's {VAR}. The rebuild changed no rule: the parsed policy equals the one before it
(PRE_REBUILD, read from git when the history is there). REFERENCE is each reference rule's shape, frozen here:
(route, scope, mode, targets, tools, profiles, env_off), in the reference's order.
"""
from __future__ import annotations

import importlib.util
import subprocess

import pytest

from t0_policy import EXAMPLE, GATE, ROOT
from t0_policy import yaml as _yaml

load_yaml = _yaml.safe_load

PRE_REBUILD = "4f0d2aa"          # the commit that last wrote config/tier0.example.yaml before the rebuild

REFERENCE = [
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('window', 'crew', 'enforce', ('command', 'script', 'write'), ('terminal', 'execute_code', 'process'), (), 'KREW_T0_UNIT_CONTROL'),
    ('tmp-checkout', 'crew', 'report', ('command', 'script', 'write'), ('terminal', 'execute_code', 'process'), (), 'KREW_T0_TMP_CHECKOUT'),
    ('solo-tests', 'crew', 'report', ('command', 'script', 'write'), ('terminal', 'execute_code', 'process'), (), 'KREW_T0_SOLO_TESTS'),
    ('crissa-kit', 'crew', 'enforce', (), ('terminal', 'execute_code', 'process', 'process_manage'), ('crissa',), 'KREW_T0_CRISSA_KIT'),
    ('crissa-kit', 'crew', 'enforce', (), ('write_file', 'patch'), ('crissa',), 'KREW_T0_CRISSA_KIT'),
    ('crissa-kit', 'crew', 'enforce', (), ('write_file', 'patch'), ('crissa',), 'KREW_T0_CRISSA_KIT'),
    ('prodgate', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('protected', '', '', (), (), (), ''),
    ('protected', '', '', (), (), (), ''),
    ('protected', '', '', (), (), (), ''),
    ('protected', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('prodgate', '', '', (), (), (), ''),
    ('prodgate', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('prodgate', '', '', (), (), (), ''),
    ('prodgate', '', '', (), (), (), ''),
    ('prodgate', '', '', (), (), (), ''),
    ('prodgate', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('assent', '', '', (), (), (), ''),
    ('crissa', 'crew', '', (), (), (), ''),
    ('crissa', 'crew', '', (), (), (), ''),
    ('crissa', 'crew', '', (), (), (), ''),
    ('crissa', 'crew', '', (), (), (), ''),
    ('crissa', 'crew', '', (), (), (), ''),
    ('crissa', 'crew', '', (), (), (), ''),
    ('crissa', 'crew', '', (), (), (), ''),
    ('crissa', 'crew', '', (), (), (), ''),
    ('worktree', 'crew', 'enforce', (), (), (), 'KREW_T0_GIT_TREE'),
    ('worktree', 'crew', 'enforce', (), (), (), 'KREW_T0_GIT_TREE'),
    ('crissa', 'crew', 'enforce', (), (), (), 'KREW_T0_HERMES_TREE'),
    ('crissa', 'crew', 'enforce', (), (), (), 'KREW_T0_HERMES_TREE'),
    ('crissa', 'crew', 'enforce', (), (), (), 'KREW_T0_HERMES_TREE'),
    ('clone', 'crew', 'enforce', (), (), (), 'KREW_T0_HERMES_CLONE'),
    ('push', 'crew', 'enforce', (), (), (), 'KREW_T0_GIT_PUSH'),
    ('board', 'crew', 'enforce', (), (), (), 'KREW_T0_BOARD_SWITCH'),
    ('kanban-env', 'crew', 'enforce', (), (), (), 'KREW_T0_KANBAN_ENV_TESTS'),
]


def _policy():
    return load_yaml(EXAMPLE.read_text(encoding="utf-8"))


def _shape(r: dict) -> tuple:
    return (r.get("route", "assent"), r.get("scope", ""), r.get("mode", ""), tuple(r.get("targets") or ()),
            tuple(r.get("tools") or ()), tuple(r.get("profiles") or ()), r.get("env_off") or "")


def test_every_reference_rule_is_here_in_its_order_with_its_shape():
    rules = _policy()["blocklist"]
    assert len(rules) == len(REFERENCE) == 55
    for i, (r, want) in enumerate(zip(rules, REFERENCE)):
        assert _shape(r) == want, (i, _shape(r), want)


def test_every_enforced_reference_rule_stays_enforced():
    rules = _policy()["blocklist"]
    for i, want in enumerate(REFERENCE):
        if want[2] == "enforce":
            assert rules[i].get("mode") == "enforce", i
    assert sum(1 for r in rules if r.get("mode") == "enforce") == sum(1 for w in REFERENCE if w[2] == "enforce") == 13


def test_the_rebuild_changed_no_rule():
    try:
        old = subprocess.run(["git", "-C", str(ROOT), "show", f"{PRE_REBUILD}:config/tier0.example.yaml"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("no git here")
    if old.returncode != 0:
        pytest.skip("no history here (a packed copy): the shape test above still holds")
    # the one change since: her name in the refusal reasons a person reads (Crissa -> Crissa, the User 2026-10-10)
    before, after = load_yaml(old.stdout.replace("Crissa", "Crissa")), _policy()
    for key in ("mode", "vars", "trusted_repos", "blocklist", "why_before_fix", "allowlist"):
        assert after.get(key) == before.get(key), key


def test_the_history_rides_along_and_says_no_name():
    doc = _policy()
    meta = doc["metadata"]
    logs = {k: v for k, v in meta.items() if k.startswith("changelog")}
    assert len(logs) == 17 and meta["source_task"] == "t_9014d920"
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "the User" not in text          # the scrub (tests/test_scrub.py) holds the rest of the house words out
    assert "# Parallax Tier 0" in text.splitlines()[0]


# ---- the gate: every reference symbol, by its own name or its krew counterpart ----------------------------------
COUNTERPART = {
    "_SYSTEM_PYTHON": "_SYSTEM_PYTHON", "_rerun_under_system_python": "_rerun_under_system_python",
    "_REAL_HOME": "_REAL_HOME", "POLICY": "POLICY", "LOG": "LOG",
    "MODE": "policy_mode",                 # the policy's mode:, KREW_T0_MODE overrides (read per call)
    "ROUTE_GUIDANCE": "ROUTE_GUIDANCE", "TEXT_FIELDS": "TEXT_FIELDS", "BROKEN_OPEN": "BROKEN_OPEN",
    "CREW_RULES": "CREW_RULES",
    "ID_ROOT": "_identity_user",           # an identity store named by KREW_ID_ROOT or vars.ID_ROOT
    "_user_id": "_user_id", "_is_crew": "_is_crew", "_load_rules": "_load_rules", "_haystack": "_haystack",
    "SCRIPT_BODY": "SCRIPT_BODY",
    "(the reference root constant)": "trusted_repos",   # its one trusted repo is the policy's trusted_repos list
    "SCRIPT_MAX_BYTES": "SCRIPT_MAX_BYTES", "_INTERPRETERS": "_INTERPRETERS", "_PREFIXES": "_PREFIXES",
    "_SCRIPT_EXT": "_SCRIPT_EXT", "_SPLIT": "_SPLIT", "_words": "_words", "_executed_files": "_executed_files",
    "_DATA_EXT": "_DATA_EXT", "DATA_IS_NOT_RUN": "DATA_IS_NOT_RUN", "_runs": "_runs", "_trusted": "_trusted",
    "_script_bodies": "_script_bodies", "_write_payload": "_write_payload", "_path_rule": "_path_rule",
    "_real": "_real", "_under": "_under", "_blocked_write": "_blocked_write", "_profile_name": "_profile_name",
    "_outside_roots": "_outside_roots", "WHY_LIB": "WHY_LIB", "_why_policy": "_why_policy",
    "_why_findings": "_why_findings", "_record": "_record", "main": "main",
}
REFERENCE_ROUTES = ("protected", "prodgate", "crissa", "worktree", "board", "kanban-env", "clone", "push", "window",
                    "crissa-kit", "tmp-checkout", "solo-tests", "why", "assent")


def _gate():
    spec = importlib.util.spec_from_file_location("krew_t0_gate_parity", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_gate_has_every_reference_symbol():
    g = _gate()
    missing = [ref for ref, ours in COUNTERPART.items() if not hasattr(g, ours)]
    assert not missing, missing
    assert set(REFERENCE_ROUTES) <= set(g.ROUTE_GUIDANCE), set(REFERENCE_ROUTES) - set(g.ROUTE_GUIDANCE)
    every_route = {w[0] for w in REFERENCE} | {"why"}
    assert every_route <= set(g.ROUTE_GUIDANCE)


def test_the_owner_id_is_never_a_literal(monkeypatch):
    g = _gate()
    monkeypatch.delenv("KREW_ID_ROOT", raising=False)
    monkeypatch.setenv("KREW_USER_ID", "Owner1")
    assert g._user_id({"USER": "other"}) == "owner1"
    monkeypatch.delenv("KREW_USER_ID")
    assert g._user_id({"USER": "Other"}) == "other" and g._user_id({}) == ""
    assert "USER" not in g.policy_vars({"vars": {}})


def test_the_discipline_section_is_the_reference_one():
    doc = _gate().__doc__
    for line in ("a MISSING policy fails OPEN and says so loudly", "the hook spec carries `fail_closed: true`",
                 "A NEW rule arms on its own (2026-09-24)", "A rule can read WHAT A COMMAND RUNS (2026-09-30)",
                 "A BLOCK LIST of paths (2026-10-01, second ruling)", "WHY BEFORE FIX"):
        assert line in doc, line
