#!/usr/bin/env python3
"""krew-janitor — keep PROD up all day, log metrics, escalate to kanban.

The owner 2026-08-04: "a janitor to restart or report to kanban when red. Log and report any metrics on uptime,
temp high/low, reboot, errors, cycle time in kanban for the Weekly review by Steward. Issues should turn into
kanban research and mitigation plans as protocol."

Every 5 min (krew-janitor.timer):
  * sample unit states + NRestarts, GPU temp/power/mem, load, disk, port probes
  * append one JSONL row to <metrics_dir>/janitor-YYYYMMDD.jsonl
  * red unit -> one restart attempt per REST_COOLDOWN (only when ARMED; else it logs would_restart); still red ->
    kanban card (research + mitigation protocol), one card per unit per CARD_COOLDOWN
  * NRestarts climbing >= CRASH_LOOP_RESTARTS within CRASH_LOOP_WINDOW_S = crash loop -> card with journal
    crash-signature counters (observe-only; the GPU owner restarts GPU tenants, the janitor only reports)
  * parked units (train window / occupancy hold) are expected-down: never restarted or carded
  * every unit in the registry (krew_registry.py, discovered from systemd) is watched, report-only

Weekly (krew-janitor-weekly.timer, Mon): --weekly aggregates the week and files the Steward review card.

Every machine value (units, probes, ports, flags, paths) is $KREW_HOME/janitor.yaml; config/janitor.example.yaml
lists every key. Stdlib only (+ krew_settings beside it).
"""
from __future__ import annotations

import fnmatch
import glob
import importlib.util
import json
import re
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import sys as _sys
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))
import krew_settings  # noqa: E402
import krew_hermes as KH  # noqa: E402  where Hermes lives


def _cfg(section: str) -> dict:
    """One section of $KREW_HOME/janitor.yaml, or of $KREW_JANITOR_CONFIG (config/janitor.example.yaml lists every key)."""
    return krew_settings.load(section, path=os.environ.get("KREW_JANITOR_CONFIG") or krew_settings.krew_home() / "janitor.yaml")


def _p(v) -> Path:
    return Path(os.path.expanduser(str(v)))


_P = _cfg("paths")
_G = _cfg("gpu")
_T = _cfg("train")
_RS = _cfg("restart")


def _services_lists() -> dict:
    """The one services list ($KREW_HOME/services.json, crew/lib/krew_services.py, the owner 2026-10-08) as the
    janitor's lists: watch / report_only / observe_only / retired, probes, the Wall's units, the GPU tenants.
    Loaded by path from krew's crew/lib (absent = {} and janitor.yaml alone)."""
    lib = _HERE.parent / "crew" / "lib" / "krew_services.py"
    try:
        spec = importlib.util.spec_from_file_location("krew_janitor_services", lib)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        p = mod.path()
        return dict(mod.janitor_lists(mod.load(p)), present=True, path=str(p)) if p.is_file() else {"present": False, "path": str(p)}
    except Exception as e:  # noqa: BLE001 - a broken list is said on the tick, never a crash here
        return {"present": False, "error": f"{type(e).__name__}: {e}"[:160]}


_SV = _services_lists()


def _merged_units(cfg: dict, sv: dict) -> dict:
    """janitor.yaml's units: plus the services list's rows (a unit on both is listed once)."""
    out = dict(cfg)
    for key, sk in (("watch", "watch"), ("report_only", "report_only"), ("observe_only", "observe_only"),
                    ("retired", "retired")):
        have = list(cfg.get(key) or [])
        for u in sv.get(sk) or []:
            if u not in have and u.rpartition(":")[2] not in have:
                have.append(u)
        out[key] = have
    return out


_U = _merged_units(_cfg("units"), _SV)
METRICS_DIR = _p(_P.get("metrics_dir") or krew_settings.krew_home() / "janitor" / "metrics")
STATE_PATH = METRICS_DIR / "janitor-state.json"
HERMES = os.environ.get("HERMES_BIN") or krew_settings.hermes_bin()
CARD_PROFILE = str(_cfg("cards").get("profile") or "ada")      # the board the reboot-card reads go through
CARD_SIGNER = str(_cfg("cards").get("created_by") or "krew-janitor")

# GPU occupancy: the GPU owner (a mode script) and systemd restart these, never the janitor (a third
# restarter fought the owner once: a model walked back onto the card mid-load). Parked = expected-down.
GPU_OCCUPANCY_UNITS = set(_G.get("occupancy_units") or ()) | set(_SV.get("gpu_units") or ())
GPU_OCCUPANCY_PROBES = set(_G.get("occupancy_probes") or ())
GPU_MODE_FILE = Path(os.environ.get("KREW_GPU_MODE_FILE") or _p(_G.get("mode_file") or str(KH.hermes_home() / "gpu-mode")))
GPU_MODES = tuple(_G.get("modes") or ("render", "normal", "beast", "heavy-render"))
BEAST_HOLD = Path(os.environ.get("KREW_BEAST_HOLD") or _p(_G.get("beast_hold") or "/run/hermes/beast-hold"))
BEAST_PARKED_UNITS = set(_G.get("beast_parked_units") or ())
BEAST_PARKED_PROBES = set(_G.get("beast_parked_probes") or ())
RENDER_PARKED_PROBES: set[str] = set(_G.get("render_parked_probes") or ())
RENDER_PARKED_UNITS: set[str] = set(_G.get("render_parked_units") or ())
RENDER_SLEEP_UNITS = set(_G.get("render_sleep_units") or ())

# Retired by order: expected-down forever (never restarted, carded or probed). Do NOT resurrect.
RETIRED_UNITS = set(_U.get("retired") or ())
RETIRED_PROBES = set(_U.get("retired_probes") or ())
# Watched BY UNIT STATE but report-only: GPU tenants (restarts stay with the restart window).
GPU_TENANT_REPORT_ONLY = set(_U.get("report_only") or ())


def is_report_only_unit(unit: str) -> bool:
    """A unit the janitor observes by state and cards on, but never restarts (GPU tenants)."""
    return unit in GPU_TENANT_REPORT_ONLY


GLASS_UNIT = str(_U.get("glass_unit") or "")
GLASS_MODE_FILE = _p(_U.get("glass_mode_file") or str(KH.hermes_home() / "glass-mode"))


def glass_keeps_brave() -> bool:
    """The display browser unit (units.glass_unit) is patrolled in exactly ONE state: its mode file's first word
    is `brave` (the browser IS the glass). Anything else - another mode, unreadable, missing - means do not
    patrol it, card it or restart it: a missing flag must never start a browser."""
    try:
        return GLASS_MODE_FILE.read_text().strip().split()[0] == "brave"
    except (OSError, IndexError):
        return False


# Observe + card, never restart (a restart steals a lease or kills the wall window).
OBSERVE_ONLY = set(_U.get("observe_only") or ())


def _unit_rows(v) -> list:
    out = []
    for x in v or ():
        if isinstance(x, dict):
            out.append((str(x["unit"]), str(x.get("manager") or x.get("scope") or "user")))
        elif isinstance(x, (list, tuple)):
            out.append((str(x[0]), str(x[1])))
        else:
            scope, _, name = str(x).rpartition(":")
            out.append((name, scope or "user"))
    return out


UNITS = _unit_rows(_U.get("watch"))
# name -> (port, path or None for bare TCP)
PROBES = {str(k): (int(v[0]), (v[1] if len(v) > 1 else None))
          for k, v in {**(_SV.get("probes_ports") or {}), **(_cfg("probes").get("ports") or {})}.items()}
# probe -> its systemd unit (a 100%-red probe whose unit is ALSO red is an outage, not a stale target)
PROBE_UNITS = {str(k): str(v) for k, v in {**(_SV.get("probes_units") or {}), **(_cfg("probes").get("units") or {})}.items()}
# Crissa, live or not (the owner 2026-10-06): its units and probes on this machine are watched only while its
# player answers /v1/state; not live = parked "Crissa not installed — watching krew only", never red
_Z = _cfg("crissa")
CRISSA_UNITS = set(_Z.get("units") or ()) | set(_SV.get("crissa_units") or ())
CRISSA_PROBES = set(_Z.get("probes") or ()) | set(_SV.get("crissa_probes") or ())
CRISSA_UNIT_PATTERNS = tuple(_Z.get("unit_patterns") or ("crissa-*",))
CRISSA_PLAYER = str(_Z.get("player") or "")      # "" = $CRISSA_PLAYER, else http://127.0.0.1:8100
CRISSA_EAR = str(_Z.get("ear") or "")            # a switch: off = do not watch her ear; else read via the player's /v1/ear
CRISSA_NOT_INSTALLED = "Crissa not installed — watching krew only"

REST_COOLDOWN = int(_RS.get("cooldown_s") or 30 * 60)      # one restart attempt per unit per 30 min
# Narrow and a dry run until armed: only a `failed` unit, never in a crash loop, never observe-only or a wall
# unit, never under a GPU hold, one try per 30 min. Unarmed it only LOGS `would_restart`.
RESTART_ARMED = krew_settings.switch_on("KREW_JANITOR_RESTART", bool(_RS.get("armed", False)))
NEVER_RESTART = set(_RS.get("never") or ())   # the wall is the owner's call


def restart_refusal(unit: str, active: str, crash_loop: bool, since_last: float,
                    *, hold: bool | None = None) -> str:
    """Why the janitor leaves this red unit alone; "" = a restart is in order."""
    if is_report_only_unit(unit):
        return "report-only (gpu tenant — restart stays with the restart window)"
    if unit in OBSERVE_ONLY:
        return "observe-only"
    if unit in NEVER_RESTART:
        return "wall unit"
    if crash_loop:
        return "crash loop"
    if active != "failed":
        return f"state {active}"          # deactivating/activating = systemd is already at it
    if since_last <= REST_COOLDOWN:
        return "cooldown"
    if hold is None:
        hold = any(p.is_file() for p in TRAIN_HOLD_PATHS)
    if hold:
        return "gpu hold"
    return ""


CARD_COOLDOWN = int(_cfg("cards").get("cooldown_s") or 12 * 3600)    # one kanban card per unit per 12 h
# A crash loop = NRestarts climbing >= CRASH_LOOP_RESTARTS within CRASH_LOOP_WINDOW_S.
CRASH_LOOP_RESTARTS = int(_cfg("crash_loop").get("restarts") or 2)
CRASH_LOOP_WINDOW_S = int(_cfg("crash_loop").get("window_s") or 5 * 60)
CRASH_SIGNATURES = tuple(_cfg("crash_loop").get("signatures") or (
    "CUDA out of memory",
    "illegal memory access",
    "EngineDeadError",
))
PROBE_TIMEOUT = int(_cfg("probes").get("timeout_s") or 8)

