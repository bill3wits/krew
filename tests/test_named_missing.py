"""Anywhere krew calls a script or lib that is not installed, it says so (the owner 2026-10-06: a check
that quietly skips is the hidden failure). team-crissa's two by-path libs ship in crew/lib and a missing one
is named once on stderr; config-restore, eval-rank, the settings check and the board gate name what they
could not run. (The Janitor's own rows: test_janitor_missing.py; the gateway topology: test_topology_own_lib.py. The announce path is not covered here.)"""
from __future__ import annotations

import importlib.util
import subprocess
import sys

from conftest import ROOT, load_plugin

CREW = ROOT / "crew"


class Ctx:
    def __init__(self, profile="bash"):
        self.profile_name = profile
        self.tools, self.hooks = {}, {}

    def register_tool(self, name=None, toolset=None, schema=None, handler=None, **kw):
        self.tools[name] = handler

    def register_hook(self, name, fn):
        self.hooks.setdefault(name, []).append(fn)

    def __getattr__(self, k):
        return lambda *a, **kw: None


def _slim_hook(monkeypatch):
    monkeypatch.setenv("KREW_TEAM_BOARD_FROM_KREW_BOARD", "0")
    mod = load_plugin("team-crissa")
    ctx = Ctx()
    mod.register(ctx)
    hooks = [f for f in ctx.hooks.get("transform_tool_result", []) if f.__name__ == "_slim_kanban_show"]
    assert hooks, "team-crissa registers the kanban_show slimming hook"
    return mod, hooks[0]


def test_team_crissas_libs_ship_beside_the_plugin_and_load_from_there():
    team = ROOT / "plugins" / "team-crissa"
    src = (team / "__init__.py").read_text()
    assert (team / "kanban_show_slim.py").is_file() and (team / "board_parallel.py").is_file()
    assert 'path = Path(__file__).resolve().parent / "kanban_show_slim.py"' in src
    assert 'path = Path(__file__).resolve().parent / "board_parallel.py"' in src
    for gone in ('_CRISSA / "lib/kanban_show_slim.py"', '_CREW / "lib" / "kanban_show_slim.py"', '"lib" / "board_parallel.py"'):
        assert gone not in src, gone
    for name in ("kanban_show_slim.py", "board_parallel.py"):     # crew/lib keeps its copy for the crew: one source
        if (CREW / "lib" / name).is_file():
            assert (CREW / "lib" / name).read_bytes() == (team / name).read_bytes(), name


def test_slimming_runs_from_the_shipped_lib(monkeypatch):
    mod, hook = _slim_hook(monkeypatch)
    assert hook(tool_name="kanban_show", result="not json") is None      # left exactly as it was
    assert mod._KANBAN_SHOW_SLIM.get("mod") is not None and mod._KANBAN_SHOW_SLIM["mod"] is not False


def test_a_missing_slimming_lib_is_named_once(tmp_path, monkeypatch, capsys):
    mod, hook = _slim_hook(monkeypatch)
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "__init__.py"))     # a plugin dir without its libs
    assert hook(tool_name="kanban_show", result="{}") is None
    assert hook(tool_name="kanban_show", result="{}") is None
    err = capsys.readouterr().err
    assert err.count("kanban_show slimming is off") == 1 and "kanban_show_slim.py is missing" in err, err


def test_parallel_board_reads_name_what_they_lack(tmp_path, monkeypatch, capsys):
    mod = load_plugin("team-crissa")
    monkeypatch.setenv("KREW_PARALLEL_BOARD_READS", "1")
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "__init__.py"))     # a plugin dir without its libs
    assert mod._register_parallel_board_reads() is False
    assert "KREW_PARALLEL_BOARD_READS is off" in capsys.readouterr().err
    monkeypatch.setenv("KREW_PARALLEL_BOARD_READS", "0")
    assert mod._register_parallel_board_reads() is False
    assert capsys.readouterr().err == "", "switched off on purpose says nothing"


def _load(rel: str, name: str):
    for p in (str(CREW / "lib"), str(CREW / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(name, CREW / "scripts" / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_config_restore_names_the_repins_it_cannot_run(tmp_path, monkeypatch, capsys):
    m = _load("krew-config-restore.py", "krew_config_restore_missing_t")
    monkeypatch.setattr(m, "SCRIPTS", tmp_path)
    m._repin_ops_for_occupancy()
    m._repin_turbo_krew()
    out = capsys.readouterr().out
    assert "ops occupancy re-pin not run" in out and "krew-mode is missing" in out
    assert "turbo re-pin not run" in out and "krew-turbo-krew.py is missing" in out


def test_config_restore_names_a_missing_kitting_plugin(tmp_path, monkeypatch, capsys):
    m = _load("krew-config-restore.py", "krew_config_restore_kit_t")
    monkeypatch.setenv("KREW_PACKS_ROOT", str(tmp_path / "packs"))
    live = m.LIVE / "crissa" / "config.yaml"
    m._apply_kitting(live)
    assert "skill allowlist not re-applied" in capsys.readouterr().out


def test_eval_rank_names_a_missing_seen_store(capsys):
    m = _load("team-eval-rank.py", "team_eval_rank_missing_t")
    if (CREW / "scripts" / "nnt-seen.py").is_file():
        return
    try:
        m._nnt_seen()
        raise AssertionError("a missing nnt-seen.py must not load")
    except FileNotFoundError:
        pass
    try:
        m._nnt_seen()
    except FileNotFoundError:
        pass
    assert capsys.readouterr().err.count("seen store off") == 1


def test_settings_check_names_the_missing_topology_lib():
    src = (CREW / "scripts" / "hermes-settings-check.sh").read_text()
    assert "lib/hermes_topology.py is missing" in src
    assert '"$topo" unit ada 2>/dev/null || true' in src and 'if [ -f "$topo" ]' in src
    r = subprocess.run(["bash", "-n", str(CREW / "scripts" / "hermes-settings-check.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
