"""krew-board: the board in one tool, the clock, a config self-audit, and a slimmer kanban_show.

Tools (toolset `team`):
  team_board         list | show | create | comment | unblock | complete | answer, over `hermes kanban --json`
                     (board.py: the reference install's board tool, lifted whole; `answer` lands a ruling and
                     clears Crissa's list, the one team-crissa keeps under $KREW_HOME/team/list)
  team_time          the local wall-clock time (the system prompt carries the date only)
  team_config_audit  what this profile is granted vs what the framework offers, and grant gaps
Hook: transform_tool_result on kanban_show (slim.py). Off: KREW_KANBAN_SHOW_SLIM=0.

Settings (`board:` in $KREW_HOME/krew.yaml): author (the name on comments and created cards; default:
the calling profile), default_assignee (who a created card goes to when none is named; default: none,
the board's own default), pack_hint (where a config change should be proposed).
"""
from __future__ import annotations

import logging
import os

from . import board as B
from . import krew_settings as S
from . import slim as SLIM

logger = logging.getLogger("krew-board")


def _profile(ctx=None) -> str:
    return str(getattr(ctx, "profile_name", None) or os.environ.get("HERMES_PROFILE") or "").strip().lower()


def _child_env():
    """Under one host gateway serving several profiles, the CLI child gets the served profile's home;
    otherwise the process env is already the profile's (None = inherit)."""
    try:
        from agent.secret_scope import is_multiplex_active
        if not is_multiplex_active():
            return None
        from tools.environments.local import served_profile_child_env
        return served_profile_child_env(inherit_credentials=True)
    except Exception:  # noqa: BLE001 - an older framework: one profile per process
        return None


def _cli(args: list):
    return B.kanban_cli(args, env=_child_env())


def _schema(name: str, description: str, properties: dict, required: list | None = None) -> dict:
    params = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {"name": name, "description": description, "parameters": params}


BOARD_DESC = ("The board in one tool: list [status= assignee= since= offset=], show id= [full=true], create title= "
              "[text= assignee=], comment, unblock, complete, answer id= text= (lands the owner's words, unblocks, "
              "clears Crissa's list). A shortened reply always says what was left out and how to get it. "
              "Skills crissa-needs, crissa-big-push.")
TIME_DESC = "Current local time and date. The system prompt has the date only."
AUDIT_DESC = "Audit this profile's granted toolsets against what the framework offers, and grant gaps per platform."


def _audit(ctx) -> str:
    try:
        from toolsets import TOOLSETS
        from hermes_cli.config import get_config_path
    except Exception as e:  # noqa: BLE001
        return f"cannot read the toolset catalogue ({e.__class__.__name__}: {e})"
    try:
        path = get_config_path()
        with open(path, encoding="utf-8") as f:
            cfg = S._safe_load(f.read()) or {}
    except Exception as e:  # noqa: BLE001
        return f"cannot read my own config ({e.__class__.__name__}: {e})"
    return B.config_audit(toolsets=TOOLSETS, cfg=cfg, config_path=str(path),
                          pack_hint=str(S.load("board").get("pack_hint") or ""))


def register(ctx) -> None:
    def team_board(*args, **kwargs):
        st = S.load("board")
        return B.team_board(B.params(args, kwargs), cli=_cli,
                            author=str(st.get("author") or _profile(ctx) or ""),
                            default_assignee=str(st.get("default_assignee") or ""))

    def team_time(*args, **kwargs):
        return B.team_time()

    def team_config_audit(*args, **kwargs):
        return _audit(ctx)

    ctx.register_tool(name="team_board", toolset="team", handler=team_board, description=BOARD_DESC,
                      schema=_schema("team_board", BOARD_DESC, {
                          "action": {"type": "string", "enum": list(B.ACTIONS)},
                          "id": {"type": "string", "description": "t_… for show/comment/unblock/complete/answer"},
                          "title": {"type": "string", "description": "create: the card"},
                          "text": {"type": "string",
                                   "description": "create body · comment · answer · unblock reason · complete result"},
                          "status": {"type": "string", "enum": list(B.STATUS)},
                          "assignee": {"type": "string"},
                          "since": {"type": "string", "description": "list: a window, e.g. 2h or 90m"},
                          "offset": {"type": "integer", "description": "list: rows to skip (the next page)"},
                          "full": {"type": "boolean", "description": "show: every comment and field whole"},
                          "triage": {"type": "boolean", "description": "create: park in triage (default true)"},
                      }, ["action"]))
    ctx.register_tool(name="team_time", toolset="team", handler=team_time, description=TIME_DESC,
                      schema=_schema("team_time", TIME_DESC, {}))
    ctx.register_tool(name="team_config_audit", toolset="team", handler=team_config_audit, description=AUDIT_DESC,
                      schema=_schema("team_config_audit", AUDIT_DESC, {}))

    def _slim_kanban_show(tool_name=None, result=None, **_kw):
        if tool_name != "kanban_show" or not isinstance(result, str) or not S.switch_on("KREW_KANBAN_SHOW_SLIM"):
            return None
        try:
            return SLIM.slim(result, os.environ.get("HERMES_KANBAN_TASK") or None)
        except Exception:  # noqa: BLE001 - never cost a worker its card
            return None

    try:
        ctx.register_hook("transform_tool_result", _slim_kanban_show)
    except Exception as e:  # noqa: BLE001
        logger.warning("krew-board: kanban_show slim hook not registered (%s: %s)", type(e).__name__, e)
