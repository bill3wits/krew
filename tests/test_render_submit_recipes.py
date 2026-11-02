"""render-submit recipe table (2026-09-19).

The krew-comfy plugin refuses ltx23-flf2v and ltx25-flf2v as wall mints and
sends workers to render-submit.py, where ltx23-flf2v was missing: the M&S
Comfy v0.36.0 smoke (t_e336b159) had no way to run its LTX 2.3 hub test.
The 2.3 graph has the same node ids as 2.5, checked against the workflow.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "crew" / "scripts" / "render-submit.py").read_text(encoding="utf-8")
WF = Path("/data/comfy/workflows")


def _recipe_block(name: str) -> str:
    m = re.search(r'^\s*"%s": \{(.*?)\},\s*$' % re.escape(name), SRC, re.M | re.S)
    assert m, name
    return m.group(1)


def test_ltx23_flf2v_is_a_submit_recipe_like_ltx25():
    a, b = _recipe_block("ltx23-flf2v"), _recipe_block("ltx25-flf2v")
    assert '"wf": "ltx23-flf2v.json"' in a
    strip = lambda s: re.sub(r'"wf":"[^"]+",', "", re.sub(r"\s+", "", s))
    assert strip(a) == strip(b)                     # same node ids, size, steps, len_mod


def test_a_slide_takes_a_last_frame_on_either_ltx_flf():
    assert 'a.recipe in ("ltx25-flf2v", "ltx23-flf2v")' in SRC


def test_the_ltx23_graph_has_the_node_ids_the_recipe_names():
    p = WF / "ltx23-flf2v.json"
    if not p.is_file():
        return                                      # not on a box without Comfy
    g = json.loads(p.read_text(encoding="utf-8"))
    want = {"4": "CLIPTextEncode", "7": "EmptyLTXVLatentVideo", "8": "KSampler",
            "10": "CreateVideo", "11": "SaveVideo", "12": "LoadImage", "13": "LoadImage"}
    assert {k: g[k]["class_type"] for k in want} == want
