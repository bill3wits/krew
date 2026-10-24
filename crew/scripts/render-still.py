#!/usr/bin/env python3
"""Render a krew-Comfy still from a card, the house way (t_f7ecaf4d).

Usage:
    render-still.py --card t_f7ecaf4d --name <take> --prompt-file brief.txt
                    [--model flux2-turbo] [--aspect landscape] [--out PATH]
                    [--size 1024x1024] [--dry-run] [--no-park]

Flow (house rules, skill krew-render):
  1. Gate: render_window_gate.refuse_reason(card) — a kanban worker outside
     the User's off-hours is refused (render-now in a Crissa/User/the owner comment
     exempts it). Refusal comments + blocks the card, exit 3 (same shape as
     render-submit.py).
  2. Occupancy: if ~/.hermes/krew-gpu-mode != render, run
     `krew-mode render <recipe>` (parks ops, starts Comfy) unless --no-park.
  3. Draw: the graph is the plugin's own krew-comfy pipeline (workflow from
     recipes.json, prompt + size patched) posted to Comfy :8188 — same
     client_id 'krew-wip' so the Control Panel stage watches the draw.
  4. Leave: on success the occupancy is restored with `krew-mode normal`
     (SIGKILLs Comfy, restarts ops). On failure the flag is left as found
     and the error is reported.
  5. Lands: <out> or $KREW_HOME/crew/display/stills/crew-<date>-<name>.png
     + the path is printed on the last stdout line.

Exit codes: 0 ok, 2 usage/missing file, 3 window-gate refusal or
pre-flight refusal, 4 draw failed (occupancy left as found).
"""
import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("KREW_RAILS", str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(ROOT / "lib"))
import render_window_gate  # noqa: E402

import pwd  # noqa: E402

