"""The voice front door's hands: one tool, CLI kit behind it.

The front door's gateway must not carry computer_use / browser / web / team schemas
(zippy mouth, 2026-09-15). This plugin registers a single `hands` tool on
toolset `hands`. The handler is $KREW_HOME/crew/scripts/zoey-hands.py: `hermes -p zoey --cli`
(the reference install's child, shipped under crew/); when that file is not installed the
in-plugin child in hands.py runs the job instead (the fallback, never the first choice).

Nested CLI runs refuse (KREW_HANDS_NESTED). Occupancy Render/Beast/Off
refuse because ops is parked. One job at a time (flock).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from . import hands as H
from . import krew_settings as S

logger = logging.getLogger(__name__)

SCRIPT = S.krew_home() / "crew" / "scripts" / "zoey-hands.py"   # the shipped child (krew-crew-install copies crew/)
TIMEOUT = int(os.environ.get("KREW_HANDS_TIMEOUT", "120")) + 5


# Hermes dispatch (model_tools.handle_function_call → registry.dispatch)
# always forwards these as kwargs. They are never the job.
# 2026-09-16 08:47: mouth sent hands {} four times; session_id leaked
# through ask_from's unknown-string loop and spawned a child whose Job
# was "session_id freeair-1789562845".
RUNTIME_KW = frozenset({
    "task_id",
    "session_id",
    "user_task",
    "enabled_tools",
    "parent_agent",
    "callback",
    "tool_call_id",
    "turn_id",
    "api_request_id",
    "scope",
})


def _args(args, kwargs: dict) -> dict:
    p: dict = {}
    if isinstance(args, dict):
        p.update(args)
    elif isinstance(args, (list, tuple)) and args and isinstance(args[0], dict):
        p.update(args[0])
    p.update({k: v for k, v in kwargs.items() if k not in RUNTIME_KW})
    for k in RUNTIME_KW:
        p.pop(k, None)
    return p


# ── What the model is sent is the SCHEMA (2026-09-30) ─────────────────────────
# hermes builds a tool definition as {"type": "function", "function": {**schema, "name": name}} and its
# plugin guide says the model-facing description belongs in schema["description"]: the `description=`
# keyword is registry metadata and is never copied back. Every crew tool passed a bare JSON-schema
# object as `schema`, so the model was sent {"type": "object", "properties": ..., "name": "hands"} -
# no description at all and no `parameters`. It picked tools by name (team_compose for a card,
# `team_scout({})`, `team_look({})` on 09-30). Kill: KREW_PLUGIN_CANON_SCHEMA=0.
def canonical_schema(name, description, schema):
    """{"name", "description", "parameters"} - the shape every built-in tool registers."""
    import os as _os
    if _os.environ.get("KREW_PLUGIN_CANON_SCHEMA", "1").strip().lower() in ("0", "false", "no", "off"):
        return schema
    if isinstance(schema, dict) and "parameters" in schema:
        return schema
    params = schema if isinstance(schema, dict) and schema else {"type": "object", "properties": {}}
    return {"name": name, "description": str(description or "").strip(), "parameters": params}

HANDS_DESCRIPTION = (
    "Your CLI kit: search, recall, click, display, board. "
    "Pass the job in plain words. One at a time. Not a krew spawn. "
    "Say a thing was filed, shown or done only if the reply has a confirmed: line for it."
)

# The mouth reads skills; it does not write them. The built-in `skills` toolset is three tools and
# skill_manage alone is 3,725 of the mouth's 5,150 tool bytes (called once in seven days, skills_list three
# times). `skill_read` is skill_view alone: the built-in entry itself, nothing re-registered. A profile
# wears it by listing it in platform_toolsets INSTEAD of `skills` - only once its gateway runs this code
# (an unknown toolset resolves to nothing, and she would lose skill_view).
SKILL_READ_TOOLSET = "skill_read"


def declare_skill_read() -> bool:
    try:
        from toolsets import create_custom_toolset
        create_custom_toolset(SKILL_READ_TOOLSET, "Read a skill (skill_view only; krew-hands).", tools=["skill_view"])
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("krew-hands: toolset %s not declared (%s: %s)", SKILL_READ_TOOLSET, type(e).__name__, e)
        return False

EMPTY_ASK = (
    'hands: empty ask. One argument, the job in plain words — '
    '{"ask": "put the board up"} or {"ask": "list the kanban, all statuses"}. '
    "No action names, no JSON inside."
)


def ask_from(p: dict) -> str:
    """The job in plain words, from whatever shape the mouth sent.

    2026-09-16 07:58, first live turn on the new belt: the 27B had just loaded
    `zoey-list`, which describes the child's tools by their call shape, and
    sent `{"action": "team_board", "action_args": "{\\"action\\": \\"list\\"}"}`
    to `hands` eight times. Each came back "empty ask"; thirteen LLM rounds
    and 110k prompt tokens for one spoken sentence. The child understands
    "team_board list" perfectly well, so fold that shape into words instead
    of refusing it.
    """
    ask = str(p.get("ask") or p.get("text") or p.get("job") or "").strip()
    if ask:
        return ask
    words: list[str] = []
    action = p.get("action") or p.get("tool") or p.get("name")
    if action:
        words.append(str(action).strip())
    extra = p.get("action_args") or p.get("args") or p.get("arguments") or p.get("params")
    if isinstance(extra, str):
        s = extra.strip()
        if s.startswith("{"):
            try:
                import json
                extra = json.loads(s)
            except ValueError:
                extra = s
        else:
            extra = s
    if isinstance(extra, dict):
        for k, v in extra.items():
            if v in (None, "", [], {}):
                continue
            if isinstance(v, (dict, list)):
                import json
                v = json.dumps(v)
            words.append(f"{k} {v}" if str(k) != "action" else str(v))
    elif isinstance(extra, (list, tuple)):
        words.extend(str(x) for x in extra if x not in (None, ""))
    elif extra:
        words.append(str(extra))
    skip = {"ask", "text", "job", "action", "tool", "name", "action_args",
            "args", "arguments", "params"} | RUNTIME_KW
    for k, v in p.items():
        if k in skip:
            continue
        if isinstance(v, str) and v.strip():
            words.append(f"{k} {v.strip()}")
    return " ".join(w for w in words if w).strip()


def _child_env():
    """The hands child is ZOEY's. While she runs in a gateway of her own the process env is hers
    and the child inherits it (None: today's behaviour byte for byte). Under ONE host gateway
    (hermes multiplex) the process env is the DEFAULT profile's, so the child is given the served
    profile's HERMES_HOME and its own .env, and T0 in enforce whatever the host unit carries. No
    scope bound there RAISES: a --yolo child must never be born as the default profile."""
    try:
        from agent.secret_scope import is_multiplex_active
        if not is_multiplex_active():
            return None
    except Exception:  # noqa: BLE001 - an older hermes: one profile per process
        return None
    from hermes_constants import get_hermes_home_override
    if get_hermes_home_override() is None:
        raise RuntimeError("no profile scope is bound under the host gateway")
    from tools.environments.local import served_profile_child_env
    env = served_profile_child_env(inherit_credentials=True)
    env.setdefault("KREW_T0_MODE", "enforce")
    # The session-end belt runs IN the child (team-zoey), so the host's belt switch must reach it: dropped
    # here, the child's belt closed every tab and sent the kept one home, and ZOS Brave exited and
    # relaunched in a loop (10-03 05:30 on; the User 2026-10-03, go). Passed as the host carries it.
    if os.environ.get("KREW_ZOEY_BELT_OWN_TABS"):
        env.setdefault("KREW_ZOEY_BELT_OWN_TABS", os.environ["KREW_ZOEY_BELT_OWN_TABS"])
    return env


def run_hands(ask: str, *, script: Path | None = None, runner=None, cfg: dict | None = None) -> str:
    ask = (ask or "").strip()
    if not ask:
        return EMPTY_ASK
    path = script or SCRIPT
    if not path.is_file():
        # The shipped child is not installed (a plugin placed alone, no `krew-crew-install`): the in-plugin
        # child (hands.py, the same profile's CLI kit as a one-shot) does the job. A fallback, never the first choice.
        cfg = cfg or H.settings()
        return H.run(ask, profile=str(cfg.get("profile") or "zoey"), cfg=cfg, runner=runner)
    run = runner or subprocess.run
    try:
        env = _child_env()
        r = run(
            ["python3", str(path), ask[:1200]],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            **({"env": env} if env is not None else {}),
        )
    except subprocess.TimeoutExpired:
        return "Hands hit the time cap. Ask me to try a smaller piece."
    except Exception as e:  # noqa: BLE001
        return f"hands {type(e).__name__}: {e}"
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return out[-900:] if out else f"hands rc={r.returncode}"


# ── Her docs database (the User 2026-10-02) ───────────────────────────────────
# The docs index was a CLI and the child has no terminal, so she never reached it (10-02 08:19 she called it
# `state.db`). `krew_docs` is the reach, toolset `docs`; the logic is the sibling krew-docs plugin's docs.py
# (the port of the reference's docs module). A profile wears it by listing `docs` in a kit. Kill: KREW_DOCS=0
# (the tool says lookup is off).
DOCS_TOOLSET = "docs"
DOCS_MODULE = Path(__file__).resolve().parent.parent / "krew-docs" / "docs.py"


def _zoey_docs(path: Path | None = None):
    import importlib.util
    d = path or DOCS_MODULE
    # docs.py imports its sibling krew_settings relatively: load it as a member of a package rooted at its folder
    pkg = "krew_hands_docs_pkg"
    import sys
    if pkg not in sys.modules:
        pspec = importlib.util.spec_from_file_location(pkg, d.parent / "__init__.py",
                                                       submodule_search_locations=[str(d.parent)])
        pmod = importlib.util.module_from_spec(pspec)
        sys.modules[pkg] = pmod
    spec = importlib.util.spec_from_file_location(pkg + ".docs", str(d))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[pkg + ".docs"] = mod
    spec.loader.exec_module(mod)
    return mod


def register_docs(ctx) -> bool:
    # Registered only where the switch is on (the hands child of a drill after-arm, or once armed): the full
    # CLI kit loads every toolset, and an unknown plugin toolset defaults ON for the mouth.
    if os.environ.get("KREW_HANDS_DOCS", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False
    try:
        zd = _zoey_docs()
    except Exception as e:  # noqa: BLE001 - a tree without the module: no tool, today's kit
        logger.warning("krew-hands: krew_docs not registered (%s: %s)", type(e).__name__, e)
        return False

    def krew_docs(*args, **kwargs):
        try:
            return zd.handle(_args(args, kwargs))
        except Exception as e:  # noqa: BLE001
            return f"krew_docs {type(e).__name__}: {e}"

    try:
        ctx.register_tool(
            name="krew_docs",
            toolset=DOCS_TOOLSET,
            description=zd.DESCRIPTION,
            schema=canonical_schema("krew_docs", zd.DESCRIPTION, zd.SCHEMA),
            handler=krew_docs,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("krew-hands: krew_docs did NOT register (%s: %s)", type(e).__name__, e)
        return False


def register(ctx) -> None:
    # Always register so toolset `hands` is a known plugin key (Hermes
    # drops unknown names from platform_toolsets before the tool exists).
    # The handler is who-gated: only Zoey's mouth should call it.
    # The hands child wears `skill_read` too (its lean kit), so the toolset is declared before the
    # nested return: only the `hands` TOOL is withheld from a child.
    declare_skill_read()
    # krew_docs registers in the child too: the hands child is where she looks things up.
    register_docs(ctx)
    if os.environ.get("KREW_HANDS_NESTED", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return

    def hands(*args, **kwargs):
        # The owner of the tool is `hands.profile` in $KREW_HOME/krew.yaml (default zoey, the front door).
        cfg = H.settings()
        owner = str(cfg.get("profile") or "zoey").strip().lower()
        who = (
            getattr(ctx, "profile_name", None)
            or os.environ.get("HERMES_PROFILE")
            or owner
        ).strip().lower()
        if who and who != owner:
            return f"hands: the {owner} profile's CLI kit, not this profile's"
        p = _args(args, kwargs)
        return run_hands(ask_from(p), cfg=cfg)

    try:
        ctx.register_tool(
            name="hands",
            toolset="hands",
            description=HANDS_DESCRIPTION,
            schema=canonical_schema("hands", HANDS_DESCRIPTION, {
                "type": "object",
                "properties": {
                    "ask": {
                        "type": "string",
                        "description": "the job, in plain words",
                    },
                },
                "required": ["ask"],
            }),
            handler=hands,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("krew-hands: hands did NOT register (%s: %s)",
                     type(e).__name__, e)