# A train window parks the brains on purpose (its script writes the busy flag). While it is fresh, parked
# units are expected-down: never restart or card them mid-train.
TRAIN_BUSY_FLAG = _p(_T.get("busy_flag") or "/run/hermes/train-busy")
TRAIN_BUSY_MAX_AGE = int(_T.get("busy_max_age_s") or 6 * 3600)
TRAIN_PARKED_UNITS = set(_T.get("parked_units") or ())
TRAIN_PARKED_PROBES = set(_T.get("parked_probes") or ())
TRAIN_HOLD_PATHS = tuple(Path(os.environ.get("KREW_TRAIN_HOLD") or _p(x)) if i == 0 else _p(x)
                         for i, x in enumerate(_T.get("hold_paths") or ("/tmp/krew-train-hold",)))


def train_window_active() -> bool:
    try:
        return time.time() - TRAIN_BUSY_FLAG.stat().st_mtime < TRAIN_BUSY_MAX_AGE
    except OSError:
        return False


def read_gpu_mode() -> str:
    """Same occupancy flag as scripts/krew-mode (~/.hermes/krew-gpu-mode)."""
    try:
        first = GPU_MODE_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        return "normal"
    if first in GPU_MODES:
        return first
    return "normal"


def occupancy_parks_probe(name: str, mode: str | None = None) -> bool:
    """True when krew-mode parked this GPU tenant on purpose.

    Beast: ops parked, Cosy stays, crew is Unsloth 27B.
    Canonical Render: crew :8005 parked; ops + Comfy stay.
    Park-ops / dual-park: the hold file is SoT — a listed unit is parked,
    not RED (2026-09-05 stall: dual-park looked like an ops outage).
    Comfy is the inverse of crew: expected-down unless mode is render.
    Janitor keeps ticking either way — these are parked, not RED.
    """
    if mode is None:
        mode = read_gpu_mode()
    unit = PROBE_UNITS.get(name)
    if unit:
        for path in TRAIN_HOLD_PATHS:
            if not path.is_file():
                continue
            try:
                want = [
                    ln.strip()
                    for ln in path.read_text().splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
            except OSError:
                want = []
            if unit in want:
                return True
    if name == "comfy" and mode not in ("render", "heavy-render"):
        return True
    if mode == "heavy-render" and name == "crew":  # heavy-render: crew off the card on purpose (the User 09-29)
        return True
    if mode == "beast" and name in BEAST_PARKED_PROBES:
        return True
    if mode == "render" and name in RENDER_PARKED_PROBES:
        # Crew is the fallback brain when ops is on the hold. Hold already
        # returned True above if crew itself is listed (exclusive dual-park).
        if name == "crew":
            ops_unit = PROBE_UNITS.get("ops")
            if ops_unit:
                for path in TRAIN_HOLD_PATHS:
                    if not path.is_file():
                        continue
                    try:
                        want = [
                            ln.strip()
                            for ln in path.read_text().splitlines()
                            if ln.strip() and not ln.strip().startswith("#")
                        ]
                    except OSError:
                        want = []
                    if ops_unit in want:
                        return False
        return True
    return False


def row_unit_parked(unit: str, row: dict) -> bool:
    """Same-row parked predicate for the JSONL unit table.

    2026-09-08 (t_87a3c5b9, from research t_bb614aaf): the tick's unit loop
    marks `parked` from train_window + occupancy_parks_unit + unit_on_hold,
    while the probe loop marks `parked` from occupancy_parks_probe (hold-file
    driven) + GPU_OCCUPANCY_PROBES + TRAIN_PARKED_PROBES. Those are DIFFERENT
    predicates: a Render-sleep unit (hermes-crissa-gateway) is NOT in
    TRAIN_HOLD_PATHS, so its probe (crissa-gw) read 180 red rows in the weekly
    uptime while the unit row read 100% — the "100% unit + 180 probe reds +
    294 parked" anomaly. One helper, one predicate, both sides.
    """
    active = row.get("active")
    mode = row.get("gpu_mode")
    return (
        (row.get("train_window") and unit in TRAIN_PARKED_UNITS)
        or occupancy_parks_unit(unit, mode)
        or unit_on_hold(unit, mode)
    ) and active != "active"


def occupancy_parks_unit(unit: str, mode: str | None = None) -> bool:
    """True when occupancy parked this systemd unit on purpose."""
    if mode is None:
        mode = read_gpu_mode()
    if mode == "beast" and unit in BEAST_PARKED_UNITS:
        return True
    if mode == "heavy-render" and unit == "crew.service":  # heavy-render: crew off the card on purpose
        return True
    if mode == "render" and unit in (RENDER_SLEEP_UNITS | RENDER_PARKED_UNITS):
        if unit == "crew.service":
            ops_unit = "ops.service"
            for path in TRAIN_HOLD_PATHS:
                if not path.is_file():
                    continue
                try:
                    want = [
                        ln.strip()
                        for ln in path.read_text().splitlines()
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
                except OSError:
                    want = []
                if ops_unit in want and unit not in want:
                    return False
        return True
    return False


def unit_on_hold(unit: str, mode: str | None = None) -> bool:
    """Beast/train/Render-sleep hold — same SoT as krew-keeper. Do not resurrect.

    `mode` threaded from tick's single gpu-mode read: a mid-tick flip must not
    make the parked check and the unit-red check disagree (2026-08-24,
    t_56818ed9, adversary nit-9).
    """
    if occupancy_parks_unit(unit, mode):
        return True
    if unit in BEAST_PARKED_UNITS and BEAST_HOLD.is_file():
        return True
    for path in TRAIN_HOLD_PATHS:
        if not path.is_file():
            continue
        try:
            want = [
                ln.strip()
                for ln in path.read_text().splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
        except OSError:
            continue
        if not want or unit in want:
            return True
    return False


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _reboot_intent() -> str:
    """Ask krew-reboot-stamp.py to CONSUME the pre-reboot intent stamp.

    Called only on a detected boot_id change. The consume path checks:
      1. pending stamp is fresh (<2h)
      2. pending.boot_id matches the boot that just ended (journalctl)
    On both-pass: moves pending → consumed (audit), returns "intentional=<reason>".
    On either-fail: returns "UNKNOWN" (full forensic pass still runs).
    Never raises — the janitor must keep sampling even if the stamp script is
    missing; an unknown intent falls through to the full forensic card.
    """
    stamp = _p(_P.get("reboot_stamp") or _HERE / "krew-reboot-stamp.py")
    try:
        r = run([sys.executable, str(stamp), "consume"], timeout=15)
        return (r.stdout or "").strip().splitlines()[-1].strip() if r.stdout else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


# A reboot the User explained is not UNKNOWN (the User 2026-09-30): his comment naming the reboot before it,
# or his comment on the UNKNOWN card after it, is the intent (lib/reboot_comment.py).
# Kill: KREW_JANITOR_REBOOT_COMMENT=0.
def _reboot_comment():
    _add_libs()
    import reboot_comment
    return reboot_comment


def _kanban_rc(*args: str) -> int:
    try:
        return run([HERMES, "-p", CARD_PROFILE, "kanban", *args], timeout=60).returncode
    except Exception:  # noqa: BLE001
        return 1


def reboot_stated_by_user(now: float) -> dict | None:
    """The User's own word on this reboot, from the live board; None when off, absent or unreadable."""
    try:
        rc = _reboot_comment()
        db = rc.board_db() if rc.enabled() else None
        if db is None:
            return None
        conn = rc.connect(db)
        try:
            return rc.stated_before(conn, now)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - the watchdog never falls over a board read
        print(f"[janitor] reboot comment read failed: {e!r}"[:200])
        return None


def reboot_comment_tick() -> int:
    """Archive open UNKNOWN reboot cards the User has explained. Returns the count; -1 when unreadable."""
    try:
        rc = _reboot_comment()
        db = rc.board_db() if rc.enabled() else None
        if db is None:
            return 0
        conn = rc.connect(db)
        try:
            cards = rc.explained_cards(conn)
        finally:
            conn.close()
        n = rc.resolve(cards, _kanban_rc)
        for c in cards[:n]:
            print(f"[janitor] reboot card {c['card']} explained by {c['author']}: archived")
        return n
    except Exception as e:  # noqa: BLE001
        print(f"[janitor] reboot comment tick failed: {e!r}"[:200])
        return -1


def sysctl(manager: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    base = ["systemctl", "--user"] if manager == "user" else ["sudo", "-n", "systemctl"]
    if manager == "system":
        # metrics-only paths never need sudo; restart path degrades gracefully
        probe = ["systemctl"] + list(args)
        if args[0] in ("is-active", "show"):
            return run(probe, timeout=timeout)
    return run(base + list(args), timeout=timeout)


def unit_state(unit: str, manager: str) -> dict:
    active = sysctl(manager, "is-active", unit).stdout.strip()
    show = sysctl(manager, "show", unit, "-p", "NRestarts", "-p", "SubState").stdout
    props = dict(l.split("=", 1) for l in show.splitlines() if "=" in l)
    return {
        "active": active,
        "sub": props.get("SubState", "?"),
        "nrestarts": int(props.get("NRestarts", "0") or 0),
    }


# 2026-09-27 (t_d5c76f93, from t_774195ef): a clean SIGTERM stop reads
# `deactivating` (and a start reads `activating`) for ~1 s. unit_state() sampled
# krew-lila-conductor in that window and the tick carded what it saw — six
# clean restarts in 24 h, zero crashes, one RED card per sample that landed in
# the window. `deactivating`/`activating` are INERT for carding: they log as
# `in-transition` and mint no card; the card mint is gated on the FINAL state
# (resolve below). CARD_COOLDOWN is not the protection — it only spaced the
# false cards 12 h apart.
IN_TRANSITION_STATES = ("deactivating", "activating")
IN_TRANSITION_SETTLE_MAX_S = 10
IN_TRANSITION_POLL_S = 1


def transition_inert(active: str) -> bool:
    """A stop/start transient — systemd is mid-teardown or mid-start."""
    return active in IN_TRANSITION_STATES


def settle_state(unit: str, manager: str) -> str:
    """Re-sample `is-active` until it leaves deactivating/activating (up to
    IN_TRANSITION_SETTLE_MAX_S @ IN_TRANSITION_POLL_S). Returns the first
    terminal reading, or the last reading if the window expires.

    5 s per sysctl: the default 30 s run timeout would let one hung
    `is-active` blow the 10 s settle contract (11 polls x 30 s)."""
    for _ in range(IN_TRANSITION_SETTLE_MAX_S // IN_TRANSITION_POLL_S):
        time.sleep(IN_TRANSITION_POLL_S)
        active = sysctl(manager, "is-active", unit, timeout=5).stdout.strip()
        if not transition_inert(active):
            return active
    return sysctl(manager, "is-active", unit, timeout=5).stdout.strip()


def probe(port: int, path: str | None) -> dict:
    t0 = time.monotonic()
    try:
        if path is None:
            with socket.create_connection(("127.0.0.1", port), timeout=PROBE_TIMEOUT):
                pass
            ok = True
        else:
            import urllib.request

            r = urllib.request.urlopen(
                f"http://127.0.0.1:{port}{path}", timeout=PROBE_TIMEOUT
            )
            # 200-299 green — same rule as krew-keeper http_ok (krew-keeper.py
            # line ~164); 4xx means the port is alive but serving the wrong app
            # (route removed, swapped service) and must read RED.
            ok = 200 <= r.status < 300
    except Exception:
        ok = False
    return {"ok": ok, "ms": round((time.monotonic() - t0) * 1000, 1)}


def gpu_sample() -> dict:
    try:
        out = run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,power.draw,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ]
        ).stdout.strip()
        t, p, mu, mt, u = [x.strip() for x in out.split(",")]
        return {
            "temp_c": int(t),
            "power_w": float(p),
            "mem_used_mib": int(mu),
            "mem_total_mib": int(mt),
            "util_pct": int(u),
        }
    except Exception as e:
        return {"error": str(e)[:80]}


def host_sample() -> dict:
    load1 = os.getloadavg()[0]
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    up = float(Path("/proc/uptime").read_text().split()[0])
    disk = {}
    for mnt in [m for m in (_P.get("disks") or ("/",)) if os.path.isdir(m)]:
        st = os.statvfs(mnt)
        disk[mnt] = round(st.f_bavail * st.f_frsize / 2**30, 1)
    return {"load1": round(load1, 2), "boot_id": boot_id, "uptime_s": int(up), "disk_free_gib": disk}


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(st: dict) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE_PATH)


def file_card(title: str, body: str) -> bool:
    try:
        # Signed by the janitor, never the CLI default `user` (the User 09-28): a machine card counted as his.
        r = run([HERMES, "kanban", "create", title, "--body", body[:3500], "--triage",
                 "--created-by", CARD_SIGNER], timeout=60)
        print(f"[janitor] kanban {'filed' if r.returncode == 0 else 'FAILED rc=%d' % r.returncode}: {title}")
        return r.returncode == 0
    except Exception as e:
        print(f"[janitor] kanban error: {e!r}")
        return False


PIN_CHECK = _p(_P.get("pin_check") or _HERE.parent / "crew" / "scripts" / "krew-pin-check.py")


def crissa_unit(unit: str) -> bool:
    """A unit of Crissa's on this machine: named in `crissa.units`, or matching `crissa.unit_patterns`."""
    return bool(unit) and (unit in CRISSA_UNITS or any(fnmatch.fnmatchcase(unit, p) for p in CRISSA_UNIT_PATTERNS))


def _crissa_watch():
    """krew's own crew/lib/crissa_watch.py, by path (beside the Janitor's crew/), else `paths.lib_dirs`."""
    path = _HERE.parent / "crew" / "lib" / "crissa_watch.py"
    if path.is_file():
        spec = importlib.util.spec_from_file_location("krew_own_crissa_watch", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    _add_libs()
    import crissa_watch
    return crissa_watch


def crissa_tick(state: dict, *, zw=None) -> dict:
    """Is Crissa live (its player answers /v1/state)? One line in the journal when that changes."""
    try:
        zw = zw or _crissa_watch()
        z = zw.probe(CRISSA_PLAYER or None, CRISSA_EAR or None)
        line = zw.flip(state.get("crissa"), z)
    except Exception as e:  # noqa: BLE001 - krew-only is the safe reading; never stops the tick
        z = {"live": False, "level": "ok", "headline": CRISSA_NOT_INSTALLED, "problems": [],
             "why": f"crissa_watch.py did not load ({type(e).__name__}: {e})"[:200]}
        line = "" if state.get("crissa") and not state["crissa"].get("live") else f"{CRISSA_NOT_INSTALLED} ({z['why']})"
    if line:
        print(f"[janitor] {line}")
    state["crissa"] = {"live": bool(z.get("live"))}
    return z


def _add_libs() -> None:
    """Optional helper modules (the reboot comment reader, audio orphans, the code canary, the gateway topology):
    beside this file first, then `paths.lib_dirs` in janitor.yaml. A helper that is not installed makes its
    tick report it `missing` (a warn on the Janitor tile), never stops the tick."""
    for d in [str(_HERE)] + [os.path.expanduser(str(x)) for x in (_P.get("lib_dirs") or [])]:
        if d not in sys.path:
            sys.path.append(d)


def _gateway_libs():
    """(hermes_topology, hermes_endpoint): krew's own crew/lib copies, loaded by path, first (never a same-named
    module from another tree on sys.path); only when krew's crew/ is not beside the Janitor, `paths.lib_dirs`."""
    import importlib.util
    crew_lib = _HERE.parent / "crew" / "lib"
    if (crew_lib / "hermes_topology.py").is_file() and (crew_lib / "hermes_endpoint.py").is_file():
        mods = []
        for name in ("hermes_topology", "hermes_endpoint"):
            spec = importlib.util.spec_from_file_location(f"krew_own_{name}", crew_lib / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mods.append(mod)
        return mods[0], mods[1]
    _add_libs()
    import hermes_endpoint
    import hermes_topology
    return hermes_topology, hermes_endpoint


def apply_gateway_topology() -> bool:
    """ONE host gateway serves every profile (KREW_GATEWAY_TOPOLOGY=host, lib/hermes_topology).

    The roster, the two gateway probes and their units are rewritten IN PLACE to the host unit and
    its one listener; Render sleeps nobody's gateway (Crissa rides crew, and the host serves Ada and
    Lila through a Render).
    Under the default `units` this returns False and touches nothing.
    """
    try:
        topo, hermes_endpoint = _gateway_libs()
        if not topo.is_host():
            return False
        host, port = topo.HOST_UNIT, hermes_endpoint.host_port()
    except Exception:  # noqa: BLE001 - the watchdog never falls over a topology read
        return False
    seen = False
    for i, (unit, scope) in enumerate(list(UNITS)):
        if topo.profile_of_unit(unit):
            UNITS[i] = (host, scope)
    kept = []
    for row in UNITS:                       # two gateway rows became one host row
        if row[0] == host:
            if seen:
                continue
            seen = True
        kept.append(row)
    UNITS[:] = kept
    for name in ("crissa-gw", "ada-gw"):
        if name in PROBES:
            PROBES[name] = (port, "/health")
        if name in PROBE_UNITS:
            PROBE_UNITS[name] = host
    RENDER_SLEEP_UNITS.clear()
    return True


apply_gateway_topology()


def _say_missing(state: dict, key: str, missing: list) -> None:
    """One log line when what a tick could not check changes (the Janitor tile shows it every tick)."""
    seen = state.setdefault("missing", {})
    if list(missing) != seen.get(key, []):
        for m in missing:
            print(f"[janitor] {key}: not checked: {m}")
        if not missing and seen.get(key):
            print(f"[janitor] {key}: everything is checked again")
        seen[key] = list(missing)


def pin_tick(state: dict, now: float) -> dict:
    """KV + skill-belt + Render switchover flags. Observe only — never restart a brain."""
    out: dict = {"ok": True, "failures": []}
    if not PIN_CHECK.is_file():
        miss = [f"pin check not installed: {PIN_CHECK} (crew/scripts/krew-crew-install.py places it; "
                "paths.pin_check in janitor.yaml names another)"]
        _say_missing(state, "pins", miss)
        return {"ok": True, "failures": [], "missing": miss}
    try:
        sp = importlib.util.spec_from_file_location("krew_pin_check", PIN_CHECK)
        if sp is None or sp.loader is None:
            raise RuntimeError("pin-check loader failed")
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        out = mod.check()
    except Exception as e:
        out = {"ok": False, "failures": [f"pin-check import: {type(e).__name__}: {e}"]}
    _say_missing(state, "pins", out.get("missing") or [])
    if not out.get("ok"):
        print(f"[janitor] pin RED: {out.get('failures')}")
        last = state.get("cards", {}).get("pins", 0)
        if now - last > CARD_COOLDOWN:
            fails = out.get("failures") or []
            switch = any("bad Render switchover" in str(f) for f in fails)
            title = (
                "JANITOR RED: bad Render switchover — lane pinned to a parked 27B"
                if switch
                else "JANITOR RED: Hermes pin drift (krew-pin-check)"
            )
            body = (
                "krew-pin-check failed (python3 crew/scripts/krew-pin-check.py shows it). It pins the skill "
                "belts (krew-kitting allowlist), Crissa's skill preload and SOUL cap, the patch queue on the Hermes "
                "tree, T0, fallbacks and the crew SOUL caps.\n\n"
                + "\n".join(fails)[:3000]
            )
            if file_card(title, body):
                state.setdefault("cards", {})["pins"] = now
    return out


def journal_tail(unit: str, manager: str, lines: int = 15) -> str:
    cmd = ["journalctl", "--no-pager", "-n", str(lines), "-u", unit]
    if manager == "user":
        cmd.insert(1, "--user")
    try:
        return run(cmd).stdout[-1800:]
    except Exception:
        return "(journal unavailable)"


def crash_signature_count(unit: str, manager: str, since_s: int) -> dict:
    """Count journal crash signatures for a unit in the last `since_s` seconds.

    Observe-only: reads the journal, never touches the unit. Returns
    {signature: count}; all-zero dict if the journal is unavailable.
    """
    cmd = ["journalctl", "--no-pager", "-u", unit, "--since", f"-{since_s}s"]
    if manager == "user":
        cmd.insert(1, "--user")
    out = {sig: 0 for sig in CRASH_SIGNATURES}
    try:
        text = run(cmd).stdout
    except Exception:
        return out
    for sig in CRASH_SIGNATURES:
        out[sig] = sum(1 for ln in text.splitlines() if sig in ln)
    return out


# 2026-09-30 (t_e04462b5 logs-anomaly, filed t_7f5da45c): an AUTH retry loop on ops
# read as ordinary startup churn in the journal tail — the unit was "active" the
# whole time, so the janitor's unit-state row stayed green and only the port probe
# flickered. A unit that keeps failing to authenticate a model endpoint is sick even
# while it reads `active`. The detector counts auth-failure signatures in the recent
# journal window; >= AUTH_FAIL_THRESHOLD within AUTH_FAIL_WINDOW_S is a RED row, and
# the row carries the matching signatures so a reader sees the failure, not churn.
# Startup churn stays green: one-off auth lines at boot do not reach the threshold,
# and the window slides so a recovered unit goes back to green on its own.
AUTH_FAIL_SIGNATURES = (
    "401 Unauthorized",
    "401 Client Error",
    "AuthenticationError",
    "Authentication failed",
    "Invalid API key",
    "InvalidApiKeyError",
    "invalid api key",
    "invalid API key",
    "PermissionDenied",
    "403 Forbidden",
    "403 Client Error",
    "unauthorized",
    "unauthenticated",
    "authentication failed",
)
AUTH_FAIL_WINDOW_S = int(os.environ.get("JANITOR_AUTH_WINDOW_S", str(15 * 60)))
AUTH_FAIL_THRESHOLD = int(os.environ.get("JANITOR_AUTH_THRESHOLD", "3"))


def auth_failure_count(unit: str, manager: str, since_s: int) -> dict:
    """Count journal auth-failure signatures for a unit in the last `since_s` seconds.

    Observe-only: reads the journal, never touches the unit. Returns
    {signature: count}; all-zero dict if the journal is unavailable. Reuses the
    crash-signature counting shape so tests and the weekly card stay comparable.
    """
    cmd = ["journalctl", "--no-pager", "-u", unit, "--since", f"-{since_s}s"]
    if manager == "user":
        cmd.insert(1, "--user")
    out = {sig: 0 for sig in AUTH_FAIL_SIGNATURES}
    try:
        text = run(cmd).stdout
    except Exception:
        return out
    for sig in AUTH_FAIL_SIGNATURES:
        out[sig] = sum(1 for ln in text.splitlines() if sig in ln)
    return out


def auth_repeating(unit: str, manager: str) -> dict:
    """A repeating auth failure in the journal -> a red row, not startup churn.

    Returns {} when the journal has fewer than AUTH_FAIL_THRESHOLD auth-failure
    lines in AUTH_FAIL_WINDOW_S (or is unavailable). Otherwise returns
    {"count": N, "signatures": {sig: n}} for the unit's metrics row and card body.
    """
    sigs = auth_failure_count(unit, manager, AUTH_FAIL_WINDOW_S)
    matched = {s: c for s, c in sigs.items() if c}
    total = sum(matched.values())
    if total < AUTH_FAIL_THRESHOLD:
        return {}
    return {"count": total, "signatures": matched}


def detect_crash_loop(
    unit: str, manager: str, nrestarts: int, prev_nrestarts: int, state: dict
) -> dict | None:
    """Crash-loop detector: >= CRASH_LOOP_RESTARTS within CRASH_LOOP_WINDOW_S.

    Records each NRestarts climb in state["crash"][unit] as a timestamp;
    returns a loop summary (with crash-signature counters) when the restarts
    in the window reach the threshold, else None. Janitor's own restarts also
    bump NRestarts, so a unit janitor keeps restarting shows up here too.
    """
    if nrestarts <= prev_nrestarts:
        return None
    loops = state.setdefault("crash", {}).setdefault(unit, [])
    for _ in range(nrestarts - prev_nrestarts):
        loops.append(time.time())
    loops[:] = [t for t in loops if time.time() - t <= CRASH_LOOP_WINDOW_S]
    if len(loops) < CRASH_LOOP_RESTARTS:
        return None
    sigs = crash_signature_count(unit, manager, CRASH_LOOP_WINDOW_S)
    sigs = {k: v for k, v in sigs.items() if v}
    return {"restarted": len(loops), "signatures": sigs}



# ---------------------------------------------------------------------------
# Render leftover: the house parked for a card that is no longer running.
# 2026-09-06, 09-08 and 09-09 the Comfy card finished (or died) and the
# house stayed in Render for hours — ops down, Crissa down — because the way
# back was the worker's last step and nobody else was allowed to take it.
# krew-gpu-hold now launches `krew-mode normal` when the owning card
# completes; this is the net under it for crashes, docker completes and
# gave_up cards. Two consecutive ticks (~10 min) of "render, no live owner"
# → launch `krew-mode normal` detached, once per RENDER_RESTORE_COOLDOWN.
# A hold with no card line is a human/training hold: never touched.
# ---------------------------------------------------------------------------
RENDER_RESTORE_COOLDOWN = int(os.environ.get("JANITOR_RENDER_RESTORE_COOLDOWN", str(60 * 60)))
RENDER_LEFTOVER_TICKS = int(os.environ.get("JANITOR_RENDER_LEFTOVER_TICKS", "2"))
KREW_MODE_BIN = _p(_G.get("mode_bin") or _HERE / "gpu-mode")
GPU_PENDING_FILE = Path(os.environ.get("KREW_GPU_PENDING_FILE") or _p(_G.get("pending_file") or str(KH.hermes_home() / "gpu-pending")))
TERMINAL_CARD_STATUSES = {"done", "blocked", "archived", "missing"}
# NOT KANBAN CARDS (2026-09-14): krew-mode's Mode-button Render stamps "occupancy" on the hold and a
# running Init stamps "init-wizard"; `kanban show` answers "no such task" -> "missing" -> terminal, so the
# janitor took the house back to Standard ~10 min after every Mode-button Render (12:55, before the User's
# opener test, which then waited 5 min on "Switching to Render…"). These holds are never auto-released.
HUMAN_HOLD_CARDS = {"occupancy", "init-wizard"}
INIT_STATE_FILE = Path(os.environ.get("KREW_ID_ROOT") or _p(_P.get("id_root") or krew_settings.krew_home() / "identity")) / "site" / "init-wizard.json"


def init_running() -> bool:
    """The first-boot / re-register routine is running: it owns Render until its own mode_normal."""
    try:
        return str(json.loads(INIT_STATE_FILE.read_text()).get("status") or "") == "running"
    except (OSError, ValueError):
        return False
RENDER_JOBS_FILE = Path(os.environ.get("KREW_RENDER_JOBS") or _p(_P.get("render_jobs") or str(KH.hermes_home() / "render-jobs.json")))


def render_pending_cards() -> set[str]:
    """Cards with a render still on Comfy (render-submit / render-collect).
    Their hold is live whatever the board says: a BLOCKED card can own a
    render that render-collect will land and hand back. 2026-09-11 11:30 this
    tick judged t_5b423dc9 (blocked, render at step 6/10) terminal, launched
    krew-mode normal, and SIGKILLed Comfy under the render."""
    try:
        jobs = json.loads(RENDER_JOBS_FILE.read_text())
    except (OSError, ValueError):
        return set()
    return {str(j.get("card")) for j in jobs if isinstance(j, dict) and j.get("status") in ("pending", "preparing")}


def _hold_card_ids() -> tuple[bool, list[str]]:
    """(hold file exists, card ids stamped on it) — same format as krew-mode."""
    paths = [Path(str(x)) for x in TRAIN_HOLD_PATHS]
    p = next((x for x in paths if x.is_file()), None)
    if p is None:
        return False, []
    ids: list[str] = []
    for ln in p.read_text().splitlines():
        ln = ln.strip()
        if ln.startswith("#") and "card=" in ln:
            cid = ln.split("card=", 1)[1].strip()
            if cid and cid not in ids:
                ids.append(cid)
    return True, ids


def card_status(card: str) -> str:
    """Kanban status of a card, 'missing' if no board answers, 'unknown' on error."""
    try:
        r = run([HERMES, "kanban", "show", card, "--json"], timeout=30)
    except Exception:
        return "unknown"
    if r.returncode != 0:
        return "missing"
    try:
        d = json.loads(r.stdout or "{}")
        d = d.get("task", d)
        return str(d.get("status") or "unknown").lower()
    except Exception:
        return "unknown"


def launch_gpu_mode_normal(reason: str) -> bool:
    unit = "crew-mode-restore-janitor-%d" % int(time.time())
    try:
        r = run(["systemd-run", "--user", "--collect", "--quiet", "--unit", unit,
                 "--description", "crew-mode normal (janitor: %s)" % reason,
                 "python3", str(KREW_MODE_BIN), "normal"], timeout=20)
        return r.returncode == 0
    except Exception as e:
        print(f"[janitor] crew-mode normal launch error: {e!r}")
        return False


def render_leftover_decision(gpu_mode: str, hold_exists: bool, card_ids: list[str],
                             statuses: dict[str, str], pending: bool, init_live: bool = False) -> tuple[bool, str]:
    """Pure: is the house parked in Render with nobody left to bring it back?"""
    if gpu_mode != "render":
        return False, "not render"
    if pending:
        return False, "transition pending"
    if init_live:
        return False, "Init running — the routine owns Render"
    held = [c for c in card_ids if c in HUMAN_HOLD_CARDS]
    if hold_exists and held:
        return False, "held by %s — the Mode button / Init, never auto-released" % ",".join(held)
    if hold_exists and not card_ids:
        return False, "human/training hold — never auto-released"
    if not hold_exists:
        return True, "render flag with no hold file"
    pending_render = render_pending_cards()
    live = [c for c in card_ids
            if statuses.get(c, "unknown") not in TERMINAL_CARD_STATUSES or c in pending_render]
    if live:
        return False, "owner still live: %s" % ",".join(
            c + ("(render pending)" if c in pending_render else "") for c in live)
    return True, "owners terminal: %s" % ",".join(
        "%s=%s" % (c, statuses.get(c, "?")) for c in card_ids)


# ---- hermes venv-generation inventory (report only) ------------------------
# t_706a5db8: generations under ~/.hermes/installs are never pruned; every
# `hermes-env-pins.py apply` / nightly trail resync materializes a new gen.
# This row names which are not live / not mapped, with age and size. It
# DELETES NOTHING — a prune is the User's call.
# The heavy per-file walk is deferred to lib/hermes_venv_gen; the janitor
# row wraps the one-shot result and guards the whole thing (one probe never
# costs the tick).

def hermes_venv_gen_tick() -> dict:
    """Inventory of hermes venv generations: live vs stale, age, size (GiB).

    Live  = named in the install's `facts.json` packages.venv.environment.
             facts.json alone is live. A gen mapped by a running process but
             absent from facts.json is reported STALE with mapped:true — the
             annotation never promotes a gen to live, so the report never
             under-states what a prune could touch.
    Stale = every other env generation.
    Report-only — never deletes; a prune is the User's call.
    """
    try:
        import krew_venv_gen as hv
    except Exception as e:  # noqa: BLE001
        return {"error": f"import: {type(e).__name__}: {e}"[:160]}
    try:
        return hv.hermes_venv_gen_report()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:160]}


def render_leftover_tick(state: dict, now: float, gpu_mode: str, *,
                         hold=None, status_fn=None, launch=None, filer=None) -> dict:
    hold = hold or _hold_card_ids
    status_fn = status_fn or card_status
    launch = launch or launch_gpu_mode_normal
    filer = filer or file_card
    hold_exists, card_ids = hold()
    statuses = {c: status_fn(c) for c in card_ids if c not in HUMAN_HOLD_CARDS} if gpu_mode == "render" else {}
    pending = GPU_PENDING_FILE.is_file()
    leftover, why = render_leftover_decision(gpu_mode, hold_exists, card_ids, statuses, pending,
                                             init_live=init_running())
    ticks = int(state.get("render_leftover_ticks", 0)) + 1 if leftover else 0
    state["render_leftover_ticks"] = ticks
    out = {"leftover": leftover, "why": why, "ticks": ticks, "action": None}
    if not leftover:
        return out
    print(f"[janitor] render leftover ({why}) tick {ticks}/{RENDER_LEFTOVER_TICKS}")
    if ticks < RENDER_LEFTOVER_TICKS:
        return out
    last = float(state.get("render_restore_ts", 0) or 0)
    if last and now - last < RENDER_RESTORE_COOLDOWN:
        out["action"] = "cooldown"
        return out
    state["render_restore_ts"] = now
    if launch(why):
        out["action"] = "launched"
        print("[janitor] launched crew-mode normal (render leftover)")
    else:
        out["action"] = "launch_failed"
        filer("JANITOR RED: Render leftover — crew-mode normal launch failed",
              f"Occupancy flag says render, hold owners: {why}. The janitor could not "
              "launch `crew-mode normal` (systemd-run). Run it by hand and check "
              "scripts/krew-janitor.py render_leftover_tick.")
    return out


# PipeWire client leak (2026-09-19): 46 of 64 pulse clients, 31 of them orphaned
# `pactl subscribe` left by tests that loaded the :8099 server. At the cap the house
# reads "no tiles" / Say does nothing. The rule is lib/audio_orphans (keyed on the
# cgroup: a .service child is its unit's; a scope child whose stdout pipe has no
# reader is an orphan). A job that kills every 5 min starts as a DRY RUN: it logs
# `would_reap` until the User sets KREW_JANITOR_AUDIO_REAP=1. Kill the whole
# probe: KREW_JANITOR_AUDIO=0.
AUDIO_PROBE = krew_settings.switch_on("KREW_JANITOR_AUDIO", bool(_cfg("audio").get("probe", True)))
AUDIO_REAP_ARMED = krew_settings.switch_on("KREW_JANITOR_AUDIO_REAP", bool(_cfg("audio").get("reap", False)))
PW_RED_FRACTION = 56 / 64


def audio_orphan_tick() -> dict:
    """Client count against the cap, plus the orphaned audio clients (reaped only when armed)."""
    if not AUDIO_PROBE:
        return {"skipped": True}
    _add_libs()
    try:
        import audio_orphans
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:160]}
    cap = audio_orphans.client_cap()
    found = audio_orphans.scan()
    orphans = found["orphans"]
    out: dict = {"cap": cap, "owned": found["owned"], "armed": AUDIO_REAP_ARMED,
                 "orphans": [{k: o[k] for k in ("pid", "comm", "args", "cgroup", "age_s")} for o in orphans][:12],
                 "suspects": [{k: o[k] for k in ("pid", "comm", "args", "cgroup", "age_s")} for o in found["suspects"]][:6]}
    if orphans and AUDIO_REAP_ARMED:
        out["reaped"] = len(audio_orphans.reap(orphans))
        print(f"[janitor] reaped {out['reaped']} orphaned audio client(s): "
              f"{sorted({o['comm'] for o in orphans})}")
    elif orphans:
        out["would_reap"] = len(orphans)
        print(f"[janitor] would reap {len(orphans)} orphaned audio client(s) — KREW_JANITOR_AUDIO_REAP=1 arms it")
    n, names = audio_orphans.client_count()
    out["clients"] = n
    out["top"] = dict(sorted(names.items(), key=lambda kv: -kv[1])[:6])
    out["red"] = n is not None and n >= int(cap * PW_RED_FRACTION)
    if out["red"]:
        print(f"[janitor] pipewire RED: {n} of {cap} clients")
    return out


