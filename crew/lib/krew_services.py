"""One services list for the whole machine: $KREW_HOME/services.json (schema krew-services/v1).

The janitor, the keeper, the Daily's health units, Steward's log collect, the restart window, the boot check and
the Control Panel tiles all read this one file, so a service cannot be on one list and missing from another
(the owner 2026-10-08). `krew-install init` pre-fills it from discovery (systemd + listening ports) and asks the
owner to name and approve every row; nothing is watched, restarted or tiled from a row the owner did not approve.

One row per service:

    {"name": "ops",                     # short lane name (the keeper's lane, the janitor's probe, the panel's tile)
     "unit": "ops.service",             # the systemd unit ("" = no unit of ours: observed through its health only)
     "scope": "user",                   # user | system
     "port": 8000,                      # what it listens on (0 = none)
     "health": "/v1/models",            # a path on 127.0.0.1:<port>; a full URL; "player:<path>" = through the Wall
                                        #   player (ZOEYOS_PLAYER); "" = the unit's state alone
     "role": "watch",                   # watch         the keeper heals it, the janitor watches it
                                        # report-only   watched and carded, never restarted by a patrol (GPU tenants)
                                        # observe       not ours to touch: watched through its health, never restarted
                                        # never-restart ours, but a restart is the owner's call (the display)
                                        # retired       stopped on purpose: never restarted, carded or probed
     "gpu": true,                       # holds VRAM: parked by a GPU mode, never healed while parked
     "kind": "engine",                  # engine | gateway | dashboard | voice | announcer | ear | glass | presence |
                                        #   camera | tool | loop | other   (what the patrols need to know)
     "tier": 3,                         # restart order for the restart window (1 back office .. 5 the front door)
     "label": "Ops - the front door's model",   # the panel tile's words
     "section": "",                     # panel section ("" = the add-on's own)
     "href": "",                        # a page for the panel's Open key
     "zoeyos": false,                   # one of the Wall's: parked "Crissa not installed" while its player is down
     "wake_at_boot": false,             # the keeper starts it once after a boot, when the crew engine serves
     "parked_in": ["beast"],            # GPU modes in which this unit is down on purpose
     "no_heal_in": [],                  # GPU modes in which the keeper leaves it down (another unit owns the GPU)
     "group": "front_door",             # restart window: crew | front_door | api | hermes | ""
     "profile": "",                     # a gateway's Hermes profile
     "approved": true}                  # the owner said yes (init); an unapproved row is listed, never acted on

No file = no services: every reader says so ("no services list: run krew-install init") and watches nothing.
Standard library only; a janitor or window script loads this module by path.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

SCHEMA = "krew-services/v1"
ROLES = ("watch", "report-only", "observe", "never-restart", "retired")
KINDS = ("engine", "gateway", "dashboard", "voice", "announcer", "ear", "glass", "presence", "camera", "tool",
         "loop", "other")
GROUPS = ("", "crew", "front_door", "api", "hermes")
NO_LIST = "no services list: run krew-install init (or copy config/services.example.json to $KREW_HOME/services.json)"
_UNIT = re.compile(r"^[A-Za-z0-9@._:-]{1,120}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def krew_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


def path() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_SERVICES") or str(krew_home() / "services.json")))


def local_url(port: int, p: str = "") -> str:
    """A local service URL: 127.0.0.1 and the row's port, each movable (KREW_LOCAL_HOST, KREW_PORT_<port>)."""
    host = os.environ.get("KREW_LOCAL_HOST") or "127.0.0.1"
    return f"http://{host}:{os.environ.get(f'KREW_PORT_{port}') or port}{p}"


