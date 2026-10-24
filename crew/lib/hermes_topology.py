"""Where a hermes profile's gateway lives, and how the house acts on it.

Two topologies (docs/checkpoints/hermes-multiplex-20260927 on the reference install):

  units   one systemd unit per profile (`hermes-<profile>-gateway.service`) — the default,
          and what a Hermes without the one-host gateway runs.
  host    ONE gateway (`hermes-gateway.service`, launched from the default profile) serves every
          profile; a profile is taken offline by PARKING it (`<profile home>/gateway.parked`),
          never by a unit.

`KREW_GATEWAY_TOPOLOGY=units|host`: the process env, else `~/.hermes/krew-topology.env`, else
`units`. Every answer under `units` is what the call site did before this module existed, so a
caller can move here with no change in behaviour until the cutover writes `host`.

ONE definition of "up" for the whole house: `profile_up()`. `activating` is never up — a
crash-looping unit read as up on 2026-09-26 and suppressed the Zoey-down fallback all night.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_REAL_HOME = Path(os.environ.get("HERMES_REAL_HOME", str(Path.home())))
HERMES_ROOT = Path(os.environ.get("KREW_HERMES_ROOT", str(_REAL_HOME / ".hermes")))
TOPOLOGY_FILE = HERMES_ROOT / "krew-topology.env"
HERMES_BIN = os.environ.get("KREW_HERMES_BIN", str(_REAL_HOME / ".local" / "bin" / "hermes"))

HOST_UNIT = "hermes-gateway.service"
# The profiles that ran a gateway of their own. Worker-only profiles (bash, vera, judge, scout)
# never had a unit; under `host` they are served like everyone else.
UNIT_PROFILES = ("ada", "zoey", "lila", "steward")
TOPOLOGIES = ("units", "host")


def _unit_name(profile: str) -> str:
    return f"hermes-{profile}-gateway.service"


def topology() -> str:
    """Process env, else the file, else units.

    krew: the reference install reads a missing topology file as the shared
    hermes-gateway (its cutover was 09-27); krew reads it as ``units``, because a Hermes
    krew did not set up may not run the one host gateway. ``krew-install init`` writes
    ``KREW_GATEWAY_TOPOLOGY=host`` into ``krew-topology.env``, so an installed crew is
    the host like the reference. A word that is neither is ``units``: a typo must not be
    read as host.
    """
    raw = (os.environ.get("KREW_GATEWAY_TOPOLOGY") or "").strip().lower()
    if not raw:
        try:
            for line in TOPOLOGY_FILE.read_text().splitlines():
                key, _, value = line.strip().partition("=")
                if key.strip() == "KREW_GATEWAY_TOPOLOGY":
                    raw = value.strip().strip("'\"").lower()
        except OSError:
            raw = ""
    return raw if raw in TOPOLOGIES else "units"


def is_host() -> bool:
    return topology() == "host"


def profile_home(profile: str) -> Path:
    return HERMES_ROOT if profile in ("", "default") else HERMES_ROOT / "profiles" / profile


def unit_for(profile: str) -> str:
    """The systemd unit whose process answers for `profile` today."""
    return HOST_UNIT if is_host() else _unit_name(profile)


def profile_of_unit(unit: str) -> str | None:
    """`hermes-zoey-gateway.service` -> `zoey`. The old names stay readable forever: a hold written
    before the cutover must still release after it (an unknown name in the hold file is a
    permanent latch)."""
    name = unit.strip()
    if name.endswith(".service"):
        name = name[: -len(".service")]
    if name.startswith("hermes-") and name.endswith("-gateway") and name != "hermes-gateway":
        return name[len("hermes-"): -len("-gateway")] or None
    return None


def gateway_units() -> list[str]:
    """Every gateway unit that exists under the topology in force."""
    return [HOST_UNIT] if is_host() else [_unit_name(p) for p in UNIT_PROFILES]


def _systemctl(*args: str, timeout: float = 5.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def unit_active(unit: str, *, timeout: float = 1.5) -> bool | None:
    """True only for `active`. None when systemd could not be asked (the caller decides)."""
    r = _systemctl("is-active", unit, timeout=timeout)
    if r is None:
        return None
    return r.stdout.strip() == "active"


def _pid_alive(pid: object) -> bool:
    try:
        n = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return n > 0 and Path(f"/proc/{n}").exists()


def host_record() -> dict:
    """What the host gateway says it serves (`~/.hermes/gateway_state.json`), or {}.

    The file outlives its process — a gateway that died without a clean stop leaves `running`
    behind — so the record counts only while its pid is alive."""
    try:
        doc = json.loads((HERMES_ROOT / "gateway_state.json").read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    if doc.get("gateway_state") != "running" or not _pid_alive(doc.get("pid")):
        return {}
    return doc


def parked(profile: str) -> bool:
    return (profile_home(profile) / "gateway.parked").exists()


def profile_served(profile: str) -> bool:
    """Under `host`: the live host gateway lists the profile and it is not parked."""
    if parked(profile):
        return False
    served = host_record().get("served_profiles") or []
    return profile in served


def profile_up(profile: str, *, timeout: float = 1.5, unknown: bool = False) -> bool:
    """Is this profile's gateway answering for it? THE liveness definition.

    units: its unit is exactly `active`.
    host:  the host unit is `active` AND the host serves the profile (not parked).
    `unknown` is returned when systemd could not be asked at all.
    """
    if is_host():
        state = unit_active(HOST_UNIT, timeout=timeout)
        if state is None:
            return unknown
        return bool(state) and profile_served(profile)
    state = unit_active(_unit_name(profile), timeout=timeout)
    return unknown if state is None else bool(state)


def _hermes(profile: str, *verb: str, timeout: float = 120.0) -> bool:
    try:
        r = subprocess.run([HERMES_BIN, "-p", profile, "gateway", *verb], capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _unit_action(action: str, unit: str, *, block: bool = True, timeout: float = 180.0) -> bool:
    args = [action] + ([] if block else ["--no-block"]) + [unit]
    r = _systemctl(*args, timeout=timeout)
    return r is not None and r.returncode == 0


def restart_profile(profile: str, *, block: bool = True) -> bool:
    """Make `profile` re-read its config and reconnect, disturbing nobody else.

    host: `hermes -p <profile> gateway restart` (the host unserves and serves it; rehearsed
    2026-09-27, Ada kept answering while Zoey restarted)."""
    if is_host():
        return _hermes(profile, "restart")
    return _unit_action("restart", _unit_name(profile), block=block)


def park(profile: str) -> bool:
    """Take the profile's gateway offline. host: the marker + unserve; units: stop the unit.

    Parking does NOT stop kanban dispatch to the profile (upstream: adapters and cron only), so a
    Render sleep still stops the profile's WORKERS by argv — see krew-mode `profile_worker_pids`.
    """
    if is_host():
        return _hermes(profile, "stop")
    return _unit_action("stop", _unit_name(profile))


def unpark(profile: str) -> bool:
    if is_host():
        return _hermes(profile, "start")
    return _unit_action("start", _unit_name(profile))


def restart_host(*, block: bool = False) -> bool:
    """Restart the one host gateway: every profile reconnects and the dispatcher re-reads its caps.
    Under `units` there is no host; the dispatcher lives in Ada's unit, so that is what restarts."""
    unit = HOST_UNIT if is_host() else _unit_name("ada")
    return _unit_action("restart", unit, block=block)


