"""Render cards: a recipe's how-to, put in front of the krew only while that renderer is in play (the owner 2026-10-06).

The renders came back bad and nobody said so: the render skill's table covered 11 of 18 recipes with no licence column (flux
was "the default" for marketing), no render skill named the order queue, and `look_compare` - the crew's render-vs-spec
check - had 0 calls in 30 days while open-ended `vision_analyze` questions flattered every picture. A card is built
from your recipes file (`render.recipes` in $KREW_HOME/krew.yaml, else krew-comfy's recipes.json beside this plugin), so it cannot drift from
the recipes, and it carries the licence, the inputs, the order path and the look step.

Used by plugin krew-render (Lila, Bash, Vera): `pre_llm_call` adds the card(s) to the turn's message while a
render signal is present and nothing after it is gone (ephemeral, never the cached system prompt); `transform_tool_result`
appends a recipe's card once per session when `image_generate` / `video_generate` / `render_order` / `render-orders.py add` picks it
mid-turn. Coaching only: nothing here refuses, blocks or grades a render.

Switch: OFF unless `$KREW_HOME/krew-render-card.on` exists, `render.cards: true` in krew.yaml, or KREW_RENDER_CARD=1;
KREW_RENDER_CARD=0 always wins.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import krew_settings as S
try:
    from . import krew_hermes as KH
except ImportError:                 # imported alone (tests, scripts)
    import krew_hermes as KH  # noqa: E402

HOME = Path.home()
ON_FILE = S.krew_home() / "krew-render-card.on"
GPU_MODE = Path(os.environ.get("KREW_GPU_MODE") or (Path(os.environ.get("HERMES_HOME") or KH.hermes_home()) / "krew-gpu-mode"))
RECIPE_PATHS = (Path(__file__).resolve().parent.parent / "krew-comfy" / "recipes.json",)
RENDER_MODES = ("render", "heavy-render")
NOT_COMMERCIAL = re.compile(r"internal|community|research|non-commercial|cloud", re.I)
COMMERCIAL_OK = re.compile(r"apache|\bmit\b|\bopen\b", re.I)
CARD_MAX = 1800           # one card's characters
TABLE_MAX = 2200          # the whole-menu table


def enabled(env: dict | None = None, on_file: Path | None = None, settings: dict | None = None) -> bool:
    e = os.environ if env is None else env
    v = str(e.get("KREW_RENDER_CARD", "")).strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    st = S.load("render") if settings is None else settings
    return (on_file or ON_FILE).exists() or bool(st.get("cards"))


def recipes_path(settings: dict | None = None) -> Path:
    st = S.load("render") if settings is None else settings
    return Path(os.path.expanduser(str(st.get("recipes") or (S.krew_home() / "recipes.json"))))


def recipes(paths=None) -> list[dict]:
    if isinstance(paths, (str, Path)):
        paths = (paths,)
    for p in (paths or (recipes_path(),) + RECIPE_PATHS):
        try:
            return list(json.loads(Path(p).read_text(encoding="utf-8")).get("recipes") or [])
        except (OSError, ValueError):
            continue
    return []


def commercial(r: dict) -> str:
    lic = str(r.get("license") or "")
    if "lightricks" in lic.lower():     # LTX community licence: commercial under its own terms (the User 2026-10-08)
        return "yes - under $10M annual revenue, outputs labelled AI-generated, no competing product"
    if NOT_COMMERCIAL.search(lic):
        return "NO - not for publishing or marketing"
    if COMMERCIAL_OK.search(lic):
        return "yes"
    return "check the licence first"


def needs(r: dict) -> str:
    kind = str(r.get("kind") or "")
    return {"i2v": "a still (image_url / the image node)", "i2i": "the still to edit",
            "flf2v": "a first AND a last frame", "t2i": "a prompt", "t2v": "a prompt"}.get(kind, "see the recipe")


def card(r: dict) -> str:
    rid = r["id"]
    wf = str(r.get("workflow") or "").removesuffix(".json")
    sets = f'--set {r["prompt_node"]}.text="<prompt>"' if r.get("prompt_node") else '--set <node>.text="<prompt>"'
    if r.get("image_node"):
        sets += f" --set {r['image_node']}.image=<file in ComfyUI's input folder>"
    lines = [
        f"RENDER CARD {rid} ({r.get('kind')}, ~{r.get('vram_gib', '?')} GiB) - licence {r.get('license') or '?'}; "
        f"commercial: {commercial(r)}",
        f"When: {r.get('purpose') or r.get('popup') or '-'}",
        f"Needs: {needs(r)}.",
        (f"Order (any mode): render_order recipe={rid} prompt=\"...\"" + (" image=<still>" if r.get("image_node") else "")
         + f", or crew/scripts/render-orders.py add --workflow {wf} {sets} --by <you> --card <card id>; "
         "the render window releases it and the render runner works the queue."
         if wf else "No local workflow: not an order-queue recipe."),
        f"Inside a Render you are already in: image_generate / video_generate with model={rid}.",
        ("Then LOOK, as a step, not a vibe: look_compare with target=<the reference or brief still>, render=<the "
         "output>, spec=<subject, proportions, must / never from the card> - it answers PASS or FAIL with defects "
         "and one fix. Quote its verdict on the card. FAIL: re-roll once with the fix in the prompt, then report. "
         "Never an open 'is it clean?' question - the vision brain praises whatever it is shown."),
    ]
    return "\n".join(lines)[:CARD_MAX]


def table(rs: list[dict]) -> str:
    rows = ["RENDERERS (licence first; for marketing use only commercial: yes):"]
    for r in rs:
        if not r.get("workflow") and r.get("kind") not in ("t2i", "t2v"):
            continue
        rows.append(f"- {r['id']} ({r.get('kind')}) commercial: {commercial(r).split(' - ')[0]} - "
                    f"{(r.get('purpose') or r.get('popup') or '')[:70]}")
    rows.append("Order path: render_order recipe= prompt= (or crew/scripts/render-orders.py add ...); LOOK with look_compare + spec.")
    return "\n".join(rows)[:TABLE_MAX]


def named(text: str, rs: list[dict]) -> list[dict]:
    """Recipes whose id appears in `text` (longest id first, so ltx25-i2v is not read as ltx25-flf2v)."""
    t = (text or "").lower()
    out, seen = [], set()
    for r in sorted(rs, key=lambda x: -len(x["id"])):
        rid = r["id"].lower()
        if re.search(rf"(?<![\w.-]){re.escape(rid)}(?![\w.-])", t) and rid not in seen:
            out.append(r); seen.add(rid)
    return out


def gpu_mode(path: Path | None = None) -> str:
    try:
        return (path or GPU_MODE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def _text(content) -> str:
    if isinstance(content, list):
        return " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return str(content or "")


RENDER_WORDS = re.compile(r"\b(?:render|image_generate|video_generate|render_order|render-orders|heavy-render|comfy|still|clip)\b", re.I)


def task_text(env: dict | None = None) -> str:
    """A kanban worker's own card - title, body and its comments - read-only. A worker's opening message is only
    'work task t_...' (the body lives in worker_context), so without this its turn carried no render signal at all
    (2026-10-06). '' outside a worker or when the board cannot be read."""
    e = os.environ if env is None else env
    tid, db = e.get("HERMES_KANBAN_TASK", ""), e.get("HERMES_KANBAN_DB", "")
    if not tid or not db:
        return ""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        row = con.execute("SELECT title, body FROM tasks WHERE id=?", (tid,)).fetchone()
        try:
            notes = [r[0] for r in con.execute("SELECT body FROM task_comments WHERE task_id=? ORDER BY id DESC LIMIT 6", (tid,))]
        except sqlite3.Error:
            notes = []
        con.close()
    except sqlite3.Error:
        return ""
    return " ".join([*(row or ("", "")), *notes]) if row else ""


def turn_context(user_message, history: list | None, *, mode: str | None = None, rs: list[dict] | None = None,
                 task: str | None = None) -> str:
    """What pre_llm_call adds this turn: the named recipes' cards, the menu in a render mode, or ''."""
    rs = recipes() if rs is None else rs
    if not rs:
        return ""
    recent = " ".join(_text(m.get("content")) + " " + json.dumps(m.get("tool_calls") or "")
                      for m in (history or [])[-8:] if isinstance(m, dict))
    msg = _text(user_message) + " " + (task_text() if task is None else task)
    hit = named(msg, rs) or (named(recent, rs) if RENDER_WORDS.search(msg + " " + recent) else [])
    if hit:
        return "\n\n".join(card(r) for r in hit[:2])
    if (mode if mode is not None else gpu_mode()) in RENDER_MODES or RENDER_WORDS.search(msg):
        return table(rs)
    return ""


def tool_recipe(tool_name: str, args) -> str:
    """The recipe id a tool call picked: image/video_generate's model, or a render-orders add --workflow/--recipe."""
    a = args if isinstance(args, dict) else {}
    if tool_name in ("image_generate", "video_generate"):
        return str(a.get("model") or "")
    if tool_name == "render_order":
        return str(a.get("recipe") or "")
    if tool_name in ("terminal", "shell", "run_command"):
        cmd = str(a.get("command") or a.get("cmd") or "")
        m = re.search(r"(?:render-orders(?:\.py)?\s+add|render-submit(?:\.py)?)\b.*?--(?:workflow|recipe)[ =](\S+)", cmd)
        return m.group(1).strip("'\"") if m else ""
    return ""


def recipe_for(name: str, rs: list[dict] | None = None) -> dict | None:
    rs = recipes() if rs is None else rs
    n = (name or "").lower().removesuffix(".json")
    for r in rs:
        wf = str(r.get("workflow") or "").lower().removesuffix(".json")
        if n in (r["id"].lower(), wf):
            return r
    return None
