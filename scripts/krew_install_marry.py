"""krew-install's marry step (2026-10-06): offer a Hermes profile to the Wall (Crissa) as an agent endpoint.

The Wall touches krew only through its agent endpoint; krew touches the Wall only through the player's
routes and this offer. The step writes one file, `<crissa home>/endpoints.d/hermes-<profile>.json`:

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

2026-10-30 (the 1.0.2 buyer test, findings 3 and 10): init passes --api-server-on, so a buyer sets nothing by hand.
Only what is unset is written: API_SERVER_ENABLED=true, API_SERVER_HOST=127.0.0.1, an API_SERVER_PORT that is free
here (from 8792 up, never 8642, the reference house's) and a key in $HERMES_HOME/.env, and the profile's own key in
its .env (kill: KREW_MARRY_API_ON=0). And the marry step writes where her player really is: Crissa's launcher table
(<crissa home>/run/procs.json, children.player.port: the port it bound, --port-offset included) becomes
CRISSA_PLAYER in $HERMES_HOME/.env, which the one gateway loads into its environment at start, so team-crissa and
every plugin reads it instead of the :8100 default (a line of yours is kept; kill: KREW_MARRY_PLAYER=0). No key
value is ever printed or logged.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import urllib.error
import urllib.request
from pathlib import Path

SESSION_HEADER = "X-Hermes-Session-Id"
DEFAULT_PORT = 8642          # Hermes's default API port and the reference house's own: never chosen for a buyer
PORT_FIRST = 8792            # where the search for a free port starts (the 1.0.2 buyer test's own pick)
PLAYER = "http://127.0.0.1:8100"
PLAYER_LINE = "# krew marry: the Wall's player, from Crissa's launcher table (krew-install rewrites this line)"


def add_args(ap) -> None:
    ap.add_argument("--no-marry", action="store_true",
                    help="do not offer a Hermes profile to the Wall (Crissa), even when it is installed")
    ap.add_argument("--marry-profile", default="",
                    help="the profile the Wall talks to (default: crissa when that profile exists, else main)")
    ap.add_argument("--crissa-home", default="", help="the Wall's state home (default: $CRISSA_HOME or ~/.crissa)")
    ap.add_argument("--crissa-player", default="", help="the Wall's player (default: $CRISSA_PLAYER or %s)" % PLAYER)
    ap.add_argument("--api-server-on", action="store_true",
                    help="marry: turn the gateway's API server on and give the profile its own key where unset "
                         "(init does this)")


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


def crissa_home(a) -> Path:
    return Path(os.path.expanduser(a.crissa_home or os.environ.get("CRISSA_HOME") or "~/.crissa"))


def player(a) -> str:
    return (a.crissa_player or os.environ.get("CRISSA_PLAYER") or PLAYER).rstrip("/")


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


def _off(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() in ("0", "false", "no", "off")


def _set_lines(path: Path, values: dict, *, dry: bool, comment: str = "") -> list[str]:
    """Add the VAR=value lines that are not set (as an uncommented line). Returns the names added; never a value."""
    have = read_env(path)
    add = {k: v for k, v in values.items() if k not in have}
    if add and not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = path.read_text(encoding="utf-8") if path.exists() else ""
        if body and not body.endswith("\n"):
            body += "\n"
        body += (comment + "\n" if comment else "") + "".join(f"{k}={v}\n" for k, v in add.items())
        tmp = path.with_name(path.name + ".krew-tmp")
        tmp.write_text(body, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return list(add)


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.3)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return False
    finally:
        s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        s.close()
    return True


def free_port(first: int = PORT_FIRST, span: int = 200, free=_port_free) -> int:
    for p in range(first, first + span):
        if p != DEFAULT_PORT and free(p):
            return p
    raise SystemExit(f"krew-install: no free port in {first}..{first + span - 1} for the API server")


def api_server_on(hh: Path, profile: str, say, *, dry: bool, free=_port_free) -> list[str]:
    """The one host gateway's API server on, and the profile's own key: only what is unset (yours is kept)."""
    root = hh / ".env"
    have = read_env(root)
    want = {"API_SERVER_ENABLED": "true", "API_SERVER_HOST": "127.0.0.1"}
    if "API_SERVER_PORT" not in have:
        want["API_SERVER_PORT"] = str(free_port(free=free))
    if "API_SERVER_KEY" not in have:
        want["API_SERVER_KEY"] = secrets.token_urlsafe(32)
    if "API_SERVER_ENABLED" in have and not _on(have["API_SERVER_ENABLED"]):
        say(f"marry    kept     API_SERVER_ENABLED={have['API_SERVER_ENABLED']} in {root} (yours: the server stays off)")
    added = [f"{root}: {n}" for n in _set_lines(root, want, dry=dry,
                                                comment="# krew marry: the one gateway's API server (the Wall talks to it)")]
    if profile not in ("", "main", "default"):
        pe = profile_env(hh, profile)
        added += [f"{pe}: {n}" for n in _set_lines(pe, {"API_SERVER_KEY": secrets.token_urlsafe(32)}, dry=dry,
                                                   comment="# krew marry: this profile's own API key (the Wall reads it here)")]
    for x in added:
        say(f"marry    set      {x}")
    return added


def wall_player(zh: Path) -> tuple[str, str]:
    """(player url, where it came from) from Crissa's launcher table; ('', why not) when it names none."""
    f = zh / "run" / "procs.json"
    try:
        row = (json.loads(f.read_text(encoding="utf-8")).get("children") or {}).get("player") or {}
        port = int(row.get("port") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        return "", f"no launcher table at {f} (start Crissa once, then run krew-install again)"
    if not port:
        return "", f"{f} names no player port"
    return f"http://127.0.0.1:{port}", f"{f} children.player.port"


def player_env(hh: Path, url: str, say, *, dry: bool) -> str:
    """CRISSA_PLAYER in $HERMES_HOME/.env under krew's marker line; a CRISSA_PLAYER line of yours is kept."""
    root = hh / ".env"
    lines = (root.read_text(encoding="utf-8") if root.exists() else "").splitlines()
    for i, ln in enumerate(lines):
        if re.match(r"\s*(?:export\s+)?CRISSA_PLAYER\s*=", ln):
            cur = read_env(root).get("CRISSA_PLAYER", "")
            if not (i > 0 and lines[i - 1].strip() == PLAYER_LINE):
                say(f"marry    kept     CRISSA_PLAYER={cur} in {root} (yours; her launcher says {url})")
                return "kept"
            if cur == url:
                say(f"marry    same     CRISSA_PLAYER={url} in {root}")
                return "same"
            lines[i] = f"CRISSA_PLAYER={url}"
            break
    else:
        lines += [PLAYER_LINE, f"CRISSA_PLAYER={url}"]
    say(f"marry    player   CRISSA_PLAYER={url} -> {root} (the gateway loads it at start)")
    if not dry:
        root.parent.mkdir(parents=True, exist_ok=True)
        tmp = root.with_name(root.name + ".krew-tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, root)
    return "written"


def default_profile(hh: Path) -> str:
    return "crissa" if (hh / "profiles" / "crissa").is_dir() else "main"


def marry(a, hh: Path, say) -> bool:
    """Write the offer when the Wall is here. Never changes the endpoint the Wall uses. True unless it failed."""
    if a.no_marry:
        return True
    if os.environ.get("KREW_WALL_REFUSED"):           # krew init: a Crissa home it was not given (krew_install_homes)
        say(f"refused  marry: {os.environ['KREW_WALL_REFUSED']}")
        return True
    zh, pl = crissa_home(a), player(a)
    if not zh.is_dir():
        if player_answers(pl):
            say("marry    a player answers at %s but its home is not %s: pass --crissa-home (or set CRISSA_HOME)"
                % (pl, zh))
        else:
            say("marry    Crissa not installed (no %s, no player at %s): skipped" % (zh, pl))
        return True
    profile = a.marry_profile or default_profile(hh)
    if getattr(a, "api_server_on", False) and not _off("KREW_MARRY_API_ON"):
        api_server_on(hh, profile, say, dry=a.dry_run)
    if not _off("KREW_MARRY_PLAYER"):
        url, src = wall_player(zh)
        if url:
            player_env(hh, url, say, dry=a.dry_run)
        else:
            say(f"marry    player   not written: {src}")
    offer, why = endpoint(hh, profile) if not a.dry_run or not getattr(a, "api_server_on", False) else (None, "dry run")
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
