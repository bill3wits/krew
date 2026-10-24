"""krew-render: orders file and move whole; the runner records outputs or the whole failure; cards coach only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import ROOT, load_plugin, submodule

GRAPH = {
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "positive": ["6", 0], "negative": ["7", 0],
                                               "model": ["4", 0], "latent_image": ["5", 0]}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "t"}},
}


@pytest.fixture
def studio(krew_home, tmp_path):
    wf, models = tmp_path / "wf", tmp_path / "models"
    (models / "checkpoints").mkdir(parents=True)
    (models / "checkpoints" / "base.safetensors").write_bytes(b"\0" * 1024)
    wf.mkdir()
    (wf / "still_t2i.json").write_text(json.dumps(GRAPH))
    recipes = json.loads((ROOT / "config" / "recipes.example.json").read_text())
    (krew_home / "recipes.json").write_text(json.dumps(recipes))
    (krew_home / "krew.yaml").write_text(
        f"render:\n  workflows_dir: {wf}\n  models_dir: {models}\n  orders_dir: {tmp_path / 'orders'}\n"
        f"  comfy_input_dir: {tmp_path / 'input'}\n  recipes: {krew_home / 'recipes.json'}\n")
    return tmp_path


def test_render_order_files_a_pending_order(studio):
    t = submodule("krew-render", "render_order_tool")
    out = t.file_order({"recipe": "still-t2i", "prompt": "a red kite", "negative": "blurry"}, by="lila")
    assert out.startswith("Filed order"), out
    ro = submodule("krew-render", "render_orders")
    [o] = ro.orders("pending")
    assert o["graph"]["6"]["inputs"]["text"] == "a red kite" and o["graph"]["7"]["inputs"]["text"] == "blurry"


def test_a_non_commercial_recipe_is_filed_with_a_warning_never_refused(studio):
    t = submodule("krew-render", "render_order_tool")
    out = t.file_order({"recipe": "clip-i2v", "prompt": "x"}, by="lila")
    assert "needs image=" in out                       # an i2v recipe needs its still
    rc = submodule("krew-render", "render_cards")
    assert rc.commercial({"license": "non-commercial research"}).startswith("NO")


def test_profiles_allowlist_is_checked_at_call_time(studio, krew_home):
    (krew_home / "krew.yaml").write_text((krew_home / "krew.yaml").read_text() + "  profiles: [lila]\n")
    t = submodule("krew-render", "render_order_tool")
    assert "Not filed" in t.file_order({"recipe": "still-t2i", "prompt": "x"}, by="vera")


class FakeComfy:
    def __init__(self, ok=True):
        self.ok, self.posted = ok, []

    def post(self, path, body):
        self.posted.append(path)
        return {"prompt_id": "p1"}

    def get(self, path):
        st = {"completed": self.ok, "status_str": "success" if self.ok else "error"}
        return {"p1": {"status": st, "outputs": {"9": {"images": [{"filename": "t_0001.png", "subfolder": "", "type": "output"}]}}}}

    def raw(self, path):
        return b"PNG"


def _runner():
    load_plugin("krew-render")
    sys.path.insert(0, str(ROOT / "plugins" / "krew-render"))
    import importlib
    for m in ("render_runner", "render_orders", "krew_settings"):
        sys.modules.pop(m, None)
    return importlib.import_module("render_runner")


def test_runner_records_outputs(studio):
    t = submodule("krew-render", "render_order_tool")
    t.file_order({"recipe": "still-t2i", "prompt": "x"}, by="lila")
    rr = _runner()
    assert rr.run(comfy=FakeComfy(), c={**rr.cfg(), "before_run": "", "after_run": ""}) == 0
    [o] = rr.ro.orders("done")
    assert o["outputs"] and Path(o["outputs"][0]).read_bytes() == b"PNG"


def test_runner_records_the_whole_failure(studio):
    t = submodule("krew-render", "render_order_tool")
    t.file_order({"recipe": "still-t2i", "prompt": "x"}, by="lila")
    rr = _runner()
    assert rr.run(comfy=FakeComfy(ok=False), c={**rr.cfg(), "before_run": "", "after_run": ""}) == 1
    [o] = rr.ro.orders("failed")
    assert "ComfyUI status error" in o["why"]


def test_cards_are_off_by_default_and_coach_when_on(studio, monkeypatch):
    rc = submodule("krew-render", "render_cards")
    assert rc.enabled() is False
    monkeypatch.setenv("KREW_RENDER_CARD", "1")
    assert rc.enabled() is True
    text = rc.turn_context("make a still-t2i of a lighthouse", [], task="")
    assert "RENDER CARD still-t2i" in text and "LOOK" in text
