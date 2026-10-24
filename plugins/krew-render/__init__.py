"""krew-render - the recipe's card while the renderer is in play (render_cards.py; the owner 2026-10-06), and the
render_order verb (render_order_tool.py) that files a ComfyUI render order by recipe id.

Coaching only: it adds text, it never refuses or blocks. OFF unless $KREW_HOME/krew-render-card.on exists,
`render.cards: true` in krew.yaml, or KREW_RENDER_CARD=1 (read on every call, so the file flips it with no restart);
KREW_RENDER_CARD=0 always wins. The order queue is worked by render_runner.py. Settings: `render:` in krew.yaml.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import render_cards
from . import render_order_tool

logger = logging.getLogger("krew-render")
_MOD: dict = {}
_SEEN: dict[str, set] = {}          # session -> recipe ids already carded by a tool result


def _rc():
    if "m" not in _MOD:
        _MOD["m"] = render_cards
    return _MOD["m"]


def on_pre_llm_call(session_id=None, user_message=None, conversation_history=None, **_kw):
    try:
        rc = _rc()
        if not rc.enabled():
            return None
        text = rc.turn_context(user_message, conversation_history)
        return {"context": text} if text else None
    except Exception as e:  # noqa: BLE001 - a coaching card never breaks a turn
        logger.warning("krew-render: pre_llm_call skipped (%s: %s)", e.__class__.__name__, str(e)[:160])
        return None


def on_tool_result(tool_name=None, args=None, result=None, session_id=None, **_kw):
    try:
        rc = _rc()
        if not rc.enabled() or not isinstance(result, str):
            return None
        rid = rc.tool_recipe(str(tool_name or ""), args)
        r = rc.recipe_for(rid) if rid else None
        if not r:
            return None
        seen = _SEEN.setdefault(str(session_id or ""), set())
        if r["id"] in seen:
            return None
        seen.add(r["id"])
        return result + "\n\n" + rc.card(r)
    except Exception as e:  # noqa: BLE001
        logger.warning("krew-render: tool card skipped (%s: %s)", e.__class__.__name__, str(e)[:160])
        return None


_ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "recipe": {"type": "string", "description": "recipe id from the recipes file, e.g. qwen-image-2512, qwen-edit-2511, wan22-i2v"},
        "prompt": {"type": "string", "description": "positive prompt"},
        "negative": {"type": "string", "description": "negative prompt (optional)"},
        "image": {"type": "string", "description": "path of the still to start from (i2v / edit recipes)"},
        "width": {"type": "integer"}, "height": {"type": "integer"}, "seed": {"type": "integer"},
        "card": {"type": "string", "description": "the card id (default: the card you are working)"},
        "extra": {"type": "object", "description": "other node.input -> value overrides"},
    },
    "required": ["recipe", "prompt"],
}
ORDER_SCHEMA = _ORDER_SCHEMA
ORDER_DESC = ("File a render order (any mode): recipe id + prompt [+ image for i2v/edit]. "
              "The render window releases it; the render runner renders it. Then look_compare.")


def _profile() -> str:
    """The calling profile: the served home (per call under the host gateway: .../profiles/<name>), else
    HERMES_PROFILE (a kanban worker's dispatcher sets it), else ''."""
    import os
    try:
        from hermes_constants import get_hermes_home
        h = Path(str(get_hermes_home()))
        if h.parent.name == "profiles":
            return h.name
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("HERMES_PROFILE", "")


def render_order(*args, **kwargs):
    p: dict = dict(args[0]) if args and isinstance(args[0], dict) else {}
    p.update({k: v for k, v in kwargs.items() if k not in ("task_id", "session_id", "tool_call_id", "turn_id")})
    try:
        rot = render_order_tool
        return rot.file_order(p, by=_profile())
    except Exception as e:  # noqa: BLE001
        return f"Not filed: render_order failed ({e.__class__.__name__}: {str(e)[:160]})."


def register(ctx) -> None:
    if render_order_tool.enabled():
        # image_gen: Lila and Bash carry it, Vera (review) does not (the owner 2026-10-06, "just the verb").
        # The model-facing description goes IN the schema (the framework never copies `description=` to the model).
        ctx.register_tool(name="render_order", toolset="image_gen", handler=render_order, description=ORDER_DESC,
                          schema={"name": "render_order", "description": ORDER_DESC, "parameters": _ORDER_SCHEMA},
                          emoji="🎬")
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("transform_tool_result", on_tool_result)
    logger.info("krew-render: hooks registered (pre_llm_call, transform_tool_result)")