def dispatcher_unit() -> str:
    """The unit whose process runs the kanban dispatcher."""
    return HOST_UNIT if is_host() else _unit_name("ada")


def dispatcher_config() -> Path:
    """The config.yaml whose `kanban:` block the dispatcher reads (caps, intervals, decomposer)."""
    return HERMES_ROOT / "config.yaml" if is_host() else profile_home("ada") / "config.yaml"


def describe() -> dict:
    """One read for a status page or a preflight."""
    out = {"topology": topology(), "units": gateway_units(), "dispatcher_unit": dispatcher_unit(),
           "dispatcher_config": str(dispatcher_config())}
    if is_host():
        rec = host_record()
        out["host_pid"] = rec.get("pid")
        out["served"] = list(rec.get("served_profiles") or [])
    out["up"] = {p: profile_up(p) for p in UNIT_PROFILES}
    out["parked"] = [p for p in UNIT_PROFILES if parked(p)]
    return out


def main(argv: list[str] | None = None) -> int:
    """The shell's door: `hermes_topology.py describe | unit <p> | up <p> | restart <p> | park <p>
    | unpark <p> | restart-host`. `up` exits 0 when up, 1 when not."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    verb = args[0] if args else "describe"
    prof = args[1] if len(args) > 1 else ""
    if verb == "describe":
        print(json.dumps(describe(), indent=1))
        return 0
    if verb == "restart-host":
        return 0 if restart_host(block="--block" in args) else 1
    if not prof:
        print(f"hermes_topology: {verb} needs a profile", file=sys.stderr)
        return 2
    if verb == "unit":
        print(unit_for(prof))
        return 0
    if verb == "up":
        return 0 if profile_up(prof) else 1
    act = {"restart": restart_profile, "park": park, "unpark": unpark}.get(verb)
    if act is None:
        print(f"hermes_topology: unknown verb {verb!r}", file=sys.stderr)
        return 2
    return 0 if act(prof) else 1


if __name__ == "__main__":
    raise SystemExit(main())