def normalize(r: dict) -> dict | None:
    """One row with every key present and typed, or None when it has no name."""
    if not isinstance(r, dict):
        return None
    name = str(r.get("name") or "").strip().lower()
    if not _NAME.match(name):
        return None
    unit = str(r.get("unit") or "").strip()
    if unit and "." not in unit:
        unit += ".service"
    role = str(r.get("role") or "watch").strip().lower()
    if role not in ROLES:
        role = "observe"
    kind = str(r.get("kind") or "other").strip().lower()
    if kind not in KINDS:
        kind = "other"
    group = str(r.get("group") or "").strip().lower()
    if group not in GROUPS:
        group = ""
    try:
        port = int(r.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    try:
        tier = int(r.get("tier") or 0)
    except (TypeError, ValueError):
        tier = 0
    return {
        "name": name, "unit": unit if _UNIT.match(unit or "x.service") else "",
        "scope": "system" if str(r.get("scope") or "user") == "system" else "user",
        "port": port if 0 < port < 65536 else 0,
        "health": str(r.get("health") or "").strip(),
        "role": role, "gpu": bool(r.get("gpu")), "kind": kind, "tier": tier,
        "label": str(r.get("label") or name)[:120], "section": str(r.get("section") or "")[:60],
        "href": str(r.get("href") or "").strip(), "zoeyos": bool(r.get("zoeyos")),
        "wake_at_boot": bool(r.get("wake_at_boot")),
        "parked_in": [str(m) for m in (r.get("parked_in") or []) if str(m)],
        "no_heal_in": [str(m) for m in (r.get("no_heal_in") or []) if str(m)],
        "group": group, "profile": str(r.get("profile") or "").strip(),
        "approved": bool(r.get("approved", True)),
    }


def load(p: Path | str | None = None, *, approved_only: bool = True) -> list[dict]:
    """The rows ([] when the file is absent). A file that does not parse raises: a broken list is never silent."""
    f = Path(p) if p else path()
    if not f.is_file():
        return []
    d = json.loads(f.read_text(encoding="utf-8"))
    raw = d.get("services") if isinstance(d, dict) else d
    out = []
    for r in raw or []:
        n = normalize(r)
        if n and (n["approved"] or not approved_only):
            out.append(n)
    return out


def present(p: Path | str | None = None) -> bool:
    return (Path(p) if p else path()).is_file()


def write(rows: list[dict], p: Path | str | None = None, *, by: str = "krew") -> Path:
    f = Path(p) if p else path()
    f.parent.mkdir(parents=True, exist_ok=True)
    body = {"schema": SCHEMA, "by": by,
            "_doc": "One row per service of this machine; every patrol reads this list. See crew/lib/krew_services.py.",
            "services": [normalize(r) for r in rows if normalize(r)]}
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, f)
    return f


def by_name(rows: list[dict]) -> dict:
    return {r["name"]: r for r in rows}


def by_unit(rows: list[dict]) -> dict:
    return {r["unit"]: r for r in rows if r["unit"]}


def health_url(r: dict, player: str | None = None) -> str:
    """The row's health URL, or '' (watch the unit's state alone)."""
    h = r.get("health") or ""
    if not h:
        return ""
    if h.startswith("player:"):
        base = (player if player is not None else os.environ.get("ZOEYOS_PLAYER") or "http://127.0.0.1:8100").rstrip("/")
        return base + h[len("player:"):]
    if h.startswith("http://") or h.startswith("https://"):
        return h
    if h.startswith("/") and r.get("port"):
        return local_url(int(r["port"]), h)
    return ""


# ---- the readers ---------------------------------------------------------------------------------------------
def keeper_lanes(rows: list[dict], player: str | None = None) -> dict:
    """name -> (health_url or None, scope or None, unit or None). scope None = observe only (not the keeper's)."""
    out = {}
    for r in rows:
        if r["role"] == "retired":
            continue
        url = health_url(r, player) or None
        if r["role"] in ("observe", "report-only", "never-restart") or not r["unit"]:
            # watched by its health; the keeper never starts or restarts it. A report-only GPU tenant with no
            # health is still watched by state below (scope kept) but the janitor cards it, the keeper does not.
            if r["role"] == "report-only" and r["unit"] and not url:
                out[r["name"]] = (None, r["scope"], r["unit"])
            else:
                out[r["name"]] = (url, None, None)
            continue
        out[r["name"]] = (url, r["scope"], r["unit"])
    return out


def keeper_sets(rows: list[dict]) -> dict:
    """The keeper's unit sets, from the rows: gpu lanes, parked/no-heal by mode, wake-at-boot, by kind."""
    gpu = {r["unit"] for r in rows if r["gpu"] and r["unit"]}
    parked: dict[str, set] = {}
    no_heal: dict[str, set] = {}
    for r in rows:
        for m in r["parked_in"]:
            parked.setdefault(m, set()).add(r["unit"])
        for m in r["no_heal_in"]:
            no_heal.setdefault(m, set()).add(r["unit"])
    kinds: dict[str, list] = {}
    for r in rows:
        kinds.setdefault(r["kind"], []).append(r)
    return {"gpu": gpu, "parked_in": parked, "no_heal_in": no_heal, "kinds": kinds,
            "wake_at_boot": tuple(r["unit"] for r in rows if r["wake_at_boot"] and r["unit"]),
            "zoeyos_lanes": frozenset(r["name"] for r in rows if r["zoeyos"]),
            "heal": {r["unit"] for r in rows if r["role"] == "watch" and r["unit"]}}