# Code canary (2026-09-23): the 09-22 wipe emptied every .py under the hermes venvs, three
# plugins and Steward's uv python and nothing noticed for 14 h. Worker exits count the fast
# trees (scripts/run_kanban_task.sh); this tick is the backstop — the FULL set (the service
# envs too, ~0.7 s) and damage from outside a card. Suspects = cards whose runs overlapped
# the window since the dropped trees last counted clean. Report-only unless
# KREW_CODE_CANARY=act. Kill: KREW_CODE_CANARY=off.
def code_canary_tick() -> dict:
    _add_libs()
    try:
        import code_canary as cc
    except ImportError as e:
        return {"missing": [f"code_canary.py not importable beside {_HERE} ({e})"]}
    if cc.mode() == "off":
        return {"skipped": True}
    res = cc.check(source="janitor", scope="full")
    out = {k: res.get(k) for k in ("ok", "drops", "ms", "trees", "mode", "suspects", "repeat", "ms_running")
           if res.get(k) is not None}
    if not res["ok"]:
        if not res.get("repeat"):
            out["acted"] = cc.act(res)
        print(f"[janitor] code canary DROP: {cc.describe(res)}")
    return out


def reboot_detected(row: dict, now: float) -> None:
    """The boot id changed: the stamp's intent, else the User's own word, else an UNKNOWN card."""
    row["reboot"] = True
    intent = _reboot_intent()
    if intent.startswith("intentional="):
        reason = intent.split("=", 1)[1]
        body = (
            f"Boot id changed since last janitor sample. Recorded pre-reboot "
            f"intent; verify against the operator log before closing. Gate 4 "
            f"re-check only if the house changed config."
        )
        file_card(
            f"JANITOR: reboot detected {row['ts']} — INTENT: {reason}",
            body,
        )
    elif (said := reboot_stated_by_user(now)) is not None:
        # The User said why before the janitor noticed: no UNKNOWN card, his reason in the audit.
        row["reboot_intent"] = said["reason"]
        _reboot_comment().audit({"verdict": "stated-before", "boot_id": row["boot_id"], **said})
        print(f"[janitor] reboot explained by {said['author']} on {said['card']}: {said['reason'][:120]}")
    else:
        file_card(
            f"JANITOR: reboot detected {row['ts']} — INTENT: UNKNOWN",
            "Boot id changed since last janitor sample. No intent stamp. Verify "
            "the reboot was intentional — journalctl -b -1, protocol: research "
            "cause, file mitigation plan. Check docs/PLAN-PROD-LAUNCH-20260804.md "
            "Gate 4 checklist. "
            "Close rule: if the forensic child's verdict is clean + user-initiated "
            "(orderly teardown, OOM/panic/power-loss/update ruled out), this root "
            "closes as 'known-gap, covered by <deliverables>/2026-09-24-reboot-mitigation-plan.md' — do not "
            "re-decompose, do not wait for a new forensic pass.",
        )


