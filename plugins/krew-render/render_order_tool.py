"""render_order - the one verb a render profile needs to file a render order (the owner 2026-10-06, "just the verb").

Lila has no terminal by design, so `crew/scripts/render-orders.py add` was out of her reach: on 10-06 she hand-wrote a queue
file with write_file, then scripted an order against a stale checkout's lib. This is the same `render_orders.add`
the CLI calls, behind one tool (toolset image_gen: Lila and Bash, never Vera), taking a recipe id instead of node
numbers: the workflow, the prompt node, the image node and the size node come from the recipes file; the
negative node is found in the workflow; a still for an i2v/edit recipe is copied into Comfy's input folder for you.

It files an order and nothing else: the render window releases the card and the render runner works the queue. A recipe
whose licence is not commercial is filed with a warning, never refused (coach, don't enforce).
Kill: KREW_RENDER_ORDER_TOOL=0 (the plugin does not register the tool; a call answers "switched off").
Settings: `render:` in $KREW_HOME/krew.yaml (profiles, comfy_input_dir, recipes).
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from . import krew_settings as S
from . import render_cards as rc

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
NEEDS_IMAGE = ("i2v", "i2i")


def enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_RENDER_ORDER_TOOL", "1")).strip().lower() not in ("0", "false", "no", "off")


def comfy_input(settings: dict | None = None) -> Path:
    """Comfy's input folder: `render.comfy_input_dir`, else the one Comfy location's input (krew_comfy.py: the Wall's
    render.json, else KREW_COMFY_INPUT / KREW_COMFY_ROOT, else ~/ComfyUI/input)."""
    st = S.load("render") if settings is None else settings
    got = str(st.get("comfy_input_dir") or "").strip()
    if got:
        return Path(os.path.expanduser(got))
    try:
        from . import krew_comfy
    except ImportError:
        import krew_comfy  # noqa: E402
    return Path(krew_comfy.get("input"))


DEFAULT_PROFILES = ("lila", "bash")


# WHO MAY FILE (the owner 2026-10-06: Lila and Bash render, Vera reviews). The toolset alone did not keep Vera out -
# her Tool Search found render_order on 10-06 - and under one host gateway a tool registers for every profile, so the
# check is at call time. KREW_RENDER_ORDER_PROFILES overrides the list; `render.profiles` in krew.yaml sets it.
def who_files(settings: dict | None = None) -> set[str] | None:
    """The profiles that may file (lower-case), or None = anyone. Default: Lila and Bash."""
    st = S.load("render") if settings is None else settings
    who = os.environ.get("KREW_RENDER_ORDER_PROFILES") or st.get("profiles", DEFAULT_PROFILES)
    if who is None:
        who = DEFAULT_PROFILES
    if isinstance(who, str):
        who = who.split(",")
    names = {str(x).strip().lower() for x in who if str(x).strip()}
    return None if not names or "*" in names else names


def allowed(profile: str, settings: dict | None = None) -> bool:
    names = who_files(settings)
    return names is None or (profile or "").strip().lower() in names


def negative_node(graph: dict, prompt_node: str) -> str:
    """The one other CLIPTextEncode feeding a sampler's `negative` input, or ''."""
    for node in graph.values():
        neg = (node.get("inputs") or {}).get("negative") if isinstance(node, dict) else None
        if isinstance(neg, list) and neg and str(neg[0]) != str(prompt_node):
            nid = str(neg[0])
            if (graph.get(nid) or {}).get("class_type", "").startswith("CLIPTextEncode"):
                return nid
    return ""


def _stage_image(src: str, card: str, input_dir: Path) -> str:
    p = Path(os.path.expanduser(src))
    if not p.is_file() or p.suffix.lower() not in IMAGE_EXT:
        raise ValueError(f"image {src!r} is not a still on disk ({', '.join(IMAGE_EXT)})")
    input_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{card or 'order'}-{p.name}")
    shutil.copyfile(p, input_dir / name)
    return name


def file_order(args: dict, *, by: str, add=None, load_workflow=None, rs: list[dict] | None = None,
               input_dir: Path | None = None, settings: dict | None = None) -> str:
    if not enabled():
        return "render_order is switched off (KREW_RENDER_ORDER_TOOL=0). Nothing filed."
    if not allowed(by, settings):
        return (f"Not filed: render_order is for the profiles that render ({', '.join(sorted(who_files(settings) or ()))}); "
                f"{by or 'this profile'} is not one of them. Ask the card's renderer to file it.")
    a = dict(args or {})
    rs = rc.recipes() if rs is None else rs
    r = rc.recipe_for(str(a.get("recipe") or ""), rs)
    if not r or not r.get("workflow"):
        ids = ", ".join(x["id"] for x in rs if x.get("workflow")) or "(none: no recipes file, see render.recipes)"
        return f"Not filed: recipe {a.get('recipe')!r} is not an order-queue recipe. Recipes: {ids}."
    prompt = str(a.get("prompt") or "").strip()
    if not prompt:
        return "Not filed: prompt= is required (the positive prompt)."
    if add is None or load_workflow is None:
        from . import render_orders as ro
        add = add or ro.add
        load_workflow = load_workflow or ro.load_workflow
    wf = str(r["workflow"]).removesuffix(".json")
    ov: dict = {f"{r['prompt_node']}.text": prompt}
    neg = str(a.get("negative") or "").strip()
    if neg:
        _, graph = load_workflow(wf)
        nn = negative_node(graph, str(r["prompt_node"]))
        if not nn:
            return f"Not filed: {r['id']} has no negative-prompt node; drop negative= or put it in extra=."
        ov[f"{nn}.text"] = neg
    size = r.get("size_nodes") or []
    for key in ("width", "height"):
        if a.get(key):
            if not size:
                return f"Not filed: {r['id']} has no size node; drop {key}=."
            ov[f"{size[0]}.{key}"] = int(a[key])
    card = str(a.get("card") or os.environ.get("HERMES_KANBAN_TASK") or "")
    if r.get("kind") in NEEDS_IMAGE:
        if not a.get("image") or not r.get("image_node"):
            return f"Not filed: {r['id']} ({r['kind']}) needs image= - the still to start from."
        try:
            ov[f"{r['image_node']}.image"] = _stage_image(str(a["image"]), card, input_dir or comfy_input(settings))
        except (OSError, ValueError) as e:
            return f"Not filed: {e}."
    extra = a.get("extra") or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            return "Not filed: extra= must be an object of node.input -> value."
    ov.update({str(k): v for k, v in dict(extra).items()})
    seed = a.get("seed")
    try:
        o = add(wf, overrides=ov, seed=int(seed) if seed not in (None, "") else None, by=by or "agent", card=card)
    except Exception as e:  # noqa: BLE001 - the library's own refusal, said plainly
        return f"Not filed: {e}"
    warn = "" if rc.commercial(r) == "yes" else f" WARNING: {r['id']} licence {r.get('license')} - not for publishing or marketing."
    return (f"Filed order {o['id']} ({r['id']}, {o.get('need_gib')} GiB) for card {card or '-'} - pending.{warn} "
            f"The render window releases it and the render runner works the queue; when it is done the output is in "
            f"{o.get('out_dir')}. Then look_compare it against the spec.\nconfirmed: filed order {o['id']}")
