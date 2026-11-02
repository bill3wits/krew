"""The Crissa Wall's verbs for an agent (cloned from Crissa's crissa_wall_verbs, 2026-10-06).

An agent drives the avatar only through the player's own public HTTP routes. Each verb is a name, a description
the model reads, a JSON schema, and a function that makes one HTTP call. Crissa serves the same verbs to any
agent over MCP (its scripts/crissa-mcp.py); this is the Hermes plugin's copy.

The player is CRISSA_PLAYER (default http://127.0.0.1:8100). Standard library only. Requests carry no Origin
header, so the player treats them as this machine's own (loopback) callers. No player answering means Crissa is
not installed here (or not running): every verb says so.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

try:                                            # kill-search: the player must be on the LAN
    from . import wall_egress as _egress
except ImportError:                             # loaded as a lone file beside wall_egress.py
    import wall_egress as _egress  # type: ignore[no-redef]  # noqa: E402

PLAYER = os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/")


class WallError(RuntimeError):
    pass


def _call(method: str, path: str, body: dict | None = None, timeout: float = 15.0, base: str | None = None,
          soft: tuple = ()) -> dict:
    url = (base or PLAYER).rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        opened = (_egress.urlopen(req, timeout, "the Wall's player") if _egress is not None
                  else urllib.request.urlopen(req, timeout=timeout))
        with opened as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read() or b"{}")
        except ValueError:
            out = {}
        if e.code in soft and isinstance(out, dict) and out.get("reply"):
            return dict(out, ok=False, spoke=False)      # answered, but not heard: the reply is still hers
        raise WallError(out.get("error") or "the player said %d on %s" % (e.code, path)) from None
    except PermissionError as e:                 # kill-search refused an address off the LAN
        raise WallError(str(e)) from None
    except (OSError, ValueError) as e:
        raise WallError("Crissa not installed: no Wall player is answering at %s (%s)" % (base or PLAYER, e)) from None


def state(args: dict, base: str | None = None) -> dict:
    s = _call("GET", "/v1/state", base=base)
    keep = ("now", "clip", "hub", "on_air", "comms", "reason", "speaker", "mouth_fps")
    return {k: s[k] for k in keep if k in s}


def keys(args: dict, base: str | None = None) -> dict:
    return _call("GET", "/v1/kit", base=base)


def cue(args: dict, base: str | None = None) -> dict:
    key = str(args.get("key") or "").strip()
    if not key:
        raise WallError("key required (see wall_keys)")
    body = {"key": key, "by": "agent"}
    if args.get("ttl") is not None:
        body["ttl"] = float(args["ttl"])
    return _call("POST", "/v1/cue", body, base=base)


def home(args: dict, base: str | None = None) -> dict:
    return _call("POST", "/v1/home", {"by": "agent"}, base=base)


def comms(args: dict, base: str | None = None) -> dict:
    if not isinstance(args.get("on"), bool):
        raise WallError("on: true|false")
    return _call("POST", "/v1/comms", {"on": args["on"], "why": "agent"}, base=base)


def speak(args: dict, base: str | None = None) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        raise WallError("text required")
    return _call("POST", "/v1/speak", {"text": text, "by": "agent"}, base=base)


def ask(args: dict, base: str | None = None) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        raise WallError("text required")
    # A 502 that carries a reply means the agent answered and her voice failed (the player checks since
    # 2026-10-04): the caller gets the reply with spoke false, never an error that hides what she said.
    return _call("POST", "/v1/ask", {"text": text, "wait": True, "by": "agent"}, timeout=120.0, base=base,
                 soft=(502,))


def stop(args: dict, base: str | None = None) -> dict:
    return _call("POST", "/v1/say", {"stop": True, "by": "agent"}, base=base)


_OBJ = {"type": "object", "properties": {}, "additionalProperties": False}
VERBS = [
    ("wall_state", "What the avatar is doing now: the clip on screen, its hub, whether she is speaking "
     "(on_air) and whether a conversation is open (comms).", _OBJ, state),
    ("wall_keys", "The named clips (keys) this avatar can play, for wall_cue.", _OBJ, keys),
    ("wall_cue", "Play a named clip (a gesture or pose change) at the next natural seam.",
     {"type": "object", "properties": {"key": {"type": "string", "description": "a key from wall_keys"},
                                       "ttl": {"type": "number", "description": "seconds the cue may wait"}},
      "required": ["key"], "additionalProperties": False}, cue),
    ("wall_home", "Send the avatar back to her resting loop.", _OBJ, home),
    ("wall_comms", "Open or close a conversation: on = she faces the person and listens between lines.",
     {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"],
      "additionalProperties": False}, comms),
    ("wall_speak", "Say these exact words in her voice, with her mouth moving. One to three short sentences, "
     "plain text.", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                     "additionalProperties": False}, speak),
    ("wall_ask", "Ask the agent connected on her Connect page and have her speak its answer; returns the "
     "reply.", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                "additionalProperties": False}, ask),
    ("wall_stop", "Stop her speaking now.", _OBJ, stop),
]
BY_NAME = {name: (desc, schema, fn) for name, desc, schema, fn in VERBS}


def call(name: str, args: dict | None = None, base: str | None = None) -> dict:
    """Run one verb; never raises: {"ok": False, "error": ...} on any failure."""
    if name not in BY_NAME:
        return {"ok": False, "error": "no such verb: %s" % name}
    try:
        out = BY_NAME[name][2](dict(args or {}), base=base)
        return out if isinstance(out, dict) and "ok" in out else {"ok": True, "result": out}
    except WallError as e:
        return {"ok": False, "error": str(e)}