def tick() -> None:
    t_start = time.monotonic()
    now = time.time()
    state = load_state()
    prev_units = state.get("units", {})
    row: dict = {"ts": datetime.now().isoformat(timespec="seconds")}
    row.update(host_sample())
    row["gpu"] = gpu_sample()
    row["crissa"] = crissa_tick(state)      # live or not, asked again every tick
    crissa_live = bool(row["crissa"].get("live"))

    # reboot detection
    if state.get("boot_id") and state["boot_id"] != row["boot_id"]:
        reboot_detected(row, now)
    state["boot_id"] = row["boot_id"]
    row["reboot_cards_explained"] = reboot_comment_tick()

    training = train_window_active()
    gpu_mode = read_gpu_mode()
    row["train_window"] = training
    row["gpu_mode"] = gpu_mode

    units_out: dict = {}
    retired_skipped = [u for u, _ in UNITS if u in RETIRED_UNITS]
    if retired_skipped:
        # One line per tick so the suppression is visible, not silent.
        print(f"[janitor] skipping retired unit(s) {', '.join(retired_skipped)}")
    for unit, manager in UNITS:
        if unit in RETIRED_UNITS:
            continue  # expected-down forever; never restart or card it
        st = unit_state(unit, manager)
        if not crissa_live and crissa_unit(unit):
            units_out[unit] = {**st, "red": False, "parked": True, "parked_reason": CRISSA_NOT_INSTALLED}
            continue
        if GLASS_UNIT and unit == GLASS_UNIT and not glass_keeps_brave():
            units_out[unit] = {**st, "red": False, "parked": True,
                               "parked_reason": "glass is not Brave"}
            continue
        prev = prev_units.get(unit, {})
        crash = detect_crash_loop(unit, manager, st["nrestarts"], prev.get("nrestarts", st["nrestarts"]), state)
        # t_7f5da45c: a repeating auth failure in the journal is a red row even
        # while the unit reads `active` (an AUTH retry loop on ops is startup
        # churn in the tail, not a green unit). Only meaningful for the units
        # that actually authenticate a model endpoint; the cheap check is a
        # journal read, so run it for every watched unit.
        auth = auth_repeating(unit, manager) if unit in GPU_TENANT_REPORT_ONLY else {}
        red = st["active"] != "active" or crash is not None or bool(auth)
        # 2026-09-27 (t_d5c76f93): a deactivating/activating sample is INERT
        # for carding — resolve the FINAL state before the red check. The
        # observed transient stays in the row (in_transition), so the weekly
        # row still shows the ~1 s window the janitor saw; only the card
        # mint and the red flag follow the resolved state. A unit wedged in
        # a transition past the 10 s settle window keeps its transient state
        # (active != "active" -> red) and cards as before.
        entry = {**st, "red": red}
        if transition_inert(st["active"]):
            final = settle_state(unit, manager)
            entry["in_transition"] = True
            if final != st["active"]:
                entry["settled_to"] = final
                st = {**st, "active": final}
                entry["active"] = final
                # Card on the FINAL state, not the transient. `unknown` (a
                # one-shot mid-stop edge) is red like any non-active state —
                # never mask it as green. (Reviewer SHOULD-1, t_d5c76f93.)
                red = final != "active" or crash is not None
                entry["red"] = red
        if crash is not None:
            entry["crash"] = crash
        if auth:
            entry["auth"] = auth
        parked_now = row_unit_parked(unit, {
            "active": st["active"],
            "gpu_mode": gpu_mode,
            "train_window": training,
        })
        if parked_now:
            # 2026-08-24 (t_56818ed9, adversary should-2): a parked unit
            # restarts (systemd keeps retrying) must not bank crash-window
            # timestamps — the first card after unpark would be inflated.
            # Reset the window at the parked transition instead.
            if state.get("crash", {}).get(unit):
                state["crash"][unit] = []
            units_out[unit] = {**st, "red": False, "parked": True}
            continue

        if red:
            last_restart = state.get("restarts", {}).get(unit, 0)
            last_card = state.get("cards", {}).get(unit, 0)
            if crash is not None:
                why = "crash loop (>=2 restarts/5min)"
            elif auth:
                why = f"repeating auth failure in journal ({auth['count']} in window)"
            else:
                why = st["active"]
            no = restart_refusal(unit, st["active"], crash is not None, now - last_restart)
            if not no and not RESTART_ARMED:
                # Dry run: say what an armed janitor would do, then card as before.
                print(f"[janitor] would restart {unit} ({why}) — KREW_JANITOR_RESTART=1 arms it")
                entry["would_restart"] = True
            if not no and RESTART_ARMED:
                print(f"[janitor] restarting {unit} ({why})")
                sysctl(manager, "restart", unit)
                state.setdefault("restarts", {})[unit] = now
                entry["action"] = "restart"
            elif now - last_card > CARD_COOLDOWN:
                sig = f" crash_signatures={json.dumps(crash['signatures'])}" if crash else ""
                asig = f" auth_signatures={json.dumps(auth['signatures'])}" if auth else ""
                if file_card(
                    f"JANITOR RED: {unit} {why}",
                    f"unit={unit} manager={manager} state={st['active']}/{st['sub']} "
                    f"nrestarts={st['nrestarts']}{sig}{asig}\n\n"
                    f"Janitor restart did not clear it (or unit is in a crash loop). "
                    f"{'Report-only GPU tenant: restart stays with the restart window; this card is the report.' if is_report_only_unit(unit) else ''}\n"
                    f"Protocol: research root cause, write mitigation plan, link back to this card.\n\n"
                    f"journal tail:\n{journal_tail(unit, manager)}",
                ):
                    state.setdefault("cards", {})[unit] = now
                entry["action"] = "card"
        units_out[unit] = entry
    row["units"] = units_out
    state["units"] = {u: {"nrestarts": e["nrestarts"]} for u, e in units_out.items()}
    # prune crash-loop timestamps so state["crash"] can't grow unbounded
    live_units = {u for u, _ in UNITS}
    for u in list(state.get("crash", {})):
        # 2026-08-24 (t_56818ed9, adversary nit-7): drop crash state for units
        # retired from UNITS (e.g. parakeet-stt.service, killed 08-07).
        if u not in live_units:
            del state["crash"][u]
            continue
        ts = [t for t in state["crash"][u] if now - t <= CRASH_LOOP_WINDOW_S]
        if ts:
            state["crash"][u] = ts
        else:
            del state["crash"][u]

    row["probes"] = {
        name: probe(*spec)
        for name, spec in PROBES.items()
        if name not in RETIRED_PROBES
    }
    for name, pr in row["probes"].items():
        if not crissa_live and (name in CRISSA_PROBES or crissa_unit(PROBE_UNITS.get(name, ""))):
            pr["parked"] = True
            pr["parked_reason"] = CRISSA_NOT_INSTALLED
            continue
        if occupancy_parks_probe(name, gpu_mode):
            pr["parked"] = True
            continue
        # 2026-09-08 (t_87a3c5b9): symmetric exclusion — a probe is parked
        # when its backing UNIT is parked by the SAME predicate the unit
        # loop used this tick (train / occupancy / hold, all != active).
        # occupancy_parks_probe alone diverges: a Render-sleep unit (e.g.
        # hermes-crissa-gateway, in RENDER_SLEEP_UNITS not TRAIN_HOLD_PATHS)
        # left its probe (crissa-gw) red-counted 180 times in the weekly
        # uptime while the unit row read 100%.
        unit = PROBE_UNITS.get(name)
        if unit is not None:
            ue = units_out.get(unit)
            if isinstance(ue, dict) and row_unit_parked(
                unit, {
                    "active": ue.get("active"),
                    "gpu_mode": gpu_mode,
                    "train_window": training,
                }
            ):
                pr["parked"] = True
                pr["parked_reason"] = "unit"
                continue
        if name in GPU_OCCUPANCY_PROBES:
            pr["gpu_occupancy"] = True
            if not pr["ok"]:
                print(f"[janitor] probe RED (observe only, krew-mode owns): {name}")
            continue
        if training and name in TRAIN_PARKED_PROBES:
            pr["parked"] = True
            continue
        if not pr["ok"]:
            print(f"[janitor] probe RED: {name}")

    row["pins"] = pin_tick(state, now)
    row["render_leftover"] = render_leftover_tick(state, now, gpu_mode)
    try:
        row["pipewire"] = audio_orphan_tick()
    except Exception as e:  # noqa: BLE001 — one probe never costs the tick
        row["pipewire"] = {"error": f"{type(e).__name__}: {e}"[:160]}
    try:
        row["code_canary"] = code_canary_tick()
    except Exception as e:  # noqa: BLE001 — one probe never costs the tick
        row["code_canary"] = {"error": f"{type(e).__name__}: {e}"[:160]}
    try:
        row["hermes_venv_gen"] = hermes_venv_gen_tick()
    except Exception as e:  # noqa: BLE001 — one probe never costs the tick
        row["hermes_venv_gen"] = {"error": f"{type(e).__name__}: {e}"[:160]}

    try:
        row["watch"] = watch_tick(state, now, gpu_mode, training, units_out, row)
    except Exception as e:  # noqa: BLE001 — one probe never costs the tick
        row["watch"] = {"error": f"{type(e).__name__}: {e}"[:160]}

    try:
        row["watcher"] = watcher_tick(state, now)
    except Exception as e:  # noqa: BLE001 — one probe never costs the tick
        row["watcher"] = {"error": f"{type(e).__name__}: {e}"[:160]}
    try:
        row["door_view"] = door_view_tick(state, now)
    except Exception as e:  # noqa: BLE001 — one probe never costs the tick
        row["door_view"] = {"error": f"{type(e).__name__}: {e}"[:160]}

    row["cycle_ms"] = round((time.monotonic() - t_start) * 1000, 1)
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    out = METRICS_DIR / f"janitor-{date.today():%Y%m%d}.jsonl"
    with out.open("a") as f:
        f.write(json.dumps(row) + "\n")
    save_state(state)
    print(f"[janitor] tick ok cycle={row['cycle_ms']}ms reds="
          f"{[u for u, e in units_out.items() if e['red']] or 'none'}")


