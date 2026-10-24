"""The reference install's plugin code, lifted into krew (the owner 2026-10-10): each plugin carries the reference's
functions by name, behaves as the reference did where krew did not change it on purpose, and imports from a temp
tree of only its own package's files."""
from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from conftest import ROOT, load_plugin

MANIFEST = json.loads((ROOT / "config" / "krew-packages.json").read_text(encoding="utf-8"))


def _package_files(plugin: str) -> list[str]:
    """The files the manifests (main + fragments) place under plugins/<plugin>/."""
    out = []
    docs = [MANIFEST] + [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "config" / "krew-packages.d").glob("*.json"))]
    for d in docs:
        for pkg in (d.get("packages") or {}).values():
            for f in pkg.get("files") or []:
                if f.startswith(f"plugins/{plugin}/"):
                    out += [p.relative_to(ROOT).as_posix() for p in ROOT.glob(f) if p.is_file()] if "*" in f else [f]
    return sorted(set(out))


def _load_from_own_files(plugin: str, tmp_path: Path):
    """Import plugins/<plugin> from a temp tree holding only the files its package lists."""
    files = _package_files(plugin)
    assert files, plugin
    tree = tmp_path / "tree" / plugin
    for rel in files:
        dst = tree / Path(rel).relative_to(f"plugins/{plugin}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dst)
    name = "krewown_" + plugin.replace("-", "_")
    for k in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[k]
    spec = importlib.util.spec_from_file_location(name, tree / "__init__.py", submodule_search_locations=[str(tree)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Ctx:
    def __init__(self, profile="bash"):
        self.profile_name, self.tools, self.hooks = profile, {}, {}

    def register_tool(self, name=None, toolset=None, schema=None, handler=None, **kw):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kw}

    def register_hook(self, event, fn):
        self.hooks.setdefault(event, []).append(fn)


# ---- krew-board: the reference's team_board, team_time, team_config_audit ------------------------------------
@pytest.fixture
def board():
    return importlib.import_module(load_plugin("krew-board").__name__ + ".board")


def _cli(replies=None):
    calls = []

    def cli(args, *a, **k):
        calls.append(list(args))
        for key, val in (replies or {}).items():
            if args and args[0] == key:
                return val
        return 0, ""
    cli.calls = calls
    return cli


def _list_item(root: Path, item_id: str, card: str, source: str = "zoey"):
    d = root / "pending"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{item_id}.json").write_text(json.dumps({"id": item_id, "card_id": card, "source": source, "title": "q"}))


def test_board_carries_the_reference_functions_by_name(board):
    for name in ("_board_tally", "_board_ts", "_board_hhmm", "_parse_window", "_window_seconds", "_board_head",
                 "_board_line", "_clip_marked", "_board_block_reason", "_board_show_text", "_list_clear_card",
                 "_list_is_users", "_answer_holds", "_answer_done", "_later_date", "_is_done_ruling", "_card_body",
                 "_commercial_render_line", "_xspaces_drill_beat", "team_board", "team_time", "config_audit"):
        assert callable(getattr(board, name)), name
    assert board.ACTIONS == ("list", "show", "create", "comment", "unblock", "complete", "answer")


def test_answer_lands_the_words_unblocks_and_clears_only_the_teams_row(board, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "_LIST_ROOT", tmp_path)
    _list_item(tmp_path, "z1", "t_aa", source="zoey")
    _list_item(tmp_path, "u1", "t_aa", source="restart-window")      # the owner's ask, filed by another tool
    cli = _cli({"show": (0, json.dumps({"task": {"id": "t_aa", "body": "do the thing"}}))})
    out = board.team_board({"action": "answer", "id": "t_aa", "text": "try it"}, cli=cli, author="zoey")
    assert out.startswith("Landed on t_aa and cleared Crissa's list")
    assert ["comment", "t_aa", "try it", "--author", "zoey"] in cli.calls and cli.calls[-1][:2] == ["unblock", "t_aa"]
    assert (tmp_path / "done" / "z1.json").exists() and (tmp_path / "pending" / "u1.json").exists()


def test_a_park_ruling_holds_the_card_off_the_dispatcher(board, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "_LIST_ROOT", tmp_path)
    cli = _cli({"show": (0, json.dumps({"task": {"id": "t_bb", "body": ""}}))})
    out = board.team_board({"action": "answer", "id": "t_bb", "text": "park: not now"}, cli=cli, author="zoey")
    assert "ruling held for kanban-loop" in out and not any(c[0] == "unblock" for c in cli.calls)


def test_a_dated_park_is_one_write_to_scheduled(board, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "_LIST_ROOT", tmp_path)
    cli = _cli({"show": (0, json.dumps({"task": {"id": "t_cc", "body": ""}}))})
    out = board.team_board({"action": "answer", "id": "t_cc", "text": "later 2026-11-01"}, cli=cli, author="zoey")
    assert "parked until 2026-11-01" in out
    assert [c[:2] for c in cli.calls if c[0] == "schedule"] == [["schedule", "t_cc"]]


def test_done_closes_only_when_switched_on(board, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "_LIST_ROOT", tmp_path)
    monkeypatch.setenv("KREW_ZOEY_DONE_CLOSES", "1")
    cli = _cli()
    out = board.team_board({"action": "answer", "id": "t_dd", "text": "done"}, cli=cli, author="zoey")
    assert "closed in the same step" in out and cli.calls[-1][:2] == ["complete", "t_dd"]


def test_complete_and_unblock_clear_the_list_row(board, tmp_path, monkeypatch):
    monkeypatch.setattr(board, "_LIST_ROOT", tmp_path)
    _list_item(tmp_path, "z2", "t_ee")
    assert board.team_board({"action": "complete", "id": "t_ee"}, cli=_cli()) == "Completed t_ee."
    assert (tmp_path / "done" / "z2.json").exists()


def test_a_commercial_render_card_carries_its_engines(board):
    cli = _cli({"create": (0, "created t_0123abcd")})
    out = board.team_board({"action": "create", "title": "hero render for the launch", "text": "a still"},
                           cli=cli, author="zoey", default_assignee="ada")
    assert out == "Filed for ada: hero render for the launch (t_0123abcd)."
    body = cli.calls[0][cli.calls[0].index("--body") + 1]
    assert "qwen-image-2512" in body and "Never flux2-*" in body
    assert cli.calls[0][2:6] == ["--assignee", "ada", "--created-by", "zoey"] and cli.calls[0][-1] == "--triage"


def test_the_register_schema_offers_answer_and_triage(tmp_path):
    ctx = Ctx("zoey")
    load_plugin("krew-board").register(ctx)
    params = ctx.tools["team_board"]["schema"]["parameters"]
    assert "answer" in params["properties"]["action"]["enum"] and "triage" in params["properties"]
    assert params["required"] == ["action"] and set(ctx.tools) == {"team_board", "team_time", "team_config_audit"}


def test_board_imports_from_its_own_files(tmp_path):
    mod = _load_from_own_files("krew-board", tmp_path)
    ctx = Ctx()
    mod.register(ctx)
    assert "team_board" in ctx.tools


# ---- krew-kanban-lean, krew-docs, krew-render, krew-comfy, krew-complete-probe ------------------------------------
@pytest.mark.parametrize("plugin,names", [
    ("krew-kanban-lean", ("register", "thin", "LEAN", "TOOLSET")),
    ("krew-docs", ("register", "profile_of", "profile_on", "_docs", "_declare_empty", "SWITCHES", "RUNTIME_KW")),
    ("krew-render", ("register", "on_pre_llm_call", "on_tool_result", "render_order", "_profile", "_rc", "_ORDER_SCHEMA")),
    ("krew-comfy", ("register", "load_recipes", "recipe_wired", "default_image_model", "commercial_ok")),
    ("krew-complete-probe", ("initialize", "register", "_log", "SPIKE_DIR")),
])
def test_each_plugin_carries_the_reference_names_and_imports_from_its_own_files(plugin, names, tmp_path):
    mod = _load_from_own_files(plugin, tmp_path)
    for n in names:
        assert hasattr(mod, n), (plugin, n)


def test_kanban_lean_says_the_way_back():
    doc = load_plugin("krew-kanban-lean").__doc__
    assert "23,442 B" in doc and "crew/scripts/kanban-lean.sh off" in doc and (ROOT / "crew" / "scripts" / "kanban-lean.sh").is_file()


def test_docs_reads_the_reference_switch_file_first_then_krew_yaml(krew_home):
    m = load_plugin("krew-docs")
    f = krew_home / "krew-docs.json"
    assert m.profile_on("steward", path=f) is True                    # no file: krew.yaml's list ([] = everyone)
    f.write_text(json.dumps({"profiles": {"steward": True}}))
    assert m.profile_on("steward", path=f) is True and m.profile_on("judge", path=f) is False
    assert m.list_on("judge", settings={"profiles": ["bash"]}) is False


def test_docs_declares_an_empty_toolset_for_an_off_profile(krew_home, monkeypatch):
    (krew_home / "krew-docs.json").write_text(json.dumps({"profiles": {}}))
    m = load_plugin("krew-docs")
    said = []
    monkeypatch.setattr(m, "_declare_empty", lambda: said.append(1))
    ctx = Ctx("judge")
    m.register(ctx)
    assert said == [1] and "krew_docs" not in ctx.tools


def test_render_registers_the_verb_and_both_card_hooks():
    ctx = Ctx("lila")
    load_plugin("krew-render").register(ctx)
    assert ctx.tools["render_order"]["toolset"] == "image_gen" and ctx.tools["render_order"]["emoji"] == "🎬"
    assert ctx.tools["render_order"]["schema"]["parameters"]["required"] == ["recipe", "prompt"]
    assert set(ctx.hooks) == {"pre_llm_call", "transform_tool_result"}


def test_comfy_default_still_stays_commercial_ok():
    m = load_plugin("krew-comfy")
    recs = {r["id"]: r for r in m.load_recipes()}
    assert not any(rid.startswith("qwen-image-2.1") or rid.startswith("qwen-edit-2.1") for rid in recs)   # reference-only asset (research licence)
    for rid in ("flux2-turbo", "flux2-klein", "flux2-max", "ltx25-i2v"):
        assert recs[rid].get("opt_in") is True and not m.commercial_ok(recs[rid]), rid


def test_the_probe_logs_under_krew_home_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_KANBAN_HOOK_SPIKE_DIR", str(tmp_path / "spike"))
    m = load_plugin("krew-complete-probe")
    ctx = Ctx()
    m.register(ctx)
    ctx.hooks["kanban_task_completed"][0](task_id="t_1", obj=object())
    rows = [json.loads(x) for f in (tmp_path / "spike").glob("hook-*.jsonl") for x in f.read_text().splitlines()]
    assert rows and rows[0]["hook_kwargs"]["task_id"] == "t_1"
    profile_kit = json.loads((ROOT / "crew" / "config" / "profile-kit.json").read_text())
    assert "krew-complete-probe" not in json.dumps(profile_kit.get("plugins") or {})    # enabled nowhere, as the reference
