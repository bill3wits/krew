"""krew-render's cards (the owner 2026-10-06): a recipe's card, only while the renderer is in play; coaching, never
blocking. Lifted from the reference install's render-card tests against plugins/krew-render (render_cards.py,
render_order_tool.py, the plugin's hooks). Two reference tests stay behind: the live-plugin == pack-copy check (a
deploy check of that machine) and the render skills' wording (the skills ship separately and carry their own tests)."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from conftest import ROOT, load_plugin

RS = [
    {"id": "qwen-image-2512", "kind": "t2i", "license": "Apache 2.0", "workflow": "qwen-image-2512-txt2img.json",
     "prompt_node": "5", "purpose": "Apache still.", "vram_gib": 30},
    {"id": "flux2-turbo", "kind": "t2i", "license": "BFL internal", "workflow": "flux2-turbo-txt2img.json",
     "prompt_node": "6", "purpose": "Fast photoreal still."},
    {"id": "wan22-i2v", "kind": "i2v", "license": "open", "workflow": "apple_split_wan_i2v.json", "prompt_node": "9",
     "image_node": "11", "purpose": "Clip from a still."},
    {"id": "ltx25-i2v", "kind": "i2v", "license": "LTX-2 Community", "workflow": "ltx25-i2v.json"},
    {"id": "ltx25-flf2v", "kind": "flf2v", "license": "LTX-2 Community", "workflow": "ltx25-flf2v.json"},
]


@pytest.fixture
def plugin():
    return load_plugin("krew-render")


@pytest.fixture
def rc(plugin):
    return importlib.import_module(plugin.__name__ + ".render_cards")


@pytest.fixture
def rot(plugin):
    return importlib.import_module(plugin.__name__ + ".render_order_tool")


def test_off_unless_switched_on(rc, tmp_path):
    f = tmp_path / "on"
    assert not rc.enabled({}, on_file=f, settings={})
    f.write_text("")
    assert rc.enabled({}, on_file=f, settings={})
    assert not rc.enabled({"KREW_RENDER_CARD": "0"}, on_file=f, settings={})
    assert rc.enabled({"KREW_RENDER_CARD": "1"}, on_file=tmp_path / "absent", settings={})
    assert rc.enabled({}, on_file=tmp_path / "absent", settings={"cards": True})       # krew.yaml render.cards


def test_licence_decides_commercial(rc):
    assert rc.commercial(RS[0]) == "yes"
    assert rc.commercial(RS[1]).startswith("NO")
    assert rc.commercial(RS[3]).startswith("NO")


def test_card_carries_the_order_path_inputs_and_the_look_step(rc):
    c = rc.card(RS[2])
    assert "render-orders.py add --workflow apple_split_wan_i2v" in c and "--set 11.image=" in c
    assert "render_order recipe=wan22-i2v" in c and "image=<still>" in c
    assert "a still" in c and "look_compare" in c and "PASS or FAIL" in c and "commercial: yes" in c
    assert len(c) <= rc.CARD_MAX


def test_a_named_recipe_brings_its_card_and_nothing_else_brings_nothing(rc):
    assert rc.turn_context("render it with wan22-i2v", [], mode="normal", rs=RS).startswith("RENDER CARD wan22-i2v")
    assert rc.turn_context("what's the weather", [], mode="normal", rs=RS, task="") == ""
    menu = rc.turn_context("anything", [], mode="heavy-render", rs=RS, task="")
    assert menu.startswith("RENDERERS") and "flux2-turbo (t2i) commercial: NO" in menu


def test_the_card_leaves_with_the_window(rc):
    hist = [{"role": "user", "content": "render the hero with qwen-image-2512"}]
    assert "qwen-image-2512" in rc.turn_context("and the clip?", hist, mode="normal", rs=RS, task="")
    assert rc.turn_context("thanks, that's all", [{"role": "user", "content": "the weather"}], mode="normal", rs=RS,
                           task="") == ""


def test_longest_id_wins(rc):
    assert [r["id"] for r in rc.named("use ltx25-i2v", RS)] == ["ltx25-i2v"]


def test_tool_calls_name_their_recipe(rc):
    assert rc.tool_recipe("image_generate", {"model": "qwen-image-2512"}) == "qwen-image-2512"
    assert rc.tool_recipe("render_order", {"recipe": "wan22-i2v"}) == "wan22-i2v"
    cmd = "python3 crew/scripts/render-orders.py add --workflow apple_split_wan_i2v --set 9.text=x --by lila"
    assert rc.recipe_for(rc.tool_recipe("terminal", {"command": cmd}), RS)["id"] == "wan22-i2v"
    assert rc.tool_recipe("terminal", {"command": "ls"}) == ""


def test_plugin_is_silent_when_off_and_cards_a_tool_once(plugin, rc, monkeypatch):
    plugin._MOD["m"] = rc
    plugin._SEEN.clear()
    monkeypatch.setattr(rc, "recipes", lambda paths=None: RS)
    monkeypatch.setattr(rc, "task_text", lambda env=None: "")
    monkeypatch.setenv("KREW_RENDER_CARD", "0")
    assert plugin.on_pre_llm_call(user_message="render with wan22-i2v", conversation_history=[]) is None
    assert plugin.on_tool_result(tool_name="image_generate", args={"model": "qwen-image-2512"}, result="ok", session_id="s") is None
    monkeypatch.setenv("KREW_RENDER_CARD", "1")
    first = plugin.on_tool_result(tool_name="image_generate", args={"model": "qwen-image-2512"}, result="ok", session_id="s")
    assert first.startswith("ok\n\nRENDER CARD qwen-image-2512")
    assert plugin.on_tool_result(tool_name="image_generate", args={"model": "qwen-image-2512"}, result="ok", session_id="s") is None
    assert plugin.on_pre_llm_call(user_message="render with wan22-i2v", conversation_history=[])["context"].startswith("RENDER CARD")


def test_a_kanban_worker_reads_its_own_card(rc, tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "k.db"
    con = sqlite3.connect(db)
    con.execute("create table tasks (id text, title text, body text)")
    con.execute("create table task_comments (id integer primary key, task_id text, author text, body text, created_at int)")
    con.execute("insert into tasks values ('t_1', 'Hero clip', 'render a clip')")
    con.execute("insert into task_comments (task_id, author, body) values ('t_1', 'user', 'use wan22-i2v from the still')")
    con.commit()
    con.close()
    env = {"HERMES_KANBAN_TASK": "t_1", "HERMES_KANBAN_DB": str(db)}
    assert "wan22-i2v" in rc.task_text(env)
    assert rc.task_text({}) == ""
    monkeypatch.setattr(rc, "task_text", lambda env=None: "use wan22-i2v from the still")
    assert rc.turn_context("work kanban task t_1", [], mode="normal", rs=RS).startswith("RENDER CARD wan22-i2v")


def test_render_order_maps_a_recipe_to_its_nodes_and_stages_the_still(rot, tmp_path):
    graph = {"5": {"class_type": "CLIPTextEncode", "inputs": {}}, "6": {"class_type": "CLIPTextEncode", "inputs": {}},
             "3": {"class_type": "KSampler", "inputs": {"positive": ["5", 0], "negative": ["6", 0]}}}
    rs = RS + [{"id": "qwen-image-2512", "kind": "t2i", "license": "Apache 2.0", "workflow": "q.json",
                "prompt_node": "5", "size_nodes": ["7"]}]
    calls = []
    add = lambda wf, **k: calls.append((wf, k)) or {"id": "ro-1", "need_gib": 1, "out_dir": "/o"}  # noqa: E731
    out = rot.file_order({"recipe": "qwen-image-2512", "prompt": "a case", "negative": "speaker", "width": 1296,
                          "seed": 7, "card": "t_1"}, by="lila", add=add, load_workflow=lambda wf: (wf, graph), rs=rs[-1:])
    assert "Filed order ro-1" in out and "WARNING" not in out
    assert calls[0][1]["overrides"] == {"5.text": "a case", "6.text": "speaker", "7.width": 1296}
    still = tmp_path / "s.png"
    still.write_bytes(b"x")
    out = rot.file_order({"recipe": "wan22-i2v", "prompt": "arc", "image": str(still), "card": "t_1"}, by="lila",
                         add=add, load_workflow=lambda wf: (wf, graph), rs=RS, input_dir=tmp_path / "in")
    assert (tmp_path / "in" / "t_1-s.png").exists() and calls[-1][1]["overrides"]["11.image"] == "t_1-s.png"
    assert "needs image=" in rot.file_order({"recipe": "wan22-i2v", "prompt": "x"}, by="lila", add=add, rs=RS)
    assert "WARNING" in rot.file_order({"recipe": "flux2-turbo", "prompt": "x"}, by="lila", add=add,
                                       load_workflow=lambda wf: (wf, graph), rs=RS)
    assert "not an order-queue recipe" in rot.file_order({"recipe": "nope", "prompt": "x"}, by="lila", add=add, rs=RS)


def test_render_order_kill_switch(rot, monkeypatch):
    monkeypatch.setenv("KREW_RENDER_ORDER_TOOL", "0")
    assert "switched off" in rot.file_order({"recipe": "wan22-i2v", "prompt": "x"}, by="lila", rs=RS)


def test_only_the_renderers_file_orders(rot, monkeypatch):
    assert rot.allowed("lila", {}) and rot.allowed("bash", {}) and not rot.allowed("vera", {})
    # krew names the list it read (render.profiles, default lila + bash) where the reference said "reviews"
    assert "is not one of them" in rot.file_order({"recipe": "wan22-i2v", "prompt": "x"}, by="vera", rs=RS, settings={})
    monkeypatch.setenv("KREW_RENDER_ORDER_PROFILES", "vera")
    assert rot.allowed("vera", {}) and not rot.allowed("lila", {})


def test_ltx23_is_commercial_under_lightricks_terms(rc):
    """LTX-2.3 is Lightricks' community licence, commercial under $10M annual revenue with outputs labelled
    AI-generated and no competing product; never labelled 'not commercial'."""
    recs = {r["id"]: r for r in json.loads((ROOT / "plugins" / "krew-comfy" / "recipes.json").read_text())["recipes"]}
    for rid in ("ltx23-nvfp4", "ltx23-flf2v"):
        assert "$10M" in recs[rid]["license"] and "AI-generated" in recs[rid]["license"] and "competing" in recs[rid]["license"]
        assert rc.commercial(recs[rid]).startswith("yes - under $10M")
    assert rc.commercial({"license": "LTX-2 Community"}).startswith("NO")


def test_recipes_fall_back_to_krew_comfys_file(rc, tmp_path):
    """No render.recipes and no $KREW_HOME/recipes.json: the cards read krew-comfy's recipes.json beside the plugin
    (the reference read its own comfy plugin's file the same way)."""
    assert rc.RECIPE_PATHS[0].parts[-2:] == ("krew-comfy", "recipes.json")
    rs = rc.recipes((tmp_path / "missing.json", ROOT / "plugins" / "krew-comfy" / "recipes.json"))
    assert any(r["id"] == "qwen-image-2512" for r in rs)
