"""krew-skill-preload — skill bodies once per session, same as TUI.

TUI/CLI honor ``HERMES_TUI_SKILLS`` by putting skill bodies on
``ephemeral_system_prompt`` (once per session, system role, cache-stable).

``gateway/platforms/api_server.py`` did that; ``hermes update`` wipes it.

mechanism.

This plugin wraps ``AIAgent.__init__`` for ``platform=api_server`` and
appends the same ``build_preloaded_skills_prompt`` block to
``ephemeral_system_prompt``. No tree file. Not ``pre_llm_call`` (that hook
injects the user message). Not ``register_system_prompt_section`` (4k/8k
cap; four SKILL.md bodies are 23,808 bytes).

If the checkout still carries the old tree patch, this stands down so the
two never double. Durable permanence is an upstream PR: API server honors
the env the TUI already honors.
"""
from __future__ import annotations

import logging
import os
try:
    from . import krew_hermes as KH
except ImportError:                 # imported alone (tests, scripts)
    import krew_hermes as KH  # noqa: E402

logger = logging.getLogger("krew-skill-preload")

PLATFORMS = ("api_server",)
_API_SERVER = str(KH.hermes_home() / "hermes-agent/gateway/platforms/api_server.py")
_STAND_DOWN = False
_WRAPPED = False


def api_server_preloads_itself(path: str = _API_SERVER) -> bool:
    ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "HERMES_TUI_SKILLS" in fh.read()
    except OSError:
        return False


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


def skill_ids() -> list[str]:
    raw = os.environ.get("HERMES_TUI_SKILLS", "")
    if _multiplex_on():
        # One process, one env: HERMES_TUI_SKILLS was a line on Crissa's OWN unit, and the host's
        # env is the default profile's. Each profile's config decides (krew_preload.skills,
        # read through the scope-aware loader); Crissa's says `none`.
        try:
            from hermes_cli.config import load_config
            sk = ((load_config() or {}).get("krew_preload") or {}).get("skills") or []
        except Exception:  # noqa: BLE001
            sk = []
        raw = sk if isinstance(sk, str) else ",".join(str(s) for s in sk)

    # Must not fall through to krew_preload.skills.
    if raw.strip().lower() in ("none", "off", "0", "-"):
        return []
    ids: list[str] = []
    seen: set = set()
    for part in raw.replace("\n", ",").split(","):
        item = part.strip()
        if item and item not in seen:
            seen.add(item)
            ids.append(item)
    if ids:
        return ids
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        sk = ((cfg.get("krew_preload") or {}).get("skills")) or []
        return [str(s) for s in sk if str(s).strip()]
    except Exception:
        return []


def preload_block(ids: list[str], session_id: str) -> tuple[str, list, list]:
    from agent.skill_commands import build_preloaded_skills_prompt
    sp, loaded, missing = build_preloaded_skills_prompt(ids, task_id=session_id)
    return (sp or ""), list(loaded or []), list(missing or [])


def apply_ephemeral(kwargs: dict) -> dict:
    """Put skill bodies on ephemeral_system_prompt for api_server agents.

    Mutates ``kwargs`` in place (same dict AIAgent.__init__ receives) and
    returns it. No-op when the tree still has the old patch, when the
    platform is not api_server, or when the block is already present.
    """
    if _STAND_DOWN:
        return kwargs
    if str(kwargs.get("platform") or "") not in PLATFORMS:
        return kwargs
    ids = skill_ids()
    if not ids:
        return kwargs
    sid = str(kwargs.get("session_id") or "")
    try:
        block, loaded, missing = preload_block(ids, sid)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "krew-skill-preload: preload failed (%s: %s)",
            e.__class__.__name__, str(e)[:160],
        )
        return kwargs
    if missing:
        logger.warning("krew-skill-preload: unknown skill(s) %s", ", ".join(missing))
    if not block:
        return kwargs
    extra = kwargs.get("ephemeral_system_prompt") or ""
    if block in extra:
        return kwargs
    kwargs["ephemeral_system_prompt"] = "\n\n".join(p for p in (extra, block) if p)
    logger.info(
        "krew-skill-preload: %d skill(s) → ephemeral_system_prompt session %s (%d chars): %s",
        len(loaded), sid[:12], len(block), ", ".join(loaded),
    )
    return kwargs


def _wrap_ai_agent() -> bool:
    """Install the ephemeral wrap on run_agent.AIAgent.__init__. Idempotent."""
    global _WRAPPED
    if _WRAPPED:
        return True
    try:
        import run_agent
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "krew-skill-preload: cannot import run_agent (%s) — room voice will not preload",
            e.__class__.__name__,
        )
        return False
    orig = run_agent.AIAgent.__init__
    if getattr(orig, "_krew_skill_preload", False):
        homes = getattr(orig, "_krew_skill_preload_homes", None)
        if isinstance(homes, set):
            homes.add(_home_key())              # a second profile enabling the plugin in one process
        _WRAPPED = True
        return True
    # The wrap lands on the ONE AIAgent class of the process. Under one host gateway that is every
    # served profile's agent, so it acts only for the homes whose profile enabled this plugin.
    homes = {_home_key()}

    def wrapped(self, *args, **kwargs):
        if not _multiplex_on() or _home_key() in homes:
            apply_ephemeral(kwargs)
        return orig(self, *args, **kwargs)

    wrapped._krew_skill_preload = True  # type: ignore[attr-defined]
    wrapped._krew_skill_preload_homes = homes  # type: ignore[attr-defined]
    run_agent.AIAgent.__init__ = wrapped
    _WRAPPED = True
    return True


def register(ctx) -> None:
    global _STAND_DOWN
    _STAND_DOWN = api_server_preloads_itself()
    if _STAND_DOWN:
        logger.info(
            "krew-skill-preload: api_server.py still carries HERMES_TUI_SKILLS — "
            "standing down (delete the tree patch; this plugin is the krew path)"
        )
        return
    if _wrap_ai_agent():
        logger.info(
            "krew-skill-preload: ephemeral_system_prompt wrap armed for %s; skills %s",
            ", ".join(PLATFORMS), ", ".join(skill_ids()) or "(none)",
        )
    else:
        logger.error("krew-skill-preload: wrap failed — room voice has no skill preload")
