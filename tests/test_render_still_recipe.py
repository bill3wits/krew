"""render-still enters Render with krew-mode's recipe name, not the plugin's (09-29)."""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "crew" / "scripts" / "render-still.py"
RECIPES = ROOT / "plugins" / "krew-comfy" / "recipes.json"


def _load():
    spec = importlib.util.spec_from_file_location("render_still", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _krew_mode_recipes() -> set:
    src = (ROOT / "crew" / "scripts" / "krew-mode").read_text()
    block = src[src.index("\nRECIPES = {"):]
    block = block[: block.index("\n}\n")]
    return set(re.findall(r'^\s+"([\w.-]+)": \{"need_gib"', block, re.M))


def test_every_local_t2i_recipe_maps_to_a_krew_mode_recipe():
    rs = _load()
    hm = _krew_mode_recipes()
    assert {"turbo", "klein", "sdxl", "qwen", "qwen-edit"} <= hm
    local = [r for r in json.loads(RECIPES.read_text())["recipes"]
             if r.get("kind") == "t2i" and r.get("workflow")]
    assert local
    for rec in local:
        got = rs.krew_mode_recipe(rec)
        assert got in hm, f"{rec['id']} -> {got!r} is not a krew-mode recipe"


def test_cloud_recipe_has_no_krew_mode_recipe():
    rs = _load()
    assert rs.krew_mode_recipe({"id": "xai-imagine"}) is None


def test_i2i_recipe_accepted_only_with_source():
    rs = _load()
    rec = rs.load_recipe("qwen-edit-2511", source="x.png")
    assert rec["kind"] == "i2i" and rec["image_node"] == "5"
    with pytest.raises(SystemExit) as e:
        rs.load_recipe("qwen-edit-2511")  # no --source: i2i is not a t2i path
    assert "--source" in str(e.value)


def test_t2i_with_source_is_refused():
    rs = _load()
    with pytest.raises(SystemExit) as e:
        rs.load_recipe("flux2-turbo", source="x.png")
    assert "is t2i" in str(e.value)


def test_kill_switch_passes_the_plugin_id(monkeypatch):
    rs = _load()
    monkeypatch.setenv("KREW_STILL_RECIPE_MAP", "0")
    assert rs.krew_mode_recipe({"id": "flux2-turbo"}) == "flux2-turbo"


@pytest.mark.parametrize("model,want", [(("flux2-turbo", None), "turbo"),
                                        (("qwen-image-2512", None), "qwen"),
                                        (("flux2-klein", None), "klein"),
                                        (("qwen-edit-2511", "src.png"), "qwen-edit")])
def test_main_calls_krew_mode_with_its_own_name(tmp_path, model, want):
    model, source = model
    log = tmp_path / "krew-mode.args"
    stub = tmp_path / "krew-mode"
    stub.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 1\n")
    stub.chmod(0o755)
    wf = tmp_path / "wf"
    wf.mkdir()
    inp = tmp_path / "in"
    inp.mkdir()
    rec = next(r for r in json.loads(RECIPES.read_text())["recipes"] if r["id"] == model)
    fake_wf = json.dumps({"1": {"class_type": "X", "inputs": {}}})
    if source:  # the edit leg needs the recipe's image node present
        fake_wf = json.dumps({str(rec.get("image_node", "5")):
                              {"class_type": "X", "inputs": {}}})
    (wf / rec["workflow"]).write_text(fake_wf)
    args = ["--card", "t_test", "--name", "x", "--prompt", "a fox", "--model", model]
    if source:
        src = tmp_path / source
        src.write_bytes(b"\x89PNG fake")
        args += ["--source", str(src)]
    env = {k: v for k, v in os.environ.items() if k != "HERMES_KANBAN_TASK"}
    env.update({
        "KREW_MODE_BIN": str(stub),
        "KREW_GPU_MODE_FILE": str(tmp_path / "mode"),
        "KREW_COMFY_WORKFLOWS": str(wf),
        "KREW_COMFY_INPUT": str(inp),
        "KREW_COMFY_RECIPES": str(RECIPES),
        "KREW_COMFY_URL": "http://127.0.0.1:9",
    })
    (tmp_path / "mode").write_text("normal\n")
    p = subprocess.run([sys.executable, str(SCRIPT), *args],
                       env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 3, p.stderr  # the stub refused; nothing drew
    assert (log.read_text().split() if log.exists() else []) == ["render", want], p.stderr


def test_i2i_without_source_refused_before_park(tmp_path):
    """--model qwen-edit-2511 without --source: refuse with rc 2, before the
    gate and before any park — a refused draw leaves ops parked for no reason."""
    log = tmp_path / "krew-mode.args"
    stub = tmp_path / "krew-mode"
    stub.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 0\n")
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "HERMES_KANBAN_TASK"}
    env.update({
        "KREW_MODE_BIN": str(stub),
        "KREW_GPU_MODE_FILE": str(tmp_path / "mode"),
        "KREW_COMFY_WORKFLOWS": str(tmp_path / "wf"),
        "KREW_COMFY_RECIPES": str(RECIPES),
        "KREW_COMFY_URL": "http://127.0.0.1:9",
    })
    (tmp_path / "mode").write_text("normal\n")
    p = subprocess.run([sys.executable, str(SCRIPT), "--card", "t_test", "--name", "x",
                        "--prompt", "a fox", "--model", "qwen-edit-2511"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert "pass --source" in p.stderr
    assert not log.exists()  # krew-mode never ran — a refused draw parks nothing


def _load_with_env(tmp_path, **env):
    """Import render-still with pointed env paths (module caches them)."""
    saved = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location("render_still_i2i", SCRIPT)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_i2i_dry_run_stages_source_and_feeds_image_node(tmp_path):
    """--model qwen-edit-2511 --source X --dry-run: source staged into Comfy
    input/ (plugin _stage_input_image parity), image node 5 fed the staged
    name, prompt patched, no size nodes, no park/submit."""
    rec = next(r for r in json.loads(RECIPES.read_text())["recipes"]
               if r["id"] == "qwen-edit-2511")
    wf = tmp_path / "wf"
    wf.mkdir()
    (wf / rec["workflow"]).write_text(
        json.dumps(json.loads(
            (ROOT / "plugins" / "krew-comfy" / "workflows" / rec["workflow"]).read_text())))   # krew's own copy
    inp = tmp_path / "in"
    inp.mkdir()
    src = tmp_path / "plate.png"
    src.write_bytes(b"\x89PNG fake")
    rs = _load_with_env(tmp_path, KREW_COMFY_WORKFLOWS=str(wf),
                        KREW_COMFY_INPUT=str(inp),
                        KREW_COMFY_RECIPES=str(RECIPES),
                        KREW_COMFY_URL="http://127.0.0.1:9")
    rec_ = rs.load_recipe("qwen-edit-2511", source=str(src))
    staged = rs.stage_source(str(src))
    assert staged.startswith("hermes_") and staged.endswith("plate.png")
    staged_file = inp / staged
    assert staged_file.is_file() and staged_file.read_bytes() == src.read_bytes()
    graph = rs.patch_graph(rec_, "a fox", 1024, 576, str(src), staged)
    assert graph[rec["image_node"]]["inputs"]["image"] == staged
    # prompt node patched; i2i has no size nodes so nothing else moved
    assert graph[rec["prompt_node"]]["inputs"].get("prompt") == "a fox"


def test_i2i_patch_refuses_without_source(tmp_path):
    rec = next(r for r in json.loads(RECIPES.read_text())["recipes"]
               if r["id"] == "qwen-edit-2511")
    wf = tmp_path / "wf"
    wf.mkdir()
    (wf / rec["workflow"]).write_text(
        json.dumps(json.loads(
            (ROOT / "plugins" / "krew-comfy" / "workflows" / rec["workflow"]).read_text())))   # krew's own copy
    rs = _load_with_env(tmp_path, KREW_COMFY_WORKFLOWS=str(wf),
                        KREW_COMFY_RECIPES=str(RECIPES),
                        KREW_COMFY_URL="http://127.0.0.1:9")
    rec_ = rs.load_recipe("qwen-edit-2511", source="x.png")
    with pytest.raises(SystemExit) as e:
        rs.patch_graph(rec_, "a fox", 1024, 576, None, None)
    assert "--source" in str(e.value)


# The reference install's Qwen-Image 2.1 recipes (research licence, never shipped) are not in krew's recipes.json: the
# edit leg is tested on qwen-edit-2511, the edit of record.