def janitor_lists(rows: list[dict]) -> dict:
    """The janitor's `units:` and `probes:` sections, from the rows (merged over janitor.yaml's own)."""
    watch, report_only, observe, retired, zoeyos_units, zoeyos_probes = [], [], [], [], [], []
    ports, units = {}, {}
    for r in rows:
        u = r["unit"]
        if r["role"] == "retired":
            if u:
                retired.append(u)
            continue
        if u:
            watch.append(f"{r['scope']}:{u}" if r["scope"] == "system" else u)
            if r["role"] == "report-only" or r["gpu"]:
                report_only.append(u)      # a GPU tenant's restart is the keeper's or the window's, never the janitor's
            if r["role"] in ("observe", "never-restart"):
                observe.append(u)
            if r["zoeyos"]:
                zoeyos_units.append(u)
        h = r["health"]
        if h.startswith("/") and r["port"]:
            ports[r["name"]] = [r["port"], h]
            if u:
                units[r["name"]] = u
            if r["zoeyos"]:
                zoeyos_probes.append(r["name"])
        elif r["port"] and not h:
            ports[r["name"]] = [r["port"], None]
            if u:
                units[r["name"]] = u
    return {"watch": watch, "report_only": report_only, "observe_only": observe, "retired": retired,
            "zoeyos_units": zoeyos_units, "zoeyos_probes": zoeyos_probes, "probes_ports": ports,
            "probes_units": units,
            "gpu_units": [r["unit"] for r in rows if r["gpu"] and r["unit"]]}


def site_units(rows: list[dict]) -> dict:
    """What site-units.json carried: the Daily's health units (name -> unit) and Steward's log units."""
    live = [r for r in rows if r["role"] != "retired" and r["unit"]]
    return {"health_units": {r["name"].replace("-", "_"): r["unit"] for r in live},
            "steward_log_units": [r["unit"] for r in live]}


def restart_window(rows: list[dict]) -> dict:
    """The restart window's unit lists, from the rows: tiers, the four groups, the post-check ports."""
    out = {"tiers": {}, "crew_units": [], "front_door_units": [], "api_units": [], "hermes_units": [],
           "unit_ports": {}, "wall_units": []}
    for r in rows:
        u = r["unit"]
        if not u or r["role"] == "retired":
            continue
        if r["tier"]:
            out["tiers"][u] = r["tier"]
        if r["group"]:
            out[r["group"] + "_units"].append(u)
        if r["port"]:
            out["unit_ports"][u] = r["port"]
        if r["kind"] == "glass" or r["role"] == "never-restart":
            out["wall_units"].append(u)
    return out


def bootcheck_checks(rows: list[dict], *, tries: int = 24, nap: int = 10) -> list[dict]:
    """fleet-bootcheck's `checks`: one per row with a unit or a local health URL (observe rows by health only)."""
    out = []
    for r in rows:
        if r["role"] == "retired":
            continue
        url = health_url(r) if not r["health"].startswith("player:") else ""
        if r["unit"] and r["scope"] == "user":
            c = {"name": r["name"], "kind": "user_unit", "unit": r["unit"], "tries": tries, "nap": nap}
            if url:
                c["url"] = url
            out.append(c)
        elif r["unit"]:
            out.append({"name": r["name"], "kind": "unit", "unit": r["unit"], "tries": tries, "nap": nap})
        elif url:
            out.append({"name": r["name"], "kind": "http", "url": url, "tries": tries, "nap": nap})
        elif r["port"]:
            out.append({"name": r["name"], "kind": "port", "port": r["port"], "tries": tries, "nap": nap})
    return out


def panel_tiles(rows: list[dict]) -> list[dict]:
    """A Control Panel tile per row with a unit (zoeyos-panel-addon/v1 shape): label, unit, port, scope, href."""
    out = []
    for r in rows:
        if not r["unit"] or r["role"] == "retired" or r["zoeyos"]:
            continue
        t = {"unit": r["unit"], "label": r["label"]}
        if r["port"]:
            t["port"] = r["port"]
        if r["scope"] == "system":
            t["scope"] = "system"
        if r["href"]:
            t["href"] = r["href"]
        if r["section"]:
            t["section"] = r["section"]
        out.append(t)
    return out


# ---- discovery (init pre-fills the list; the owner approves) -------------------------------------------------
_GPU_WORDS = re.compile(r"vllm|sglang|llama|comfy|torch|cuda|whisper|tts|asr|diffusion|ollama", re.I)
_KIND_WORDS = (
    ("gateway", re.compile(r"gateway", re.I)), ("dashboard", re.compile(r"dashboard", re.I)),
    ("voice", re.compile(r"voice|tts|speak", re.I)), ("ear", re.compile(r"asr|stt|ear|listen", re.I)),
    ("presence", re.compile(r"presence|identity|face", re.I)), ("camera", re.compile(r"camera", re.I)),
    ("engine", re.compile(r"vllm|sglang|llama|ollama|serve|model", re.I)),
    ("glass", re.compile(r"display|wall|glass|kiosk|browser", re.I)),
    ("loop", re.compile(r"krew-|kanban|sweep|janitor|window|loop", re.I)),
)


