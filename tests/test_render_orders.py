"""Render orders (heavy-render H1, the User 09-29): a written list that empties as it is
processed. Everything here runs on tmp dirs except one read-only look at the real saved
workflows and model files."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import render_orders as ro  # noqa: E402

GB = 2 ** 30


@pytest.fixture
def box(tmp_path):
    wf, models, root = tmp_path / "workflows", tmp_path / "models", tmp_path / "orders"
    wf.mkdir()
    (models / "diffusion_models").mkdir(parents=True)
    (models / "text_encoders" / "gemma").mkdir(parents=True)
    (models / "loras" / "ltxv").mkdir(parents=True)

    def model(rel, gib):
        p = models / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as f:
            f.truncate(int(gib * GB))  # sparse: size without disk
        return p
    model("diffusion_models/flux2_dev_fp8mixed.safetensors", 30)
    model("text_encoders/mistral_3_small_flux2_fp8.safetensors", 8)
    model("vae/flux2-vae.safetensors", 0.5)
    model("diffusion_models/flux-2-klein-4b-nvfp4.safetensors", 3)
    model("checkpoints/ltx-2.3-22b-dev-nvfp4.safetensors", 14)
    for i in range(1, 4):
        model(f"text_encoders/gemma/model-0000{i}-of-00003.safetensors", 2)
    model("loras/ltxv/distilled.safetensors", 1)
    graphs = {
        "flux2-dev": {"10": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux2_dev_fp8mixed.safetensors"}},
                      "11": {"class_type": "CLIPLoader", "inputs": {"clip_name": "mistral_3_small_flux2_fp8.safetensors"}},
                      "12": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
                      "20": {"class_type": "CLIPTextEncode", "inputs": {"text": "default prompt"}},
                      "30": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}}},
        "klein": {"10": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux-2-klein-4b-nvfp4.safetensors"}},
                  "12": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
                  "30": {"class_type": "KSampler", "inputs": {"seed": 42}}},
        "ltx23": {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-2.3-22b-dev-nvfp4.safetensors"}},
                  "2": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "ltxv/distilled.safetensors"}},
                  "3": {"class_type": "LTXVGemmaCLIPModelLoader",
                        "inputs": {"gemma_path": "gemma/model-00001-of-00003.safetensors"}},
                  "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42}}},
    }
    for n, g in graphs.items():
        (wf / f"{n}.json").write_text(json.dumps(g))
    (wf / "ui.json").write_text(json.dumps({"nodes": [{"type": "KSampler"}]}))
    idx = ro.model_index(models)
    return {"wf": wf, "models": models, "root": root, "idx": idx}


def _add(box, wf, **kw):
    kw.setdefault("by", "test")
    return ro.add(wf, root=box["root"], workflows_dir=box["wf"], index=box["idx"], **kw)


def test_an_order_is_one_whole_file_with_what_it_needs_derived(box):
    o = _add(box, "flux2-dev", overrides={"20.text": "a red fox"}, seed=7, by="crissa", card="t_1", priority=2)
    p = box["root"] / "pending" / f"{o['id']}.json"
    on_disk = json.loads(p.read_text())
    assert on_disk == o and o["state"] == "pending"
    assert o["graph"]["20"]["inputs"]["text"] == "a red fox" and o["graph"]["30"]["inputs"]["noise_seed"] == 7
    assert o["family"] == "flux2" and o["main"] == "flux2_dev_fp8mixed.safetensors"
    assert o["need_gib"] == 38.5  # 30 + 8 + 0.5, typed by nobody
    assert o["requested_by"] == "crissa" and o["card"] == "t_1" and o["out_dir"].endswith(o["id"])
    assert not list((box["root"] / "pending").glob(".*"))  # no temp file left behind


def test_sharded_folders_count_whole_and_subpaths_resolve(box):
    o = _add(box, "ltx23")
    assert o["family"] == "ltx23"
    assert o["need_gib"] == 21.0  # 14 + 3 shards x 2 + 1 lora
    assert sum(1 for m in o["models"] if "gemma" in m["path"]) == 3


@pytest.mark.parametrize("wf,kw,msg", [
    ("nope", {}, "no workflow"),
    ("ui", {}, "not an API-format graph"),
    ("flux2-dev", {"overrides": {"99.text": "x"}}, "no node '99'"),
    ("flux2-dev", {"overrides": {"text": "x"}}, "use <node>.<input>"),
    ("flux2-dev", {"by": ""}, "--by is required"),
])
def test_bad_orders_are_refused_and_nothing_is_written(box, wf, kw, msg):
    with pytest.raises(ro.OrderError, match=msg):
        _add(box, wf, **kw)
    assert not (box["root"] / "pending").exists() or not list((box["root"] / "pending").iterdir())


def test_a_missing_model_file_is_refused(box):
    (box["wf"] / "ghost.json").write_text(json.dumps(
        {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": "not-on-disk.safetensors"}}}))
    with pytest.raises(ro.OrderError, match="not-on-disk.safetensors"):
        _add(box, "ghost")


def test_a_seed_on_a_graph_without_one_is_refused(box):
    (box["wf"] / "noseed.json").write_text(json.dumps(
        {"1": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}}}))
    with pytest.raises(ro.OrderError, match="no seed input"):
        _add(box, "noseed", seed=3)


def test_moves_are_atomic_claims_and_history_is_kept(box):
    o = _add(box, "klein")
    r = ro.move(o["id"], "running", root=box["root"])
    assert r["state"] == "running" and not (box["root"] / "pending" / f"{o['id']}.json").exists()
    with pytest.raises(ro.OrderError, match="running -> running"):
        ro.move(o["id"], "running", root=box["root"])
    d = ro.move(o["id"], "done", root=box["root"], outputs=["/x.png"])
    assert d["outputs"] == ["/x.png"] and [h["to"] for h in d["history"]] == ["running", "done"]
    with pytest.raises(ro.OrderError, match="done -> pending"):
        ro.move(o["id"], "pending", root=box["root"])


def test_cancel_only_a_pending_order(box):
    o = _add(box, "klein")
    c = ro.cancel(o["id"], by="the User", root=box["root"])
    assert c["state"] == "failed" and c["cancelled"] is True and c["history"][-1]["why"] == "cancelled by the User"
    with pytest.raises(ro.OrderError, match="only a pending"):
        ro.cancel(o["id"], by="x", root=box["root"])


def test_plan_batches_by_family_priority_first_union_counted_once(box):
    a = _add(box, "flux2-dev")
    b = _add(box, "klein")
    c = _add(box, "flux2-dev", overrides={"20.text": "second"})
    d = _add(box, "ltx23", priority=5)
    batches = ro.plan(root=box["root"])
    assert [x["family"] for x in batches] == ["ltx23", "flux2", "flux2-klein"]
    flux = batches[1]
    assert flux["orders"] == [a["id"], c["id"]] and flux["n"] == 2
    assert flux["need_gib"] == 38.5  # the same weights twice are held once
    assert batches[0]["orders"] == [d["id"]] and batches[0]["top_priority"] == 5
    assert b["id"] in batches[2]["orders"]


@pytest.mark.parametrize("main,fam", [
    ("flux-2-klein-4b-nvfp4.safetensors", "flux2-klein"), ("flux2-dev-Q4_K_M.gguf", "flux2"),
    ("flux1-dev-fp8.safetensors", "flux1"), ("qwen_image_edit_2511_fp8mixed.safetensors", "qwen-edit"),
    ("qwen_image_2512_fp8_e4m3fn.safetensors", "qwen-image"),
    ("ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", "ltx25"),
    ("ltx-2.3-22b-dev-nvfp4.safetensors", "ltx23"), ("wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "wan22"),
    ("sd_xl_base_1.0.safetensors", "sdxl"), ("mystery.safetensors", "other:mystery"),
])
def test_families_from_the_main_weights(main, fam):
    assert ro.family_of(main) == fam


def test_the_cli_round_trip(box, tmp_path):
    env = dict(os.environ, KREW_RENDER_ORDERS=str(box["root"]), KREW_RENDER_WORKFLOWS=str(box["wf"]),
               KREW_RENDER_MODELS=str(box["models"]))
    cli = [sys.executable, str(ROOT / "crew" / "scripts" / "render-orders.py")]
    r = subprocess.run(cli + ["add", "--workflow", "flux2-dev", "--set", "20.text=a red fox", "--seed", "9",
                              "--by", "crissa"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    oid = r.stdout.split()[0]
    assert "family=flux2" in r.stdout
    assert oid in subprocess.run(cli + ["list", "--state", "pending"], capture_output=True, text=True, env=env).stdout
    shown = json.loads(subprocess.run(cli + ["show", oid], capture_output=True, text=True, env=env).stdout)
    assert shown["overrides"] == {"20.text": "a red fox"} and "graph" not in shown
    assert "flux2" in subprocess.run(cli + ["plan"], capture_output=True, text=True, env=env).stdout
    bad = subprocess.run(cli + ["add", "--workflow", "flux2-dev", "--set", "oops", "--by", "x"],
                         capture_output=True, text=True, env=env)
    assert bad.returncode == 2 and "refused" in bad.stderr
    assert subprocess.run(cli + ["cancel", oid, "--by", "the User"], capture_output=True, text=True,
                          env=env).returncode == 0


WF_LIVE = Path(os.path.expanduser(os.environ.get("KREW_COMFY_WORKFLOWS") or os.path.join(os.environ.get("KREW_COMFY_ROOT") or "~/ComfyUI", "workflows")))


@pytest.mark.skipif(not WF_LIVE.is_dir(), reason="no saved workflows on this machine (KREW_COMFY_ROOT)")
def test_every_saved_api_workflow_gets_a_family_and_its_models_resolve():
    """Read-only: the house's own 14 API workflows (apple_split_i2v is a UI graph)."""
    idx = ro.model_index()
    fams = {}
    for p in sorted(WF_LIVE.glob("*.json")):
        if ".bak" in p.name or "smoke" in p.name:
            continue
        try:
            _n, g = ro.load_workflow(str(p))
        except ro.OrderError:
            continue
        m = ro.models_of(g, index=idx)
        assert m["missing"] == [], (p.name, m["missing"])
        fams[p.stem] = ro.family_of(m["main"])
    assert not [f for f in fams.values() if f.startswith("other:")], fams
    assert fams["flux2-turbo-txt2img"] == fams["flux2-dev-txt2img"] == "flux2"
    assert fams["flux2-klein-txt2img"] == "flux2-klein" and fams["ltx25-i2v"] == "ltx25"


def test_a_dead_runners_order_goes_back_to_pending(box, monkeypatch):
    """H3 crash drill 09-29: a SIGKILLed runner left its order in running/ for good."""
    root = box["root"]
    dead = _add(box, "klein")
    live = _add(box, "klein")
    ro.move(dead["id"], "running", root=root, runner_pid=999999991)
    ro.move(live["id"], "running", root=root, runner_pid=os.getpid())
    back = ro.requeue_orphans(root=root, alive=lambda pid: pid == os.getpid())
    assert back == [dead["id"]]
    assert ro.show(dead["id"], root=root)["state"] == "pending"
    assert ro.show(live["id"], root=root)["state"] == "running"
    monkeypatch.setenv("KREW_ORDERS_REQUEUE", "0")
    ro.move(dead["id"], "running", root=root, runner_pid=999999991)
    assert ro.requeue_orphans(root=root, alive=lambda pid: False) == []
