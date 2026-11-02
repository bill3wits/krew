"""The Control Panel as tools.

The human at the Control Panel (`panel.url` in krew.yaml) can read every unit lamp, pull a journal, open a
unit's start script, tail the camera/voice/security lanes, see the security
summary and the health battery, and press a small set of buttons. A kanban
worker could do none of it. This plugin closes that gap.

Every handler calls the SAME panel-server route (`panel.url`) the panel's own JavaScript calls,
so nothing here grants authority the glass did not already have — the
server's own guards (allowlisted units, occupancy checks, the Voice master's
local-only 403) stay the only gate. The panel server itself is never edited
from here.

Deliberately NOT here:
  * unit restart and the Voice master — a human's call at the glass.
  * `who_is_here` / error_check — those take the Inspector lease.
  * occupancy — that is the machine's GPU-mode tool on the `image_gen` toolset. One
    switcher, not two.

**Report the glass, never the POST.** Every actuator re-reads the real state
afterwards and answers with what landed. The render plugin learned this the
hard way ("Never report the command, report the card"); a cheerful string for an
accepted-but-not-landed POST is how a tool makes the machine lie to the owner.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

from . import krew_settings as S

_CFG = S.load("panel")


def _setting(env: str, key: str) -> str:
    """The environment first, then `panel:` in krew.yaml. "" = not set: no machine value is guessed."""
    return (os.environ.get(env) or str(_CFG.get(key) or "")).strip()


PANEL = _setting("KREW_PANEL_URL", "url").rstrip("/")
CAMERA = _setting("KREW_CAMERA_URL", "camera_url").rstrip("/")
ANNOUNCE_MUTE = os.path.expanduser(_setting("KREW_ANNOUNCE_MUTE", "announce_mute"))
NOT_SET = "NotConfigured"   # the error a call answers with when its URL is not set (nothing is sent)

# Lanes the panel offers. Anything else is a 400 from the server; refuse it
# here instead so the answer names the real choices.
LOG_LANES = ("camera", "voice", "security")
KIOSK_DESTS = ("panel", "wall")

# The panel server's health_battery action: a tier-3 battery speaks, moves the camera and
# restarts ws-shadow, so the server demands this exact sentence back.
T3_CONFIRM = "will speak, move camera, restart ws-shadow"


# --------------------------------------------------------------------------
# HTTP


def _get(path: str, timeout: float = 8.0) -> Tuple[int, Any]:
    """GET a panel route. A 400 body is an answer, not an exception."""
    if not PANEL:
        return 0, {"ok": False, "error": f"{NOT_SET}: panel.url"}
    url = f"{PANEL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return 0, {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def _post_action(action: str, body: Optional[dict] = None,
                 timeout: float = 30.0) -> Tuple[int, Any]:
    if not PANEL:
        return 0, {"ok": False, "error": f"{NOT_SET}: panel.url"}
    payload = dict(body or {})
    payload["action"] = action
    req = urllib.request.Request(
        f"{PANEL}/v1/action",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return 0, {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def _camera_health(timeout: float = 5.0) -> dict:
    if not CAMERA:
        return {"ok": False, "error": f"{NOT_SET}: panel.camera_url"}
    try:
        with urllib.request.urlopen(f"{CAMERA}/health", timeout=timeout) as r:
            out = json.loads(r.read().decode())
        return out if isinstance(out, dict) else {}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def _house() -> Tuple[int, Any]:
    return _get("/v1/house")


def _args(args: Any, kwargs: dict) -> dict:
    """Handlers are called both ways; normalise to one dict."""
    p: Dict[str, Any] = {}
    if isinstance(args, dict):
        p.update(args)
    elif isinstance(args, (list, tuple)) and args and isinstance(args[0], dict):
        p.update(args[0])
    p.update({k: v for k, v in kwargs.items() if k != "task_id"})
    return p


def _err(msg: str, **extra: Any) -> str:
    return json.dumps(dict({"ok": False, "error": msg}, **extra))


def _unreachable(payload: Any) -> Optional[str]:
    """One sentence when the panel server itself is down, so a caller never guesses."""
    if isinstance(payload, dict) and payload.get("error") and not payload.get("ok"):
        e = str(payload.get("error"))
        if e.startswith(NOT_SET):
            return (f"The Control Panel is not configured ({e.split(': ', 1)[-1]} in krew.yaml, or "
                    "KREW_PANEL_URL). Nothing was read and nothing changed.")
        if e.startswith(("URLError", "TimeoutError", "ConnectionRefusedError",
                         "socket", "OSError", "HTTPError")):
            return (f"The Control Panel at {PANEL} did not answer ({e}). "
                    "Nothing was read and nothing changed.")
    return None


def _chips(summary: Any) -> list:
    """The group chips the panel paints, without the layout noise."""
    rows = []
    for g in (summary or []):
        if not isinstance(g, dict):
            continue
        rows.append({
            "id": g.get("id"),
            "label": g.get("label"),
            "level": g.get("level"),
            "parts": g.get("parts"),
            "why": g.get("why") or "",
        })
    return rows


# --------------------------------------------------------------------------
# Read tools


def panel_units(args=None, **kwargs):
    """The 21 unit lamps, exactly as the panel paints them."""
    p = _args(args, kwargs)
    code, crew = _house()
    down = _unreachable(crew)
    if down:
        return _err(down)
    if code != 200 or not isinstance(crew, dict):
        return _err(f"panel returned HTTP {code}")

    svc = crew.get("svc") or {}
    units = svc.get("services") or []
    want = str(p.get("unit") or "").strip()
    trouble_only = bool(p.get("trouble_only"))

    rows = []
    for u in units:
        if not isinstance(u, dict):
            continue
        if want and u.get("unit") != want:
            continue
        if trouble_only and u.get("health") == "ok":
            continue
        rows.append({
            "unit": u.get("unit"),
            "label": u.get("label"),
            "health": u.get("health"),
            "active": u.get("active"),
            "sub": u.get("sub"),
            "restarts": u.get("restarts"),
            "port": u.get("port"),
            "note": u.get("note") or "",
        })

    if want and not rows:
        known = sorted(str(u.get("unit")) for u in units if isinstance(u, dict))
        return _err(f"no unit named {want!r} on the Control Panel", units=known)

    return json.dumps({
        "ok": True,
        "count": len(rows),
        "units": rows,
        "summary": _chips(svc.get("summary")),
        "fleet": svc.get("fleet"),
        "announcer_muted": svc.get("announcer_muted"),
        "gpu": crew.get("gpu"),
        "note": "A lit tile is asking for you. health: ok | warn | bad.",
    })


# GET /v1/journal takes only unit=. read_journal() hard-caps at n=100 and the
# server is chattr +i, so 100 is the honest maximum — do not advertise 400.
JOURNAL_MAX_LINES = 100


def panel_journal(args=None, **kwargs):
    """journalctl for one allowlisted unit."""
    p = _args(args, kwargs)
    unit = str(p.get("unit") or "").strip()
    if not unit:
        return _err("unit is required — call panel_units first if you need the name")
    asked = p.get("lines")
    try:
        want = max(1, int(asked if asked is not None else 60))
    except (TypeError, ValueError):
        want = 60
    lines = min(want, JOURNAL_MAX_LINES)

    code, out = _get("/v1/journal?" + urllib.parse.urlencode({"unit": unit}))
    down = _unreachable(out)
    if down:
        return _err(down)
    if code != 200:
        return _err(str((out or {}).get("error") or f"HTTP {code}"), unit=unit)

    rows = (out or {}).get("lines") or []
    body = {
        "ok": True,
        "unit": unit,
        "shown": len(rows[-lines:]),
        "total": len(rows),
        "lines": rows[-lines:],
        "cap": JOURNAL_MAX_LINES,
    }
    if want > JOURNAL_MAX_LINES:
        body["note"] = (
            f"The Control Panel journal route returns at most "
            f"{JOURNAL_MAX_LINES} lines (asked {want}). The server does not "
            "take n=."
        )
    return json.dumps(body)


def panel_script(args=None, **kwargs):
    """The unit file / start script behind a tile. Read-only, allowlisted."""
    p = _args(args, kwargs)
    unit = str(p.get("unit") or "").strip()
    if not unit:
        return _err("unit is required")
    try:
        cap = max(400, min(40000, int(p.get("max_chars") or 6000)))
    except (TypeError, ValueError):
        cap = 6000

    code, out = _get("/v1/script?" + urllib.parse.urlencode({"unit": unit}))
    down = _unreachable(out)
    if down:
        return _err(down)
    if code != 200 or not (out or {}).get("ok"):
        return _err(str((out or {}).get("error") or f"HTTP {code}"), unit=unit)

    # A start script can be thousands of lines. Handing all of it back burns a
    # worker's context on something it usually only needs the head of, so cap
    # each body and say plainly that it was cut.
    out = dict(out)
    files = []
    for f in (out.get("files") or []):
        if not isinstance(f, dict):
            continue
        f = dict(f)
        text = str(f.get("text") or "")
        if len(text) > cap:
            f["text"] = text[:cap]
            f["truncated"] = True
            f["full_chars"] = len(text)
        files.append(f)
    out["files"] = files
    if any(f.get("truncated") for f in files):
        out["note"] = (f"Bodies cut at {cap} chars. Raise max_chars if you need "
                       "more, or read the path directly.")
    return json.dumps(out)


def panel_log(args=None, **kwargs):
    """Tail a crew log lane (not a systemd journal — these are files)."""
    p = _args(args, kwargs)
    lane = str(p.get("lane") or "").strip().lower()
    if lane not in LOG_LANES:
        return _err(f"lane must be one of {', '.join(LOG_LANES)} (got {lane!r})")
    code, out = _get("/v1/logs?" + urllib.parse.urlencode({"lane": lane}))
    down = _unreachable(out)
    if down:
        return _err(down)
    if code != 200:
        return _err(str((out or {}).get("error") or f"HTTP {code}"), lane=lane)
    # The server already answers with its own `ok`. Forcing True here would
    # turn a real failure into a cheerful one — the exact lie this plugin is
    # supposed to make impossible.
    out = dict(out or {})
    out.setdefault("ok", True)
    out["lane"] = lane
    return json.dumps(out)


def panel_security(args=None, **kwargs):
    """Who was seen, and what was refused."""
    code, out = _get("/v1/security/summary")
    down = _unreachable(out)
    if down:
        return _err(down)
    if code != 200:
        return _err(str((out or {}).get("error") or f"HTTP {code}"))
    out = dict(out or {})
    out.setdefault("ok", True)
    return json.dumps(out)


def panel_health(args=None, **kwargs):
    """Read the last health battery. Starting a new one is opt-in.

    `/v1/house` already carries the latest battery result, so the default is
    a free read. `run: true` starts a real one; tier 3 is disruptive (it
    speaks, moves the camera and restarts ws-shadow) and the server demands
    the confirm sentence back, so pass it or be refused.
    """
    p = _args(args, kwargs)
    run = bool(p.get("run"))

    if not run:
        code, crew = _house()
        down = _unreachable(crew)
        if down:
            return _err(down)
        if code != 200 or not isinstance(crew, dict):
            return _err(f"panel returned HTTP {code}")
        return json.dumps({
            "ok": True,
            "ran": False,
            "battery": crew.get("battery"),
            "note": ("Last result, not a fresh check. Pass run=true for a new "
                     "one — tier 1 is quiet, tier 3 needs the confirm string."),
        })

    try:
        tier = int(p.get("tier") or 1)
    except (TypeError, ValueError):
        tier = 1
    tier = max(1, min(3, tier))
    body: Dict[str, Any] = {"tier": tier}
    if tier >= 3:
        confirm = str(p.get("confirm") or "")
        if confirm != T3_CONFIRM:
            return _err(
                "tier 3 is disruptive and needs the confirm sentence",
                need=T3_CONFIRM, tier=tier,
            )
        body["confirm"] = confirm

    code, out = _post_action("health_battery", body, timeout=120.0)
    down = _unreachable(out)
    if down:
        return _err(down)
    if code != 200 or not (out or {}).get("ok"):
        return _err(str((out or {}).get("error") or f"HTTP {code}"),
                    need=(out or {}).get("need"), tier=tier)

    # Report the battery, not the accepted POST.
    _, crew = _house()
    return json.dumps({
        "ok": True,
        "ran": True,
        "tier": tier,
        "job": out,
        "battery": (crew or {}).get("battery"),
        "note": "A started job is not a verdict — read `battery.worst`.",
    })


# --------------------------------------------------------------------------
# Actuators


def panel_camera_home(args=None, **kwargs):
    """Aim the camera back at the door — unless someone else holds it.

    `/panel/camera-home` POSTs `:8192/aim` with no lease check of its own, so
    the check lives here: a sweep, a tool hold, a pause or a live lease means
    somebody is mid-walk and this would steal the camera out from under them.
    """
    before = _camera_health()
    if str(before.get("error") or "").startswith(NOT_SET):
        return _err("panel.camera_url is not set in krew.yaml (or KREW_CAMERA_URL): nothing moved")
    if before.get("ok") is False and before.get("error"):
        return _err(f"the camera at {CAMERA} did not answer ({before['error']}) "
                    "— nothing moved")

    holders = []
    if before.get("lease"):
        holders.append(f"lease {before.get('lease')}")
    if before.get("tool_hold"):
        holders.append(f"tool hold ({before.get('hold_remaining_sec')}s left)")
    if before.get("sweeping"):
        holders.append("a sweep is running")
    if before.get("paused"):
        holders.append(f"paused by {before.get('pause_owner') or 'someone'}"
                       f" ({before.get('pause_reason') or 'no reason given'})")
    if holders:
        return _err(
            "the camera is held — " + "; ".join(holders)
            + ". Not stealing it. Try again when the walk is done.",
            at_door=before.get("at_door"),
        )

    code, out = _post_action("camera_home", timeout=20.0)
    down = _unreachable(out)
    if down:
        return _err(down)

    after = _camera_health()
    at_door = after.get("at_door")
    return json.dumps({
        "ok": bool(at_door),
        "at_door": at_door,
        "pan": after.get("pan_actual"),
        "door_home_pan": after.get("door_home_pan"),
        "panel": out,
        "note": ("Camera is resting on the door." if at_door else
                 "The panel accepted it but the camera is NOT at the door yet — "
                 "it may still be moving, or the aim failed. Look again."),
    })


def panel_kiosk(args=None, **kwargs):
    """Read, or flip, what the room kiosk is showing.

    Render no longer pins the studio panel (the owner 2026-09-13: Lila takes Crissa's
    place on the glass, the panel is the owner's choice), so `wall` is allowed
    in Render. KREW_RENDER_LILA_HOST=0 restores the old refusal.
    """
    p = _args(args, kwargs)
    dest = str(p.get("dest") or "").strip().lower()

    code, crew = _house()
    down = _unreachable(crew)
    if down:
        return _err(down)
    if code != 200 or not isinstance(crew, dict):
        return _err(f"panel returned HTTP {code}")

    gpu = crew.get("gpu") or {}
    if not dest:
        return json.dumps({
            "ok": True, "changed": False,
            "kiosk": crew.get("kiosk"), "gpu_mode": gpu.get("mode"),
        })
    if dest not in KIOSK_DESTS:
        return _err(f"dest must be one of {', '.join(KIOSK_DESTS)} (got {dest!r})")
    if dest == "wall" and gpu.get("mode") == "render" and os.environ.get(
        "KREW_RENDER_LILA_HOST", "1"
    ).strip().lower() in ("0", "false", "no", "off"):
        return _err(
            "occupancy is Render — the GPU-mode switcher owns the kiosk pin and keeps the "
            "studio panel up. Leave Render first if the wall is really wanted.",
            gpu_mode="render", kiosk=crew.get("kiosk"),
        )

    code, out = _post_action("kiosk_panel" if dest == "panel" else "kiosk_wall")
    down = _unreachable(out)
    if down:
        return _err(down)

    _, after = _house()
    return json.dumps({
        "ok": bool((out or {}).get("ok")),
        "changed": True,
        "asked": dest,
        "kiosk": (after or {}).get("kiosk"),
        "panel": out,
        "note": "`kiosk` is read back after the flip — trust it over `asked`.",
    })


def panel_announcer(args=None, **kwargs):
    """Read, or set, the announcer mute master.

    The keeper drops every announcement while the mute file exists, so the
    file's presence IS the state — that is what gets read back.
    """
    p = _args(args, kwargs)
    mute = p.get("mute")

    if mute is None:
        code, crew = _house()
        down = _unreachable(crew)
        if down:
            return _err(down)
        if code != 200 or not isinstance(crew, dict):
            return _err(f"panel returned HTTP {code}")
        return json.dumps({
            "ok": True, "changed": False,
            "muted": (crew.get("svc") or {}).get("announcer_muted"),
        })

    if isinstance(mute, str):
        mute = mute.strip().lower() in ("1", "true", "yes", "on", "mute", "muted")
    mute = bool(mute)

    code, out = _post_action("announcer_off" if mute else "announcer_on")
    down = _unreachable(out)
    if down:
        return _err(down)

    # The flag file is the state; read it, not the response. With no flag file configured
    # (panel.announce_mute), the panel's own answer, read back after the POST.
    if ANNOUNCE_MUTE:
        landed = os.path.exists(ANNOUNCE_MUTE)
    else:
        _, after = _house()
        landed = bool(((after or {}).get("svc") or {}).get("announcer_muted"))
    return json.dumps({
        "ok": landed == mute,
        "changed": True,
        "asked": "mute" if mute else "unmute",
        "muted": landed,
        "panel": out,
        "note": ("Announcer is muted — the keeper will drop announcements."
                 if landed else "Announcer will speak."),
    })


# --------------------------------------------------------------------------
# Registration

# (name, handler, description, schema)
_TOOLS = (
    (
        "panel_units", panel_units,
        "Read the Control Panel's unit lamps — every crew service tile, with its "
        "systemd state, health lamp, restart count and port. Use it whenever you "
        "need to know whether something is actually running before you blame it, "
        "or when the owner asks what is up / what is broken. unit= one name to look "
        "at just that tile; trouble_only=true to see only the lamps that are lit. "
        "health is ok | warn | bad; a lit tile is asking for you.",
        {
            "type": "object",
            "properties": {
                "unit": {"type": "string",
                         "description": "one systemd unit name, e.g. crew.service"},
                "trouble_only": {"type": "boolean",
                                 "description": "only units that are not ok"},
            },
        },
    ),
    (
        "panel_journal", panel_journal,
        "Tail journalctl for one crew unit. Use after panel_units shows a lamp "
        "lit, or when you need the actual traceback instead of a guess. The unit "
        "must be one the Control Panel lists — panel_units gives you the names. "
        "lines= how many trailing lines (default 60). The panel route caps at "
        "100 and does not take n= — asking for more returns 100 and a note.",
        {
            "type": "object",
            "properties": {
                "unit": {"type": "string"},
                "lines": {"type": "integer"},
            },
            "required": ["unit"],
        },
    ),
    (
        "panel_script", panel_script,
        "Read the systemd unit file and start script behind a crew service, so "
        "you can see how it is actually launched — flags, env, working dir — "
        "before changing anything. Read-only and path-allowlisted server-side.",
        {
            "type": "object",
            "properties": {
                "unit": {"type": "string"},
                "max_chars": {"type": "integer",
                              "description": "per-file body cap, default 6000"},
            },
            "required": ["unit"],
        },
    ),
    (
        "panel_log", panel_log,
        "Tail a crew log LANE — these are files the rails write, not systemd "
        "journals. camera = door/PTZ activity; voice = what was heard and spoken; "
        "security = face stamps and refusals. Use panel_journal instead when you "
        "want a unit's own stderr.",
        {
            "type": "object",
            "properties": {
                "lane": {"type": "string", "enum": list(LOG_LANES)},
            },
            "required": ["lane"],
        },
    ),
    (
        "panel_security", panel_security,
        "The Control Panel's security summary — who was seen and what was "
        "refused, from the incidents on disk. Use when asked who has been here "
        "or whether anything was turned away.",
        {"type": "object", "properties": {}},
    ),
    (
        "panel_health", panel_health,
        "Read the last crew health battery (free — it is already in crew "
        "state). Pass run=true to start a NEW one: tier 1 is quiet, tier 2 "
        "probes harder, tier 3 is disruptive — it speaks out loud, moves the "
        "camera and restarts ws-shadow — and needs confirm set to the exact "
        "sentence the refusal hands you. A started job is not a verdict: read "
        "battery.worst.",
        {
            "type": "object",
            "properties": {
                "run": {"type": "boolean",
                        "description": "start a new battery instead of reading"},
                "tier": {"type": "integer",
                         "description": "1 quiet, 2 harder, 3 disruptive"},
                "confirm": {"type": "string",
                            "description": "required verbatim for tier 3"},
            },
        },
    ),
    (
        "panel_camera_home", panel_camera_home,
        "Aim the door camera back to its rest position. Refuses — and says who "
        "holds it — if a sweep, an Inspector walk, a tool hold or a pause is in "
        "flight, so it never steals the camera mid-tour. Reports where the "
        "camera actually ended up, not whether the button was accepted.",
        {"type": "object", "properties": {}},
    ),
    (
        "panel_kiosk", panel_kiosk,
        "Read what the room kiosk is showing, or flip it: dest='panel' for the "
        "Control Panel, dest='wall' for Crissa's wall. Omit dest to just read. "
        "dest='wall' is refused while occupancy is Render, because the GPU-mode switcher "
        "pins the studio panel up and would undo it on the next tick.",
        {
            "type": "object",
            "properties": {
                "dest": {"type": "string", "enum": list(KIOSK_DESTS)},
            },
        },
    ),
    (
        "panel_announcer", panel_announcer,
        "Read or set the announcer mute master. mute=true silences crew "
        "announcements (the keeper drops them while the mute flag exists), "
        "mute=false lets them through. Omit mute to just read the state. Use "
        "when the machine is talking over someone, or before a quiet-hours job.",
        {
            "type": "object",
            "properties": {
                "mute": {"type": "boolean"},
            },
        },
    ),
)


def register(ctx) -> None:
    """Register every panel tool on the `team` toolset.

    register_tool's real signature is (name, toolset, schema, handler, ...).
    A wrong call raises TypeError, and a bare `except: pass` would turn that
    into a tool that simply never exists — the exact failure krew-comfy
    documents. Log loudly and keep going, so one bad tool cannot take the
    other eight down with it.
    """
    for name, handler, description, schema in _TOOLS:
        try:
            ctx.register_tool(
                name=name,
                toolset="team",
                description=description,
                schema=schema,
                handler=handler,
            )
        except Exception as e:
            logger.error("krew-panel: %s did NOT register (%s: %s)",
                         name, type(e).__name__, e)