# ---- who watches the watcher (the User 2026-10-06) -------------------------------------------------
# scripts/janitor-watch.py (timer, 10 min) checks this janitor ticked; this checks janitor-watch's heartbeat back.
# Report only: a red line and one card per episode. Kill KREW_JANITOR_WATCH=0 (the watcher) also quiets this.
WATCH_BEAT = KH.hermes_home() / "janitor-watch.beat"
WATCH_BEAT_STALE_S = 35 * 60


def watcher_tick(state: dict, now: float, *, card=None) -> dict:
    if os.environ.get("KREW_JANITOR_WATCH", "1").strip().lower() in ("0", "false", "no", "off") or \
            (KH.hermes_home() / "janitor-watch.off").exists():
        return {"mode": "off"}
    try:
        age = now - float(WATCH_BEAT.read_text().strip())
    except (OSError, ValueError):
        age = None
    st = state.setdefault("watcher", {})
    if age is not None and age <= WATCH_BEAT_STALE_S:
        st.pop("episode", None)
        return {"ok": True, "beat_age_s": round(age)}
    why = "janitor-watch has never beaten" if age is None else f"janitor-watch last beat {int(age / 60)} min ago"
    print(f"[janitor] watcher RED: {why}")
    ep = st.setdefault("episode", {"since": now})
    if not ep.get("card"):
        ep["card"] = (card or _crissa_card)(f"Janitor's watcher is silent: {why}",
                                          f"{why}. Check: systemctl --user status krew-janitor-watch.timer; "
                                          "journalctl --user -u krew-janitor-watch -n 30. The janitor itself is running.",
                                          f"janitor-watcher:{time.strftime('%Y-%m-%d-%H', time.localtime(ep['since']))}")
    return {"ok": False, "why": why, "card": ep.get("card")}


