"""The restart window: Crissa decides when the house restarts, inside the User's
off-hours; the machine carries it out and checks it; the User is last resort.

the User 2026-09-17: "Can Crissa decide on their self reboots AND her own? She is the
one we will soon have operating COS. She can make that call. User in this
design is meant for hardware reconnects … we need User as Last resort … the very
best time for these will be the window designated by User in Init routine as off
hours. This is when these cards should go up the levels to Crissa for her
mid-night disposal … the kanban chops its wood all from here 8pm to 2am".

Three roles, kept apart:
- **Facts** (this module, no judgement): what is owed a restart and why
  (`os_update.restart_debt`: replaced code still mapped, env packages newer than
  the process), whether the host wants a reboot, how busy the board is, the
  occupancy, whether the User is mid-conversation, whether we are in the window.
- **Crissa rules** on one card per window on her Decide queue (blocked
  `needs_input`, body marker `gate: restart-window`, so her `answer` holds the
  card instead of unblocking it). Words: `go` · `wait [N]` (go once the board
  has ≤ N running workers, default 1) · `later HH:MM` · `park` · `hands` (bring
  it to the User) · `reboot` — a host reboot is never hers alone: it becomes a
  YES/NO on Crissa's list and only the User's YES reboots (the User 2026-09-17,
  "Narrower seems right").
- **The window is the User's**, the one global `site.json` → `training_window`
  Init collects (`lib/krew_hours.py`). Not written → the tick stays idle and
  says so; nothing here guesses.
- **The law vetoes** at execution, per unit, every tick: outside the window,
  the nightly M&S apply running, an occupancy switch in flight, the User
  talking; GNOME session units never; crew while workers run; Crissa's own units
  (gateway, ops brain) last and never while the User talks.

The User is last resort, through Crissa's list (`:8898`, YES/NO): when Crissa rules
`hands`, when a security-relevant restart (replaced native code, or a reboot the
OS asks for) waits two windows with no ruling, and when a restart does not come
back (that one asks for eyes, not a yes).

Kill: `~/.hermes/restart-window.off` or KREW_RESTART_WINDOW=0 (the tick does
nothing).

krew: the per-machine lists (tiers, crew / front-door / API / hermes units, rulers, the ear and speak probes,
the API health URL) come from $KREW_HOME/restart-window.json (config/restart-window.example.json carries the
reference install's values) and the one services list; the window is OFF until `enabled: true` there. The flag
and state files live under $KREW_HOME. Every cut of text an agent reads goes through text_cut.clip.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import krew_window_settings as _KWS
import text_cut

_KH = _KWS.krew_home()
RW = _KWS.load("restart-window")


def _services_fill(rw: dict) -> dict:
    """tiers / crew_units / front_door_units / api_units / hermes_units / unit_ports / wall_units from the one
    services list ($KREW_HOME/services.json, crew/lib/krew_services.py) where restart-window.json leaves them
    empty. The file's own values win. No list = nothing filled."""
    try:
        import importlib.util
        lib = Path(__file__).resolve().parents[2] / "crew" / "lib" / "krew_services.py"
        spec = importlib.util.spec_from_file_location("restart_window_services", lib)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not mod.path().is_file():
            return rw
        sv = mod.restart_window(mod.load())
    except Exception:  # noqa: BLE001 - the window never falls over the list
        return rw
    out = dict(rw)
    for k, v in sv.items():
        if not out.get(k):
            out[k] = v
    return out


RW = _services_fill(RW)
_BUSY = RW.get("busy") or {}

STATE_PATH = Path(os.environ.get("KREW_RESTART_WINDOW_STATE",
                                 str(_KH / "state" / "restart-window.json")))
OFF_FLAG = _KH / "restart-window.off"
GATE_MARK = "gate: restart-window"
AUTHOR = "restart-window"
LIST_ROOT = _KWS.path(RW.get("list_root") or str(_KH / "approvals"), unset="list_root")
ESCALATE_AFTER_WINDOWS = int(os.environ.get("KREW_RESTART_WINDOW_ESCALATE", "2"))
WAIT_DEFAULT_RUNNING = 1

# Order: the house keeps its senses and its voice as long as it can, and Crissa
# restarts herself last (she is the one who ruled).
TIER_NAMES = {1: "back office", 2: "senses", 3: "heavy", 4: "glass", 5: "Crissa herself"}
# krew: unit -> tier comes from restart-window.json `tiers` (else the services list). The reference install's:
#   2 — the live senses
#   3 — the heavy engines
#   4 — the glass
#   5 — Crissa
#   the ONE host gateway (KREW_GATEWAY_TOPOLOGY=host) is Crissa AND the dispatcher: last, like her
#   (the per-profile gateway units are retired and masked since 09-27: no tier names them)
TIER_OF: dict[str, int] = {str(k): int(v) for k, v in (RW.get("tiers") or {}).items()}
# Never restarted by anyone's ruling: the desktop session itself.
NEVER_RE = re.compile(
    r"^(gnome-|org\.gnome\.|at-spi|dconf|gvfs|xdg-|pipewire|wireplumber|dbus|"
    r"evolution-|gcr-|gpg-agent|ssh-agent|snap\.|localsearch|mpris-proxy|filter-chain|"
    r"user@|init\.scope|session-)")
CREW_UNITS = set(RW.get("crew_units") or [])
# Units a conversation with Crissa runs THROUGH: her gateway and brain, and since 2026-09-30 her ear
# (an ear-bridge restart is an ear restart), her body on the wall and the glass's own server. None
# of them restarts while the User is talking to her. (krew: restart-window.json `front_door_units`.)
CRISSA_HER_UNITS = set(RW.get("front_door_units") or [])


# ── the window (the User's global off-hours) ──────────────────────────────
import krew_hours
import krew_hermes as KH  # noqa: E402  where Hermes lives


def _hhmm(s: str) -> int:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", s or "")
    if not m:
        raise ValueError(f"not HH:MM: {s!r}")
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError(f"not HH:MM: {s!r}")
    return h * 60 + mi


