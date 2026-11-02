"""crissa-wall: the Crissa Wall's verbs as agent tools, and kill-search for the agent's web tools.

krew's plugin (2026-10-06; cloned from the one Crissa used to carry: Crissa ships no agent-framework plugins).
The tools are registered through the plugin API: register(ctx) -> ctx.register_tool(name, toolset, schema,
handler). A tool's description goes INSIDE its schema ({"name", "description", "parameters"}), the shape built-in
tools register, so the model always reads it. Each handler makes one HTTP call to the player; with no player
answering it says Crissa is not installed. The pre_tool_call hook and the tool_execution step are kill-search
(wall_egress): off, and with no relay set, they change nothing.
"""
from __future__ import annotations

import json

from . import wall_egress as E                    # kill-search
from . import wall_verbs as V                     # the Wall's verbs (HTTP to the player only)

TOOLSET = "crissa"
_MIDDLEWARE = []      # set at register: whether the framework runs our execution step


def _on_pre_tool_call(tool_name: str = "", args=None, **_kw):
    """kill-search, the refusal half: with the switch on, search with no relay set is off, page reads with no page
    reader set are off, and a browser tool may not open an address outside your network. None (the tool runs
    untouched) whenever the switch is off and no relay is set. A broken switch file never stops a tool call."""
    try:
        v = E.agent_tool_verdict(tool_name, args)
    except Exception:  # noqa: BLE001
        return None
    if v and v.get("action") == "relay" and not _MIDDLEWARE:
        # a framework with no execution step: answer from the relay here (its veto message is size-capped,
        # which is still better than the framework's own backend leaving the network)
        return {"action": "block", "message": E.relay_answer(tool_name, args)}
    return v if v and v.get("action") == "block" else None


def _on_tool_execution(tool_name: str = "", args=None, next_call=None, **_kw):
    """kill-search, the relay half: search and page reads the relay serves are answered from it, so the
    framework's own search backend and page reader never run; every other call goes on untouched. (A refusal
    here, or a failure, falls through to the framework's own call: the audit, not this plugin, proves the claim.)"""
    got = E.relay_answer(tool_name, args)
    return next_call(args) if got is None else got


def _handler(name: str):
    def run(args=None, **_kw):
        return json.dumps(V.call(name, args if isinstance(args, dict) else {}))
    run.__name__ = name
    return run


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    if hasattr(ctx, "register_middleware"):        # the framework's execution step (2026-09); older ones: refusals only
        ctx.register_middleware("tool_execution", _on_tool_execution)
        _MIDDLEWARE.append(True)
    for name, description, params, _fn in V.VERBS:
        ctx.register_tool(name=name, toolset=TOOLSET,
                          schema={"name": name, "description": description, "parameters": params},
                          handler=_handler(name), description=description)
