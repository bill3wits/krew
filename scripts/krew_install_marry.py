"""krew-install's marry step (2026-10-06): offer a Hermes profile to the Wall (Crissa) as an agent endpoint.

The Wall touches krew only through its agent endpoint; krew touches the Wall only through the player's
routes and this offer. The step writes one file, `<zoeyos home>/endpoints.d/hermes-<profile>.json`:

    {"name": ..., "url": "http://127.0.0.1:<port>/v1", "model": ..., "session_header": "X-Hermes-Session-Id",
     "system_prompt": "", "key_ref": {"file": "<the profile's .env>", "name": "API_SERVER_KEY"}, "by": "krew"}

The key is referenced, never copied: the Wall reads it from the profile's .env when it calls. `system_prompt ""`
says the profile brings its own personality (its SOUL). Nothing live changes here: the Wall keeps the endpoint it
has until the user clicks Use under "Detected" in Connect (or USE IT in Init) and the test call passes.

The profile's API server must be on: API_SERVER_ENABLED=true and an API_SERVER_KEY of 16+ characters in its
.env, then a gateway restart, which is yours to do. Hermes runs one gateway per host, so a named profile is served
at /p/<profile>/v1 by the default profile's server (API_SERVER_ENABLED in $HERMES_HOME/.env and
gateway.multiplex_profiles: true); the key is still the profile's own, in the profile's .env, because a named
profile never inherits the owner's key. With anything missing the step says how and writes nothing.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

SESSION_HEADER = "X-Hermes-Session-Id"
DEFAULT_PORT = 8642
PLAYER = "http://127.0.0.1:8100"


def add_args(ap) -> None:
    ap.add_argument("--no-marry", action="store_true",
                    help="do not offer a Hermes profile to the Wall (Crissa), even when it is installed")
    ap.add_argument("--marry-profile", default="",
                    help="the profile the Wall talks to (default: zoey when that profile exists, else main)")
    ap.add_argument("--zoeyos-home", default="", help="the Wall's state home (default: $ZOEYOS_HOME or ~/.zoeyos)")
    ap.add_argument("--zoeyos-player", default="", help="the Wall's player (default: $ZOEYOS_PLAYER or %s)" % PLAYER)


def read_env(path: Path) -> dict[str, str]:
    """VAR=value lines (export and quotes allowed). Values stay in memory; callers never print a key."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", ln)
        if not m:
            continue
        v = m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        out[m.group(1)] = v
    return out


def _on(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def zoeyos_home(a) -> Path:
    return Path(os.path.expanduser(a.zoeyos_home or os.environ.get("ZOEYOS_HOME") or "~/.zoeyos"))


def player(a) -> str:
    return (a.zoeyos_player or os.environ.get("ZOEYOS_PLAYER") or PLAYER).rstrip("/")


def player_answers(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url + "/v1/agent"), timeout=timeout) as r:
            return r.status == 200
    except (OSError, ValueError):
        return False


def profile_env(hh: Path, profile: str) -> Path:
    return hh / ".env" if profile in ("", "main", "default") else hh / "profiles" / profile / ".env"


def _multiplexed(hh: Path) -> bool:
    try:
        text = (hh / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"^\s*multiplex_profiles\s*:\s*(true|yes|on)\s*$", text, re.M | re.I))


def endpoint(hh: Path, profile: str) -> tuple[dict | None, str]:
    """The offer for `profile`, or (None, why not). The key's value is checked, never returned."""
    main = profile in ("", "main", "default")
    own = profile_env(hh, profile)
    env = read_env(own)
    base_env, prefix, key_file = env, "", own
    if not main:
        root = read_env(hh / ".env")
        if _on(root.get("API_SERVER_ENABLED")) and _multiplexed(hh):
            # one gateway serves every profile at /p/<profile>/v1 (gateway.multiplex_profiles).
            # Prefer that even when the named profile also has API_SERVER_ENABLED=true: a leftover
            # ENABLED+PORT on the profile does not mean a second server is listening (2026-10-06:
            # a named profile had ENABLED on :8652 while only :8642/p/<name> answered). A named profile never
            # inherits the owner's key (Hermes fails closed, 401): its own .env must carry API_SERVER_KEY.
            base_env, prefix = root, "/p/%s" % profile
        elif not _on(env.get("API_SERVER_ENABLED")):
            return None, "the API server is off for profile %s (API_SERVER_ENABLED in %s)" % (profile or "main", own)
    elif not _on(base_env.get("API_SERVER_ENABLED")):
        return None, "the API server is off for profile %s (API_SERVER_ENABLED in %s)" % (profile or "main", own)
    key = env.get("API_SERVER_KEY") or ""
    if len(key) < 16:
        return None, "profile %s has no API_SERVER_KEY of 16+ characters in %s" % (profile or "main", key_file)
    host = base_env.get("API_SERVER_HOST") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    try:
        port = int(base_env.get("API_SERVER_PORT") or DEFAULT_PORT)
    except ValueError:
        return None, "API_SERVER_PORT is not a number in %s" % own
    model = env.get("API_SERVER_MODEL_NAME") or ("hermes-agent" if main else profile)
    return {"name": "Hermes (%s)" % (profile or "main"), "url": "http://%s:%d%s/v1" % (host, port, prefix),
            "model": model, "session_header": SESSION_HEADER, "system_prompt": "",
            "key_ref": {"file": str(key_file.resolve()), "name": "API_SERVER_KEY"}, "by": "krew"}, ""


def default_profile(hh: Path) -> str:
    return "zoey" if (hh / "profiles" / "zoey").is_dir() else "main"


def marry(a, hh: Path, say) -> bool:
    """Write the offer when the Wall is here. Never changes the endpoint the Wall uses. True unless it failed."""
    if a.no_marry:
        return True
    zh, pl = zoeyos_home(a), player(a)
    if not zh.is_dir():
        if player_answers(pl):
            say("marry    a player answers at %s but its home is not %s: pass --zoeyos-home (or set ZOEYOS_HOME)"
                % (pl, zh))
        else:
            say("marry    Crissa not installed (no %s, no player at %s): skipped" % (zh, pl))
        return True
    profile = a.marry_profile or default_profile(hh)
    offer, why = endpoint(hh, profile)
    if offer is None:
        say("marry    not offered: %s." % why)
        say("         Turn it on (README: The Wall): API_SERVER_ENABLED=true and API_SERVER_KEY=<16+ characters>")
        say("         in that .env, restart that profile's gateway, then run krew-install again.")
        return True
    dst = zh / "endpoints.d" / ("hermes-%s.json" % re.sub(r"[^A-Za-z0-9._-]", "-", profile or "main"))
    body = json.dumps(offer, indent=2) + "\n"
    if dst.is_file() and dst.read_text(encoding="utf-8") == body:
        say("marry    same     %s" % dst)
    else:
        say("marry    offer    %s -> %s (key referenced in %s, not copied)" % (profile, dst, offer["key_ref"]["file"]))
        if not a.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".tmp")
            tmp.write_text(body, encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, dst)
    say("         The Wall still uses the endpoint it had. To switch: open %s/connect and click Use under" % pl)
    say("         Detected (or USE IT on Init's Connect step); the test call must pass.")
    return True