# ---- the door view (the User 2026-10-06) ------------------------------------------------------------
# 04:00-09:35 the camera said "door" while its picture sat on the User's seat, and presence refused him as a guest for
# five hours; every check here stayed green because each asks the camera about itself. lib/door_view_check asks the
# PICTURE and presence's own log. One card per episode, blocked on Crissa (she decides and tells the User). Mode
# KREW_DOOR_VIEW_CHECK = report (default) | act (a real re-home + ONE legitimate ensure-auth look; never Secure,
# never a grant) | off.
DOOR_VIEW_CARD_COOLDOWN_S = 2 * 3600


def _door_view():
    _add_libs()          # janitor/door_view_check.py beside this file (krew), else `paths.lib_dirs`
    import door_view_check
    return door_view_check


def _crissa_card(title: str, body: str, key: str) -> str:
    r = run([HERMES, "-p", "crissa", "kanban", "create", title, "--assignee", "crissa", "--body", body[:3500],
             "--created-by", "krew-janitor", "--idempotency-key", key], timeout=60)
    m = re.search(r"\bt_[0-9a-f]{8}\b", (r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0 or not m:
        return ""
    run([HERMES, "-p", "crissa", "kanban", "block", m.group(0),
         "The camera's door view looks wrong (details in the card). Tell the User; ask him to press Secure if he "
         "is stuck as a guest.", "--kind", "needs_input"], timeout=60)
    return m.group(0)


def door_view_tick(state: dict, now: float, *, dv=None, card=None) -> dict:
    dv = dv or _door_view()
    m = dv.mode()
    if m == "off":
        return {"mode": "off"}
    r = dv.check(now)
    out = {"mode": m, "state": r["state"], "why": r["why"]}
    st = state.setdefault("door_view", {})
    if r["state"] != "suspect":
        if st.get("episode"):
            dv.log({"event": "cleared", "episode": st["episode"]})
        st.pop("episode", None)
        return out
    print(f"[janitor] door-view RED: {r['why']}")
    out["red"] = True
    ep = st.setdefault("episode", {"since": now})
    acted = {}
    if m == "act" and not ep.get("acted"):
        # Guarded (the User 2026-10-06): never turns off Double Secure, Secure or User auth; always ends at the door.
        acted = dv.act(r, dv._get(f"{dv.CAMERA}/health"))
        ep["acted"] = round(now, 1)
        out["acted"] = acted
        if acted.get("violations"):
            print(f"[janitor] door-view GUARD VIOLATION: {acted['violations']} went off during act")
        if not (acted.get("rehome") or {}).get("at_door"):
            print("[janitor] door-view: the camera is NOT at the door after re-home")
    dv.log({"event": "suspect", "mode": m, **{k: r.get(k) for k in ("why", "picture", "frame_on", "glances",
                                                                       "stuck_guest")}, "acted": acted})
    if "carded" not in ep or now - float(ep["carded"]) >= DOOR_VIEW_CARD_COOLDOWN_S:
        mins = int((now - ep["since"]) / 60)
        body = (f"House janitor, door-view check ({m}). For {mins} min: {r['why']}.\n"
                f"Picture match: {json.dumps(r.get('picture'))[:300]}. Head-framing on: {r.get('frame_on')}.\n"
                + (f"GUARD VIOLATION - went off during act: {acted['violations']}. Tell the User now.\n"
                   if acted.get("violations") else "")
                + ("The camera is NOT at the door after the re-home.\n"
                   if acted and not (acted.get("rehome") or {}).get("at_door") else "")
                + (f"Acted: {json.dumps(acted)[:400]}\n" if acted else
                   "Report-only: nothing was moved. A real re-home is the Control Panel Home key.\n")
                + ("If the User is at his desk and still a guest, ask him to press Secure (nothing here grants "
                   "auth).\n" if r.get("stuck_guest") else "")
                + "Log: ~/.hermes/door-view-check.jsonl. Switch: KREW_DOOR_VIEW_CHECK=report|act|off.")
        tid = (card or _crissa_card)(f"Door view: {r['why'][:90]}", body,
                                   f"door-view:{time.strftime('%Y-%m-%d-%H', time.localtime(ep['since']))}")
        if tid:
            ep["carded"] = now
            out["card"] = tid
    return out


# ---- the watch list + the Control Panel's Janitor tile (the User 2026-10-06) -------------------
# "We cannot operate like this": a unit nobody listed failed silently (krew-crissa-screen, failed since
# 10-05 01:22, on no list), pause switches were files nobody could see, and red lived in a JSONL
# and a triage card. config/krew-watch.json is the ONE list of the extra units; each red one is
# carded like a UNITS row (JANITOR RED: <unit> <why>, CARD_COOLDOWN). Report-only: the janitor
# never restarts a listed unit. Every tick also writes ONE snapshot the panel paints (red = a name
# and a why, every switch, the open gaps). Kill KREW_JANITOR_WATCH=0.
def _krew_watch():
    import krew_watch
    return krew_watch


def watch_tick(state: dict, now: float, gpu_mode: str, training: bool, units_out: dict, row: dict,
               *, hw=None, card=None) -> dict:
    hw = hw or _krew_watch()
    card = card or file_card
    if not hw.on():
        rows: list = []
    else:
        def parked(u: str) -> bool:
            return bool(occupancy_parks_unit(u, gpu_mode) or unit_on_hold(u, gpu_mode)
                        or (training and u in TRAIN_PARKED_UNITS)
                        or (u in GPU_OCCUPANCY_UNITS and gpu_mode not in ("normal", "")))
        # THE registry (lib/krew_registry.py): a cheap check every tick (two list-unit-files calls; a full
        # discovery only when unit files appeared or vanished), so a new unit is watched within 5 minutes
        # and a removed one is culled with a note. The janitor's own UNITS are watched by unit state above
        # and skipped here so one unit never cards twice.
        reg_check = {}
        try:
            reg_check = hw._registry().cheap_check()
        except Exception as e:  # noqa: BLE001 - the registry never stops the tick
            reg_check = {"ok": False, "error": f"{type(e).__name__}: {e}"[:160]}
        for u in reg_check.get("new") or []:
            print(f"[janitor] registry NEW unit: {u} (now watched)")
        for u in reg_check.get("removed") or []:
            print(f"[janitor] registry REMOVED unit: {u} (culled from the watch list)")
        rows = hw.check_all(parked=parked, skip=frozenset(u for u, _s in UNITS))
        if not (row.get("crissa") or {}).get("live"):
            for r in rows:
                if crissa_unit(r.get("unit", "")):
                    r.update(level="parked", red=False, why=CRISSA_NOT_INSTALLED)
    cards = state.setdefault("cards", {})
    for r in rows:
        if not r.get("red"):
            continue
        key = f"watch:{r['unit']}"
        print(f"[janitor] watch RED: {r['unit']} {r['why']}")
        if now - cards.get(key, 0) > CARD_COOLDOWN:
            if card(f"JANITOR RED: {r['unit']} {r['why']}",
                    f"unit={r['unit']} scope={r['scope']} state={r.get('state')} why={r['why']}\n"
                    f"what it does: {r.get('what', '')}\nwho leans on it: {r.get('feeds', '')}\n\n"
                    f"Watched from the unit registry (janitor/krew_registry.py; words from unit-notes.json) - report-only: the janitor does not restart it.\n"
                    f"Protocol: research root cause, write mitigation plan, link back to this card.\n\n"
                    f"journal tail:\n{journal_tail(r['unit'], 'system' if r['scope'] == 'system' else 'user')}"):
                cards[key] = now
                r["action"] = "card"
            else:
                r["action"] = "card failed"
        else:
            r["action"] = "carded"
    snap = janitor_snapshot(now, units_out, row, rows, hw)
    for k in ("code_canary", "pipewire", "hermes_venv_gen", "render_leftover"):
        _say_missing(state, k, [m["why"] for m in snap.get("missing") or [] if m["from"] == k])
    snap["written"] = hw.write_snapshot(snap)
    return {"n": len(rows), "red": [r["unit"] for r in rows if r.get("red")],
            "paused": [r["unit"] for r in rows if r.get("level") == "paused"], "written": snap["written"]}


def janitor_snapshot(now: float, units_out: dict, row: dict, watch_rows: list, hw) -> dict:
    """Everything the janitor looked at this tick, each red one with its name and why."""
    reds: list[dict] = []
    units = []
    for u, e in units_out.items():
        lvl = "parked" if e.get("parked") else ("bad" if e.get("red") else "ok")
        why = ""
        if e.get("red"):
            why = ("crash loop" if e.get("crash") else "repeating auth failure" if e.get("auth")
                   else f"{e.get('active')}/{e.get('sub')}")
            reds.append({"name": u, "why": why, "from": "unit", "action": e.get("action") or ""})
        units.append({"unit": u, "state": f"{e.get('active')}/{e.get('sub')}", "level": lvl, "why": why,
                      "note": e.get("parked_reason") or ""})
    probes = []
    for n, pr in (row.get("probes") or {}).items():
        lvl = "parked" if pr.get("parked") else ("ok" if pr.get("ok") else "bad")
        if lvl == "bad":
            reds.append({"name": f"probe {n}", "why": f"no answer on :{PROBES.get(n, ('?',))[0]}",
                         "from": "probe", "action": "observe" if pr.get("gpu_occupancy") else "log"})
        probes.append({"probe": n, "port": PROBES.get(n, (None,))[0], "level": lvl,
                       "unit": PROBE_UNITS.get(n, "")})
    for k in ("pins",):
        v = row.get(k) or {}
        if isinstance(v, dict) and v.get("failures"):
            reds.append({"name": k, "why": "; ".join(str(x) for x in v["failures"])[:300], "from": k,
                         "action": "card"})
    for r in watch_rows:
        if r.get("red"):
            reds.append({"name": r["unit"], "why": r["why"], "from": "watch", "action": r.get("action") or "card"})
    z = row.get("crissa") or {}
    if z.get("live"):
        for p in z.get("problems") or []:
            reds.append({"name": "Crissa", "why": p, "from": "crissa", "action": "log"})
    prev = hw.read_snapshot() or {}
    gaps = hw.gaps(prev=prev)
    n_all = len(units) + len(probes) + len(watch_rows)
    paused = [r for r in watch_rows if r.get("level") == "paused"]
    missing = [{"name": k, "why": str(m)[:300], "from": k}
               for k in ("pins", "code_canary", "pipewire", "hermes_venv_gen", "render_leftover")
               if isinstance(row.get(k), dict) for m in (row[k].get("missing") or [])]
    if z.get("live"):   # e.g. a Crissa older than the player's /v1/ear: not checked (warn), never red
        missing += [{"name": "Crissa", "why": str(n)[:300], "from": "crissa"} for n in z.get("notes") or []]
    if reds:
        level = "bad"
        headline = f"Red: {reds[0]['name']} ({reds[0]['why'][:80]})" + (f" +{len(reds) - 1} more" if len(reds) > 1 else "")
    elif missing:
        level = "warn"
        headline = (f"Not checked: {missing[0]['why'][:80]}" + (f" +{len(missing) - 1} more" if len(missing) > 1 else "")
                    + (f"; paused by switch: {', '.join(r['label'] for r in paused)}" if paused else ""))
    elif paused:
        level = "warn"
        headline = f"All running; paused by switch: {', '.join(r['label'] for r in paused)}"
    else:
        level = "ok"
        headline = f"All {n_all} watched things are fine"
    if z.get("live"):
        n_all += 1
        if not reds and not missing and not paused:
            headline = f"All {n_all} watched things are fine (Crissa live, watched through its player)"
    elif z:
        headline = f"{headline} · {z.get('headline') or CRISSA_NOT_INSTALLED}"
    return {"epoch": now, "ts": row.get("ts"), "gpu_mode": row.get("gpu_mode"), "level": level,
            "headline": headline, "reds": reds, "missing": missing, "units": units, "probes": probes,
            "watch": watch_rows, "crissa": z,
            "gaps": gaps, "open_gaps": sum(1 for g in gaps if g.get("status") == "open"),
            "restart_armed": RESTART_ARMED, "card_cooldown_h": CARD_COOLDOWN // 3600,
            "how_red_reports": ("a unit row or a listed unit that is red files ONE kanban triage card "
                                "'JANITOR RED: <unit> <why>' (signed krew-janitor, 12 h cooldown); a probe is "
                                "logged; this tile names every red thing")}


# ---- runaway logs ---------------------------------------------------------
# the User 2026-09-04: "Do we have a patrol to trim logs after time? Nothing should
# grow to the sky." It had grown to the sky: /var/log/syslog at 1.4 GB with
# 4.7 MILLION lines of vLLM's own telemetry POSTing to stats.vllm.ai 13 times a
# second, and journald at 4 GB with no size limit configured at all (the
# default is 10% of the filesystem, which on a 1.8 TB disk is 180 GB — a cap in
# name only). rsyslog rotates WEEKLY with no size trigger, so a chatty process
# has seven days to fill the disk before anything intervenes.
#
# This REPORTS, it does not delete. A log that grew is evidence about whatever
# grew it, and the weekly card is where a human decides. The hard caps that
# stop the sky belong in logrotate and journald.conf, not in a sweep.
LOG_ROOTS = tuple(os.path.expanduser(str(x)) for x in (_cfg("logs").get("roots") or (
    "/var/log", str(KH.hermes_home()), "~/projects", "~/agents")))
LOG_BIG_MB = 50          # worth naming
LOG_LOUD_MB_PER_DAY = 20  # worth acting on


def log_growth() -> list[str]:
    """Big log files and how fast each one is growing since the last sweep."""
    import time as _t
    seen: dict = {}
    now = _t.time()
    for root in LOG_ROOTS:
        r = Path(root)
        if not r.is_dir():
            continue
        try:
            walk = list(r.rglob("*.log")) + list(r.rglob("*.jsonl")) \
                + list(r.rglob("syslog*")) + list(r.rglob("*.out"))
        except (OSError, PermissionError):
            continue
        for f in walk:
            try:
                st = f.stat()
            except (OSError, PermissionError):
                continue
            if not f.is_file() or st.st_size < LOG_BIG_MB * 1048576:
                continue
            seen[str(f)] = {"mb": round(st.st_size / 1048576, 1),
                            "age_d": round((now - st.st_mtime) / 86400, 1)}
    st_path = METRICS_DIR / "log-sizes.json"
    prev = {}
    try:
        prev = json.loads(st_path.read_text())
    except (OSError, ValueError):
        pass
    out = ["", "LOGS — files over %d MB (report only, nothing is deleted)" % LOG_BIG_MB]
    if not seen:
        out.append("  none")
    rows = []
    for path, cur in seen.items():
        was = (prev.get("files") or {}).get(path, {}).get("mb")
        days = None
        if prev.get("ts"):
            days = max((now - float(prev["ts"])) / 86400.0, 1e-6)
        rate = None
        if was is not None and days:
            rate = (cur["mb"] - was) / days
        rows.append((cur["mb"], rate, path, cur["age_d"]))
    for mb, rate, path, age in sorted(rows, reverse=True)[:12]:
        r = "  first sweep" if rate is None else ("  %+.1f MB/day" % rate)
        loud = " <-- LOUD" if (rate or 0) >= LOG_LOUD_MB_PER_DAY else ""
        stale = " (untouched %.0fd)" % age if age > 7 else ""
        out.append("  %8.1f MB%s  %s%s%s" % (mb, r, path, stale, loud))
    try:
        st_path.write_text(json.dumps(
            {"ts": now, "files": seen}, indent=1))
    except OSError:
        pass
    # the two structural caps, checked rather than assumed
    jd = Path("/etc/systemd/journald.conf")
    try:
        if not re.search(r"^\s*SystemMaxUse=", jd.read_text(), re.M):
            out.append("  journald has NO SystemMaxUse — defaults to 10% of the "
                       "filesystem, which is not a cap on a big disk")
    except OSError:
        pass
    rs = Path("/etc/logrotate.d/rsyslog")
    try:
        t = rs.read_text()
        if "size" not in t:
            out.append("  rsyslog rotates on TIME only — a chatty process has "
                       "the whole window to fill the disk")
    except OSError:
        pass
    return out


def weekly() -> None:
    rows = []
    torn = 0
    for i in range(7):
        d = date.today() - timedelta(days=i)
        p = METRICS_DIR / f"janitor-{d:%Y%m%d}.jsonl"
        if p.exists():
            for l in p.read_text(errors="replace").splitlines():
                if not l.strip():
                    continue
                try:
                    rows.append(json.loads(l))
                except ValueError:
                    # a line torn by a power loss (09-18: 147 NUL bytes) must not
                    # stop the week's review; count it and read the rest
                    torn += 1
    if torn:
        print("[janitor] weekly: skipped %d unreadable metrics line(s)" % torn)
    if not rows:
        print("[janitor] weekly: no samples")
        return
    rows.sort(key=lambda r: r["ts"])
    temps = [r["gpu"]["temp_c"] for r in rows if "temp_c" in r.get("gpu", {})]
    boots = len({r["boot_id"] for r in rows})
    reboots = sum(1 for r in rows if r.get("reboot"))
    cycles = sorted(r["cycle_ms"] for r in rows)
    up: dict = {}
    parked_ct: dict = {}
    restarts: dict = {}
    loops: dict = {}
    in_trans: dict = {}
    for r in rows:
        for u, e in r.get("units", {}).items():
            # 2026-09-27 (t_d5c76f93): deactivating/activating samples are
            # inert for carding — the tick resolves the final state. Count
            # them as their own bucket so the raw JSONL signal (the ~1 s
            # SIGTERM window) is visible in the report, not just in rows.
            if e.get("in_transition"):
                in_trans[u] = in_trans.get(u, 0) + 1
            # 2026-08-24 (t_56818ed9): parked = intentionally stopped
            # (train window / occupancy hold) — excluded from the uptime
            # denominator, not counted as downtime.
            # 2026-09-08 (t_87a3c5b9): the unit side uses row_unit_parked —
            # the SAME helper the tick's probe loop consults, so both
            # counters exclude exactly the same rows.
            if e.get("parked") or row_unit_parked(u, e):
                # 2026-08-24 (t_56818ed9, adversary should-1): parked samples
                # are invisible to the uptime ratio, so print them as a third
                # bucket — render-mode gateway downtime must not read as 100%.
                parked_ct[u] = parked_ct.get(u, 0) + 1
                continue
            up.setdefault(u, [0, 0])
            up[u][1] += 1
            if not e.get("red"):
                up[u][0] += 1
            if e.get("action") == "restart":
                restarts[u] = restarts.get(u, 0) + 1
            if e.get("crash"):
                loops[u] = loops.get(u, 0) + 1
    # 2026-08-24 (t_56818ed9): probe uptime with parked excluded.
    # Probe uptime = green / (green + red), parked samples not counted.
    probe_up: dict = {}
    for name in PROBES:
        green = 0
        red = 0
        present = 0
        unit = PROBE_UNITS.get(name)
        for r in rows:
            pr = r.get("probes", {}).get(name)
            if not isinstance(pr, dict):
                continue
            if pr.get("parked"):
                continue
            # 2026-09-08 (t_87a3c5b9): symmetric exclusion — also skip rows
            # where the backing unit was parked by the unit-side predicate.
            # This is the gap that left 180 crissa-gw reds in the uptime
            # denominator while the unit row read 100%.
            if unit is not None:
                ue = r.get("units", {}).get(unit)
                if isinstance(ue, dict) and row_unit_parked(unit, ue):
                    continue
            if occupancy_parks_probe(name, r.get("gpu_mode") or "normal"):
                continue
            # 2026-08-24 (t_56818ed9, adversary A3): count rows where the
            # probe was ACTUALLY SAMPLED, not len(rows). A probe added or
            # renamed mid-window (asr-audio8 replaced parakeet this week) is
            # absent from older rows; using len(rows) would make exact-100%
            # red impossible and hide the one case the suspect flag exists for.
            present += 1
            if pr.get("ok"):
                green += 1
            else:
                red += 1
        probe_up[name] = (green, red, present)

    def _probe_failed(sample: dict, name: str) -> bool:
        pr = sample.get("probes", {}).get(name)
        # 2026-08-24 (t_56818ed9, adversary nit-6): a probe ABSENT from a row
        # (added mid-window) is unsampled, not passing — same definition as
        # the uptime loop above. Previously defaulted to ok=True.
        if not isinstance(pr, dict):
            return False
        if pr.get("parked"):
            return False
        # 2026-09-08 (t_87a3c5b9): same symmetric exclusion as the uptime
        # loop above — a parked backing unit is not a probe failure.
        unit = PROBE_UNITS.get(name)
        if unit is not None:
            ue = sample.get("units", {}).get(unit)
            if isinstance(ue, dict) and row_unit_parked(unit, ue):
                return False
        if occupancy_parks_probe(name, sample.get("gpu_mode") or "normal"):
            return False
        return not pr.get("ok", True)

    probe_fail = {
        n: sum(1 for r in rows if _probe_failed(r, n))
        for n in PROBES
    }
    # 2026-08-24 (t_56818ed9, adversary A3): suspect-config — a probe red in
    # 100% of the rows it was SAMPLED (green==0, red==present, present>0) is
    # almost certainly a stale/misconfig probe target (e.g. parakeet :8092
    # w/e 08-24), not a real outage. `present` (not len(rows)) handles
    # probes added/renamed mid-window. The exact-100% check means a long
    # real outage that recovered at least once is not flagged.
    # 2026-08-24 (t_56818ed9, adversary nit-4): only call it "stale target"
    # when the backing UNIT reads mostly green in the same window — that is
    # the signature of a dead/wrong port (unit up, probe red). If the unit
    # itself is red, the 100%-red probe is a REAL outage and the banner must
    # not steer the reader away from it.
    suspect: dict = {}
    for name in PROBES:
        green, red, present = probe_up[name]
        if not (present > 0 and green == 0 and red == present):
            continue
        unit = PROBE_UNITS.get(name)
        ug, ut = up.get(unit, (None, None)) if unit else (None, None)
        # unit up most of the window -> stale target; unit also red -> real outage
        if ut is not None and ug is not None and (ug / ut) >= 0.9:
            suspect[name] = f"{red}/{present} (unit {unit} {100*ug/ut:.0f}% up — stale target)"
        elif unit is None or (ut is None):
            # no unit mapping, or unit absent from samples: keep the original
            # banner wording — do not hide a genuinely red probe.
            suspect[name] = f"{red}/{present}"
    lines = [
        f"Window: {rows[0]['ts']} .. {rows[-1]['ts']}  samples={len(rows)}",
        f"GPU temp C: min {min(temps)} / max {max(temps)}" if temps else "GPU temp: n/a",
        f"Boot ids seen: {boots}  reboot events: {reboots}",
        f"Janitor cycle ms p50/p95: {cycles[len(cycles)//2]}/{cycles[int(len(cycles)*.95)]}",
        "",
        "Uptime % (samples green/total, parked excluded):",
    ]
    for u, (g, t) in sorted(up.items()):
        p = parked_ct.get(u, 0)
        it = in_trans.get(u, 0)
        lines.append(
            f"  {u}: {100*g/t:.1f}% ({g}/{t}"
            + (f", {p} parked" if p else "")
            + (f", {it} in-transition (inert, final-state carded)" if it else "")
            + ")")
    lines += ["", "Probe uptime % (green/(green+red), parked excluded; sampled rows):"]
    # 2026-08-24 (t_56818ed9, adversary should-3): parakeet (:8092) was killed
    # 08-07 for vLLM room and nothing listens on 8092 since. Its 100%-red rows
    # (1817/1817 w/e 08-24) were pure config error, and the probe was repointed
    # to asr-audio8 :8096 on 08-24. Those parakeet reds are excluded by design
    # from every future weekly card — explain the gap so the tiny asr-audio8
    # sample next week is not mistaken for a missing probe.
    lines.append("  (a probe that was renamed or retired keeps its old rows out of this table: "
                 "retire it in services.json or janitor.yaml, never by deleting history)")
    for name in sorted(probe_up):
        green, red, present = probe_up[name]
        total = green + red
        # 2026-08-24 (t_56818ed9, adversary A6): a probe never sampled in the
        # window (present==0, e.g. added after the window opened) is not 100%
        # up — label it n/a instead of a misleading 100.0% (0/0).
        if total == 0:
            lines.append(f"  {name}: n/a (0 sampled)")
        else:
            lines.append(f"  {name}: {100 * green / total:.1f}% ({green}/{total})")
    if restarts:
        lines += ["", "Janitor restarts: " + json.dumps(restarts)]
    if loops:
        lines += [
            "",
            f"Crash-loop episodes (>=2 restarts/<{CRASH_LOOP_WINDOW_S//60}min): "
            + json.dumps(loops),
        ]
    bad_probes = {n: c for n, c in probe_fail.items() if c}
    if bad_probes:
        lines += ["Probe failures: " + json.dumps(bad_probes)]
    if suspect:
        lines += [
            "",
            "SUSPECT CONFIG — 100% red, likely stale probe target (verify, not a real outage): "
            + json.dumps(suspect),
        ]
    lines += log_growth()
    lines += [
        "",
        "Protocol: every red row above needs a research card and a mitigation "
        "plan card if one is not already linked.",
    ]
    file_card(f"Weekly PROD review — janitor metrics w/e {date.today():%Y-%m-%d}", "\n".join(lines))


if __name__ == "__main__":
    if "--weekly" in sys.argv:
        weekly()
    else:
        tick()
