"""krew_docs for the krew (the owner 2026-10-03): lookup + suggest, for reframes and error recovery.

The logic is docs.py beside this file (Crissa's own copy of the tool rides krew-hands). This plugin
registers it per PROFILE from $KREW_HOME/krew-docs.json (`{"steward": true, ...}`, off by default): an off profile gets
the `docs` toolset declared with no tools, so naming it in a kit costs nothing and is never dropped as unknown.
With no such file, `docs.profiles` in $KREW_HOME/krew.yaml decides (a list; [] = every profile that lists `docs`).
Every suggestion carries `by: <profile>`; the review queue is the owner's (crew/scripts/crissa-docs-review.py), and nothing
reaches the docs index without the owner's approval. Kill: KREW_DOCS=0 (the tool answers that lookup is off).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from . import krew_settings as S

logger = logging.getLogger(__name__)

SWITCHES = S.krew_home() / "krew-docs.json"
TOOLSET = "docs"
RUNTIME_KW = frozenset({"task_id", "session_id", "user_task", "enabled_tools", "parent_agent", "callback",
                        "tool_call_id", "turn_id", "api_request_id", "scope"})


def profile_of(ctx) -> str:
    return str(getattr(ctx, "profile_name", None) or os.environ.get("HERMES_PROFILE") or "").strip().lower()


def profile_on(profile: str, path: Path | None = None) -> bool:
    if not Path(path or S.krew_home() / "krew-docs.json").is_file():
        return list_on(profile)
    try:
        d = json.loads(Path(path or S.krew_home() / "krew-docs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool((d.get("profiles") or {}).get(profile, False))


def list_on(profile: str, settings: dict | None = None) -> bool:
    """krew.yaml's `docs.profiles` list, read only when there is no switch file: [] = every profile."""
    st = S.load("docs") if settings is None else settings
    allow = st.get("profiles")
    if not allow:
        return True
    return profile in {str(x).strip().lower() for x in allow}


def _docs():
    from . import docs as mod
    return mod


def _declare_empty() -> None:
    try:
        from toolsets import create_custom_toolset
        create_custom_toolset(TOOLSET, "The docs database (switched off for this profile).", tools=[])
    except Exception as e:  # noqa: BLE001
        logger.warning("krew-docs: empty toolset not declared (%s: %s)", type(e).__name__, e)


def register(ctx) -> None:
    who = profile_of(ctx)
    if not who or not profile_on(who):
        _declare_empty()
        return
    try:
        zd = _docs()
    except Exception as e:  # noqa: BLE001
        logger.warning("krew-docs: docs.py not loadable (%s: %s)", type(e).__name__, e)
        _declare_empty()
        return

    def krew_docs(*args, **kwargs):
        p: dict = {}
        if args and isinstance(args[0], dict):
            p.update(args[0])
        p.update({k: v for k, v in kwargs.items() if k not in RUNTIME_KW})
        try:
            return zd.handle(p, by=who)
        except Exception as e:  # noqa: BLE001
            return f"krew_docs {type(e).__name__}: {e}"

    desc = zd.DESCRIPTION
    schema = {"name": "krew_docs", "description": desc, "parameters": zd.SCHEMA}
    try:
        ctx.register_tool(name="krew_docs", toolset=TOOLSET, description=desc, schema=schema, handler=krew_docs)
    except Exception as e:  # noqa: BLE001
        logger.error("krew-docs: krew_docs did NOT register for %s (%s: %s)", who, type(e).__name__, e)