_PASSWD_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
HOUSE = Path(os.environ.get("KREW_HERMES", str(_PASSWD_HOME / ".hermes")))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import krew_comfy  # noqa: E402  one Comfy location for krew and the Wall: render.json, else KREW_COMFY_*
COMFY = krew_comfy.get("url")
WF_DIR = Path(krew_comfy.get("workflows"))
# i2i source stills land here before Comfy sees them (plugin _stage_input_image
# parity, 09-29).
IN_DIR = Path(krew_comfy.get("input"))
OUT_DIR = Path(krew_comfy.get("output"))
# recipes.json lives with the krew-comfy plugin: look in the same tree as this
# script first, then fall back to the live plugin copy.
_RECIPES_FALLBACKS = [
    Path(__file__).resolve().parent / "recipes.json",
    Path(os.environ.get(
        "KREW_COMFY_PLUGIN_RECIPES",
        str(Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes" / "plugins" / "krew-comfy" / "recipes.json"))),
    Path(__file__).resolve().parents[2] / "plugins" / "krew-comfy" / "recipes.json",   # krew's own copy of the plugin
]


def _recipes_path() -> Path:
    if os.environ.get("KREW_COMFY_RECIPES"):
        return Path(os.environ["KREW_COMFY_RECIPES"])
    for p in _RECIPES_FALLBACKS:
        if p.is_file():
            return p
    return _RECIPES_FALLBACKS[0]
MODE_FILE = Path(os.environ.get("KREW_GPU_MODE_FILE", str(HOUSE / "krew-gpu-mode")))
TIMEOUT_S = float(os.environ.get("KREW_COMFY_TIMEOUT", "900"))

ASPECT = {
    "landscape": (1024, 768),
    "portrait": (768, 1024),
    "square": (1024, 1024),
}


def _gpu_mode() -> str:
    try:
        first = MODE_FILE.read_text().strip().splitlines()[0].strip().lower()
        return first if first in {"render", "normal", "beast", "off"} else "normal"
    except OSError:
        return "normal"


def _krew_mode(*args: str) -> int:
    # KREW_MODE_BIN lets a test stub the live mode switch (CLAUDE.md 09-22:
    # never call it by absolute path from code a test stubs).
    exe = os.environ.get("KREW_MODE_BIN")
    cmd = [exe] if exe else [sys.executable, str(ROOT / "scripts" / "krew-mode")]
    return subprocess.run([*cmd, *args], timeout=900).returncode


# The plugin's recipe ids are not krew-mode's (09-29: `krew-mode render
# flux2-turbo` is refused as an unknown recipe, so only sdxl could ever enter
# Render from here). krew-mode sizes the park by ITS name; a test pins every
# local t2i recipe to a key in krew-mode's RECIPES.
KREW_MODE_RECIPE = {
    "flux2-turbo": "turbo",
    "flux2-klein": "klein",
    "sdxl": "sdxl",
    "flux2-max": "max",
    "flux2-dev": "flux2-dev",
    "flux1-dev": "flux1",
    "qwen-image-2512": "qwen",
    "qwen-edit-2511": "qwen-edit",        # the edit of record (the User 2026-10-08); 30 GiB in krew-mode
    "qwen-image-2.1": "qwen21",
    # i2i first (09-29 edit leg): same qwen21 files, 17.3 GiB peak measured
    # beside crew.
    "qwen-edit-2.1": "qwen21",
}


def krew_mode_recipe(rec: dict):
    """krew-mode's recipe for a plugin recipe; None = no local path.
    Kill KREW_STILL_RECIPE_MAP=0 passes the plugin id through (the old way)."""
    if os.environ.get("KREW_STILL_RECIPE_MAP", "1") == "0":
        return rec["id"]
    return KREW_MODE_RECIPE.get(rec["id"])


def load_recipe(model: str, source: str | None = None) -> dict:
    """A t2i id, or an i2i id when --source is given (09-29 edit leg)."""
    data = json.loads(_recipes_path().read_text())
    kinds = {"t2i", "i2i"} if source else {"t2i"}
    for rec in data.get("recipes", []):
        if rec.get("id") == model and rec.get("kind") in kinds:
            if source and rec.get("kind") != "i2i":
                sys.exit("%s is t2i; --source needs an i2i id (qwen-edit-2.1, qwen-edit-2511)" % model)
            return rec
    # Known i2i id used without --source: say exactly what's missing.
    for rec in data.get("recipes", []):
        if rec.get("id") == model and rec.get("kind") == "i2i":
            sys.exit("%s is i2i; pass --source <still.png>" % model)
    sys.exit("unknown or non-t2i recipe: %s (need a t2i id from recipes.json%s)"
             % (model, ", or an i2i id with --source" if source else ""))


def patch_graph(rec: dict, prompt: str, w: int, h: int,
                source: str | None = None, source_basename: str | None = None) -> dict:
    wf_path = WF_DIR / rec["workflow"]
    if not wf_path.is_file():
        sys.exit("workflow missing: %s" % wf_path)
    graph = json.loads(wf_path.read_text())

    def apply(node: dict) -> None:
        inp = node.setdefault("inputs", {})
        ct = str(node.get("class_type") or "")
        if ct.startswith("TextEncodeQwen") or "prompt" in inp:
            inp["prompt"] = prompt
        else:
            inp["text"] = prompt

    node_id = rec.get("prompt_node")
    if node_id and node_id in graph:
        apply(graph[node_id])
    else:
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type") or ""
            if ct == "CLIPTextEncode" or ct.startswith("TextEncodeQwen"):
                apply(node)
                break
    for nid in rec.get("size_nodes") or ():
        node = graph.get(nid)
        if not isinstance(node, dict):
            continue
        inp = node.setdefault("inputs", {})
        if "width" in inp:
            inp["width"] = w
        if "height" in inp:
            inp["height"] = h
    # i2i (qwen-edit-2.1 first, 09-29): the source still is already staged
    # into Comfy input/ (stage_source, plugin _stage_input_image parity) and
    # its name feeds the recipe's image node. No size nodes — the canvas
    # is the source image, so --width/--height do not size it.
    if rec.get("kind") == "i2i":
        if not source_basename:
            sys.exit("--model %s is i2i; pass --source <still.png>" % rec.get("id"))
        inode = rec.get("image_node")
        if not inode or str(inode) not in graph:
            sys.exit("i2i recipe %s names no image_node (or it is missing from the workflow)"
                     % rec.get("id"))
        graph[str(inode)].setdefault("inputs", {})["image"] = source_basename
    return graph


def stage_source(source: str) -> str:
    """Copy/download the source still into Comfy input/ (plugin parity)."""
    import shutil
    import urllib.request
    src = Path(source).expanduser()
    if src.is_file():
        dest = IN_DIR / ("hermes_%d_%s" % (int(time.time()), src.name or "in.png"))
        shutil.copyfile(src, dest)
        return dest.name
    if source.startswith("http://") or source.startswith("https://"):
        dest = IN_DIR / ("hermes_%d_src" % int(time.time()))
        with urllib.request.urlopen(source, timeout=30) as r:
            dest.write_bytes(r.read())
        return dest.name
    sys.exit("source still not found: %s" % source)


def submit(graph: dict) -> str:
    body = json.dumps({
        "prompt": graph,
        "client_id": os.environ.get("KREW_COMFY_CLIENT_ID", "krew-wip"),
        "extra_data": {"preview_method": os.environ.get("KREW_COMFY_PREVIEW", "auto")},
    }).encode()
    req = urllib.request.Request(
        COMFY + "/prompt", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        pid = json.loads(r.read().decode()).get("prompt_id")
    if not pid:
        raise RuntimeError("Comfy returned no prompt_id")
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        time.sleep(2)
        with urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10) as r:
            hist = json.loads(r.read().decode())
        rec = hist.get(pid)
        if not rec:
            continue
        for node in (rec.get("outputs") or {}).values():
            for key in ("images",):
                for item in node.get(key) or []:
                    name = item.get("filename")
                    sub = item.get("subfolder") or ""
                    path = OUT_DIR / sub / name if name else None
                    if path and path.is_file():
                        return str(path)
        if rec.get("status", {}).get("status_str") == "error":
            raise RuntimeError(f"Comfy error: {rec.get('status')}")
    raise TimeoutError(f"Comfy job {pid} timed out after {TIMEOUT_S:.0f}s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--card", required=True, help="kanban card (window gate + stamps)")
    ap.add_argument("--name", required=True, help="take name for the out path")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--model", default="flux2-turbo", help="t2i recipe id, or an i2i id with --source")
    ap.add_argument("--aspect", default="landscape", choices=sorted(ASPECT))
    ap.add_argument("--size", default=None, help="WxH override for t2i (e.g. 1024x1024); i2i keeps the source image's size")
    ap.add_argument("--source", default=None,
                    help="i2i only (qwen-edit-2.1 first): path or http URL of the still to edit; staged into Comfy input/ like the plugin")
    ap.add_argument("--out", default=None, help="output path (default display/stills/...)")
    ap.add_argument("--dry-run", action="store_true",
                    help="gate + patch + print the graph; no park, no submit")
    ap.add_argument("--no-park", action="store_true",
                    help="never run krew-mode render (fail instead)")
    a = ap.parse_args()

    live_card = os.environ.get("HERMES_KANBAN_TASK", "").strip()
    if live_card and live_card != a.card:
        print(f"--card {a.card} is stale (carried); the live card is {live_card} — using it",
              file=sys.stderr)
        a.card = live_card

    # the recipe is read first (an unknown id exits before anything); then the window
    # gate; a refused draw must never park ops.
    rec = load_recipe(a.model, a.source)
    why = render_window_gate.refuse_reason(a.card)
    if why:
        print(why, file=sys.stderr)
        if live_card:
            subprocess.run(["kanban", "comment", a.card,
                            f"{render_window_gate.MARK}: {why}", "--author", "render-still"])
            subprocess.run(["kanban", "block", a.card, why])
        return 3
    # i2i without --source: refuse before the park (a refused draw must
    # never leave ops parked).
    if rec.get("kind") == "i2i" and not a.source:
        print("--model %s is i2i; pass --source <still.png>" % a.model,
              file=sys.stderr)
        return 2

    if a.size:
        w, h = (int(v) for v in a.size.lower().split("x"))
    else:
        w, h = ASPECT[a.aspect]
    if a.prompt:
        prompt = a.prompt
    elif a.prompt_file:
        prompt = Path(a.prompt_file).expanduser().read_text().strip()
    else:
        print("need --prompt or --prompt-file", file=sys.stderr)
        return 2
    if not prompt:
        print("empty prompt", file=sys.stderr)
        return 2

    source_basename = None
    if rec.get("kind") == "i2i":
        # Stage even on --dry-run: the copy is the part of the edit leg that
        # must work before any GPU, and it leaves nothing parked.
        source_basename = stage_source(a.source)
    graph = patch_graph(rec, prompt, w, h, a.source, source_basename)

    if a.dry_run:
        print(json.dumps({
            "dry_run": True, "card": a.card, "name": a.name, "model": rec["id"],
            "kind": rec.get("kind"), "krew_mode_recipe": krew_mode_recipe(rec),
            "size": f"{w}x{h}", "prompt": prompt, "graph_nodes": len(graph),
            "workflow": rec["workflow"],
            **({"source_staged": source_basename} if source_basename else {}),
        }, indent=1))
        return 0

    mode_before = _gpu_mode()
    if mode_before != "render":
        if a.no_park:
            print(f"occupancy is {mode_before} (need render); --no-park refuses",
                  file=sys.stderr)
            return 3
        hm = krew_mode_recipe(rec)
        if not hm:
            print(f"recipe {rec['id']} has no krew-mode recipe; not entering Render",
                  file=sys.stderr)
            return 2
        print(f"entering Render (was {mode_before}): krew-mode render {hm}")
        rc = _krew_mode("render", hm)
        if rc != 0 or _gpu_mode() != "render":
            print(f"krew-mode render failed rc={rc}; not drawing. "
                  f"Restore manually if ops was not already parked.", file=sys.stderr)
            return 3

    try:
        path = submit(graph)
    except Exception as e:
        print(f"draw failed: {type(e).__name__}: {e}", file=sys.stderr)
        if mode_before == "render":
            print("occupancy left as render (it was render before the draw)", file=sys.stderr)
        else:
            print("leaving Render (krew-mode normal)", file=sys.stderr)
            rc = _krew_mode("normal")
            if rc != 0:
                print(f"WARNING: krew-mode normal rc={rc} — leave Render manually",
                      file=sys.stderr)
        return 4

    dst = Path(a.out).expanduser() if a.out else (
        ROOT / "display" / "stills" / f"crew-{time.strftime('%Y%m%d')}-{a.name}.png"
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), dst)
    print(f"restored occupancy: krew-mode normal")
    _krew_mode("normal")
    print(str(dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