def _run(argv: list, timeout: float = 30.0) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def ephemeral_range(path: str = "/proc/sys/net/ipv4/ip_local_port_range") -> tuple[int, int]:
    """The kernel's ephemeral port range (default 32768-60999). A listener there with no unit of ours is not a
    service: the owner's init on the reference box approved 90-odd such rows under --yes (2026-10-08)."""
    try:
        a, b = open(path).read().split()
        return int(a), int(b)
    except (OSError, ValueError):
        return 32768, 60999


def listening_ports(text: str | None = None) -> dict:
    """port -> {proc, pid}, from `ss -tlnp` (or the given text)."""
    txt = _run(["ss", "-tlnpH"]) if text is None else text
    out = {}
    for ln in txt.splitlines():
        m = re.search(r":(\d+)\s", ln)
        if not m:
            continue
        port = int(m.group(1))
        pm = re.search(r'users:\(\("([^"]+)",pid=(\d+)', ln)
        out[port] = {"proc": pm.group(1) if pm else "", "pid": int(pm.group(2)) if pm else 0}
    return out


def _unit_rows(scope: str, run) -> list[dict]:
    txt = run(["systemctl"] + (["--user"] if scope == "user" else []) + ["list-units", "--type=service", "--all",
                                                                       "--no-legend", "--plain", "--no-pager"])
    out = []
    for ln in txt.splitlines():
        parts = ln.split()
        if not parts or not parts[0].endswith(".service") or "@" in parts[0]:
            continue
        out.append({"unit": parts[0], "scope": scope, "active": parts[2] if len(parts) > 2 else ""})
    return out


def discover(run=None, ports: dict | None = None, *, markers: tuple = ("~/.krew", "~/.hermes")) -> list[dict]:
    """Candidate rows from systemd (user units that are ours by name or ExecStart, running system units run as
    this user) and the listening ports. Every row comes back unapproved, role `observe`, for the owner to name."""
    run = run or _run
    ports = listening_ports() if ports is None else ports
    marks = tuple(os.path.expanduser(m) for m in markers)
    me = os.environ.get("USER") or Path.home().name
    rows = []
    for scope in ("user", "system"):
        for u in _unit_rows(scope, run):
            props = run(["systemctl"] + (["--user"] if scope == "user" else [])
                        + ["show", "-pExecStart", "-pUser", "-pDescription", "-pFragmentPath", "-pMainPID", u["unit"]])
            kv = dict(ln.split("=", 1) for ln in props.splitlines() if "=" in ln)
            ex = kv.get("ExecStart") or ""
            try:
                pid = int(kv.get("MainPID") or 0)
            except ValueError:
                pid = 0
            ours = (scope == "user" and (any(m in ex for m in marks)
                                         or (kv.get("FragmentPath") or "").startswith(str(Path.home())))) \
                or (scope == "system" and (kv.get("User") or "") == me)
            if not ours and not u["unit"].startswith(("krew-", "hermes-", "zoeyos-", "zoey-")):
                continue
            name = re.sub(r"[^a-z0-9-]", "-", u["unit"][:-8].lower())[:40]
            port = 0
            for p, who in sorted(ports.items()):
                w = who if isinstance(who, dict) else {"proc": str(who or ""), "pid": 0}
                if (pid and w.get("pid") == pid) or (w.get("proc") and w["proc"] in u["unit"]):
                    port = p             # the socket's owner is this unit's main process (or named like the unit)
                    break
            kind = "other"
            for k, pat in _KIND_WORDS:
                if pat.search(u["unit"] + " " + ex):
                    kind = k
                    break
            rows.append({"name": name, "unit": u["unit"], "scope": scope, "port": port, "health": "",
                         "role": "observe", "gpu": bool(_GPU_WORDS.search(ex)), "kind": kind, "tier": 0,
                         "label": (kv.get("Description") or name)[:120], "approved": False})
    seen_ports = {r["port"] for r in rows if r["port"]}
    lo, hi = ephemeral_range()
    for p, who in sorted(ports.items()):
        if p in seen_ports or p < 1024 or lo <= p <= hi:
            continue                 # an ephemeral-range listener with no unit is a worker's scratch socket, not a service
        proc = (who.get("proc") if isinstance(who, dict) else str(who or "")) or "a listener"
        rows.append({"name": f"port-{p}", "unit": "", "scope": "user", "port": p, "health": "", "role": "observe",
                     "gpu": False, "kind": "other", "tier": 0, "label": f"{proc} on :{p}", "approved": False})
    return [normalize(r) for r in rows if normalize(r)]
