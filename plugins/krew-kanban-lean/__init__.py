"""krew-kanban-lean — the worker's ten kanban verbs, thin.

Measured on the pinned framework (`hermes -p bash prompt-size`): the built-in `kanban`
toolset is 14 tools / 23,442 B on every turn of every crew profile that
carries it; 4,992 B of tool descriptions and 12,093 B of property
descriptions — 73% prose. The workers (bash, lila, steward, vera) call ten of
the fourteen; kanban_create,
kanban_list, kanban_unblock and kanban_attachments are Ada's.

This plugin exposes those ten as toolset `kanban_lean` — the built-in
entries themselves, handler and check_fn untouched — with their schemas
thinned: every property name, type, enum and `required` kept exactly, only
the prose trimmed.
The names stay `kanban_*`, so the dispatcher's worker prompt, the skills and
the crystal check's complete guard all keep working. No tool is re-registered
and no override capability is granted: the plugin declares the toolset
(`create_custom_toolset`) and thins the built-in entries' schema dicts in
place, which `registry.get_definitions` reads live.

krew patch 50 makes a dispatcher-spawned worker whose profile lists `kanban_lean` keep this kit (the
framework otherwise appends the full `kanban` toolset for every worker).
Way back: `crew/scripts/kanban-lean.sh off` (pack + live: toolsets `kanban_lean`
→ `kanban`, plugin disabled). `on` is the reverse. Both are set-level edits.
"""
from __future__ import annotations

import copy
import logging
import re

logger = logging.getLogger("krew-kanban-lean")

TOOLSET = "kanban_lean"
LEAN = (
    "kanban_show", "kanban_complete", "kanban_block", "kanban_comment",
    "kanban_attach", "kanban_attach_url", "kanban_heartbeat",
    "kanban_request_review", "kanban_request_changes", "kanban_link",
)
TOOL_DESC_MAX = 160
PROP_DESC_MAX = 90


def _first_sentence(text: str, cap: int) -> str:
    """The first sentence, capped. Keeps the verb; drops the essay."""
    t = " ".join(str(text or "").split())
    if not t:
        return t
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    s = m.group(1) if m else t
    if len(s) > cap:
        s = s[:cap - 1].rstrip() + "…"
    return s


def thin(schema: dict) -> dict:
    """A copy of a tool schema with every description shortened and
    `examples` dropped. Names, types, enums, items, required: untouched.
    Handles both the registry shape ({name, description, parameters}) and
    the OpenAI wrapper ({type: function, function: {...}})."""
    out = copy.deepcopy(schema)
    fn = out.get("function") if isinstance(out.get("function"), dict) else out
    if "description" in fn:
        fn["description"] = _first_sentence(fn["description"], TOOL_DESC_MAX)
    params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else None

    def walk(node):
        if not isinstance(node, dict):
            return
        node.pop("examples", None)
        if "description" in node and isinstance(node["description"], str):
            node["description"] = _first_sentence(node["description"], PROP_DESC_MAX)
        for k in ("properties",):
            for v in (node.get(k) or {}).values():
                walk(v)
        if isinstance(node.get("items"), dict):
            walk(node["items"])
        for key in ("anyOf", "oneOf", "allOf"):
            for v in node.get(key) or []:
                walk(v)

    if params:
        params.pop("examples", None)
        for v in (params.get("properties") or {}).values():
            walk(v)
    return out


def _multiplex_on() -> bool:
    """True only under ONE hermes host gateway serving several profiles in this process. Off:
    one profile per process, and everything in this file does what it always did."""
    try:
        from agent.secret_scope import is_multiplex_active
        return bool(is_multiplex_active())
    except Exception:  # noqa: BLE001 - an older hermes has no such thing
        return False


def _home_key() -> str:
    """The served profile's stable key (hermes_constants.hermes_home_key), "" when unavailable."""
    try:
        from hermes_constants import hermes_home_key
        return str(hermes_home_key())
    except Exception:  # noqa: BLE001
        return ""


def _register_keyed(registry) -> None:
    """ONE host gateway: the kanban built-ins are ONE registry entry for every served profile, so
    thinning the schema in place thinned it for Ada and Crissa too, who carry the full kit and never
    enabled this plugin. Here the schema is left alone and the lean fields are overlaid per served
    home when the definitions are built. All marker state lives on the registry and the function:
    a ToolEntry has slots."""
    homes = getattr(registry, "_krew_kanban_lean_homes", None)
    if homes is None:
        homes = set()
        registry._krew_kanban_lean_homes = homes
    homes.add(_home_key())
    done = 0
    for name in LEAN:
        entry = registry.get_entry(name)
        if entry is None:
            logger.warning("krew-kanban-lean: built-in %s not in the registry — skipped", name)
            continue
        prev = entry.dynamic_schema_overrides
        if getattr(prev, "_krew_kanban_lean", False):
            done += 1
            continue
        try:
            lean = thin(entry.schema)                      # a copy; entry.schema is not mutated

            def _overrides(lean=lean, prev=prev, homes=homes):
                base = (prev() if prev is not None else None) or {}
                if _home_key() not in homes:
                    return base
                return {**base, **{k: lean[k] for k in ("description", "parameters") if k in lean}}

            _overrides._krew_kanban_lean = True
            entry.dynamic_schema_overrides = _overrides
            done += 1
        except Exception as e:  # noqa: BLE001
            logger.error("krew-kanban-lean: %s not thinned (%s: %s) — its fat schema stays", name, e.__class__.__name__, str(e)[:160])
    try:
        from model_tools import _clear_tool_defs_cache
        _clear_tool_defs_cache()      # a home added after a memo was built must not keep the fat result
    except Exception:  # noqa: BLE001
        pass
    logger.info("krew-kanban-lean: %d of %d verbs thinned per served home on toolset %s", done, len(LEAN), TOOLSET)


def register(ctx) -> None:
    """Declare toolset `kanban_lean` = the ten built-ins, and thin their
    schemas IN PLACE. get_definitions() reads entry.schema live, so the
    model sees the lean prose; the handler, check_fn and names are the
    built-ins' own. No override: re-registering the names under a plugin
    scope hid them from toolset resolution (measured 2026-09-08 12:55 —
    the workers fell to 23 tools with no kanban verbs), and the override
    gate would have granted a capability nothing here needs."""
    from tools.registry import registry
    from toolsets import create_custom_toolset
    import tools.kanban_tools  # noqa: F401  (registers the built-ins first)
    create_custom_toolset(TOOLSET, "The worker's ten kanban verbs, thin (krew-kanban-lean).", tools=list(LEAN))
    if _multiplex_on():
        _register_keyed(registry)
        return
    done = 0
    for name in LEAN:
        entry = registry.get_entry(name)
        if entry is None:
            logger.warning("krew-kanban-lean: built-in %s not in the registry — skipped", name)
            continue
        lean = thin(entry.schema)
        try:
            entry.schema.clear()
            entry.schema.update(lean)
            fn = lean.get("function") if isinstance(lean.get("function"), dict) else lean
            if getattr(entry, "description", None):
                entry.description = str(fn.get("description") or entry.description)
            done += 1
        except Exception as e:  # noqa: BLE001
            logger.error("krew-kanban-lean: %s not thinned (%s: %s) — its fat schema stays", name, e.__class__.__name__, str(e)[:160])
    logger.info("krew-kanban-lean: %d of %d verbs thinned on toolset %s", done, len(LEAN), TOOLSET)
