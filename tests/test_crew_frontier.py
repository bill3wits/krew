"""The efficient frontier of a worker's kit: cold tools wait, hot tools are sent (2026-09-30)."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
WORKERS = ("ada", "bash", "lila", "steward", "vera")


def _mod():
    spec = importlib.util.spec_from_file_location("crew_frontier", ROOT / "crew" / "scripts" / "crew-frontier.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


KIT = {"sent_bytes": 9000, "sent_tools": 6, "default_defer": ["session_search", "todo_list"],
       "deferred_names": ["session_search", "team_board"],
       "rows": [{"tool": "terminal", "toolset": "terminal", "bytes": 4000, "core": True},
                {"tool": "skill_manage", "toolset": "skills", "bytes": 3700, "core": True},
                {"tool": "tiny_cold", "toolset": "x", "bytes": 100, "core": True},
                {"tool": "kanban_attach", "toolset": "kanban_lean", "bytes": 600, "core": True},
                {"tool": "session_search", "toolset": "session_search", "bytes": 4000, "core": True},
                {"tool": "team_board", "toolset": "team", "bytes": 1000, "core": False}]}


def test_cold_tools_are_proposed_for_the_bridge_and_hot_ones_for_the_array():
    m = _mod()
    use = {"sessions": 100, "calls": 500, "bridge_calls": 40,
           "by_session": {"terminal": 98, "session_search": 5, "team_board": 7}, "by_call": {"session_search": 9, "team_board": 20}}
    f = m.frontier(KIT, use)
    assert [r["tool"] for r in f["cold_eager"]] == ["skill_manage"], "terminal is hot, tiny_cold is not worth a bridge line"
    assert [r["tool"] for r in f["cold_protocol_verbs"]] == ["kanban_attach"], "a board verb is reported, never proposed"
    assert sorted(r["tool"] for r in f["hot_deferred"]) == ["session_search", "team_board"]
    p = f["propose"]
    assert p["defer"] == ["skill_manage", "todo_list"], "the curated names stay, the hot core tool leaves, the cold one joins"
    assert p["eager"] == ["team_board"], "a plugin tool can only be kept with the eager list"
    assert p["bytes_off_every_call"] == 3700 and p["bridged_calls_saved"] == 29


def test_a_worker_never_loses_the_verbs_it_cannot_finish_without():
    m = _mod()
    kit = {**KIT, "rows": [{"tool": t, "toolset": "kanban_lean", "bytes": 900, "core": True}
                           for t in ("kanban_complete", "kanban_block", "kanban_show", "terminal", "read_file")]}
    f = m.frontier(kit, {"sessions": 50, "calls": 0, "bridge_calls": 0, "by_session": {}, "by_call": {}})
    assert f["cold_eager"] == [] and f["cold_protocol_verbs"] == []


def test_a_bridged_call_counts_for_the_tool_it_invoked(tmp_path):
    m = _mod()
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.executescript("create table sessions (id text, source text, started_at real);"
                      "create table messages (id integer primary key, session_id text, role text, tool_calls text);")
    con.execute("insert into sessions values ('k1','kanban',100.0)")
    con.execute("insert into sessions values ('c1','cli',100.0)")
    for tc in ([{"function": {"name": "terminal", "arguments": "{}"}}],
               [{"function": {"name": "tool_search", "arguments": "{}"}}],
               [{"function": {"name": "tool_call", "arguments": json.dumps({"calls": [{"name": "team_board"}, "junk"]})}}]):
        con.execute("insert into messages (session_id, role, tool_calls) values ('k1','assistant',?)", (json.dumps(tc),))
    con.commit(); con.close()
    u = m.usage(db, 0.0)
    assert u["sessions"] == 1 and u["calls"] == 3 and u["bridge_calls"] == 2
    assert u["by_session"] == {"terminal": 1, "team_board": 1} and u["by_call"]["team_board"] == 1


PACKS = Path(os.path.expanduser(os.environ.get("KREW_PACKS_ROOT") or "~/.krew/packs"))   # krew: a packs checkout, when the owner keeps one


@pytest.mark.skipif(not (PACKS / "distributions").is_dir(), reason="no packs checkout (KREW_PACKS_ROOT)")
@pytest.mark.parametrize("profile", WORKERS)
def test_the_packs_wear_the_frontier(profile):
    m = _mod()
    cfg = yaml.safe_load((PACKS / "distributions" / profile / "config.yaml").read_text())
    ts = cfg["tools"]["tool_search"]
    defer = set(ts["defer"])
    assert {"skill_manage", "memory", "clarify"} <= defer, "used in under 1 % of sessions, sent on every call"
    assert {"computer_use", "todo_list", "image_generate", "cronjob_manage"} <= defer, "defer REPLACES the curated list"
    assert not defer & m.NEVER_DEFER, defer & m.NEVER_DEFER
    assert not any(n.startswith("kanban_") for n in defer), "board verbs stay in the array"
    assert ts.get("enabled", "auto") != "off"


@pytest.mark.skipif(not (PACKS / "distributions").is_dir(), reason="no packs checkout (KREW_PACKS_ROOT)")
def test_the_hot_tools_are_sent():
    cfg = lambda p: yaml.safe_load((PACKS / "distributions" / p / "config.yaml").read_text())["tools"]["tool_search"]
    # + krew_docs on every krew profile's eager list (the User 2026-10-03: always-on, never behind Tool Search)
    assert "session_search" not in cfg("ada")["defer"] and cfg("ada")["eager"] == ["team_board", "krew_docs"]
    assert "process_manage" not in cfg("bash")["defer"]
    assert cfg("steward")["eager"] == ["team_time", "krew_docs"]


@pytest.mark.hermes
def test_patch_130_keeps_an_eager_name_out_of_the_bridge():
    tree = Path.home() / ".hermes" / "hermes-agent"
    if not (tree / "tools" / "tool_search.py").is_file():
        pytest.skip("no hermes tree")
    import hermes_runtime
    py = hermes_runtime.runtime_python()
    if not py:
        pytest.skip("the hermes launcher names no interpreter here")
    probe = ("import os, sys\nsys.path.insert(0, os.path.expanduser('~/.hermes/hermes-agent'))\nimport hermes_bootstrap\n"
             "from tools.tool_search import ToolSearchConfig\n"
             "c = ToolSearchConfig.from_raw({'eager': ['team_board'], 'defer': ['memory']})\n"
             "print(sorted(c.eager_tools), sorted(c.effective_defer_tools), len(ToolSearchConfig.from_raw(None).eager_tools))\n")
    env = {k: v for k, v in os.environ.items() if k not in ("HERMES_HOME", "HERMES_KANBAN_TASK")}
    r = subprocess.run([py, "-I", "-c", probe], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    assert r.stdout.strip().splitlines()[-1] == "['team_board'] ['memory'] 0"
    patch = (ROOT / "patches" / "130-tool-search-eager-list.patch").read_text()
    assert "+    if name in load_config_readonly().eager_tools:" in patch
