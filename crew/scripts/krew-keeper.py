#!/usr/bin/env python3
"""krew-keeper — the house holds itself up (Fable C7, plan §5 Hold-up).

Every CHECK_SEC: reopen the ear if closed, health-check every lane, restart
dead lanes I operate, and log every action to KEEPER_LOG. The lanes come from
the one services list, $KREW_HOME/services.json (crew/lib/krew_services.py; the
owner 2026-10-08): a row's role says whether the keeper heals it (watch), only
watches it (observe, report-only, never-restart) or leaves it alone (retired);
its gpu flag and parked_in / no_heal_in say when a GPU mode owns it. No list =
no lanes, and the keeper says so every pass.
Nightly: Gate-D config assert (lazy=false on every Hermes config; the tts provider
is KREW_TTS_PROVIDER when set).

Restart policy: a lane is restarted only if its endpoint fails twice in a row
AND its unit isn't mid-startup (Type=simple never enters activating — use
process age vs START_GRACE_SEC); max RESTARTS_PER_DAY per unit, then hands-off
+ loud log (the keeper must never become a crash-loop amplifier).

Spoken announcements go through the announcer only between ANNOUNCE_START and
ANNOUNCE_END local hours — 3 AM failures are logged, not narrated.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402


KREW_MEMORY_ROOT = os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory")  # krew: the house kept this on a data disk


def _local(port: int, path: str = "") -> str:
    """A local service URL: the reference install's port by default, each movable (KREW_PORT_<port>, KREW_LOCAL_HOST)."""
    host = os.environ.get("KREW_LOCAL_HOST") or "127.0.0.1"
    return f"http://{host}:{os.environ.get(f'KREW_PORT_{port}') or port}{path}"



def _for_speech(text: str) -> str:
    """Announcer path uses the same ID/symbol hygiene as the voice server's mouths."""
    cand = Path(__file__).resolve().parents[1] / "lib/speakable.py"
    try:
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("krew_speakable", cand)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.for_speech(text)[:400]
    except Exception:
        pass
    return (text or "")[:400]

CHECK_SEC = int(os.environ.get("KEEPER_CHECK_SEC", "60"))
RESTARTS_PER_DAY = int(os.environ.get("KEEPER_RESTARTS_PER_DAY", "3"))
# Type=simple is "active" the instant it forks. vLLM HTTP is down for minutes.
# activating never fires; ExecMain/pid age is the load clock (2026-08-21).
# 300 was short: 2026-08-24 cold MTP AOT in a fresh VLLM_CACHE_ROOT ran past
# 381s and keeper stomped ops mid-load. 600 covers that compile.
START_GRACE_SEC = int(os.environ.get("KEEPER_START_GRACE_SEC", "600"))
KEEPER_LOG = os.environ.get(
    "KEEPER_LOG", (KREW_MEMORY_ROOT + "/keeper/keeper.jsonl")
)
STATE_PATH = os.environ.get(
    "KEEPER_STATE", (KREW_MEMORY_ROOT + "/keeper/state.json")
)
ANNOUNCE_START = int(os.environ.get("KEEPER_ANNOUNCE_START", "8"))
ANNOUNCE_END = int(os.environ.get("KEEPER_ANNOUNCE_END", "22"))

# The announcer is the Wall's player (/v1/announce): no voice service, unit or pressure probe of its own here.

CRISSA_PLAYER = os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/")
# Playback tempo (pitch-preserving, User 08-07: "speed up announcer 1.3 or so")
ANNOUNCE_SPEED = float(os.environ.get("ANNOUNCE_SPEED", "1.2"))  # match krew_announce (User 1.2)

# her ear, through the Wall's player (Crissa 2026-10-06): GET /v1/ear?part=stt, POST /v1/ear/open
EAR_HEALTH = CRISSA_PLAYER + "/v1/ear?part=stt"
EAR_OPEN = CRISSA_PLAYER + "/v1/ear/open"

EAR_OPEN_BODY = {
    "mode": "vad",
    "idle_timeout_sec": 1800,
    "max_open_sec": 43200,
    "hangover_sec": 1.8,
    "max_utter_sec": 20,
}