# ── what is owed ──────────────────────────────────────────────────────────
def owed_units(debt: dict) -> list[dict]:
    """Rows from os_update.restart_debt, actionable, never the desktop session."""
    out = []
    for unit, row in sorted((debt or {}).get("units", {}).items()):
        if not row.get("actionable") or row.get("session") or NEVER_RE.match(unit):
            continue
        why = []
        if row.get("maps_n"):
            why.append(f"{row['maps_n']} replaced native lib(s) still mapped")
        py = sum(len(v) for v in (row.get("venv_py") or {}).values())
        if py:
            why.append(f"{py} env package(s) newer than the process")
        out.append({"unit": unit, "tier": TIER_OF.get(unit, 1), "why": "; ".join(why) or "replaced code",
                    "security": bool(row.get("maps_n"))})
    out.sort(key=lambda r: (r["tier"], r["unit"]))
    return out


# ── restarts a CONFIG change owes (the User 09-29, heavy-render H0) ─────────
# os_update.restart_debt only sees replaced CODE (native libs still mapped, env
# packages newer than the process). A config a unit reads once at start - the host
# gateway's fallback_model - owed a restart nobody could see: the change sat unapplied.
# One row per unit: {"since": epoch of the change, "why": ..., "by": ...}. A row is owed
# until the unit's own start time passes `since`; then it is simply not owed any more.
CONFIG_OWED = Path(os.environ.get("KREW_RESTART_OWED", str(_KH / "state" / "restart-owed.json")))


class _owed_lock:
    """One writer at a time on the owed file: owe_restart and prune_paid both read, change and
    replace it, and several sessions write rows the same morning. The lock is a sidecar file, so
    the atomic replace of the JSON never swaps the inode being locked."""

    def __init__(self, p: Path):
        self.p = p.with_name(p.name + ".lock")

    def __enter__(self):
        import fcntl
        self.p.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.p, "a")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        self.f.close()
        return False


def _write_owed(p: Path, rows: dict) -> None:
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(rows, f, indent=1)
    os.replace(tmp, p)


def _unit_started(unit: str) -> float | None:
    """A unit's ActiveEnterTimestamp as an epoch, from the USER manager, else the SYSTEM one; None when neither
    knows it or it never started. (The User 2026-10-02: hindsight.service is a system unit - read only from the
    user manager its start was unknown, so its paid row stayed owed and Crissa's card offered a needless restart.)"""
    import subprocess
    for scope in (["--user"], []):
        try:
            v = subprocess.run(["systemctl", *scope, "show", "-P", "ActiveEnterTimestamp", "--timestamp=unix", unit],
                               capture_output=True, text=True, timeout=10).stdout.strip().lstrip("@")
            if v and v != "0":
                return float(v)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return None


def owe_restart(unit: str, why: str, by: str, *, since: float | None = None,
                path: Path | None = None, started=None) -> dict:
    """Record that a config change needs `unit` restarted (atomic write).

    A SECOND reason for a unit that is still owed is ADDED, never written over (the User 2026-09-30:
    steward's 13:40 row for hermes-gateway replaced the 13:28 drain-timeout row, and the record then
    named one change of two). `since` becomes the LATER of the two, so only a restart after both
    changes pays the row; `why` joins both, `by` names both, `whys` keeps each one. A row whose unit
    has already started after it is paid, so a new reason replaces it rather than dragging it along.
    `started(unit)` -> epoch or None (default: systemd's ActiveEnterTimestamp)."""
    import time as _t
    p = path or CONFIG_OWED
    p.parent.mkdir(parents=True, exist_ok=True)
    new = {"since": float(since if since is not None else _t.time()), "why": why, "by": by}
    with _owed_lock(p):
        try:
            rows = json.loads(p.read_text())
        except (OSError, ValueError):
            rows = {}
        if not isinstance(rows, dict):
            rows = {}
        old = rows.get(unit)
        if isinstance(old, dict):
            st = (started or _unit_started)(unit)
            if st is not None and st >= float(old.get("since") or 0):
                old = None                                   # paid: the new reason starts a new row
        if isinstance(old, dict):
            whys = old.get("whys") or [{"why": old.get("why", ""), "by": old.get("by", ""),
                                        "since": float(old.get("since") or 0)}]
            if not any(w.get("why") == why for w in whys):
                whys = whys + [dict(new)]
            bys = []
            for w in whys:
                if w.get("by") and w["by"] not in bys:
                    bys.append(w["by"])
            rows[unit] = {"since": max(float(old.get("since") or 0), new["since"]),
                          "why": " | ALSO: ".join(w["why"] for w in whys),
                          "by": " + ".join(bys), "whys": whys}
        else:
            rows[unit] = new
        _write_owed(p, rows)
    return rows[unit]


