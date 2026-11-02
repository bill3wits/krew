"""crissa_watch: is Crissa installed and live on this machine, and how is its side doing - asked over HTTP, every pass.

The owner 2026-10-06: krew's keeper and Janitor detect whether Crissa is installed and live. Live means its Wall player
answers GET /v1/state. Then they also watch the Crissa side, and only through the player: its /v1/state and
its /v1/ear (krew-join 2026-10-06: the player answers for her ear too). They never read Crissa's files. Not live means they watch krew's own
services only. Their status says NOT_INSTALLED where it can be seen, and a missing or stopped Crissa service
raises no alarm. Callers ask on every pass (nothing is cached), so installing Crissa or stopping it is picked up
on the next tick, with no restart.

    probe()                  -> {live, level, headline, why, player, ear, state, problems, notes, epoch}
    is_crissa_unit(u, pats)  -> True when a unit name matches one of Crissa's unit patterns
    flip(prev, now)          -> the one line to log when live/not-live changed, else ""

Where it asks: CRISSA_PLAYER (default http://127.0.0.1:8100, the Wall player krew's crissa-wall plugin and
team-crissa also use): GET /v1/state, then GET /v1/ear?part=stt. CRISSA_EAR is only a switch now ("off" = do not
watch her ear; anything else, or unset, = watch it through the player). A Crissa older than /v1/ear (404) is a note
("update Crissa"), never an alarm. The Janitor passes `crissa.player` / `crissa.ear` from janitor.yaml. Standard library only. A request carries no Origin (the player
refuses cross-origin writes; these are reads).
"""
from __future__ import annotations

import fnmatch
import json
import os
import time
import urllib.error
import urllib.request

NOT_INSTALLED = "Crissa not installed — watching krew only"
DEFAULT_PLAYER = "http://127.0.0.1:8100"
EAR_PATH = "/v1/ear?part=stt"      # the player's answer for her ear: {ok, why|error, stt: {ok, ..., ear: {open, closed_reason}}}
OFF = ("off", "none", "no", "0", "false")
DEFAULT_UNIT_PATTERNS = ("crissa-*",)
STATE_KEYS = ("on_air", "mode", "wall", "ok", "version")   # the few /v1/state facts worth a status line


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - a local URL from settings
        body = r.read(256 * 1024)
    d = json.loads(body.decode("utf-8", "replace") or "null")
    if not isinstance(d, dict):
        raise ValueError("answer is not a JSON object")
    return d


def _why(e: BaseException) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:   # the player says why in its JSON body
            d = json.loads(e.read(64 * 1024).decode("utf-8", "replace") or "null")
            if isinstance(d, dict) and (d.get("why") or d.get("error")):
                return f"HTTP {e.code}: {d.get('why') or d.get('error')}"[:160]
        except Exception:  # noqa: BLE001
            pass
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        return str(getattr(e, "reason", e))[:120]
    return f"{type(e).__name__}: {e}"[:120]


def player_url(player: str | None = None) -> str:
    return (player or os.environ.get("CRISSA_PLAYER") or DEFAULT_PLAYER).rstrip("/")


def ear_watched(ear: str | None = None) -> bool:
    """crissa.ear / CRISSA_EAR is a switch: off = do not watch her ear. Any other value (an old ear address
    included) = watch it, through the player's /v1/ear."""
    v = ear if ear not in (None, "") else (os.environ.get("CRISSA_EAR") or "on")
    return str(v).strip().lower() not in OFF


def probe(player: str | None = None, ear: str | None = None, *, timeout: float = 2.0, get=None,
          now: float | None = None) -> dict:
    """One look at Crissa, through its endpoints only. Never raises."""
    get = get or _get_json
    now = time.time() if now is None else now
    p, watch_ear = player_url(player), ear_watched(ear)
    e = p + EAR_PATH
    # not live is a normal way to run krew (level ok): said in the headline, never an alarm
    out = {"epoch": now, "player": p, "ear_url": e if watch_ear else "", "live": False, "level": "ok",
           "headline": NOT_INSTALLED, "why": "", "state": {}, "ear": {}, "problems": [], "notes": []}
    try:
        st = get(p + "/v1/state", timeout)
    except Exception as ex:  # noqa: BLE001 - not answering is the answer
        out["why"] = f"no Wall player answering GET {p}/v1/state ({_why(ex)})"
        return out
    out.update(live=True, level="ok", state={k: st[k] for k in STATE_KEYS if k in st})
    problems, notes = [], []
    if not watch_ear:
        out["ear"] = {"watched": False, "why": "CRISSA_EAR / crissa.ear is off"}
    else:
        try:
            es = get(e, timeout)
        except urllib.error.HTTPError as ex:
            if ex.code == 404:   # a Crissa from before the player answered for her ear: say so, no alarm
                out["ear"] = {"watched": False, "older": True, "why": f"GET {e}: HTTP 404"}
                notes.append(f"this Crissa is older than /v1/ear (player at {p}) — update Crissa to watch her ear")
            else:
                w = _why(ex)   # reads the body once
                out["ear"] = {"watched": True, "answering": False, "why": w}
                problems.append(f"her ear is not answering: {w} (GET {e})")
            es = None
        except Exception as ex:  # noqa: BLE001
            w = _why(ex)
            out["ear"] = {"watched": True, "answering": False, "why": w}
            problems.append(f"her ear is not answering: {w} (GET {e})")
            es = None
        if es is not None:
            stt = es.get("stt") if isinstance(es.get("stt"), dict) else {}
            ear = stt.get("ear") if isinstance(stt.get("ear"), dict) else {}
            if es.get("ok") is False or stt.get("ok") is False:
                why = (es.get("why") or es.get("error") or stt.get("why") or stt.get("error")
                       or "the player says not ok")
                out["ear"] = {"watched": True, "answering": False, "why": str(why)[:160]}
                problems.append(f"her ear is not answering: {str(why)[:160]} (GET {e})")
            else:
                is_open = ear.get("open")
                out["ear"] = {"watched": True, "answering": True, "open": is_open,
                              "closed_reason": ear.get("closed_reason") or "", "engine": stt.get("engine") or ""}
                if is_open is False:
                    problems.append("Crissa ear is closed" + (f" ({ear['closed_reason']})" if ear.get("closed_reason")
                                                              else "") + f" (GET {e})")
    out["problems"], out["notes"] = problems, notes
    if problems:
        out.update(level="bad", headline=f"Crissa live, but: {problems[0]}" + (
            f" +{len(problems) - 1} more" if len(problems) > 1 else ""))
    else:
        on_air = out["state"].get("on_air")
        out["headline"] = "Crissa live — player answering" + (", ear open" if out["ear"].get("open") else "") + (
            ", on air" if on_air else "") + (f"; {notes[0]}" if notes else "")
    return out


def is_crissa_unit(unit: str, patterns=None) -> bool:
    pats = tuple(patterns) if patterns else DEFAULT_UNIT_PATTERNS
    return bool(unit) and any(fnmatch.fnmatchcase(unit, p) for p in pats)


def flip(prev: dict | None, now: dict) -> str:
    """The line to log when Crissa came or went (or on the first pass), else ''."""
    if prev is not None and bool(prev.get("live")) == bool(now.get("live")):
        return ""
    if now.get("live"):
        return "Crissa is live — watching krew and the Crissa side (player /v1/state and /v1/ear)"
    return f"{NOT_INSTALLED} ({now.get('why') or 'its player is not answering'})"


if __name__ == "__main__":
    print(json.dumps(probe(), indent=1))
