"""The announcer's seam: what the reference install's PA library did with its own audio stack, krew does through
the Wall's player (POST /v1/announce; the player queues the line, holds it while someone is talking, drops it
in quiet hours or under its mute flag). crew/scripts/krew-announce-drain.py imports these names and nothing else
changes there. No player answering = Crissa is not installed, and the result says so.

    announce(text, category=, dedup_key=, wait_mic=, max_wait_s=, note=)   -> {"ok", "queued", "reason"|"error"}
    mic_busy()        why a new line must wait (the player is on the air) or None
    pa_hold()         a conversation is open at the ear (hold the queue, keep the cards) or None
    in_quiet_hours()  the owner's down hours (window/lib/krew_hours.py; KEEPER_ANNOUNCE_START/END still win)
    MUTE              the flag file that mutes the announcer (KREW_ANNOUNCE_MUTE)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
import krew_hermes as KH  # noqa: E402  where Hermes lives

_LIB = Path(__file__).resolve().parent
for _p in (_LIB, _LIB.parents[1] / "window" / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CRISSA_PLAYER = os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/")
MUTE = Path(os.environ.get("KREW_ANNOUNCE_MUTE", str(KH.hermes_home() / "krew-announce-mute"))).expanduser()
TOOL_HOLD = Path(os.environ.get("KREW_TOOL_HOLD", str(KH.hermes_home() / "krew-tool-hold.json"))).expanduser()
ANNOUNCE_START = int(os.environ.get("KEEPER_ANNOUNCE_START", "8"))
ANNOUNCE_END = int(os.environ.get("KEEPER_ANNOUNCE_END", "22"))
ALLOWED = frozenset({"completion", "stuck", "protocol", "stage", "test"})
PA_MAX_CHARS = int(os.environ.get("KREW_PA_MAX_CHARS", "220"))
BY = os.environ.get("KREW_ANNOUNCE_BY", "krew-announce-drain")


def _get(path: str, timeout: float = 0.6) -> dict | None:
    try:
        with urllib.request.urlopen(CRISSA_PLAYER + path, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8") or "{}")
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _for_speech(text: str) -> str:
    try:
        import speakable
        return speakable.for_speech(text, max_chars=PA_MAX_CHARS)
    except Exception:  # noqa: BLE001 - hygiene never costs the line
        t = (text or "").strip()
        if len(t) > PA_MAX_CHARS:
            t = t[: PA_MAX_CHARS - 1].rsplit(" ", 1)[0] + "."
        return t


def mic_busy() -> str | None:
    """Why the announcer must wait: the player is on the air (her own line is playing)."""
    st = _get("/v1/state")
    if st and st.get("on_air") is True:
        return "player on air"
    return None


def pa_hold() -> str | None:
    """A conversation is open at the ear: do not start a new PA line, keep the queue (the cards play when it closes)."""
    ear = _get("/v1/ear?part=stt")
    if ear and ear.get("session_open") is True:
        if ear.get("comms_hold") is True and ear.get("speaking") is not True and ear.get("dispatch_busy") is not True:
            return None
        return "session_open"
    return None


def _tool_hold() -> bool:
    """The keeper's JSON hold file: {"until": <epoch>}; the reference install's tool-hold script is not shipped."""
    try:
        if TOOL_HOLD.is_file():
            d = json.loads(TOOL_HOLD.read_text())
            until = float(d.get("until") or 0)
            return bool(until and time.time() < until)
    except Exception:  # noqa: BLE001
        pass
    return False


def in_quiet_hours(now: Any = None) -> bool:
    now = now or dt.datetime.now()
    explicit = "KEEPER_ANNOUNCE_START" in os.environ or "KEEPER_ANNOUNCE_END" in os.environ
    if explicit or os.environ.get("KREW_QUIET_FROM_GLOBAL", "1").strip().lower() in ("0", "false", "no", "off"):
        return not (ANNOUNCE_START <= now.hour < ANNOUNCE_END)
    try:
        import krew_hours
        return bool(krew_hours.is_quiet(now))
    except Exception:  # noqa: BLE001 - an unreadable settings file must not make the krew chatty at night
        return not (ANNOUNCE_START <= now.hour < ANNOUNCE_END)


def announce(text: str, *, category: str = "stuck", dedup_key: str | None = None, wait_mic: bool = True,
             max_wait_s: float = 45.0, note: str = "") -> dict[str, Any]:
    """Speak on PA through the player, and log what happened to the line (announce_log, the User 2026-09-30)."""
    r = _announce(text, category=category, dedup_key=dedup_key, max_wait_s=max_wait_s)
    try:
        import announce_log
        event = "spoken" if r.get("ok") else ("skipped" if r.get("skipped") else "failed")
        why = str(r.get("reason") or r.get("error") or ((r.get("voice") or r.get("engine")) if r.get("ok") else "") or "")
        announce_log.record(event, _for_speech(text) or text, category=(category or "").strip().lower(),
                            reason=" · ".join(x for x in (why, note) if x))
    except Exception:  # noqa: BLE001 - a log never costs the announcer its line
        pass
    return r


def _announce(text: str, *, category: str = "stuck", dedup_key: str | None = None, max_wait_s: float = 45.0) -> dict[str, Any]:
    cat = (category or "stuck").strip().lower()
    if cat not in ALLOWED:
        return {"ok": False, "skipped": True, "reason": f"category_not_allowed:{cat}"}
    spoken = _for_speech(text)
    if not spoken or spoken.lower() in ("none", "null"):
        return {"ok": False, "skipped": True, "reason": "empty"}
    if MUTE.is_file():
        return {"ok": False, "skipped": True, "reason": "muted"}
    body = {"text": spoken, "kind": cat, "by": BY, "dedup_key": dedup_key or "", "max_wait_s": max_wait_s}
    req = urllib.request.Request(CRISSA_PLAYER + "/v1/announce", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"the player said {e.code}"}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"Crissa not installed (no player at {CRISSA_PLAYER}): {e}"[:200]}
    if out.get("queued"):
        return {"ok": True, "queued": True, "depth": out.get("depth"), "engine": "crissa-player"}
    return {"ok": False, "skipped": True, "reason": str(out.get("reason") or "not queued")}