def drop_owed(unit: str, why: str, *, path: Path | None = None) -> dict | None:
    """Take ONE row out of the owed file on purpose (under the owe lock), returning the row dropped.
    For a row that should never have been owed - not for a paid row (prune_paid) or a change that
    still needs its restart."""
    p = path or CONFIG_OWED
    with _owed_lock(p):
        try:
            rows = json.loads(p.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(rows, dict) or unit not in rows:
            return None
        row = rows.pop(unit)
        _write_owed(p, rows)
    return dict(row, unit=unit, dropped=why)


# ── a .path / .timer that is switched off is not owed (the User 2026-10-01) ──
# krew-kit-reapply.path is OFF on purpose (its off file, since 09-27): a restart of it is skipped by
# its condition, never goes active, so the post-check read FAILED and the row could never be paid -
# it would sit on the card every window. A switched-off unit is left off the list and logged
# "skipped: switched off", never counted as a failure. Kill: KREW_RESTART_SKIP_OFF=0.
SWITCHABLE_SUFFIXES = (".path", ".timer")
_OFF_SWITCH_RE = re.compile(r"off switch:\s*(~?/[^\s)]+)", re.I)


def skip_off_enabled(env: dict | None = None) -> bool:
    return (env if env is not None else os.environ).get("KREW_RESTART_SKIP_OFF", "1").strip() not in ("0", "false", "no", "off")


def _unit_description(unit: str) -> str:
    import subprocess
    try:
        r = subprocess.run(["systemctl", "--user", "show", "-P", "Description", unit],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _conditions_fail(unit: str) -> str:
    """systemd's own verdict on the unit's Condition*/Assert* lines; '' when they pass or cannot be read."""
    import subprocess
    try:
        r = subprocess.run(["systemd-analyze", "--user", "condition", f"--unit={unit}"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 or not re.search(r"(Conditions|Asserts) failed", out):
        return ""
    lines = [l.strip() for l in out.splitlines() if l.strip().endswith("failed.") and "=" in l]
    return lines[0].split(": ", 1)[-1].rstrip(".") if lines else "a condition fails"


def switched_off(unit: str, *, describe=None, conditions=None, env: dict | None = None) -> str:
    """'' when the unit may restart; else WHY it is switched off: the off file its Description names
    ("off switch: ~/.hermes/x.off") exists, or its own conditions fail. Only .path / .timer units."""
    if not unit.endswith(SWITCHABLE_SUFFIXES) or not skip_off_enabled(env):
        return ""
    m = _OFF_SWITCH_RE.search((describe or _unit_description)(unit) or "")
    if m and Path(os.path.expanduser(m.group(1))).exists():
        return "off file " + m.group(1)
    why = (conditions or _conditions_fail)(unit)
    return ("condition " + why) if why else ""


def split_switched_off(owed: list[dict], **kw) -> tuple[list[dict], list[dict]]:
    """(still owed, skipped) - a skipped row carries `off` = the reason."""
    keep, skipped = [], []
    for r in owed or []:
        why = switched_off(r["unit"], **kw)
        if why:
            skipped.append(dict(r, off=why))
        else:
            keep.append(r)
    return keep, skipped


def prune_enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_RESTART_PRUNE", "1")).strip().lower() not in ("0", "false", "no", "off")


def prune_paid(started, *, path: Path | None = None) -> list[dict]:
    """Drop every row whose unit has STARTED since the row was written (the User 2026-09-30).

    `config_owed` already reads such a row as applied, but nothing took it out of the file, so paid
    rows piled up and read as owed to anyone who opened it (three of four were stale that morning,
    two paid by a boot). The rule is config_owed's own, the other way round: a row stays when the
    unit started BEFORE it, and when the start time cannot be read (None) - unknown is still owed.
    A file that cannot be parsed is left exactly as it is.

    `started(unit)` -> epoch or None. Returns the dropped rows ({unit, since, started, why}).
    """
    p = path or CONFIG_OWED
    if not p.is_file():
        return []
    with _owed_lock(p):
        try:
            rows = json.loads(p.read_text())
        except (OSError, ValueError):
            return []
        if not isinstance(rows, dict):
            return []
        dropped = []
        for unit, row in sorted(rows.items()):
            if not isinstance(row, dict):
                continue
            st = started(unit)
            since = float(row.get("since") or 0)
            if st is not None and st >= since:
                dropped.append({"unit": unit, "since": since, "started": float(st), "why": str(row.get("why") or "")})
        if dropped:
            for d in dropped:
                rows.pop(d["unit"], None)
            _write_owed(p, rows)
    return dropped


def config_owed(started, *, path: Path | None = None) -> list[dict]:
    """Owed rows (same shape as owed_units) for units started BEFORE their config
    changed. `started(unit)` -> epoch or None; None (unreadable) keeps it owed."""
    p = path or CONFIG_OWED
    try:
        rows = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for unit, row in sorted((rows or {}).items()):
        if not isinstance(row, dict) or NEVER_RE.match(unit):
            continue
        st = started(unit)
        if st is not None and st >= float(row.get("since") or 0):
            continue  # restarted since the change: applied
        out.append({"unit": unit, "tier": TIER_OF.get(unit, 1),
                    "why": "config: " + str(row.get("why") or "changed"), "security": False})
    return out


# ── a hermes env the running gateway has not loaded (the User 09-29) ────────
# hermes installs a plugin's Python deps (and an update's) into a NEW env
# generation under ~/.hermes/installs/<id>/environments/<gen>/ and points
# facts.json at it; a running gateway keeps the generation it started with until
# it restarts. 09-28: the Hindsight plugin landed at 09:43, its env at 10:57, and
# the host gateway (up since 05:37) logged `No module named 'hindsight_client'`
# on every memory save until a hand restarted it at 23:38 - owed, and nobody saw.
# Same row shape as config_owed. Kill: KREW_RESTART_HERMES_ENV=0.
HERMES_INSTALLS = Path(os.environ.get("KREW_HERMES_INSTALLS", str(KH.hermes_home() / "installs")))
_HERMES_ENV_RE = re.compile(r"(/\S*?/installs/[^/\s]+/environments/[0-9a-f]+)/")


def hermes_env_owed(main_pid, *, unit: str = "hermes-gateway.service",
                    installs: Path | None = None, proc: Path = Path("/proc")) -> list[dict]:
    """[row] when `unit`'s process maps a hermes env generation other than the one
    its install's facts.json names now; [] when it matches, is unreadable, or is
    ambiguous (unknown is never a restart). `main_pid(unit)` -> pid or None."""
    if os.environ.get("KREW_RESTART_HERMES_ENV", "1") == "0" or NEVER_RE.match(unit):
        return []
    try:
        pid = main_pid(unit)
        maps = (proc / str(int(pid)) / "maps").read_text(errors="replace") if pid else ""
    except (OSError, ValueError, TypeError):
        return []
    running = set(_HERMES_ENV_RE.findall(maps))
    if len(running) != 1:
        return []
    run_env = Path(running.pop())
    facts = run_env.parent.parent / "facts.json"          # installs/<id>/facts.json
    if installs is not None and run_env.parent.parent.parent != Path(installs):
        return []
    try:
        cur = json.loads(facts.read_text())["packages"]["venv"]["environment"]
        since = facts.stat().st_mtime
    except (OSError, ValueError, KeyError, TypeError):
        return []
    cur_env = Path(cur).parent if Path(cur).name == "venv" else Path(cur)
    if cur_env == run_env:
        return []
    return [{"unit": unit, "tier": TIER_OF.get(unit, 1), "security": False,
             "why": (f"hermes env changed after start (plugin deps or an update): running "
                     f"{run_env.name}, current {cur_env.name}"),
             "since": since}]


def merge_owed(*lists: list[dict]) -> list[dict]:
    """One row per unit (reasons joined), in tier order."""
    by: dict[str, dict] = {}
    for rows in lists:
        for r in rows or []:
            if r["unit"] in by:
                by[r["unit"]]["why"] += "; " + r["why"]
                by[r["unit"]]["security"] = by[r["unit"]]["security"] or r.get("security", False)
            else:
                by[r["unit"]] = dict(r)
    return sorted(by.values(), key=lambda r: (r["tier"], r["unit"]))


def reboot_wanted(root: Path = Path("/")) -> dict:
    flag = root / "var/run/reboot-required"
    pkgs = root / "var/run/reboot-required.pkgs"
    return {"wanted": flag.exists(),
            "pkgs": [p for p in (pkgs.read_text().split() if pkgs.is_file() else []) if p]}


# ── rulings ───────────────────────────────────────────────────────────────
RULERS = set(RW.get("rulers") or ["crissa", "user", "human", "dashboard"])
LATER_TIME_RE = re.compile(r"^later\s+(\d{1,2}:\d{2})\b", re.I)


def parse_ruling(text: str) -> tuple[str, str]:
    """(verdict, arg). go|wait|later|reboot|park|hands, or ''."""
    t = re.sub(r"^\s*unblock\b[\s:,.;!-]*", "", text or "", flags=re.I).strip()
    words = re.split(r"[\s:,.;!—-]+", t.lower())
    w = words[0] if words else ""
    if w in ("go", "yes", "try", "green", "now", "restart"):
        return "go", ""
    if w in ("reboot",):
        return "reboot", ""
    if w in ("wait",):
        # `wait N` = N running workers is few enough. Only the word right after
        # `wait`, or an explicit "down to N" / "under N" / "at most N", is that N.
        # 2026-09-17 20:09 Crissa: "wait — 17 restarts owed, 3 workers still running;
        # restart once board is down to 2": the first digit in reach was 17, so
        # the executor went at once with 3 running and logged "go (ruled by crissa)".
        m = (re.search(r"\b(?:down to|under|below|at most|fewer than|less than|<=|<)\s*(\d+)", t, flags=re.I)
             or re.match(r"\s*wait\s+(\d+)\b", t, flags=re.I))      # `wait 2`, not `wait — 17 restarts`
        return "wait", (m.group(1) if m else "")
    if w == "later":
        m = LATER_TIME_RE.match(t)
        return ("later", m.group(1)) if m else ("wait", "")
    if w in ("park", "no", "drop", "skip"):
        return "park", ""
    if w in ("hands", "ask", "user") or w in set(RW.get("hands_words") or []):
        return "hands", t
    return "", ""


def latest_ruling(comments: Iterable[dict], *, since: float = 0.0) -> dict | None:
    """Newest ruling by Crissa / the User on the card, after `since`."""
    best = None
    for c in comments or []:
        author = str(c.get("author") or "").lower()
        ts = float(c.get("created_at") or 0)
        if ts < since or author == AUTHOR or author not in RULERS:
            continue
        verdict, arg = parse_ruling(str(c.get("body") or ""))
        if verdict and (best is None or ts >= best["ts"]):
            best = {"verdict": verdict, "arg": arg, "who": author, "ts": ts,
                    "text": str(c.get("body") or "")}
    return best


# ── the law ───────────────────────────────────────────────────────────────
# ── is the User mid-conversation? (fixed 2026-09-30) ────────────────────────
# The check read `hear.session_open` / `crissa.speaking` from the oracle's
# /v1/house, which carries NEITHER key - so it answered "not busy" on every
# tick, and the window could restart Crissa's gateway under a live conversation.
# The ear bridge publishes what is true on ITS health route (:8194/health:
# session_open, speaking, dispatch_busy). A conversation is also in flight
# while a hands run works, while a line is on the air, and for QUIET_S after
# the ear's last turn (a pause between two sentences is not the end of a talk).
#
# '' = idle. A health body that ANSWERS but has lost these keys is "cannot
# tell", and that holds: a silent rename is how this went blind the first time.
# A bridge that does not answer at all cannot be carrying a conversation.
EAR_BUSY_KEYS = ("session_open", "speaking", "dispatch_busy")
QUIET_S = float(os.environ.get("KREW_RESTART_QUIET_S", "90"))


# A turn can be in flight on the gateway's API with no hands process, no speech and no ear
# event: 2026-09-30 12:27 a restart that passed the three checks drained "0 active agent(s),
# 0 in-flight cron job(s), 1 api_server run(s) ... interrupting" - the steward's board-sweep
# message to Crissa, between two hands calls. The live count is the adapter's own
# `api_server.active_runs` on the host's /health/detailed (Bearer: the default profile's key);
# gateway_state.json carries the same figure heartbeat-stale, so it is never read here.
# It is host-wide (every profile), which is right: a gateway restart takes all of them, and a
# run's model call is on ops. `active_agents` in the same body is NOT used: it comes from the
# persisted runtime record, it read 1 on an idle house on 09-30, and what that one unit is was
# not traced - holding on it could keep the window from ever restarting the gateway.
API_UNITS = set(RW.get("api_units") or [])


def api_runs_enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_RESTART_API_RUNS", "1")).strip().lower() not in ("0", "false", "no", "off")


def api_reason(api: dict | None) -> str:
    """The API term alone. api: {"runs": n} | {"down": True} | {"error": why}; None = not asked."""
    if api is None:
        return ""
    if api.get("down"):
        return ""                        # nothing can be in flight on a gateway that is not listening
    runs = api.get("runs")
    if not isinstance(runs, int) or isinstance(runs, bool):
        return "cannot tell: the gateway's API run count is unreadable (%s)" % (api.get("error") or "no active_runs")
    if runs > 0:
        return "%d run(s) in flight on the gateway's API" % runs
    return ""


def busy_reason(ear: dict | None, *, hands: bool = False, on_air: bool = False,
                quiet_for: float | None = None, quiet_s: float | None = None,
                api: dict | None = None) -> str:
    """Why a Crissa restart must hold right now; '' when nothing is in flight.

    ear       the ear bridge's /health body, None when it did not answer
    hands     a crissa-hands run is working
    on_air    a house line is being spoken
    quiet_for seconds since the ear's last turn, None when unmeasured
    api       the gateway's API run count (see api_reason); None when the unit is not an API unit
    """
    need = QUIET_S if quiet_s is None else quiet_s
    if hands:
        return "a hands run is in flight"
    if on_air:
        return "a line is being spoken"
    if isinstance(ear, dict):
        if not any(k in ear for k in EAR_BUSY_KEYS):
            return "cannot tell: the ear's health carries none of " + "/".join(EAR_BUSY_KEYS)
        for key, word in (("speaking", "Crissa is speaking"), ("dispatch_busy", "a turn is with her brain"),
                          ("session_open", "a hear session is open")):
            if ear.get(key):
                return word
    if quiet_for is not None and quiet_for < need:
        return "the ear was in a turn %d s ago (quiet for %d s needed)" % (quiet_for, need)
    return api_reason(api)


# ── the readers: ONE place, so the window and a session's own waiter cannot drift ──
EAR_HEALTH = str(_BUSY.get("ear_health_url") or "")
EAR_UNIT = str(_BUSY.get("ear_unit") or "")
# what the ear logs when a turn moves: heard him, sent it, answered, spoke
EAR_TURN_EVENTS = {"heard", "dispatch", "reply", "speak", "session_utt", "wake", "ttfs_clause",
                   "gateway_ms", "drain_pending"}


def ear_health() -> dict | None:
    """The ear bridge's own health body; None when it does not answer."""
    import urllib.request
    if not EAR_HEALTH:
        return None
    try:
        with urllib.request.urlopen(EAR_HEALTH, timeout=3) as r:
            got = json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001
        return None
    return got if isinstance(got, dict) else None


def proc_running(*names: str) -> bool:
    """Is a process running whose ARGV names one of these scripts?

    By process, never by taking the run's own lock: testing a house lock could refuse a real run.
    By argv element, never `pgrep -f`: that matches any shell whose command text merely mentions the name.
    """
    me = os.getpid()
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == me:
            continue
        try:
            argv = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(os.path.basename(a.decode("utf-8", "replace")) in names for a in argv[:4]):
            return True
    return False


def ear_quiet_for(window_min: int = 10) -> float | None:
    """Seconds since the ear's last turn event, from its journal; None when unreadable."""
    import subprocess
    if not EAR_UNIT:
        return None
    try:
        out = subprocess.run(["journalctl", "--user", "-u", EAR_UNIT, "--since", f"-{window_min} min",
                              "--no-pager", "-o", "json"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    last = 0.0
    for line in out.splitlines():
        try:
            row = json.loads(line)
            ev = json.loads(row.get("MESSAGE") or "{}").get("event")
            if ev in EAR_TURN_EVENTS:
                last = max(last, int(row["__REALTIME_TIMESTAMP"]) / 1e6)
        except (ValueError, AttributeError, KeyError, TypeError):
            continue
    return (time.time() - last) if last else float(window_min * 60)


def api_runs() -> dict:
    """The gateway's live API run count: {"runs": n} | {"down": True} | {"error": why}."""
    import urllib.error
    import urllib.request
    try:
        api = RW.get("api") or {}
        url = str(api.get("health_detailed_url") or "")
        if not url:
            return {"error": "api.health_detailed_url is not set in restart-window.json"}
        key = os.environ.get(str(api.get("key_env") or "API_SERVER_KEY"), "")
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key} if key else {})
        with urllib.request.urlopen(req, timeout=4) as r:
            body = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ConnectionRefusedError):
            return {"down": True}
        return {"error": f"URLError: {e.reason}"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    runs = (body.get("api_server") or {}).get("active_runs") if isinstance(body, dict) else None
    if not isinstance(runs, int) or isinstance(runs, bool):
        return {"error": "no api_server.active_runs in the body"}
    return {"runs": runs}


# ── a hermes unit also holds while the tree it would load is not whole (the User 2026-09-30) ──
# A restart loads whatever the hermes tree holds at that second. 13:36 the patch stack read
# "broken at 161-..." while steward's card t_7d677880 was landing patches 160/161 in the live
# tree, and the node dependency check (the one `hermes` runs before a TUI / web build) exited 1
# after patch 150 changed six package.json files without an install. A restart then would have
# loaded a half-landed patch.
HERMES_UNITS = set(RW.get("hermes_units") or [])
HERMES_TREE = Path(os.environ.get("KREW_HERMES_TREE") or os.environ.get("HERMES_TREE")
                   or str(KH.hermes_tree())).expanduser()
KREW_ROOT = Path(__file__).resolve().parents[2]
HERMES_TREE_SCRIPT = (_KWS.path(RW["hermes_tree_script"], unset="hermes_tree_script") if RW.get("hermes_tree_script")
                      else KREW_ROOT / "crew" / "scripts" / "hermes-tree.py")
HERMES_PATCH_QUEUE = (_KWS.path(RW["hermes_patch_queue"], unset="hermes_patch_queue") if RW.get("hermes_patch_queue")
                      else KREW_ROOT / "patches")


def tree_check_enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_RESTART_TREE_CHECK", "1")).strip().lower() not in ("0", "false", "no", "off")


def tree_reason(stack: dict | None, node: dict | None) -> str:
    """The tree term alone. stack = hermes-tree.py stack_check()'s dict (None = not asked);
    node = {"rc": n, "why": text} from the node dependency check (None = not asked)."""
    if stack is not None:
        if stack.get("error"):
            return "cannot tell: the hermes patch stack check failed (%s)" % stack["error"]
        if not stack.get("ok") and stack.get("why") != "off":
            where = f" at {stack['broke_at']}" if stack.get("broke_at") else ""
            what = stack.get("why") or ("differs: " + ", ".join(stack.get("differs") or []))
            return "the hermes patch stack is broken%s (%s)" % (where, what)
    if node is not None:
        if node.get("rc") is None:
            return "cannot tell: the hermes node dependency check did not run (%s)" % (node.get("why") or "?")
        if node["rc"] != 0:
            return "the hermes node dependency check fails (%s)" % (node.get("why") or f"rc={node['rc']}")
    return ""


def hermes_stack() -> dict:
    """Does krew's patch queue, reversed from the live Hermes tree, land exactly on the tree's upstream base?
    hermes-tree.py stack_check(tree, queue); a tree it cannot read (not a git checkout, no origin/main) is
    "cannot tell", never "broken"."""
    import importlib.util
    if not HERMES_TREE_SCRIPT.is_file():
        return {"ok": True, "note": f"not checked: no hermes-tree.py at {HERMES_TREE_SCRIPT} "
                                    "(hermes_tree_script in restart-window.json)"}
    try:
        spec = importlib.util.spec_from_file_location("krew_hermes_tree", HERMES_TREE_SCRIPT)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        got = m.stack_check(HERMES_TREE, HERMES_PATCH_QUEUE)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not got.get("ok") and got.get("why") in ("no merge-base", "read-tree HEAD failed"):
        return {"ok": False, "error": f"{got['why']} in {HERMES_TREE} (it needs a git checkout of Hermes with "
                                      "origin/main; KREW_HERMES_TREE names another)"}
    return got


_HERMES_BUILD_ENV = r"""
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
import hermes_bootstrap  # noqa: F401  (hermes's own dependencies; the launcher does the same)
from hermes_cli.source_build import source_build_env
from hermes_cli.main_desktop import _desktop_dist_exists, _desktop_packaged_executable
env = source_build_env()
d = root / "apps/desktop"
desktop = _desktop_dist_exists(d) or _desktop_packaged_executable(d) is not None
print(json.dumps({"env": dict(env), "workspaces": ["ui-tui", "web"] + (["apps/desktop"] if desktop else [])}))
"""


def hermes_build_env(tree: Path | None = None) -> dict:
    """{env, workspaces} exactly as hermes builds them for its own node step (source_build_env +
    prepare_launch_dependencies' workspace set), from hermes's own interpreter; {"error": ...} if not.

    The node check's receipt key hashes the node and npm versions, the args (the workspace set) and
    every npm_config_* variable, so the check means nothing run any other way: 13:36 it read "stale"
    under the box's node 22.23 while hermes, on its own node 26.7 and env, read "reusing completed
    install" on the same tree."""
    import subprocess
    if "PYTEST_CURRENT_TEST" in os.environ:
        return {"error": "never under pytest: this imports hermes_bootstrap against the live home"}
    try:
        import hermes_runtime
        py = hermes_runtime.runtime_python()
    except Exception as e:  # noqa: BLE001
        return {"error": f"no hermes runtime: {type(e).__name__}"}
    if not py:
        return {"error": "no hermes runtime"}
    t = tree or HERMES_TREE
    try:
        r = subprocess.run([py, "-I", "-c", _HERMES_BUILD_ENV, str(t)], capture_output=True, text=True,
                           timeout=60, cwd=str(t))
        got = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    return got if isinstance(got, dict) and got.get("env") else {"error": "no env from hermes"}


def hermes_node_deps(tree: Path | None = None, build: dict | None = None) -> dict:
    """The node dependency check exactly as hermes runs it before a TUI / web build - its node, its
    env, its workspace set - with installs OFF: it reads, never installs. rc 0 = reusing the install."""
    import shutil
    import subprocess
    t = tree or HERMES_TREE
    script = t / "scripts" / "build" / "node-deps.mjs"
    if not script.is_file():
        return {"rc": 0, "why": "no node-deps.mjs"}
    b = build if build is not None else hermes_build_env(t)
    if b.get("error"):
        return {"rc": None, "why": b["error"]}
    env = b["env"]
    ws = [w for w in b.get("workspaces") or [] if (t / w / "package.json").is_file()]
    if not ws:
        return {"rc": 0, "why": "no node workspaces"}
    node = shutil.which("node", path=env.get("PATH", ""))
    if not node:
        return {"rc": None, "why": "no node on hermes's PATH"}
    cmd = [node, str(script), "--source", str(t), "--reuse", "--no-install"]
    for w in ws:
        cmd += ["--workspace", w]
    try:
        r = subprocess.run(cmd, cwd=str(t), capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return {"rc": None, "why": f"{type(e).__name__}"}
    err = [l.strip() for l in (r.stderr or r.stdout).splitlines() if l.strip().startswith("Error:")]
    return {"rc": r.returncode, "why": (err[-1][len("Error: "):] if err else "")}


def tree_now(unit: str | None) -> str:
    """The tree term for `unit`: '' unless it is a hermes unit and the tree is not whole."""
    if unit not in HERMES_UNITS or not tree_check_enabled():
        return ""
    return tree_reason(hermes_stack(), hermes_node_deps())


def busy_now(unit: str | None = None, *, quiet_s: float | None = None) -> str:
    """THE one-liner (the User 2026-09-30): reads everything, applies the rule, returns the reason
    ('' = go). Pass the unit you are about to restart: the gateway's API run count is asked only for
    API_UNITS. The window's tick, its executor and any session's own waiter all call this, so a
    fifth term cannot be added to one and missed in another.

        import restart_window as rw
        why = rw.busy_now("hermes-gateway.service")      # '' -> restart now
    """
    api = api_runs() if (unit in API_UNITS and api_runs_enabled()) else None
    why = busy_reason(ear_health(), hands=proc_running(*(_BUSY.get("hands_procs") or [])),
                      on_air=proc_running(*(_BUSY.get("speak_procs") or [])),
                      quiet_for=ear_quiet_for(), quiet_s=quiet_s, api=api)
    return why or tree_now(unit)


def recheck_enabled(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_RESTART_RECHECK", "1")).strip().lower() not in ("0", "false", "no", "off")


def run_units(units: list[dict], restart_one, busy=None) -> tuple[list[dict], list[dict]]:
    """Restart `units` in order, asking `busy()` before EACH one (the User 2026-09-30).

    The decision to run is made once, at the tick; a run of several units with post-checks takes
    minutes, and a conversation that starts after the first unit used to have the rest restarted
    under it. `busy(row)` returns a reason for THAT unit ('' = go on). On a reason the run STOPS there: that unit and
    every one after it are returned as held, untouched - their rows stay owed, and the next tick
    inside the window decides again.

    Returns (results, held): results = what restart_one(row) returned for each unit it ran,
    held = the rows not run, each with `deferred` saying why.
    """
    results: list[dict] = []
    for i, row in enumerate(units):
        why = busy(row) if busy is not None else ""
        if why:
            return results, [dict(r, deferred=f"stopped before it: {why}") for r in units[i:]]
        results.append(restart_one(row))
    return results, []


def vetoes(facts: dict) -> list[str]:
    """Reasons nothing may restart this tick."""
    out = []
    if not facts.get("in_window"):
        out.append(f"outside the User's off-hours ({(facts.get('hours') or {}).get('window')})")
    if facts.get("nightly_active"):
        out.append("the nightly M&S apply is running")
    if facts.get("occupancy_pending"):
        out.append(f"an occupancy switch is in flight ({facts['occupancy_pending']})")
    if facts.get("user_busy"):
        out.append("the User is mid-conversation")
    return out


def plan(owed: list[dict], facts: dict) -> tuple[list[dict], list[dict]]:
    """(run now, deferred with reason) in tier order."""
    now, later = [], []
    running = int((facts.get("kanban") or {}).get("running") or 0)
    for r in owed:
        u = r["unit"]
        if NEVER_RE.match(u):
            continue
        if u in CREW_UNITS and running > 0:
            later.append(dict(r, deferred=f"{running} kanban worker(s) running on crew"))
        elif u in CRISSA_HER_UNITS and facts.get("user_busy"):
            later.append(dict(r, deferred="the User is talking to Crissa"))
        elif u in API_UNITS and facts.get("api_busy"):
            later.append(dict(r, deferred=str(facts["api_busy"])))
        elif u in HERMES_UNITS and facts.get("tree_busy"):
            later.append(dict(r, deferred=str(facts["tree_busy"])))
        else:
            now.append(r)
    return now, later


def wait_satisfied(arg: str, facts: dict) -> bool:
    n = int(arg) if str(arg).isdigit() else WAIT_DEFAULT_RUNNING
    return int((facts.get("kanban") or {}).get("running") or 0) <= n


def later_reached(arg: str, now: dt.datetime) -> bool:
    try:
        t = _hhmm(arg)
    except ValueError:
        return True
    m = now.hour * 60 + now.minute
    # compare inside the window: a wrapping window's post-midnight minutes rank later
    return (m if m >= 12 * 60 else m + 24 * 60) >= (t if t >= 12 * 60 else t + 24 * 60)


# ── the card ──────────────────────────────────────────────────────────────
def card_title(owed: list[dict], reboot: dict) -> str:
    bits = []
    if owed:
        bits.append(f"{len(owed)} restart(s) owed")
    if reboot.get("wanted"):
        bits.append("host reboot wanted")
    return "Restart window: " + (", ".join(bits) or "nothing owed")


def card_body(facts: dict) -> str:
    owed = facts.get("owed") or []
    kb = facts.get("kanban") or {}
    lines = [
        GATE_MARK,
        "Crissa: this is your call (Chief of Staff). Do not implement — the restart-window "
        "executor acts on your ruling, in tier order, checks each restart, and restarts you last.",
        "",
        f"Window {(facts.get('hours') or {}).get('window')} (the User's off-hours, off_hours in krew.yaml). Board: "
        f"{kb.get('running', '?')} running, {kb.get('ready', '?')} ready. Occupancy {facts.get('occupancy') or '?'}.",
        "",
        "Owed:",
    ]
    for r in owed:
        lines.append(f"- [{TIER_NAMES[r['tier']]}] {r['unit']} — {r['why']}")
    if not owed:
        lines.append("- none")
    rb = facts.get("reboot") or {}
    if rb.get("wanted"):
        lines += ["", f"Host reboot wanted by: {', '.join(rb.get('pkgs') or ['?'])}. "
                      "`reboot` asks the User (a host reboot takes their YES; ≈3–5 min dark)."]
    lines += [
        "",
        "Rule with one word (team_board answer):",
        "- `go` — restart the owed units now (crew waits for its workers; you go last)",
        "- `wait` / `wait 2` — go once the board is down to that many running workers",
        "- `later 01:00` — go at that time inside the window",
        "- `reboot` — ask the User to let the host reboot (only their YES reboots)"
        + ("" if rb.get("wanted") else " (nothing asks for one)"),
        "- `park` — not this window",
        "- `hands` — bring it to the User (last resort: hardware, or a call you will not make)",
    ]
    return "\n".join(lines)


# ── the User, last resort ─────────────────────────────────────────────────
def list_ask(title: str, body: str, *, card_id: str = "", kind: str = "ask",
             root: Path = LIST_ROOT, now: float | None = None) -> str:
    now = time.time() if now is None else now
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    item_id = f"{kind}-restart-window-{slug}-{int(now)}"
    if len(title) > 160:
        body = f"Full title: {title}\n\n{body}"
    item = {"id": item_id, "kind": kind,
            "title": text_cut.clip(title, 160, label="list-title", where="the first line of this item's body"),
            "body": text_cut.clip(body, 5000, label="restart-window-list-body"),
            "steps": [], "notes": [], "options": ["yes", "no"] if kind == "ask" else [],
            "source": AUTHOR, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))}
    if card_id:
        item["card_id"] = card_id
    d = root / "pending"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{item_id}.tmp"
    tmp.write_text(json.dumps(item, indent=2) + "\n")
    os.replace(tmp, d / f"{item_id}.json")
    return item_id


def list_answer(item_id: str, root: Path = LIST_ROOT) -> str:
    """'yes' | 'no' | '' from Crissa's list."""
    f = root / "decided" / f"decided_{item_id}.json"
    try:
        return str(json.loads(f.read_text()).get("choice") or "").lower()
    except Exception:  # noqa: BLE001
        return ""


# ── state ─────────────────────────────────────────────────────────────────
def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, path)


def enabled() -> bool:
    return bool(RW.get("enabled")) and (os.environ.get("KREW_RESTART_WINDOW", "1") != "0") and not OFF_FLAG.exists()


# ── the tick (pure decision; the CLI does the I/O) ────────────────────────
def current_ruling(state: dict, card: dict | None, key: str) -> dict | None:
    """Crissa's (or the User's) ruling for this window. The card's newest ruling
    wins; one already copied into state survives the card (the 00:05 board roll
    re-mints open cards without their comments)."""
    on_card = None
    if card:
        on_card = latest_ruling(card.get("comments") or [], since=float(card.get("created_at") or 0))
    kept = state.get("ruling") if state.get("ruling_window") == key else None
    if on_card and (not kept or float(on_card.get("ts") or 0) >= float(kept.get("ts") or 0)):
        return on_card
    return kept


def decide(state: dict, facts: dict, card: dict | None, now: dt.datetime) -> dict:
    """What to do this tick. Returns {"action": ..., ...}; never performs it.

    actions: idle · unset (no off-hours) · close · file · refile (card lost) ·
             execute · escalate (ask the User) · veto · post_reboot
    """
    owed = facts.get("owed") or []
    reboot = facts.get("reboot") or {}
    key = facts.get("window_key")
    if state.get("rebooting"):
        return {"action": "post_reboot"}
    if state.get("executing"):
        return {"action": "idle", "why": "executor running"}
    if (facts.get("hours") or {}).get("source") != "init":
        return {"action": "unset", "why": (facts.get("hours") or {}).get("why") or "off-hours not set"}
    if state.get("closed_window") == key and facts.get("in_window"):
        return {"action": "idle", "why": "closed for this window"}
    card_open = bool(card and card.get("open"))
    if not owed and not reboot.get("wanted"):
        return {"action": "close", "why": "nothing owed"} if card_open else {"action": "idle"}
    if not facts.get("in_window"):
        return {"action": "idle", "why": "outside the User's off-hours"}
    if state.get("window_key") != key:
        return {"action": "file", "window_key": key,
                "missed": int(state.get("missed_windows") or 0) + (1 if card_open and not state.get("ruling") else 0)}
    ruling = current_ruling(state, card, key)
    if not card_open and not ruling:
        return {"action": "refile", "window_key": key, "why": "the window card is gone (board roll?)"}
    answer = list_answer(state["list_item"]) if state.get("list_item") else ""
    asked = state.get("list_asked")
    if answer == "yes":
        if asked == "reboot":
            if int((facts.get("kanban") or {}).get("running") or 0) > 0:
                return {"action": "idle", "why": "the User said YES to a reboot; waiting for running workers"}
            blocked = vetoes(facts)
            if blocked:
                return {"action": "veto", "why": "; ".join(blocked)}
            return {"action": "execute", "reboot": True, "units": [], "deferred": [],
                    "ruling": {"verdict": "reboot", "who": "user"}}
        if not ruling or ruling.get("verdict") in ("hands", "reboot"):
            ruling = {"verdict": "go", "arg": "", "who": "user"}
    elif answer == "no":
        return {"action": "close", "why": "the User said NO"}
    if not ruling:
        security = reboot.get("wanted") or any(r.get("security") for r in owed)
        if (security and int(state.get("missed_windows") or 0) >= ESCALATE_AFTER_WINDOWS - 1
                and not state.get("list_item")):
            return {"action": "escalate", "why": "no ruling on a security-relevant restart for "
                    f"{ESCALATE_AFTER_WINDOWS} windows", "ask": "go"}
        return {"action": "idle", "why": "waiting on Crissa"}
    v = ruling["verdict"]
    if v == "park":
        return {"action": "close", "why": f"parked by {ruling['who']}"}
    if v in ("hands", "reboot"):
        if state.get("list_item"):
            return {"action": "idle", "why": "waiting on the User"}
        return {"action": "escalate", "ask": "reboot" if v == "reboot" else "go",
                "why": ("Crissa wants the host rebooted; a reboot takes the User's YES" if v == "reboot"
                        else f"{ruling['who']} asked for the User"),
                "text": ruling.get("text", "")}
    blocked = vetoes(facts)
    if blocked:
        return {"action": "veto", "why": "; ".join(blocked)}
    if v == "wait" and not wait_satisfied(ruling.get("arg", ""), facts):
        return {"action": "idle", "why": f"wait: board busy ({(facts.get('kanban') or {}).get('running')} running)"}
    if v == "later" and not later_reached(ruling.get("arg", ""), now):
        return {"action": "idle", "why": f"later {ruling.get('arg')}"}
    run_now, deferred = plan(owed, facts)
    if not run_now:
        return {"action": "idle", "why": "; ".join(d["deferred"] for d in deferred) or "nothing runnable"}
    return {"action": "execute", "reboot": False, "units": run_now, "deferred": deferred, "ruling": ruling}