def _own_lib(name: str):
    """crew/lib/<name>.py by its path: never a same-named module from another tree on sys.path."""
    path = Path(__file__).resolve().parents[1] / "lib" / f"{name}.py"
    if not path.is_file():
        raise ImportError(f"no {path}")
    spec = importlib.util.spec_from_file_location(f"krew_own_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The lanes: $KREW_HOME/services.json (KREW_SERVICES moves it). lane: (health_url, scope, unit); scope None =
# observe only (not my service). The sets beside it (GPU lanes, parked/no-heal by mode, wake at boot, the
# Wall's lanes) come from the same rows. With no list the keeper watches nothing and says so.
_services = _own_lib("krew_services")
SERVICES_PATH = _services.path()
SERVICES = _services.load(SERVICES_PATH) if SERVICES_PATH.is_file() else []
LANES = _services.keeper_lanes(SERVICES, CRISSA_PLAYER)
_SETS = _services.keeper_sets(SERVICES)
ROWS = _services.by_name(SERVICES)
NO_SERVICES = "" if SERVICES_PATH.is_file() else _services.NO_LIST


def _kind_rows(kind: str) -> list:
    return list(_SETS["kinds"].get(kind) or [])


def _row_url(row: dict | None, path: str = "") -> str:
    """A row's local URL with `path` in place of its health path ('' when the row has no port)."""
    if not row or not row.get("port"):
        return ""
    return _local(int(row["port"]), path)


def log(event: str, **kw) -> None:
    row = {"ts": time.time(), "ts_local": dt.datetime.now().isoformat(timespec="seconds"),
           "event": event, **kw}
    os.makedirs(os.path.dirname(KEEPER_LOG), exist_ok=True)
    with open(KEEPER_LOG, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"[keeper] {row['ts_local']} {event} {kw}", flush=True)


TTS_GATE = Path(
    os.environ.get("KREW_TTS_GATE", str(KH.hermes_home() / "krew-tts-gate.json"))
)
VOICE_WARM_URL = os.environ.get("KREW_VOICE_WARM_URL") or _row_url((_kind_rows("voice") or [None])[0], "/warm")


def mouth_busy() -> bool:
    """True when Cosy is on the air — do not steal SYNTH_LOCK for a warm ping."""
    try:
        g = json.loads(TTS_GATE.read_text(encoding="utf-8"))
        if g.get("speaking"):
            return True
        until = float(g.get("deaf_until") or 0)
        if until and time.time() < until:
            return True
    except Exception:
        return False
    return False


def keep_voice_warm() -> None:
    """Silent /warm so the voice server does not go cold between turns (no voice row = nothing to warm)."""
    if not VOICE_WARM_URL or mouth_busy():
        return
    try:
        req = urllib.request.Request(
            VOICE_WARM_URL,
            data=b'{"voice":"crissa"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            body = json.loads(r.read().decode() or "{}")
        if not body.get("ok"):
            log("voice_warm_skip", error=str(body.get("error") or "not ok")[:120])
    except Exception as e:
        log("voice_warm_skip", error=f"{type(e).__name__}: {e}"[:120])


def http_ok(url: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _player_why(url: str) -> str:
    """Why a lane read through the Wall's player failed, in words (never silent)."""
    try:
        with urllib.request.urlopen(CRISSA_PLAYER + "/v1/state", timeout=3) as r:
            r.read()
    except urllib.error.HTTPError:
        pass
    except Exception:
        return f"Crissa not installed (no player at {CRISSA_PLAYER})"
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return f"answered {r.status}"
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            body = {}
        why = body.get("why") or body.get("error") if isinstance(body, dict) else ""
        return f"the player said {e.code}: {why or e.reason}"
    except Exception as e:
        return f"{e.__class__.__name__}: {e}"


def http_json(url: str, timeout: float = 4.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _crew_is_sleeping() -> bool:
    """L1 RAM-sleep: process is up, /v1/models is down. Do not systemd-restart."""
    url = _row_url(ROWS.get("crew"), "/is_sleeping")
    if not url:
        return False
    body = http_json(url, timeout=2.0)
    if not isinstance(body, dict):
        return False
    return bool(body.get("is_sleeping"))


def post_json(url: str, body: dict, timeout: float = 6.0):
    try:
        req = urllib.request.Request(
            url, json.dumps(body).encode(), {"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def unit_state(scope: str, unit: str) -> str:
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + ["is-active", unit]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "unknown"


def unit_main_pid(scope: str, unit: str) -> int:
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + [
        "show", "-p", "MainPID", "--value", unit
    ]
    try:
        return int(
            subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
            or "0"
        )
    except Exception:
        return 0


def proc_age_s(pid: int) -> float | None:
    """Seconds since pid started, from /proc. None if gone."""
    if pid <= 0:
        return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        after = stat.rsplit(")", 1)[1].split()
        start_ticks = int(after[19])  # field 22
        clk = os.sysconf("SC_CLK_TCK") or 100
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        return max(0.0, uptime - start_ticks / float(clk))
    except Exception:
        return None


def unit_age_s(scope: str, unit: str) -> float | None:
    return proc_age_s(unit_main_pid(scope, unit))


def parse_systemd_span_s(raw: str | None) -> float | None:
    """'15min' / '1min 30s' / '2h' / '500ms' -> seconds. None for infinity/0/blank."""
    if not raw:
        return None
    raw = raw.strip()
    if raw in ("infinity", "0"):
        return None
    units = {"w": 604800.0, "d": 86400.0, "h": 3600.0, "min": 60.0, "m": 60.0,
             "s": 1.0, "ms": 0.001, "us": 0.000001, "\u00b5s": 0.000001}
    total = 0.0
    found = False
    for num, unit in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*([a-z\u00b5]+)", raw):
        if unit not in units:
            return None
        total += float(num) * units[unit]
        found = True
    return total if found and total > 0 else None


_START_TIMEOUT_CACHE: dict[tuple[str, str], float | None] = {}


def unit_start_timeout_s(scope: str, unit: str) -> float | None:
    """The unit's own TimeoutStartSec, cached per process (units rarely change)."""
    key = (scope, unit)
    if key not in _START_TIMEOUT_CACHE:
        cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + [
            "show", "-p", "TimeoutStartUSec", "--value", unit
        ]
        try:
            raw = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
        except Exception:
            raw = ""
        _START_TIMEOUT_CACHE[key] = parse_systemd_span_s(raw)
    return _START_TIMEOUT_CACHE[key]


def start_grace_s(scope: str, unit: str) -> float:
    """Load window = the unit's own TimeoutStartSec, never below START_GRACE_SEC.

    2026-09-09: crew (TimeoutStartSec=900) sat 480 s waiting for parked ops
    then loaded ~3 min; a flat 600 s grace read that as dead and the keeper
    restarted it mid-load three times. The unit already states how long a
    start may take — use it. Short-timeout units (the voice server, 90 s) keep the floor.
    """
    own = unit_start_timeout_s(scope, unit)
    return max(float(START_GRACE_SEC), own or 0.0)


def in_start_grace(scope: str, unit: str, *, age_s: float | None = None) -> bool:
    """True while a Type=simple vLLM is still loading (HTTP down, unit active)."""
    age = unit_age_s(scope, unit) if age_s is None else age_s
    if age is None:
        return False
    return age < start_grace_s(scope, unit)


def unit_action(scope: str, unit: str, action: str) -> bool:
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + [action, unit]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


# GPU tenants occupancy may park. Crash recovery is systemd (vllm_alive
# maps EngineDeadError → exit 1) plus this keeper when the lane should
# be up. Parked = observe only. The voice server is never occupancy-parked (heal it).
GPU_LANES = set(_SETS["gpu"])
TRAIN_HOLD_PATHS = (
    os.environ.get("KREW_TRAIN_HOLD", "/tmp/krew-train-hold"),
    (KREW_MEMORY_ROOT + "/train-hold"),
)
BEAST_HOLD = os.environ.get("KREW_BEAST_HOLD", "/run/hermes/beast-hold")
GPU_MODE_FILE = os.environ.get(
    "KREW_GPU_MODE_FILE", str(KH.hermes_home() / "krew-gpu-mode")
)
PROD_LOCK_FILE = os.environ.get(
    "KREW_PROD_LOCK", str(KH.hermes_home() / "krew-prod-lock")
)
BEAST_HELD_UNITS = set(_SETS["parked_in"].get("beast") or ())


BEAST_NO_HEAL = set(_SETS["no_heal_in"].get("beast") or ())


RENDER_NO_HEAL = set(_SETS["no_heal_in"].get("render") or ())


def _train_holds_gpu(unit: str) -> bool:
    """True while a training run owns the GPU AND this unit is held.

    User 2026-08-10 stopped both brains for a training run; the keeper restarted
    ops 59s later and crew 2min later, taking the card straight back.

    SELECTIVE on purpose: an empty hold file holds every GPU lane, but listing
    unit names (one per line) holds only those — so you can free crew for a run
    while the keeper still heals ops if it dies. User chose exactly that
    split on 08-10. Remove the file to resume normal healing.
    """
    for path in TRAIN_HOLD_PATHS:
        if not os.path.isfile(path):
            continue
        try:
            want = [ln.strip() for ln in open(path).read().splitlines() if ln.strip()
                    and not ln.strip().startswith("#")]
        except Exception:
            want = []
        if not want or unit in want:
            return True
    return False


def _retry_crissa_glass() -> None:
    """Lila leftover after Standard recovered Crissa's gateway: /v1/home again."""
    try:
        with urllib.request.urlopen(CRISSA_PLAYER + "/v1/state", timeout=2) as r:
            now = str(json.loads(r.read().decode()).get("now") or "")
    except Exception:
        return
    if not now.startswith("lila-"):
        return
    req = urllib.request.Request(
        CRISSA_PLAYER + "/v1/home",
        data=json.dumps({"by": "render"}).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode() or "{}")
        log("crissa_glass_retry", taken=bool(body.get("taken")), was=now)
    except Exception as e:
        log("crissa_glass_retry_fail", error=str(e)[:160])


def _gpu_mode() -> str:
    try:
        first = open(GPU_MODE_FILE).read().strip().splitlines()[0].strip().lower()
    except Exception:
        return "normal"
    if first in ("render", "normal", "beast", "heavy-render"):
        return first
    return "normal"


def _beast_holds(unit: str) -> bool:
    """Beast 122B owns the card. Do not start ops 27B or Cosy.

    Occupancy SoT is krew-gpu-mode. /run/hermes/beast-hold is the systemd pin
    so Wants=ops cannot start 27B. Either one is enough.
    """
    if unit not in BEAST_HELD_UNITS:
        return False
    if os.path.isfile(BEAST_HOLD):
        return True
    return _gpu_mode() == "beast"


def occupancy_parks_gpu_lane(unit: str, mode: str | None = None) -> bool:
    """True when this GPU tenant is down on purpose — do not revive.

    Beast: ops parked (crew is the leftover-fit Unsloth). Canonical Render:
    ops parked (crew stays — the kanban team). Emergency park-crew: crew is
    on the hold. Dual-park hold names both. The voice server is never occupancy-parked
    by the flag (Voice OFF may hold it). Standard: nothing in GPU_LANES is
    parked unless the hold lists it.
    """
    if unit not in GPU_LANES:
        return False
    if mode is None:
        mode = _gpu_mode()
    if _train_holds_gpu(unit):
        return True
    if unit in (_SETS["parked_in"].get(mode) or ()):
        if mode == "render" and any(u != unit and _train_holds_gpu(u) for u in GPU_LANES):
            # Emergency park-crew: the hold names another GPU lane (and not this one), so that lane gave up the card
            # instead and this one is the live brain.
            return False
        return True       # the row says this mode parks it (the hold file is the belt when a line goes missing)
    crew = ROWS.get("crew") or {}
    if unit == crew.get("unit") and _crew_is_sleeping():
        return True
    return False


def prod_locked() -> bool:
    """Guest/prod hold. Do not race systemd on GPU lanes."""
    try:
        first = open(PROD_LOCK_FILE).read().strip().splitlines()[0].strip().lower()
    except Exception:
        return False
    return first in ("on", "1", "true", "lock", "prod")


def _lane_held(unit: str) -> bool:
    """True → keeper must not start/restart this unit."""
    if prod_locked() and unit in GPU_LANES:
        return True
    return _beast_holds(unit) or _train_holds_gpu(unit)


def _tool_hold_active() -> bool:
    p = os.environ.get("KREW_TOOL_HOLD", "/tmp/krew-tool-hold.json")
    if not os.path.isfile(p):
        return False
    try:
        d = json.loads(open(p).read())
        until = float(d.get("until") or 0)
        return bool(until and time.time() < until)
    except Exception:
        return False


def announce(text: str, *, category: str = "stuck") -> None:
    """PA only: stuck | protocol (completions go through team-crissa kanban hook).

    Through the Wall's player (/v1/announce, 2026-10-06): it queues the line behind her answers and holds it while
    a conversation is open or someone is talking. No player answering: Crissa is not installed (logged).
    """
    spoken = _for_speech(text)
    if not spoken:
        log("announce_skipped_empty_after_sanitize", text=text)
        return
    body = {"text": spoken, "kind": category, "by": "krew-keeper", "dedup_key": "keeper:" + spoken[:120]}
    req = urllib.request.Request(CRISSA_PLAYER + "/v1/announce", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
        log("announced", text=spoken[:200], category=category, ok=bool(out.get("ok")),
            queued=bool(out.get("queued")), reason=out.get("reason"), engine="crissa-player")
    except urllib.error.HTTPError as e:
        log("announce_failed", error=f"the player said {e.code}", category=category)
    except (OSError, ValueError) as e:
        log("announce_failed", error=f"Crissa not installed (no player at {CRISSA_PLAYER}): {e}"[:200],
            category=category)

def load_state() -> dict:
    try:
        return json.load(open(STATE_PATH))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    json.dump(st, open(STATE_PATH, "w"))


def restarts_today(st: dict, unit: str) -> int:
    day = dt.date.today().isoformat()
    return st.get("restarts", {}).get(day, {}).get(unit, 0)


def note_restart(st: dict, unit: str) -> None:
    day = dt.date.today().isoformat()
    st.setdefault("restarts", {}).setdefault(day, {})
    st["restarts"][day][unit] = st["restarts"][day].get(unit, 0) + 1
    # drop old days
    st["restarts"] = {d: v for d, v in st["restarts"].items() if d == day}


# krew-enroll holds the Link mic exclusively (parec is one-at-a-time).
# Do not fight it by reopening continuous ear mid-session.
MIC_HOLD_PATHS = (
    os.environ.get("KREW_ENROLL_MIC_HOLD", "/tmp/krew-enroll.mic-hold"),
    "/tmp/krew-enroll.mic-hold",
)


def _enroll_holds_mic() -> bool:
    for p in MIC_HOLD_PATHS:
        if not p or not os.path.isfile(p):
            continue
        try:
            # Stale lock (>20 min) is ignored so a crashed enroll can't mute the house
            age = time.time() - os.path.getmtime(p)
            if age < 20 * 60:
                return True
        except OSError:
            continue
    return False


def keep_ear_open() -> None:
    if _enroll_holds_mic() or _tool_hold_active():
        return  # enroll / tool hold owns mic — leave ear closed
    h = http_json(EAR_HEALTH)
    if not h:
        return  # ear down or no player — the ear-rail lane logs why
    ear = (h.get("stt") or {}).get("ear") or {}
    if ear.get("open") is False:
        r = post_json(EAR_OPEN, EAR_OPEN_BODY)
        log("ear_reopened", ok=bool(r and r.get("open")),
            closed_reason=ear.get("closed_reason"))


# TTS hygiene — disk scratch + crash-loop signal (User 2026-08-06: mouths fragile
# under juggernaut load; janitor only probed HTTP, never cleaned /tmp or NRestarts).
_TTS_TMP_GLOBS = (
    "/tmp/qwen-*.wav",
    "/tmp/keeper-*.wav",
    "/tmp/tts_*.pcm",
    "/tmp/tts_*.wav",
    "/tmp/enroll_cap_*.wav",
    "/tmp/enroll_say_*.wav",
)
_TTS_TMP_MAX_AGE_S = int(os.environ.get("KEEPER_TTS_TMP_MAX_AGE_S", str(2 * 3600)))
_TTS_TMP_LAST = 0.0


def tts_scratch_janitor() -> None:
    """Delete stale TTS/enroll scratch in /tmp (not HF weights)."""
    global _TTS_TMP_LAST
    now = time.time()
    if now - _TTS_TMP_LAST < 300:
        return
    _TTS_TMP_LAST = now
    import glob

    removed = 0
    bytes_f = 0
    for pat in _TTS_TMP_GLOBS:
        for path in glob.glob(pat):
            try:
                st = os.stat(path)
                if now - st.st_mtime < _TTS_TMP_MAX_AGE_S:
                    continue
                bytes_f += st.st_size
                os.unlink(path)
                removed += 1
            except OSError:
                pass
    if removed:
        log("tts_scratch_cleaned", removed=removed, bytes=bytes_f)


def _sd_int(raw: str | None) -> int:
    """systemd numeric property → int, tolerating its non-numeric sentinels.

    `systemctl show -p MemoryHigh` prints the literal string `[not set]` when
    no limit is configured, and `infinity` when it is explicitly unbounded.
    `int(x or 0)` does not save you: "[not set]" is truthy, so it raised and
    took the rest of the check down with it — 2792 keeper_errors in 24h, and
    the announcer RAM watchdog below never ran once, because the throw lands
    between the thrash check and the memory check.
    """
    s = (raw or "").strip()
    if not s or s in ("[not set]", "infinity"):
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def tts_unit_pressure() -> None:
    """Log/act when a TTS unit is thrashing (NRestarts) or RAM-hot."""
    for name, unit in ((r["name"], r["unit"]) for r in _kind_rows("voice") + _kind_rows("announcer") if r["unit"]):
        try:
            out = subprocess.run(
                ["systemctl", "show", unit, "-p", "NRestarts", "-p", "ActiveState",
                 "-p", "MemoryCurrent", "-p", "MemoryHigh", "-p", "MemoryMax"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            kv = {}
            for line in out.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k] = v
            nrest = _sd_int(kv.get("NRestarts"))
            # systemd NRestarts resets on full stop; high counter = crash loop today
            if nrest >= 20:
                log("tts_unit_thrash", lane=name, unit=unit, nrestarts=nrest,
                    state=kv.get("ActiveState"),
                    note="boot/import crash-loop or OOM thrash — not a blip")
            mem = _sd_int(kv.get("MemoryCurrent"))
            mhigh = _sd_int(kv.get("MemoryHigh")) or _sd_int(kv.get("MemoryMax"))
            if name == "announcer" and mhigh and mem > 0.92 * mhigh:
                log("tts_announcer_ram_hot", mem=mem, high=mhigh,
                    note="qwen near MemoryHigh — idle-unload cycle or leak")
        except Exception as e:
            log("keeper_error", where="tts_pressure", error=str(e)[:120])


# The tts provider every profile with a tts block must name; unset = not checked (lazy installs still are).
TTS_PROVIDER = os.environ.get("KREW_TTS_PROVIDER", "").strip()


def gate_d_assert() -> None:
    import glob
    try:
        import yaml
    except Exception:
        log("gate_d_skipped", reason="no yaml module")
        return
    bad = []
    home = os.path.expanduser("~")
    paths = [f"{home}/.hermes/config.yaml"] + sorted(
        glob.glob(f"{home}/.hermes/profiles/*/config.yaml")
    )
    for p in paths:
        try:
            c = yaml.safe_load(open(p))
            lazy = (c.get("security") or {}).get("allow_lazy_installs")
            tts = (c.get("tts") or {}).get("provider")
            # 2026-09-18 (the User "I heard a DRIFT task announced … stop them"): scout has no
            # voice block at all and was announced as drift every cycle; the tts law
            # applies only to profiles that carry a tts block.
            tts_bad = ("tts" in c) and bool(TTS_PROVIDER) and tts != TTS_PROVIDER
            if lazy is not False or tts_bad:
                bad.append({"path": p, "lazy": lazy, "tts": tts})
        except Exception as e:
            bad.append({"path": p, "error": str(e)[:100]})
    if bad:
        log("gate_d_DRIFT", bad=bad)
        announce(
            "Config drift detected on the Hermes profiles. Check the keeper log.",
            category="protocol",
        )
    else:
        log("gate_d_ok", checked=len(paths))


def glass_keeps_brave() -> bool:
    """Crissa's Brave is kept alive in exactly ONE state: ZOS Wall OFF, where Brave
    IS the glass, opens with Home and one instance stays up (the User 2026-09-15:
    "the WallOS flag OFF should also open Brave With Home as well as keep one
    instance open. Only then."). ZOS Wall ON makes it a tools window, down by
    design. Crissa OFF keeps nothing open at all. So anything that is not
    `brave` means: do not patrol it, do not card it, do not restart it.
    The file can carry a note ("wallpaper from-brave"), so read the first word.
    Unreadable = do not patrol; a missing flag must never start a browser."""
    try:
        return (KH.hermes_home() / "krew-glass-mode").read_text().strip().split()[0] == "brave"
    except (OSError, IndexError):
        return False


# One-shot memory per lane: after a start or a PA for a SUSTAINED down, a
# repeated down within the same episode must not flap or re-announce
# (t_0ac35ad7). In-process only — a keeper restart re-arms, which is fine:
# a fresh process is the natural moment to try one more heal.
LANE_DOWN_ANNOUNCED: set[str] = set()


# ── boot bring-up of the crew's gateways (2026-09-17) ───────────────────────────
# Ada and Lila were `disabled` before a planned reboot (so the dispatcher does not
# come up ahead of its engine and burn cards into a dead lane) and then nothing
# started them: krew-mode only wakes gateways on the way OUT of Render/Off, the
# janitor's restart is a dry run and refuses `inactive`, and these were not lanes.
# This is a bring-up, not a lane: it never restarts, and it only acts on a unit
# that has NOT RUN SINCE BOOT — a gateway someone stopped by hand stays stopped.
# Off: KEEPER_GATEWAY_WAKE=0 or ~/.hermes/keeper-gateway-wake.off.
GATEWAY_WAKE = os.environ.get("KEEPER_GATEWAY_WAKE", "1") not in ("0", "false", "no", "off")
GATEWAY_WAKE_OFF = KH.hermes_home() / "keeper-gateway-wake.off"
GATEWAY_WAKE_UNITS = tuple(os.environ["KEEPER_GATEWAY_WAKE_UNITS"].split()) if os.environ.get("KEEPER_GATEWAY_WAKE_UNITS") \
    else tuple(_SETS["wake_at_boot"])
GATEWAY_WAKE_BRAIN = os.environ.get("KEEPER_GATEWAY_WAKE_BRAIN") or _services.health_url(ROWS.get("crew") or {}, CRISSA_PLAYER)

# One host gateway for every profile (KREW_GATEWAY_TOPOLOGY=host, lib/hermes_topology): her lane
# probes the one listener and heals the one unit, and the boot bring-up starts that unit. Under
# the default `units` none of this runs. The host unit is in no NO_HEAL set on purpose: in Render
# it still serves Ada and Lila.
try:
    _hermes_topology = _own_lib("hermes_topology")
    _hermes_endpoint = _own_lib("hermes_endpoint")
    if _hermes_topology.is_host():
        # the front door's gateway row (kind gateway, profile crissa; else the lane named crissa-gw) is the host unit
        _fd = next((r["name"] for r in _kind_rows("gateway") if r.get("profile") == "crissa"), "crissa-gw")
        LANES[_fd] = (_hermes_endpoint.health_url("crissa"), "user", _hermes_topology.HOST_UNIT)
        if not os.environ.get("KEEPER_GATEWAY_WAKE_UNITS"):
            GATEWAY_WAKE_UNITS = (_hermes_topology.HOST_UNIT,)
except Exception as _e:  # noqa: BLE001 - the keeper never falls over a topology read
    if os.environ.get("KREW_GATEWAY_TOPOLOGY", "").strip().lower() == "host":
        print(f"krew-keeper: KREW_GATEWAY_TOPOLOGY=host but lib/hermes_topology did not load "
              f"({type(_e).__name__}: {_e}); the crissa lane and the boot bring-up stay one unit per profile",
              file=sys.stderr)
GATEWAY_WAKE_TICKS = int(os.environ.get("KEEPER_GATEWAY_WAKE_TICKS", "3"))   # consecutive OK probes

# The "stopped on purpose" marker (2026-09-18 mitigation, t_d97141c8): the
# wake guard's unit_ran_this_boot reads ActiveEnterTimestampMonotonic, which
# RESETS TO 0 on every clean stop — so after the keeper's own start, every
# later clean stop looks exactly like the boot case the bring-up was written
# for, and the re-pull cycles forever (root cause: t_74be2ff3). A stop caller
# that stops the gateway deliberately (the ExecStop drain path, krew-mode)
# writes this file; the keeper leaves the unit down while the marker is
# fresh, so a deliberate stop sticks. A real reboot is covered by the
# same reasoning — the marker outlives the reboot (~/.hermes is not on
# tmpfs) and the gateways were disabled before the reboot on purpose; the
# TTL is the escape hatch for "stopped it 6 h ago and want it back".
STOP_MARKER = KH.hermes_home() / "ada-gateway-stop-marker"
STOP_MARKER_TTL_S = int(os.environ.get("KEEPER_STOP_MARKER_TTL_S", "21600"))   # 6 h

def _stop_marker_fresh(unit: str) -> bool:
    """True when a stop caller wrote the marker recently (within the TTL).

    Read-only and lenient: no file, a bad timestamp, or anything unreadable
    means "no deliberate stop" and the wake proceeds as designed."""
    try:
        ts = int(STOP_MARKER.read_text().split()[0])
    except Exception:
        return False
    return (time.time() - ts) < STOP_MARKER_TTL_S

def _gpu_flag_raw() -> str:
    """The flag's own first word. `_gpu_mode()` folds `off` (and junk) into normal;
    a bring-up must not."""
    try:
        return open(GPU_MODE_FILE).read().strip().splitlines()[0].strip().lower()
    except Exception:
        return ""


def _any_hold_file() -> bool:
    return any(os.path.isfile(p) for p in TRAIN_HOLD_PATHS) or os.path.exists(BEAST_HOLD)


def unit_ran_this_boot(scope: str, unit: str) -> bool:
    """True when the unit has been active since its manager started (or when that
    cannot be read: unknown means hands off)."""
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + [
        "show", unit, "-p", "ActiveEnterTimestampMonotonic", "--value"]
    try:
        raw = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        return int(raw) != 0
    except Exception:
        return True


def gateway_wake_refusal(*, flag: str, hold: bool, brain_ok_ticks: int, state: str,
                         ran_this_boot: bool, tried: bool) -> str:
    """Why the keeper leaves this gateway down; "" = start it. Pure."""
    if tried:
        return "one try per keeper life"
    if flag != "normal":
        return f"flag {flag or 'unreadable'}"     # Render/Beast/Off: krew-mode owns the gateways
    if hold:
        return "a GPU hold is up"
    if state != "inactive":
        return f"state {state}"                   # failed = a crash (not mine); active = nothing to do
    if ran_this_boot:
        return "ran since boot — someone stopped it"
    if brain_ok_ticks < GATEWAY_WAKE_TICKS:
        return f"crew served {brain_ok_ticks}/{GATEWAY_WAKE_TICKS} probes"
    return ""


def wake_gateways(mem: dict) -> None:
    """One step of the bring-up; `mem` = {"ok": consecutive brain probes, "tried": set()}."""
    if not GATEWAY_WAKE or GATEWAY_WAKE_OFF.is_file():
        return
    tried = mem.setdefault("tried", set())
    if len(tried) >= len(GATEWAY_WAKE_UNITS):
        return
    mem["ok"] = mem.get("ok", 0) + 1 if http_ok(GATEWAY_WAKE_BRAIN) else 0
    flag, hold = _gpu_flag_raw(), _any_hold_file()
    for unit in GATEWAY_WAKE_UNITS:
        if unit in tried:
            continue
        state = unit_state("user", unit)
        if state == "active" or (state == "inactive" and unit_ran_this_boot("user", unit)):
            tried.add(unit)                       # up already, or stopped on purpose: done with it
            continue
        if state == "inactive" and _stop_marker_fresh(unit):
            # A stop caller deliberately stopped this unit recently (clean
            # ExecStop drain, krew-mode). The ran_this_boot guard is blind
            # to the keeper's own start (ActiveEnterMonotonic resets to 0 on
            # every clean stop), so without this the re-pull cycles forever
            # (root cause: t_74be2ff3). Leave a deliberate stop down for the
            # marker's TTL.
            tried.add(unit)
            mem.setdefault("why", {})[unit] = "stop-marker"
            log("gateway_wake_wait", unit=unit, why="stop-marker")
            continue
        no = gateway_wake_refusal(flag=flag, hold=hold, brain_ok_ticks=mem["ok"], state=state,
                                  ran_this_boot=False, tried=False)
        if no:
            if mem.get("why", {}).get(unit) != no.split(" ")[0]:
                mem.setdefault("why", {})[unit] = no.split(" ")[0]
                log("gateway_wake_wait", unit=unit, why=no)
            continue
        okd = unit_action("user", unit, "start")
        tried.add(unit)
        log("gateway_wake_start", unit=unit, ok=okd, brain_ok_ticks=mem["ok"])



# ---- Crissa, live or not (the owner 2026-10-06) -----------------------------------------------------------------------
# Every pass asks crew/lib/crissa_watch.py whether Crissa is live: its Wall player answers GET /v1/state. When it
# is, the Crissa side is watched too, through the player only (its /v1/state and /v1/ear), never through
# Crissa's files. When it is not, only krew's own lanes run. The Crissa lanes (KEEPER_CRISSA_LANES) and the ear
# re-open are skipped, so a Crissa service that is not there raises nothing. The status file
# (KEEPER_STATUS: level, headline, epoch) says "Crissa not installed — watching krew only". Nothing is cached, so
# installing or stopping Crissa is picked up on the next pass, with no restart.
# Morning Hello reads presence through the Wall's player (or KEEPER_PRESENCE_URL); no player = no hello.
PRESENCE_URL = os.environ.get("KEEPER_PRESENCE_URL") or CRISSA_PLAYER + "/v1/rail/presence/v1/presence"
KEEPER_STATUS = os.environ.get(
    "KEEPER_STATUS", (KREW_MEMORY_ROOT + "/keeper/status.json")
)
CRISSA_LANES = frozenset(os.environ["KEEPER_CRISSA_LANES"].split()) if os.environ.get("KEEPER_CRISSA_LANES") \
    else _SETS["crissa_lanes"]
try:
    _crissa_watch = _own_lib("crissa_watch")
except Exception as _e:  # noqa: BLE001 - the keeper never falls over it; krew-only is the safe reading
    _crissa_watch = None
    print(f"krew-keeper: crew/lib/crissa_watch.py did not load ({type(_e).__name__}: {_e}); "
          "Crissa is taken as not installed - watching krew only", file=sys.stderr)


def crissa_look() -> dict:
    if _crissa_watch is None:
        return {"live": False, "level": "ok", "headline": "Crissa not installed — watching krew only",
                "why": "crew/lib/crissa_watch.py did not load", "problems": [], "epoch": time.time()}
    return _crissa_watch.probe()


def crissa_skips(name: str, z: dict | None) -> bool:
    """A Crissa lane while Crissa is not live: not watched, not restarted, not announced."""
    return name in CRISSA_LANES and not (z or {}).get("live")


def write_status(z: dict) -> None:
    skipped = sorted(n for n in LANES if crissa_skips(n, z))
    if z.get("live"):
        level, headline = z.get("level") or "ok", f"watching krew + Crissa: {z.get('headline', '')}"
    else:
        level, headline = "ok", z.get("headline") or "Crissa not installed — watching krew only"
    row = {"level": level, "headline": headline[:200], "epoch": time.time(), "by": "krew-keeper",
           "crissa": z, "lanes": sorted(n for n in LANES if n not in skipped), "skipped_crissa_lanes": skipped}
    try:
        os.makedirs(os.path.dirname(KEEPER_STATUS), exist_ok=True)
        tmp = KEEPER_STATUS + ".tmp"
        with open(tmp, "w") as f:
            f.write(json.dumps(row) + "\n")
        os.replace(tmp, KEEPER_STATUS)
    except OSError as e:
        log("keeper_error", where="status", error=str(e)[:200])


def crissa_pass(prev: dict | None) -> dict:
    """Once per pass: look again, log when Crissa came or went (or a Crissa-side problem changed), write status."""
    z = crissa_look()
    line = _crissa_watch.flip(prev, z) if _crissa_watch is not None else ("" if prev is not None else z["headline"])
    if line:
        print(f"krew-keeper: {line}", flush=True)
        log("crissa_watch", live=bool(z.get("live")), line=line, why=z.get("why", ""))
    elif z.get("live") and (prev or {}).get("problems") != z.get("problems"):
        log("crissa_side", problems=z.get("problems"), ok=not z.get("problems"))
    write_status(z)
    return z


def main() -> None:
    st = load_state()
    fails: dict[str, int] = {}
    wake_mem: dict = {}
    zw: dict | None = None
    last_gate_d = 0.0
    log("keeper_start", lanes=list(LANES), services=str(SERVICES_PATH), note=NO_SERVICES)
    if NO_SERVICES:
        print(f"krew-keeper: {NO_SERVICES}", file=sys.stderr, flush=True)
    gate_d_assert()

    while True:
        # 1) ear
        try:
            zw = crissa_pass(zw)
        except Exception as e:
            log("keeper_error", where="crissa", error=str(e)[:200])
            zw = {"live": False, "headline": "Crissa not installed — watching krew only"}

        # 1) ear (Crissa's: only while Crissa is live)
        try:
            if not crissa_skips("ear-rail", zw):
                keep_ear_open()
        except Exception as e:
            log("keeper_error", where="ear", error=str(e)[:200])

        # 2) lanes
        if NO_SERVICES and not LANES:
            log("no_services", note=NO_SERVICES)
        for name, (url, scope, unit) in LANES.items():
            try:
                kind = (ROWS.get(name) or {}).get("kind") or ""
                if crissa_skips(name, zw):
                    fails.pop(name, None)
                    continue
                if kind == "glass" and not glass_keeps_brave():
                    fails.pop(name, None)
                    continue
                if kind == "glass" and glass_keeps_brave():

                    # heal start (the keeper IS the glass owner the CDP-dead
                    # path defers to); a unit that keeps DYING is a crash loop,
                    # not a missed start — announce once and leave it (the
                    # StartLimit window is the hard stop). Never restart, never
                    # flap, and no PA for a single missed start.
                    ustate = unit_state(scope, unit)
                    if ustate == "active":
                        if fails.pop(name, 0) >= 2:
                            log("lane_recovered", lane=name)
                        continue
                    fails[name] = fails.get(name, 0) + 1
                    if fails[name] == 1:
                        continue  # one blip is free
                    if ustate == "activating":
                        continue  # mid-heal or systemd restarting it
                    fails[name] = 0  # re-armed for the NEXT episode
                    if ustate in ("inactive", "dead"):
                        if name in LANE_DOWN_ANNOUNCED:
                            continue
                        subprocess.run(
                            ["systemctl", "--user", "start", unit],
                            capture_output=True, text=True, timeout=30,
                        )
                        log("glass_brave_start", unit=unit, state=ustate)
                        LANE_DOWN_ANNOUNCED.add(name)
                    elif ustate == "failed":
                        if name in LANE_DOWN_ANNOUNCED:
                            continue
                        announce("Crissa's display window keeps dying. It needs a look.", category="stuck")
                        LANE_DOWN_ANNOUNCED.add(name)
                if url:
                    ok = http_ok(url)
                else:
                    ok = unit_state(scope, unit) == "active"
                if ok:
                    if fails.pop(name, 0) >= 2:
                        log("lane_recovered", lane=name)
                        if kind == "gateway" and (zw or {}).get("live") and _gpu_mode() not in ("render", "beast", "off"):
                            _retry_crissa_glass()
                    continue
                fails[name] = fails.get(name, 0) + 1
                if fails[name] == 1:
                    continue  # one blip is free
                if scope is None:
                    if fails[name] == 2:
                        log("lane_down_not_mine", lane=name,
                            why=_player_why(url) if url and url.startswith(CRISSA_PLAYER) else "")
                    continue
                if _gpu_mode() == "beast" and unit in BEAST_NO_HEAL:
                    if fails[name] == 2:
                        log(
                            "lane_down_beast_no_heal",
                            lane=name,
                            unit=unit,
                            note="Beast — leave dashboard/Crissa gw down until crew serves",
                        )
                    continue
                if _gpu_mode() == "render" and unit in RENDER_NO_HEAL:
                    if fails[name] == 2:
                        log(
                            "lane_down_render_sleep",
                            lane=name,
                            unit=unit,
                            note="Render — Crissa sleeps; Lila/Vera stay on the crew engine",
                        )
                    continue
                if occupancy_parks_gpu_lane(unit):
                    # Parked on purpose (Beast ops / Render ops). A crash on
                    # a live lane falls through to start/restart.
                    if fails[name] == 2:
                        log(
                            "lane_down_gpu_occupancy",
                            lane=name,
                            unit=unit,
                            note="observe only — occupancy parked this GPU lane",
                        )
                    continue
                if _lane_held(unit):
                    if fails[name] == 2:
                        log(
                            "lane_down_hold",
                            lane=name,
                            unit=unit,
                            beast=_beast_holds(unit),
                            note="hold owns the GPU — not restarting",
                        )
                    continue
                state = unit_state(scope, unit)
                # Type=simple is "active" at fork. activating never fires for
                # ops/crew. Age since MainPID is the load clock (~2.5 min HTTP).
                if state == "activating" or in_start_grace(scope, unit):
                    if fails[name] == 2:
                        log(
                            "lane_down_start_grace",
                            lane=name,
                            unit=unit,
                            age_s=unit_age_s(scope, unit),
                            grace_s=start_grace_s(scope, unit),
                            note="loading — systemd owns this window",
                        )
                    fails[name] = 0
                    continue
                if restarts_today(st, unit) >= RESTARTS_PER_DAY:
                    if fails[name] == 2:
                        if url and http_ok(url):
                            log("lane_recovered", lane=name, note="reprobe before budget PA")
                            fails[name] = 0
                            continue
                        log("lane_down_budget_exhausted", lane=name, unit=unit)
                        announce(
                            f"{name} is down and its restart budget is spent. Needs a human.",
                            category="stuck",
                        )
                    continue
                if fails[name] >= 2:
                    # Never thrash-restart qwen unless explicitly re-enabled
                    if kind == "announcer":
                        mute = KH.hermes_home() / "krew-announce-mute"
                        allow = os.environ.get("KEEPER_RESTART_QWEN", "0") in (
                            "1", "true", "yes"
                        )
                        if mute.is_file() or not allow:
                            if fails[name] == 2:
                                log(
                                    "lane_down_skip_qwen",
                                    reason="muted_or_policy",
                                    mute=mute.is_file(),
                                    allow=allow,
                                    note="the voice server + type wall is the free-air mouth; leave qwen stopped",
                                )
                            fails[name] = 0
                            continue
                    action = "restart" if state == "active" else "start"
                    okd = unit_action(scope, unit, action)
                    note_restart(st, unit)
                    save_state(st)
                    fails[name] = 0
                    log("lane_restarted", lane=name, unit=unit, action=action, ok=okd,
                        today_count=restarts_today(st, unit))
                    # krew-crissa-display: unit wrapper applies Hybrid minimize / VO FS
                    # on cold start; if already open, leave window alone.
                    # Do not PA routine restarts — User: stuck / protocol / completions only
                    if kind not in ("announcer", "glass"):
                        log("lane_restarted_silent_pa", lane=name, unit=unit)
            except Exception as e:
                log("keeper_error", where=name, error=str(e)[:200])

        # 2b) the crew's gateways, once crew serves after a boot
        try:
            wake_gateways(wake_mem)
        except Exception as e:
            log("keeper_error", where="gateway_wake", error=str(e)[:200])

        # 3) TTS scratch + thrash signals (same suspects every boot — User 2026-08-06)
        try:
            tts_scratch_janitor()
            tts_unit_pressure()
        except Exception as e:
            log("keeper_error", where="tts_janitor", error=str(e)[:200])

        # 4) nightly Gate-D (once per day at ~04:00-05:00)
        now = dt.datetime.now()
        if now.hour == 4 and time.time() - last_gate_d > 3600 * 20:
            last_gate_d = time.time()
            gate_d_assert()

        # 5) Morning Hello — first certain presence after wake hour, once/day
        try:
            day = dt.date.today().isoformat()
            if st.get("hello_day") != day and now.hour >= 7 and now.hour < 12:
                h = http_json(PRESENCE_URL) if PRESENCE_URL else None
                block = (h or {}).get("presence") if isinstance((h or {}).get("presence"), dict) else (h or {})
                people = [p for p in (block.get("people") or []) if p.get("certain") and p.get("name")]
                if people:
                    person = people[0]["name"]
                    st["hello_day"] = day
                    save_state(st)
                    subprocess.Popen(
                        [sys.executable,
                         str(Path(__file__).resolve().parents[1] / "scripts/morning-hello.py"),
                         "--person", person])
                    log("morning_hello_fired", person=person)
        except Exception as e:
            log("keeper_error", where="hello", error=str(e)[:200])

        try:
            keep_voice_warm()
        except Exception as e:
            log("keeper_error", where="voice_warm", error=str(e)[:200])

        time.sleep(CHECK_SEC)


if __name__ == "__main__":
    main()
