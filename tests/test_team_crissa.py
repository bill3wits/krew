"""team-crissa (lane C, group 2): the whole plugin ships. With krew-board beside it every one of the 26 team tools is
registered exactly once (krew-board: team_board, team_time, team_config_audit; team-crissa: the rest); no house
endpoint, person or path is hard-coded; agent-facing cuts are gone or marked."""
from __future__ import annotations

import re

from conftest import ROOT, load_plugin

BOARD = {"team_board", "team_time", "team_config_audit"}


class Ctx:
    def __init__(self, profile="crissa"):
        self.profile_name = profile
        self.tools, self.hooks = {}, []

    def register_tool(self, name=None, toolset=None, schema=None, handler=None, **kw):
        assert name not in self.tools, f"{name} registered twice"
        self.tools[name] = (toolset, schema, handler)

    def register_hook(self, name, fn):
        self.hooks.append(name)

    def __getattr__(self, k):                 # anything else a plugin may ask the context for
        return lambda *a, **kw: None


def _register(profile, monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    ctx = Ctx(profile)
    load_plugin("team-crissa").register(ctx)
    return ctx


def test_team_crissa_and_krew_board_together_register_every_tool_once(monkeypatch):
    tz = _register("bash", monkeypatch)
    kb = Ctx("bash")
    load_plugin("krew-board").register(kb)
    assert not BOARD & set(tz.tools), "team-crissa must leave krew-board's tools to krew-board"
    assert BOARD <= set(kb.tools)
    every = set(tz.tools) | set(kb.tools) | set(_register("crissa", monkeypatch).tools)
    assert len(every) >= 26, sorted(every)
    for name in ("team_speak", "team_announce", "team_page", "team_scout", "team_list", "team_needs_sheet",
                 "team_outbox", "team_presence", "team_look", "show_me", "team_compose", "team_morning_brief"):
        assert name in every, name


def test_crissa_gets_her_own_tools(monkeypatch):
    tz = _register("crissa", monkeypatch)
    assert "team_speak" in tz.tools and "team_page" not in tz.tools   # Crissa does not page herself


def test_switch_off_registers_team_crissas_own_board_tools(monkeypatch):
    tz = _register("bash", monkeypatch, KREW_TEAM_BOARD_FROM_KREW_BOARD="0")
    assert BOARD <= set(tz.tools)


def test_local_urls_are_movable(monkeypatch):
    monkeypatch.setenv("KREW_PORT_8099", "9099")
    monkeypatch.setenv("KREW_LOCAL_HOST", "crissa.local")
    m = load_plugin("team-crissa")
    assert m._local(8099, "/panel") == "http://crissa.local:9099/panel"


def test_coach_dont_cap():
    text = (ROOT / "plugins" / "team-crissa" / "__init__.py").read_text(encoding="utf-8")
    for pat in (r"body\[:500\]", r"\)\[-5:\]", r"note\[:500\]", r"rows\[:30\]", r"ensure_ascii=False\)\[:4000\]",
                r"out\[-900:\]", r"steps\[:20\]", r"text\[:800\]", r"ask\[:800\]", r"\(body or title\)\[:8000\]"):
        assert not re.search(pat, text), pat
    m = load_plugin("team-crissa")
    assert m._cut("x" * 20, 5, "more") == "xxxxx [cut at 5 of 20 chars; more]"


def _fresh_homes(tmp_path, monkeypatch):
    home, hh = tmp_path / "home", tmp_path / "hermes-home"
    (home / ".hermes").mkdir(parents=True)
    hh.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("KREW_ANNOUNCE_HOOK_DEDUP", raising=False)
    return home, hh


def test_announce_hook_dedup_follows_hermes_home(tmp_path, monkeypatch):
    """The panel proof found the dedup file written to ~/.hermes while HERMES_HOME pointed at a scratch home."""
    home, hh = _fresh_homes(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(hh))
    tz = load_plugin("team-crissa")
    assert tz._ANNOUNCE_HOOK_DEDUP == hh / "krew-announce-hook-dedup.json"
    assert tz._dedup_skip("t_dedup") is False and tz._dedup_skip("t_dedup") is True   # second enqueue coalesced
    assert (hh / "krew-announce-hook-dedup.json").is_file()
    assert not (home / ".hermes" / "krew-announce-hook-dedup.json").exists(), "wrote into the default Hermes home"


def test_announce_hook_dedup_override_and_default(tmp_path, monkeypatch):
    home, hh = _fresh_homes(tmp_path, monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(hh))
    monkeypatch.setenv("KREW_ANNOUNCE_HOOK_DEDUP", str(tmp_path / "mine.json"))
    assert load_plugin("team-crissa")._ANNOUNCE_HOOK_DEDUP == tmp_path / "mine.json"     # the env var still wins
    monkeypatch.delenv("KREW_ANNOUNCE_HOOK_DEDUP")
    monkeypatch.delenv("HERMES_HOME")
    assert load_plugin("team-crissa")._ANNOUNCE_HOOK_DEDUP == home / ".hermes" / "krew-announce-hook-dedup.json"
