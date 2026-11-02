"""Where a hermes profile is reached over HTTP, under either topology.

units: each gateway has its own port           http://127.0.0.1:<its port>/v1/chat/completions (UNIT_PORTS)
host:  one listener, a prefix per profile      http://127.0.0.1:<host port>/p/crissa/v1/chat/completions

`/p/<profile>/...` accepts ONLY that profile's own `API_SERVER_KEY` (rehearsed 2026-09-27: own
key 200, another profile's 401, no key 401, unknown profile 404), so `key()` reads the profile's
own `.env` under both topologies. The default profile has no prefix.

Every caller that wrote a port literal goes through here; its own env override (CRISSA_GATEWAY_URL,
EAR_BRIDGE_CRISSA, ...) still wins at the call site.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

def _sibling(name: str):
    """crew/lib/<name>.py beside this file, by path: never a same-named module from another tree on sys.path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"krew_own_{name}", Path(__file__).resolve().parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
topo = _sibling("hermes_topology")

UNIT_PORTS = {"steward": 8651, "crissa": 8652, "ada": 8653, "lila": 8654}
DEFAULT_HOST_PORT = 8642  # hermes's own default for the api_server listener


def _env_value(path: Path, name: str) -> str:
    try:
        text = path.read_text()
    except OSError:
        return ""
    m = re.search(rf"^\s*(?:export\s+)?{re.escape(name)}\s*=\s*(.*)$", text, re.M)
    return m.group(1).strip().strip("'\"") if m else ""


def host_port() -> int:
    """KREW_HERMES_HOST_PORT, else the default profile's API_SERVER_PORT, else hermes's default."""
    for raw in (os.environ.get("KREW_HERMES_HOST_PORT", ""),
                _env_value(topo.HERMES_ROOT / ".env", "API_SERVER_PORT")):
        try:
            if raw and int(raw) > 0:
                return int(raw)
        except ValueError:
            continue
    return DEFAULT_HOST_PORT


def base(profile: str) -> str:
    """`http://127.0.0.1:<port>` plus the profile prefix when the host serves it."""
    if topo.is_host():
        prefix = "" if profile in ("", "default") else f"/p/{profile}"
        return f"http://127.0.0.1:{host_port()}{prefix}"
    port = UNIT_PORTS.get(profile)
    if port is None:
        raise KeyError(f"profile {profile!r} has no gateway of its own under the units topology")
    return f"http://127.0.0.1:{port}"


def url(profile: str, path: str = "/v1/chat/completions") -> str:
    return base(profile) + (path if path.startswith("/") else "/" + path)


def health_url(profile: str) -> str:
    """The process health probe. Under `host` it is the ONE listener's /health — it says the host
    is alive, not that it serves this profile; pair it with `hermes_topology.profile_served`."""
    if topo.is_host():
        return f"http://127.0.0.1:{host_port()}/health"
    return base(profile) + "/health"


def port(profile: str) -> int:
    return host_port() if topo.is_host() else UNIT_PORTS[profile]


def key(profile: str) -> str:
    """The profile's own API_SERVER_KEY, or "". Never logged by this module."""
    return _env_value(topo.profile_home(profile) / ".env", "API_SERVER_KEY")
