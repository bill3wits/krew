"""Krew Daily — the one report the User opens (the reference install's HOUSE DAILY REPORT doc, 2026-09-17).

No LLM. Adapters read the house's own STRUCTURED files and return a slip each; the
consolidator adds what no routine can say about itself:

  * a roll-call from a registry (`config/krew-window-schedule.json`: the window jobs
    + a `watch` list), so a routine that never wrote anything is still "did not
    report", and one parked by the mode is "parked", not an alarm;
  * effect probes — a routine that ran on time and failed at its purpose is caught by
    the page's own read (reds vs restarts, cards closed vs opened), not by its word;
  * measured beats carried: every fact has `measured_at` and `source`, and a pasted
    fact never outranks a probe, however new the file that carries it.

Built by the window ticker just before the User's off-hours close (the house day runs
close to close), and on demand: `scripts/krew-daily.py`. the User 2026-09-17: "one cogent
system of parts with one cogent output for User. Concise."
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
sys.path.insert(0, str(_LIB.parents[1] / "window" / "lib"))   # krew: krew_hours lives with the window
import krew_hours  # noqa: E402
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)
import krew_hermes as KH  # noqa: E402  where Hermes lives
KREW_MEMORY_ROOT = os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory")  # krew: the house kept this on a data disk

ROOT = _LIB.parent
HOME = Path.home()
RANK = {"ok": 0, "parked": 0, "skipped": 1, "unknown": 2, "warn": 3, "bad": 4}
CARRIED_AFTER_H = 3.0        # a fact read from a file older than this is carried, not measured
DISK_WARN, DISK_BAD = 80, 90
_HANDS = __import__("re").compile(r"\b(sudo|root kernel|needs human|password|passphrase|polkit|assent|physical|plug|unplug|hands|in the room)\b", __import__("re").I)
CONTENDED_WATCH = 50         # % of a brain's working time spent sharing the GPU before it is a watch row
ENGINE_DROP = 0.10           # a brain this much under its own week is a watch row (day-to-day p75 moves ~3 %)
READY_STUCK_H, STUCK_BLOCKED_H, STUCK_BLOCKED_N = 6, 48, 10     # what "the board is stuck" means
IDLE_OK_HEALTH = {"stamp_ui", "comfy"}   # the trail itself calls these IDLE between sessions
# A health key the nightly trail called bad is re-measured when the page is built: the
# trail is hours old by then (09-17: hindsight read RED on the page 3 h after its fix).
# krew: the units come from the one services list ($KREW_HOME/services.json) or KREW_SITE_UNITS / KREW_HEALTH_UNITS;
# the house named its own eight here.
_PORTABLE_HEALTH_UNITS = {"ops": "ops.service", "crew": "crew.service",
                          "audio8_asr": "audio8-asr.service"}


def _services_site_units() -> dict:
    """The one services list ($KREW_HOME/services.json, crew/lib/krew_services.py) as site units; {} without it."""
    try:
        import importlib.util
        lib = Path(__file__).resolve().parent / "krew_services.py"
        spec = importlib.util.spec_from_file_location("krew_daily_services", lib)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.site_units(mod.load()) if mod.path().is_file() else {}
    except Exception:  # noqa: BLE001 - the Daily never falls over the list; site-units.json still applies
        return {}


def _load_health_units() -> dict:
    out = dict(_PORTABLE_HEALTH_UNITS)
    raw = os.environ.get("KREW_HEALTH_UNITS")
    if raw:
        out.update(json.loads(raw))
        return out
    out.update(_services_site_units().get("health_units") or {})
    path = Path(os.environ.get("KREW_SITE_UNITS") or (HOME / ".krew/site-units.json"))
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.update(data.get("health_units") or {})
        except (OSError, json.JSONDecodeError):
            pass
    return out


HEALTH_UNITS = _load_health_units()

PROBE_OF = {"kanban-loop": "board"}      # whose purpose an adapter's effect probe speaks for


@dataclass
class Sources:
    inbox: Path = Path(os.path.expanduser(os.environ.get("KREW_DELIVERABLES") or "~/Desktop/krew-deliverables"))
    janitor_dir: Path = Path(os.path.expanduser(os.environ.get("KREW_METRICS_ROOT") or "~/.krew/metrics"))
    battery_dir: Path = Path((KREW_MEMORY_ROOT + "/ms/logs"))
    battery_latest: Path = HOME / ".local/state/house/health/latest.json"
    ms_trail: Path = Path((KREW_MEMORY_ROOT + "/ms/reports/ms-trail-LATEST.json"))
    improvements: Path = Path((KREW_MEMORY_ROOT + "/ms/improvements"))
    keeper: Path = Path((KREW_MEMORY_ROOT + "/keeper/keeper.jsonl"))
    dream_log: Path = Path((KREW_MEMORY_ROOT + "/dream-night.log"))
    t0_hits: Path = KH.hermes_home() / "security/tier0-hits.jsonl"
    kanban_home: Path = KH.kanban_dir()
    tick_state: Path = KH.hermes_home() / "krew-window-tick.json"
    schedule: Path = Path(os.environ.get("KREW_WINDOW_SCHEDULE") or os.path.join(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"), "window-schedule.json"))   # krew: the window ticker's file
    temporaries: Path = ROOT / "config" / "krew-daily-temporaries.json"
    gpu_mode: Path = KH.hermes_home() / "krew-gpu-mode"
    hold: Path = Path("/tmp/krew-train-hold")
    reboot_flag: Path = Path("/var/run/reboot-required")
    announce_dump: Path = KH.hermes_home() / "krew-announce-dump.jsonl"
    announce_card: Path = KH.hermes_home() / "krew-announce-overflow-card.json"
    data_mount: Path = Path("/data")
    site: dict | None = None
    unit_show: Callable[[str, str], dict] | None = None     # tests inject; None = systemctl
    apt_check: Callable[[], tuple | None] | None = None
    hermes_tree_status: Callable[[], dict] | None = None   # tests inject; None = scripts/hermes-tree.py status
    hooks_doctor: Callable[[str], tuple] | None = None     # tests inject; None = `hermes -p <p> hooks doctor`
    self_healing: Callable[[dt.datetime], dict] | None = None  # tests inject; None = lib/self_claims.healing (live logs)
    cua_check: Callable[[], dict] | None = None                 # tests inject; None = lib/cua_doctor.check


# --- small helpers ------------------------------------------------------------

def _iso(ts: float | dt.datetime | None) -> str:
    if ts is None:
        return ""
    if isinstance(ts, (int, float)):
        ts = dt.datetime.fromtimestamp(ts)
    return ts.isoformat(timespec="minutes")


def _ago(now: dt.datetime, then: dt.datetime | float | None) -> str:
    if then is None:
        return "never"
    if isinstance(then, (int, float)):
        then = dt.datetime.fromtimestamp(then)
    m = int((now - then).total_seconds() // 60)
    if m < 90:
        return f"{max(m, 0)} min ago"
    if m < 48 * 60:
        return f"{m // 60} h ago"
    return f"{m // 1440} d ago"


def _hhmm(then: dt.datetime | float | None) -> str:
    if then is None:
        return "—"
    if isinstance(then, (int, float)):
        then = dt.datetime.fromtimestamp(then)
    return then.strftime("%m-%d %H:%M")


def _jsonl(path: Path, *, tail_bytes: int = 0) -> list[dict]:
    """Rows of a jsonl file; NUL-torn or half-written lines are skipped, not fatal."""
    try:
        with path.open("rb") as fh:
            if tail_bytes:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - tail_bytes))
            raw = fh.read()
    except OSError:
        return []
    rows = []
    for line in raw.replace(b"\x00", b"").splitlines():
        try:
            j = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(j, dict):
            rows.append(j)
    return rows


def _parse_ts(v: Any) -> dt.datetime | None:
    if isinstance(v, (int, float)) and v > 0:
        return dt.datetime.fromtimestamp(v)
    if isinstance(v, str) and v:
        try:
            d = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d.astimezone().replace(tzinfo=None) if d.tzinfo else d
        except ValueError:
            return None
    return None


def slip(routine: str, status: str, headline: str, *, ran_at: dt.datetime | None = None,
         facts: list[dict] | None = None, broken: list[dict] | None = None,
         changed: list[dict] | None = None, needs_user: list[dict] | None = None,
         unverified: list[str] | None = None, detail: str = "") -> dict:
    return {"routine": routine, "status": status, "headline": headline[:160],
            "ran_at": _iso(ran_at), "facts": facts or [], "broken": broken or [],
            "changed": changed or [], "needs_user": needs_user or [],
            "unverified": unverified or [], "detail": detail}


def fact(k: str, v: Any, measured_at: dt.datetime | None, now: dt.datetime, *, status: str = "ok",
         source: str | None = None) -> dict:
    if source is None:
        age_h = (now - measured_at).total_seconds() / 3600 if measured_at else 1e9
        source = "measured" if age_h <= CARRIED_AFTER_H else "carried"
    return {"k": k, "v": v, "status": status, "measured_at": _iso(measured_at), "source": source}


def merge_facts(facts: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """One fact per key. A measured fact beats a carried one whatever their ages; among
    the same kind the newest wins. Disagreements are returned, both sides cited."""
    best: dict[str, dict] = {}
    conflicts: list[dict] = []
    for f in facts:
        cur = best.get(f["k"])
        if cur is None:
            best[f["k"]] = f
            continue
        if cur["v"] != f["v"]:
            conflicts.append({"k": f["k"], "a": cur, "b": f})
        a = (cur["source"] == "measured", cur.get("measured_at") or "")
        b = (f["source"] == "measured", f.get("measured_at") or "")
        if b > a:
            best[f["k"]] = f
    for c in conflicts:
        c["kept"] = best[c["k"]]
    return best, conflicts


def day_bounds(now: dt.datetime, site: dict | None) -> tuple[dt.datetime, dict]:
    """(start of the house day being reported, the off-hours record). The day runs from
    one window close to the next; with no window it is the last 24 h and says so."""
    oh = krew_hours.off_hours(site)
    if oh["source"] != "init":
        return now - dt.timedelta(hours=24), oh
    end = oh["end"]
    close = now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    if close > now:
        close -= dt.timedelta(days=1)
    if (now - close) < dt.timedelta(hours=6):      # built just after a close: report the day that ended
        close -= dt.timedelta(days=1)
    return close, oh


# --- adapters -----------------------------------------------------------------

def a_janitor(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    rows: list[dict] = []
    for d in (since.date(), now.date()):
        rows += _jsonl(s.janitor_dir / f"janitor-{d:%Y%m%d}.jsonl")
    rows = [r for r in rows if (_parse_ts(r.get("ts")) or dt.datetime.min) >= since]
    if not rows:
        return slip("janitor", "unknown", "no janitor ticks in the day", unverified=["janitor wrote no rows"])
    last = rows[-1]
    last_ts = _parse_ts(last.get("ts"))
    reds: dict[str, int] = {}
    acts = {"restart": 0, "card": 0, "would": 0}
    for r in rows:
        for unit, e in (r.get("units") or {}).items():
            if not isinstance(e, dict):
                continue
            if e.get("red") and not e.get("parked"):
                reds[unit] = reds.get(unit, 0) + 1
            if e.get("action") in acts:
                acts[e["action"]] += 1
            if e.get("would_restart"):
                acts["would"] += 1
    broken = []
    for unit, e in (last.get("units") or {}).items():
        if isinstance(e, dict) and e.get("red") and not e.get("parked"):
            first = next((_parse_ts(r.get("ts")) for r in reversed(rows)
                          if not ((r.get("units") or {}).get(unit) or {}).get("red")), None)
            broken.append({"status": "bad", "what": f"{unit} is down ({e.get('active')}).",
                           "evidence": f"Red in {reds.get(unit, 1)} janitor samples today; {e.get('nrestarts', 0)} systemd restarts.",
                           "since": _hhmm(first) if first else "all day", "owner": "unowned"})
    temp = max((int((r.get("gpu") or {}).get("temp_c") or 0) for r in rows), default=0)
    total_reds = sum(reds.values())
    handled = acts["restart"] + acts["card"]
    status = "bad" if broken else "ok"
    probe = ""
    if total_reds and not handled:
        status = "bad" if status != "bad" else status
        probe = f"{total_reds} red samples, none restarted or carded"
        broken.append({"status": "bad", "what": "The janitor saw red units and did nothing about them.",
                       "evidence": f"{total_reds} red samples ({', '.join(sorted(reds)[:4])}); 0 restarts, 0 cards"
                                   + (f"; {acts['would']} it would have restarted (dry run)." if acts["would"] else "."),
                       "since": "today", "owner": "unowned"})
    head = (f"{len(rows)} ticks; {total_reds} red samples, {acts['restart']} restarts, {acts['card']} cards"
            + (f", {acts['would']} would-restart" if acts["would"] else ""))
    return slip("janitor", status, probe or head, ran_at=last_ts, broken=broken, facts=[
        fact("janitor.red_samples", total_reds, last_ts, now),
        fact("janitor.restarts", acts["restart"], last_ts, now),
        fact("janitor.would_restart", acts["would"], last_ts, now),
        fact("gpu.temp_max_c", temp, last_ts, now),
    ])


def a_disk(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    try:
        u = shutil.disk_usage(s.data_mount)
    except OSError as e:
        return slip("disk", "unknown", f"cannot read {s.data_mount}: {e}")
    pct = round(100 * u.used / u.total)
    free_g = u.free // 2**30
    status = "bad" if pct >= DISK_BAD else "warn" if pct >= DISK_WARN else "ok"
    broken = [] if status == "ok" else [{
        "status": status, "what": f"{s.data_mount} at {pct} %, {free_g} G free.",
        "evidence": f"Thresholds live here only: watch at {DISK_WARN} %, broken at {DISK_BAD} %.",
        "since": "trend", "owner": "you" if status == "bad" else "unowned"}]
    return slip("disk", status, f"{s.data_mount} {pct} % used, {free_g} G free", ran_at=now, broken=broken,
                facts=[fact("disk.data_pct", pct, now, now, status=status)])


def a_battery(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    nights = sorted(s.battery_dir.glob("battery-nightly-*.json")) if s.battery_dir.is_dir() else []
    path = nights[-1] if nights else s.battery_latest
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return slip("battery", "unknown", "no battery result readable", unverified=["nightly battery result missing"])
    summ = next((d[k] for k in ("nightly", "summary", "compare") if isinstance(d.get(k), dict)), d)
    ts = _parse_ts(d.get("ts")) or dt.datetime.fromtimestamp(path.stat().st_mtime)
    worst = str(summ.get("worst") or d.get("worst") or "?")
    fails = [f"{r.get('name')}" for r in (d.get("results") or []) if str(r.get("status")) == "FAIL"]
    new_n = summ.get("new_n")
    stale = (now - ts) > dt.timedelta(hours=30)
    if stale:
        return slip("battery", "unknown", f"last battery ran {_ago(now, ts)}", ran_at=ts,
                    unverified=[f"the nightly battery has not run since {_hhmm(ts)}"])
    if worst != "FAIL":
        return slip("battery", "ok", f"battery {worst}", ran_at=ts, detail=str(path))
    if summ.get("baseline"):
        # the first run under the new-vs-known compare has no night before it: nothing is "new" yet
        repos = summ.get("repos") or {}
        failing = sum(int(v.get("failing") or 0) for v in repos.values() if isinstance(v, dict))
        st, what = "bad", f"Battery FAIL: {failing} failing tests, {summ.get('known_red_n', 0)} of them known red; {len(summ.get('checks_failing') or [])} checks."
        ev = ("Baseline run: there is no earlier night to compare, so none of this is 'new' yet. Checks: "
              + ", ".join(str(c).split("/")[-1] for c in (summ.get("checks_failing") or [])[:6]) + ". "
              + "; ".join(f"{k} {v.get('failing')}" for k, v in repos.items() if isinstance(v, dict) and v.get("failing")) + ".")
    elif new_n is None:
        st, what = "bad", f"Nightly battery FAIL: {len(fails)} checks."
        ev = f"{', '.join(fails[:6])}. No new/known split in this result yet."
    elif new_n:
        st, what = "bad", f"Nightly battery: {new_n} NEW failure(s)."
        ev = "; ".join(str(x) for x in (summ.get("new") or [])[:4])
    else:
        st, what = "warn", f"Nightly battery FAIL, nothing new ({summ.get('known_red_n', 0)} known red)."
        ev = f"{', '.join(fails[:6])}."
    return slip("battery", st, what, ran_at=ts, detail=str(path),
                broken=[{"status": st, "what": what, "evidence": ev, "since": _hhmm(ts), "owner": "unowned"}])


def a_ms(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    try:
        d = json.loads(s.ms_trail.read_text())
    except (OSError, ValueError):
        return slip("ms-nightly", "unknown", "M&S trail unreadable", unverified=["no M&S trail report"])
    ts = _parse_ts(d.get("ts_local")) or dt.datetime.fromtimestamp(s.ms_trail.stat().st_mtime)
    if (now - ts) > dt.timedelta(hours=30):
        return slip("ms-nightly", "unknown", f"last M&S trail {_ago(now, ts)}", ran_at=ts,
                    unverified=[f"M&S has not reported since {_hhmm(ts)}"])
    health = d.get("health") or {}
    facts = [fact(f"health.{k}", bool(v), ts, now, status="ok" if v or k in IDLE_OK_HEALTH else "bad")
             for k, v in health.items()]
    applied = d.get("applied") or {}
    ups = sum(len(v.get("upgraded") or []) for v in applied.values() if isinstance(v, dict))
    env_bad = sorted(k for k, v in applied.items() if isinstance(v, dict) and v.get("ok") is False)
    broken = [{"status": "bad", "what": f"M&S apply failed in {', '.join(env_bad)}.",
               "evidence": "The night rolled the environment back; see the trail.", "since": _hhmm(ts), "owner": "M&S"}] if env_bad else []
    changed = [{"what": f"M&S landed {ups} upgrade(s) across {sum(1 for v in applied.values() if isinstance(v, dict) and v.get('upgraded'))} environments.",
                "evidence": str(s.ms_trail)}] if ups else []
    # self-loop step 5: the nightly unit's exit is trail AND battery; the ticker keeps `failed` on a
    # battery-only night (the Maintaining lamp reads it), and the page says which half it was
    rc = _nightly_rcs(s.battery_dir, str(d.get("day") or ts.date().isoformat()))
    if rc:
        facts += [fact("ms.trail_rc", rc[0], ts, now, status="ok" if rc[0] == 0 else "bad"),
                  fact("ms.battery_rc", rc[1], ts, now, status="ok" if rc[1] == 0 else "warn")]
    return slip("ms-nightly", "bad" if env_bad else "ok", f"{ups} upgrades; {len(env_bad)} failed envs"
                + (f"; trail rc {rc[0]}, battery rc {rc[1]}" if rc else ""),
                ran_at=ts, facts=facts, broken=broken, changed=changed, detail=str(s.ms_trail))


_DONE_RC = re.compile(r"^=== done \S+ trail_rc=(\d+) battery_rc=(\d+) ===", re.M)


def _nightly_rcs(log_dir: Path, day: str) -> tuple[int, int] | None:
    """(trail_rc, battery_rc) from the night's own log (scripts/nightly-tests.sh `=== done ... ===`)."""
    try:
        m = _DONE_RC.findall((log_dir / f"nightly-{day.replace('-', '')}.log").read_text(errors="replace"))
    except OSError:
        return None
    return (int(m[-1][0]), int(m[-1][1])) if m else None


def _board_db(s: Sources) -> tuple[str, sqlite3.Connection] | None:
    try:
        slug = (s.kanban_home / "current").read_text().strip()
        db = s.kanban_home / "boards" / slug / "kanban.db"
        if not slug or not db.is_file():
            return None
        return slug, sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except (OSError, sqlite3.Error):
        return None


# `status='blocked'` is not the whole queue: a card keeps its block_kind while parked
# (2026-09-21). Crissa's watch and team-crissa's list were widened that day; the Daily — the ONE
# report the User reads — was not, so it under-counted her queue and dropped a parked
# capability block out of "Needs you" entirely. One string so the three queries below cannot
# drift apart again. Kill KREW_CRISSA_SEES_PARKED=0, the same switch the other readers carry.
PARKED_SQL = ("" if os.environ.get("KREW_CRISSA_SEES_PARKED", "1").strip().lower()
              in ("0", "false", "no", "off") else " OR t.status='scheduled'")
STALE_PARKED_SQL = PARKED_SQL and " OR (t.status='scheduled' AND t.block_kind='needs_input')"


# "Needs you", worst first. The page shows NEEDS_SHOWN and names the remainder rather than
# dropping it silently (2026-09-22). Lower sorts first; an unlisted ask sorts after these and
# before the truncation, so a new kind of ask is never hidden by being new.
NEED_RANK = {"crissa-gateway-down": 0, "gpu-hold": 1, "capability-blocked": 2,
             "apt-security": 3, "reboot": 4}
NEEDS_SHOWN = 5


def a_board(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    got = _board_db(s)
    if got is None:
        return slip("board", "unknown", "kanban board unreadable", unverified=["the fleet's current board could not be read"])
    slug, con = got
    try:
        t0 = since.timestamp()
        counts = dict(con.execute("SELECT status, count(*) FROM tasks GROUP BY 1").fetchall())
        opened = con.execute("SELECT count(*) FROM tasks WHERE created_at >= ?", (t0,)).fetchone()[0]
        closed = con.execute("SELECT count(*) FROM tasks WHERE completed_at >= ?", (t0,)).fetchone()[0]
        viol = con.execute("SELECT count(*) FROM task_events WHERE kind='protocol_violation' AND created_at >= ?", (t0,)).fetchone()[0]
        decide = con.execute(
            "SELECT t.id, substr(t.title,1,80), (SELECT max(e.created_at) FROM task_events e WHERE e.task_id=t.id AND e.kind='blocked') "
            f"FROM tasks t WHERE t.block_kind='needs_input' AND (t.status='blocked'{PARKED_SQL})").fetchall()
        caps = con.execute(
            "SELECT t.id, substr(t.title,1,90), coalesce((SELECT e.payload FROM task_events e WHERE e.task_id=t.id "
            "AND e.kind='blocked' ORDER BY e.id DESC LIMIT 1), '') FROM tasks t "
            f"WHERE t.block_kind='capability' AND (t.status='blocked'{PARKED_SQL}) ORDER BY t.created_at").fetchall()
        stale = con.execute(
            # Stale is NOT the same widening as the two above: a park is supposed to sit, so
            # sweeping every `scheduled` card in here would cry stuck at cards doing their job.
            # A parked QUESTION is different — it is an unanswered ask that nobody has touched
            # for two days, which is exactly the alarm. Untyped blocks stay counted as before.
            f"SELECT count(*) FROM tasks t WHERE (t.status='blocked'{STALE_PARKED_SQL}) "
            "AND coalesce((SELECT max(e.created_at) FROM task_events e "
            "WHERE e.task_id=t.id AND e.kind='blocked'), t.created_at) < ?", (now.timestamp() - STUCK_BLOCKED_H * 3600,)).fetchone()[0]
        # waiting since it BECAME ready (promoted / unblocked), not since it was written
        ready_old = con.execute(
            "SELECT min(coalesce((SELECT max(e.created_at) FROM task_events e WHERE e.task_id=t.id "
            "AND e.kind IN ('promoted','unblocked')), t.created_at)) FROM tasks t WHERE t.status='ready'").fetchone()[0]
    except sqlite3.Error as e:
        return slip("board", "unknown", f"kanban query failed: {e}")
    finally:
        con.close()
    open_n = sum(v for k, v in counts.items() if k not in ("done", "archived"))
    blocked = counts.get("blocked", 0)
    oldest = min((d[2] for d in decide if d[2]), default=None)
    ready_h = (now.timestamp() - ready_old) / 3600 if ready_old else 0
    hours = (now - since).total_seconds() / 3600
    # One standing board: a card opened today and finished tomorrow is normal, so "more
    # opened than closed" is not a fault (the User 2026-09-17: "task creation and completions
    # will differ but be accurate"). Stuck is: work ready and not picked up, blocked cards
    # nobody has touched for two days, or a day with nothing finished.
    why = []
    if ready_h >= READY_STUCK_H:
        why.append(f"a ready card has waited {int(ready_h)} h for a worker")
    if stale >= STUCK_BLOCKED_N:
        why.append(f"{stale} cards blocked for more than {STUCK_BLOCKED_H} h")
    if closed == 0 and open_n and hours >= 12:
        why.append(f"nothing finished in {int(hours)} h with {open_n} open")
    status, broken = ("bad" if why else "ok"), []
    if why:
        broken.append({"status": "bad", "what": "The board is stuck: " + "; ".join(why) + ".",
                       "evidence": f"{open_n} open ({blocked} blocked, {counts.get('ready', 0)} ready, {counts.get('running', 0)} running); today +{opened} / −{closed}.",
                       "since": "today", "owner": "ada"})
    if viol >= 10 and viol > 0.5 * max(closed, 1):
        status = status if status == "bad" else "warn"
        broken.append({"status": "warn", "what": f"{viol} workers exited without finishing their card.",
                       "evidence": f"Against {closed} cards finished today. Each is a run spent for nothing; the board itself is moving.",
                       "since": "today", "owner": "ada"})
    # A capability block is usually the WRONG ROLE for the tool (09-17: 4 of 4 — no terminal in
    # Ada's sandbox, a doc path the sandbox cannot see, pixels only Vera may judge). That is a
    # re-route for Ada/Crissa. Only a block that names root, a password or a body is the User's.
    yours = [c for c in caps if _HANDS.search(c[2] or "")]
    reroute = [c for c in caps if c not in yours]
    needs = []
    if yours:
        needs.append({"id": "capability-blocked", "ask": f"{len(yours)} card(s) wait on root, a password or hands.",
                      "evidence": "; ".join(f"{i}: {t}" for i, t, _ in yours[:3]),
                      "options": ["I'll take them", "Leave it"], "recommend": 0})
    if reroute:
        status = status if status == "bad" else "warn"
        broken.append({"status": "warn", "what": f"{len(reroute)} card(s) sit with a role that lacks the tool for them.",
                       "evidence": "A re-route, not a job for you: " + "; ".join(f"{i} ({t[:48]})" for i, t, _ in reroute[:3]) + ".",
                       "since": "today", "owner": "ada"})
    return slip("board", status, f"{open_n} open ({blocked} blocked); +{opened} / −{closed} today", ran_at=now,
                broken=broken, needs_user=needs, facts=[
                    fact("board.slug", slug, now, now), fact("board.open", open_n, now, now),
                    fact("board.opened", opened, now, now), fact("board.closed", closed, now, now),
                    fact("board.blocked", blocked, now, now), fact("board.stale_blocked", stale, now, now),
                    fact("board.wasted_runs", viol, now, now),
                    fact("crissa.decide_n", len(decide), now, now),
                    fact("crissa.decide_oldest_h", round((now.timestamp() - oldest) / 3600) if oldest else 0, now, now)])


def a_board_flow(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """Board flow: the self-healing KPIs (lib/kanban_kpis) for the day, plus the reader gate's
    liveness file when it exists. Phase 0 of docs/KANBAN-SELF-HEALING-20260918.md: measure first.
    A mechanical-birth majority or a reframe cut without a real frame is a watch row; a card with
    no reader is Ada's broken row."""
    if os.environ.get("KREW_DAILY_BOARD_FLOW", "1") == "0":
        return slip("board-flow", "ok", "board flow off (KREW_DAILY_BOARD_FLOW=0)")
    got = _board_db(s)
    if got is None:
        return slip("board-flow", "unknown", "kanban board unreadable")
    slug, con = got
    liveness = None
    lpath = s.kanban_home.parent / "kanban-liveness.json"
    try:
        liveness = json.loads(lpath.read_text(encoding="utf-8")) if lpath.is_file() else None
    except (OSError, json.JSONDecodeError):
        liveness = None
    try:
        import kanban_kpis as kk
        k = kk.compute(con, since.timestamp(), now.timestamp(), liveness)
    except Exception as e:  # noqa: BLE001 — one adapter, one line, never the page
        return slip("board-flow", "unknown", f"kpi query failed: {type(e).__name__}: {e}"[:160])
    finally:
        con.close()
    status, broken = "ok", []
    ms = k.get("kanban_mechanical_share")
    if ms is not None and ms > 0.5 and (k.get("kanban_minted") or 0) >= 10:
        status = "warn"
        broken.append({"status": "warn", "what": f"{int(100 * ms)} % of today's cards were born by the loop, the gate or the decomposer.",
                       "evidence": f"{k['kanban_minted']} born, {k.get('kanban_completed') or 0} finished. The board is spending itself on itself.",
                       "since": "today", "owner": "ada"})
    rm = k.get("kanban_reframe_mechanical_share")
    if rm:
        status = "warn"
        broken.append({"status": "warn", "what": f"{int(100 * rm)} % of today's reframe cuts had no real frame (mechanical).",
                       "evidence": f"{k.get('kanban_reframe_cuts')} cut(s). A mechanical frame is the failure re-posted as a new card.",
                       "since": "today", "owner": "ada"})
    if k.get("kanban_no_reader"):
        status = "bad"
        broken.append({"status": "bad", "what": f"{k['kanban_no_reader']} open card(s) have no reader.",
                       "evidence": "The reader gate found no mechanism and no clock for them: " + ", ".join((liveness or {}).get("none", [])[:4]) + ".",
                       "since": "today", "owner": "ada"})
    facts = [fact(f"flow.{kid[len('kanban_'):]}", v, now, now) for kid, v in k.items() if v is not None]
    facts.append(fact("flow.slug", slug, now, now))
    needs: list[dict] = []
    if liveness:
        # Phase 2: the reader table. Measured when the file is younger than CARRIED_AFTER_H.
        at = _parse_ts(liveness.get("at")) or dt.datetime.fromtimestamp(float(liveness.get("at") or 0))
        for reader, n in sorted((liveness.get("counts") or {}).items()):
            facts.append(fact(f"readers.{reader}", n, at, now))
        facts.append(fact("readers.act", "on" if liveness.get("act") else "report-only", at, now))
        if liveness.get("overdue"):
            status = status if status == "bad" else "warn"
            broken.append({"status": "warn", "what": f"{len(liveness['overdue'])} open card(s) are past their reader's deadline.",
                           # The `act` flag is right there in the file (and already read one line
                           # above for readers.act). This string hardcoded "report-only" and so
                           # told the User the reader gate was inert for the whole morning of
                           # 2026-09-22, hours after it was armed. The one page he reads must
                           # not state something the page's own source contradicts.
                           "evidence": ("Reader gate ("
                                        + ("landing wakes" if liveness.get("act")
                                           else "report-only until KREW_LIVENESS_ACT=1")
                                        + "): " + ", ".join(liveness["overdue"][:5]) + "."),
                           "since": "today", "owner": "ada"})
        if liveness.get("needs_user"):
            needs.append({"id": "crissa-gateway-down", "ask": f"Crissa's gateway is down and {len(liveness['needs_user'])} software call(s) wait on her.",
                          "evidence": ", ".join(liveness["needs_user"][:4]), "options": ["Restart her gateway", "Leave it"], "recommend": 0})
    return slip("board-flow", status, kk.headline(k), ran_at=now, broken=broken, facts=facts, needs_user=needs,
                unverified=[] if liveness else ["no reader-gate liveness file yet (Phase 2): no-reader / overdue not measured"])


def a_team_eval(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    ranks = sorted(s.improvements.glob("rank-*.json")) if s.improvements.is_dir() else []
    if not ranks:
        return slip("team-eval", "unknown", "no team eval on file")
    path = ranks[-1]
    ts = dt.datetime.fromtimestamp(path.stat().st_mtime)
    if (now - ts) > dt.timedelta(hours=27):      # nightly: judged by its cadence, not by where an on-demand build cuts the day
        return slip("team-eval", "unknown", f"team eval last ran {_ago(now, ts)}", ran_at=ts,
                    unverified=["no team eval in the last day"])
    try:
        d = json.loads(path.read_text())
    except ValueError:
        return slip("team-eval", "bad", "rank file unreadable", ran_at=ts)
    voters, absent = d.get("voters") or [], d.get("absent") or []
    rows = d.get("rows") or []
    top = [r["item"] for r in rows if r.get("top")]
    disc = d.get("discounted") or {}
    status = "bad" if len(voters) < 3 else "warn" if (absent or disc) else "ok"
    head = f"{len(rows)} items, {len(voters)} of 5 voices" + (f"; top: {top[0]}" if top else "; nothing at the top tier")
    broken = [] if status == "ok" else [{
        "status": status, "what": f"Team eval counted {len(voters)} of 5 voices.",
        "evidence": (f"No note from {', '.join(absent)}. " if absent else "") + (f"Not counted: {', '.join(f'{k} ({v})' for k, v in disc.items())}." if disc else ""),
        "since": _hhmm(ts), "owner": "unowned"}]
    return slip("team-eval", status, head, ran_at=ts, broken=broken, detail=str(path),
                facts=[fact("eval.voters", len(voters), ts, now), fact("eval.top", top[:3], ts, now)])


def a_ideas(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """The improvement funnel, from ledgers that outlive the board's gc: seen.jsonl
    (filings) and outcomes.json (scripts/improvement-outcomes.py: each agreed idea's stage,
    and 7 days after a landing whether its KPI moved the right way). The last row is the
    effect — "measured better" — because "landed" only says a try card was closed."""
    seen = _jsonl(s.improvements / "seen.jsonl")
    cut = (now - dt.timedelta(days=14)).date().isoformat()
    recent = [r for r in seen if str(r.get("day") or "") >= cut]
    filed = [r for r in recent if r.get("status") == "filed"]
    nights: dict[str, int] = {}
    for r in recent:
        if r.get("status") != "filed":
            nights[r.get("key", "?")] = nights.get(r.get("key", "?"), 0) + 1
    out_p = s.improvements / "outcomes.json"
    rows, ts, unv, rnd = [], None, [], []
    try:
        od = json.loads(out_p.read_text())
        rows = [r for r in (od.get("rows") or []) if isinstance(r, dict)]
        rnd = [r for r in (od.get("round") or []) if isinstance(r, dict)]
        ts = dt.datetime.fromtimestamp(out_p.stat().st_mtime)
    except (OSError, ValueError):
        unv.append("No outcomes ledger: what became of agreed ideas is not recorded.")
    if ts and (now - ts) > dt.timedelta(hours=48):
        unv.append(f"The outcomes ledger was last written {_ago(now, ts)}.")
    st = lambda r: str(r.get("stage") or "")           # noqa: E731
    live = [r for r in rows if st(r) != "withdrawn"]
    # outcomes.json also carries every other try and the Ops note's open actions (2026-09-19);
    # "agreed" counts the team's agreed tier only
    agreed = [r for r in live if r.get("source", "agreed") == "agreed"]
    landed = [r for r in live if st(r) == "landed"]
    moved = {k: [r for r in landed if r.get("measure") == k] for k in ("better", "worse", "flat")}
    # self-loop 09-28: a confounded "better" is not the try's own effect. On 09-27 all three
    # verdicts carried a confounder (the hermes update, other tries on the same KPI) and the
    # page still said "2 change(s) measured better". Only an unconfounded better counts.
    mixed_better = [r for r in moved["better"] if r.get("confounded")]
    moved["better"] = [r for r in moved["better"] if not r.get("confounded")]
    blind = [r for r in landed if str(r.get("measure") or "").startswith(("no KPI", "KPI "))]
    funnel = [
        {"label": "Ideas filed", "n": len(filed), "note": "last 14 days, one card per idea"},
        {"label": "Agreed by the team", "n": len(agreed), "note": f"{sum(1 for r in rows if st(r) == 'withdrawn')} withdrawn" if len(rows) > len(live) else ""},
        {"label": "With Crissa", "n": sum(1 for r in agreed if st(r).startswith(("on Crissa", "on Crissa", "parked"))), "note": "her ruling: try, park or later"},
        {"label": "Try in work", "n": sum(1 for r in live if st(r).startswith("in work")), "note": ""},
        {"label": "Landed", "n": len(landed), "note": "the try card closed"},
        {"label": "Measured better", "n": len(moved["better"]),
         "note": "its KPI moved the right way, 7 days on, nothing else in the window"
                 + (f"; {len(mixed_better)} more moved but are confounded" if mixed_better else "")},
    ]
    bits = []
    for r in mixed_better[:3]:
        why = r.get("confounded")
        why = "; ".join(map(str, why)) if isinstance(why, (list, tuple)) else str(why)
        bits.append(f"Better but confounded, not counted: {str(r.get('item'))[:60]} "
                    f"({r.get('kpi_id')} {r.get('baseline')} → {r.get('after', r.get('now'))}; {why[:120]}).")
    if moved["worse"]:
        bits.append(f"{len(moved['worse'])} landed change(s) made their KPI worse: " + "; ".join(r["item"][:60] for r in moved["worse"][:2]) + ".")
    if blind:
        bits.append(f"{len(blind)} landed change(s) cannot be measured ({blind[0].get('measure')}): " + blind[0]["item"][:70] + ".")
    due = [r for r in landed if str(r.get("measure") or "").startswith("due ")]
    if due:
        bits.append(f"{len(due)} waiting for their 7-day measure.")
    repeat = sorted(nights.items(), key=lambda kv: -kv[1])[:1]
    if repeat:
        bits.append(f"Seen again most: {repeat[0][0].split(':', 1)[-1]} ({repeat[0][1]} more nights).")
    if moved["worse"]:
        status = "bad"
    elif moved["better"]:
        status = "ok"
    elif landed:
        status = "warn"
    else:
        status = "bad" if (filed or live) else "unknown"
    value = (f"{len(moved['better'])} change(s) measured better" if moved["better"] else
             f"0 attributable: {len(mixed_better)} better but confounded" if mixed_better else
             f"{len(landed)} landed, none measured yet" if landed else "Nothing landed in 14 days")
    top3 = [{k: r.get(k) for k in ("item", "stage", "kpi_id", "baseline", "now", "measure", "vote")} for r in rnd[:3]]
    conf = {str(r.get("item")): r.get("confounded") for r in rows if r.get("confounded")}
    for r in top3:                                   # the round says it too, not only the funnel
        if r.get("measure") == "better" and conf.get(str(r.get("item"))):
            r["measure"] = "better, confounded (not counted)"
    return slip("ideas", status, f"{len(filed)} filed, {len(agreed)} agreed, {len(landed)} landed, {len(moved['better'])} better",
                ran_at=ts or now, unverified=unv, detail=str(out_p),
                facts=[fact("ideas.funnel", funnel, now, now), fact("ideas.note", " ".join(bits), now, now),
                       fact("ideas.value", value, now, now), fact("ideas.round", top3, now, now)])


def a_engines(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """Decode speed of the brains, one sample a house day (scripts/ops-toks-day.py). A KPI
    nobody samples cannot measure an idea, so silence here is said out loud."""
    try:
        kpis = json.loads((s.improvements / "kpis.json").read_text()).get("kpis") or {}
    except (OSError, ValueError):
        return slip("engines", "unknown", "no KPI registry", unverified=["The KPI registry could not be read."])
    facts, broken, unv, heads = [], [], [], []
    last = None
    try:                                             # the day's real load, beside the single-stream figure
        load = json.loads((s.improvements / "ops-toks-day.json").read_text()).get("lanes") or {}
    except (OSError, ValueError):
        load = {}
    for lane, kid in (("ops", "ops_tok_s"), ("crew", "crew_tok_s")):
        hist = [h for h in (kpis.get(kid) or {}).get("history") or [] if isinstance(h.get("value"), (int, float))]
        if kid not in kpis:
            continue
        if not hist:
            unv.append(f"{kid} has never recorded a sample: an idea tied to it cannot be measured.")
            continue
        ts = _parse_ts(hist[-1].get("ts"))
        last = max(filter(None, (last, ts)), default=None)
        cur = float(hist[-1]["value"])
        if ts is None or (now - ts) > dt.timedelta(hours=50):
            unv.append(f"{kid} last sampled {_ago(now, ts)}: ideas tied to it cannot be measured until it samples again.")
        prior = sorted(float(h["value"]) for h in hist[-8:-1])
        base = prior[len(prior) // 2] if len(prior) >= 3 else None
        facts.append(fact(f"engine.{lane}_tok_s", cur, ts, now))
        ld = load.get(lane) or {}
        # "alone" = the other brain idle: they share one GPU and each about halves the other
        ctx = ""
        if ld.get("contended_p75") is not None:
            ctx = f" alone, {ld['contended_p75']:g} while the other brain works ({ld.get('contended_pct')} % of the time)"
        elif ld.get("avg_running"):
            ctx = f" single-stream (avg {ld['avg_running']:g} at once, max {ld['max_running']})"
        heads.append(f"{lane} {cur:g} tok/s{ctx}")
        if ld.get("contended_pct") is not None and ld["contended_pct"] >= CONTENDED_WATCH and ld.get("contended_p75") and cur \
                and ld["contended_p75"] < 0.8 * cur:
            broken.append({"status": "warn", "what": f"The {lane} brain spent {ld['contended_pct']} % of its working time sharing the GPU.",
                           "evidence": f"{cur:g} tok/s alone, {ld['contended_p75']:g} while the other brain worked. Kanban traffic, not the engine.",
                           "since": "today", "owner": "ada"})
        if base and cur < base * (1 - ENGINE_DROP):
            broken.append({"status": "warn", "what": f"The {lane} brain decodes {round(100 * (1 - cur / base))} % slower than its week.",
                           "evidence": f"{cur:g} tok/s against a median of {base:g} over the last {len(prior)} days (single-stream p75).",
                           "since": _hhmm(ts), "owner": "unowned"})
    status = "warn" if broken else "ok"      # an unsampled KPI is a "could not verify" line, not a dark lamp
    return slip("engines", status, "; ".join(heads) or "no decode-speed sample", ran_at=last, facts=facts, broken=broken, unverified=unv)


def a_keeper(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    rows = [r for r in _jsonl(s.keeper, tail_bytes=1_500_000) if (_parse_ts(r.get("ts")) or dt.datetime.min) >= since]
    allrows = rows or _jsonl(s.keeper, tail_bytes=20_000)
    last = _parse_ts(allrows[-1].get("ts")) if allrows else None
    if last is None or (now - last) > dt.timedelta(hours=3):
        return slip("keeper", "bad" if last else "unknown", f"keeper last wrote {_ago(now, last)}", ran_at=last,
                    broken=[{"status": "bad", "what": "The keeper has gone quiet.", "evidence": f"Last event {_ago(now, last)}.",
                             "since": _hhmm(last), "owner": "unowned"}] if last else [])
    restarts: dict[str, int] = {}
    muted: dict[str, list] = {}
    spent = 0
    for r in rows:
        ev = r.get("event")
        if ev in ("lane_restarted", "lane_restarted_silent_pa"):
            restarts[r.get("lane", "?")] = restarts.get(r.get("lane", "?"), 0) + 1
        elif ev == "lane_down_budget_exhausted":
            spent += 1
        elif ev == "announced" and r.get("skipped") and r.get("reason") not in ("duplicate", "empty"):
            muted.setdefault(str(r.get("text") or "")[:120], []).append((r.get("reason"), _parse_ts(r.get("ts"))))
    broken = []
    for text, hits in sorted(muted.items(), key=lambda kv: -len(kv[1]))[:4]:
        reasons = sorted({h[0] for h in hits if h[0]})
        broken.append({"status": "warn", "what": f"An alert nobody heard: “{text}”",
                       "evidence": f"Raised {len(hits)}× and not spoken ({', '.join(reasons)}). No card was filed.",
                       "since": _hhmm(hits[-1][1]), "owner": "you"})
    if spent:
        broken.append({"status": "bad", "what": f"A lane ran out of restart budget ({spent} events).",
                       "evidence": "The keeper stopped trying; this is its “needs a human”.", "since": "today", "owner": "you"})
    n = sum(restarts.values())
    changed = [{"what": f"The keeper restarted {n} lane(s): " + ", ".join(f"{k} {v}" for k, v in sorted(restarts.items(), key=lambda kv: -kv[1])) + ".",
                "evidence": str(s.keeper)}] if n else []
    status = "bad" if spent else "warn" if muted else "ok"
    return slip("keeper", status, f"{n} lane restarts; {sum(len(v) for v in muted.values())} muted alerts", ran_at=last,
                broken=broken, changed=changed, facts=[fact("keeper.restarts", n, last, now),
                                                       fact("keeper.muted", sum(len(v) for v in muted.values()), last, now)])


def a_security(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    rows = [r for r in _jsonl(s.t0_hits, tail_bytes=600_000)
            if (_parse_ts(r.get("ts") or r.get("time") or r.get("at")) or dt.datetime.min) >= since]
    if not s.t0_hits.is_file():
        return slip("security-gate", "unknown", "no security-gate ledger")
    enforce = [r for r in rows if str(r.get("mode")) == "enforce"]
    reasons: dict[str, int] = {}
    for r in rows:
        reasons[str(r.get("reason") or "?")[:70]] = reasons.get(str(r.get("reason") or "?")[:70], 0) + 1
    status = "warn" if enforce else "ok"
    top = "; ".join(f"{n}× {k}" for k, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:3])
    broken = [{"status": "warn", "what": f"{len(enforce)} security-gate block(s) today.", "evidence": top + ".",
               "since": "today", "owner": "you"}] if enforce else []
    return slip("security-gate", status, f"{len(rows)} hits, {len(enforce)} blocked", ran_at=now, broken=broken,
                facts=[fact("t0.hits", len(rows), now, now)])


def a_dream(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    rows = _jsonl(s.dream_log, tail_bytes=400_000)
    if not rows:
        return slip("dream-night", "unknown", "no dream-night log")
    r = rows[-1]
    ts = _parse_ts(r.get("ts"))
    if ts is None or (now - ts) > dt.timedelta(hours=30):
        return slip("dream-night", "unknown", f"dream-night last ran {_ago(now, ts)}", ran_at=ts)
    failed = r.get("failed")
    if failed is None:       # a night written before `ok` was computed: work it out the same way
        failed = [k[:-6] for st in (r.get("steps") or []) for k in st if k.endswith("_error")]
    if failed:
        return slip("dream-night", "bad", f"steps failed: {', '.join(failed)}", ran_at=ts, broken=[{
            "status": "warn", "what": "Last night's memory consolidation was partial.",
            "evidence": f"Failed steps: {', '.join(failed)}.", "since": _hhmm(ts), "owner": "unowned"}])
    return slip("dream-night", "ok", "all steps ran", ran_at=ts)


def a_mechanical(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """Asks only a person can answer, from fixed rules — no model writes these."""
    needs, unv = [], []
    if s.reboot_flag.exists():
        when = dt.datetime.fromtimestamp(s.reboot_flag.stat().st_mtime)
        needs.append({"id": "reboot", "ask": "The host asks for a reboot.",
                      "evidence": f"Flag set {_hhmm(when)}. A reboot is yours to call; the restart window carries it on your YES.",
                      "options": ["Yes, in tonight's window", "Leave it"], "recommend": 0})
    apt = (s.apt_check or _apt_check)()
    if apt is None:
        unv.append("Pending OS updates could not be counted.")
    elif apt[1] or (len(apt) > 2 and apt[2]):
        # the named list decides: apt-check does not count the NVIDIA driver as security
        # (09-17: it said 0 with 16 driver packages pending from -security, and the ask vanished)
        names = list(apt[2]) if len(apt) > 2 else []
        gpu = [n for n in names if "nvidia" in n]
        # apt-check and `apt list` count differently (09-17: 12 vs 28, phased updates); the
        # named list is what would be installed, so when we have it, it is the count.
        apt = (max(apt[0], len(names)), len(names) or apt[1], names)
        ev = f"{apt[0]} packages pending in all. The crew cannot sudo."
        if gpu:
            # New driver libraries under the old loaded kernel module = "driver/library version
            # mismatch": every NEW CUDA process fails until the reboot. Never a daytime apt upgrade.
            ev = (f"{len(gpu)} of them are the NVIDIA driver: install those only together with a reboot, in the off-hours "
                  f"(new libraries under the loaded module break every new GPU process until then). "
                  + (f"The other {apt[1] - len(gpu)} are safe any time. " if apt[1] > len(gpu) else "Nothing else is pending as security. ") + ev)
        rest = apt[1] - len(gpu)
        needs.append({"id": "apt-security", "ask": f"{apt[1]} OS security update(s) need root.", "evidence": ev,
                      "options": (["The safe ones now, the driver with tonight's reboot", "All with tonight's reboot", "Leave it"] if gpu and rest
                                  else ["Install it right before tonight's reboot", "Leave it"] if gpu
                                  else ["I'll run it", "Leave it"]), "recommend": 0})
    if s.hold.is_file():
        age_h = (now.timestamp() - s.hold.stat().st_mtime) / 3600
        owner = (s.hold.read_text(errors="replace").strip().splitlines() or ["?"])[0][:80]
        if age_h > 12:
            needs.append({"id": "gpu-hold", "ask": f"A GPU hold is {int(age_h)} h old.",
                          "evidence": f"{owner}. While it stands, Crissa's gateway and ops stay down.",
                          "options": ["Release it", "Leave it"], "recommend": 1 if "human" in owner else 0})
    broken = _outlived(now, s)
    return slip("mechanical", "warn" if broken else "ok", f"{len(needs)} ask(s), {len(broken)} temporary thing(s) past their date",
                ran_at=now, needs_user=needs, unverified=unv, broken=broken)


def _outlived(now: dt.datetime, s: Sources) -> list[dict]:
    """A probe, a shim or a debug timer installed "for a night" is the quietest leftover
    there is. Each has an end date in config/krew-daily-temporaries.json; past it, and
    still installed, it is a row — with its results, so the decision is made here."""
    try:
        items = json.loads(s.temporaries.read_text()).get("temporaries") or []
    except (OSError, ValueError):
        return []
    show = s.unit_show or _unit_show
    rows = []
    for t in items:
        try:
            until = dt.date.fromisoformat(str(t.get("until")))
        except ValueError:
            continue
        if now.date() <= until:
            continue
        live = [u for u in (t.get("units") or []) if (show(u, str(t.get("scope", "user"))) or {}).get("LoadState") not in (None, "", "not-found")]
        if not live:
            continue
        tail = ""
        if t.get("result_file"):
            try:
                lines = Path(str(t["result_file"]).format(home=str(HOME))).read_text(errors="replace").strip().splitlines()
                tail = " Results: " + " · ".join(l.strip()[:60] for l in lines[-6:]) + "."
            except OSError:
                tail = " No results were written."
        rows.append({"status": "warn", "what": f"{t.get('what') or t.get('id')} are still installed, past {until:%m-%d}.",
                     "evidence": f"{t.get('why', '')} {len(live)} unit(s) still loaded.{tail}".strip(),
                     "since": f"{until:%m-%d}", "owner": str(t.get("owner") or "unowned")})
    return rows


def _apt_check() -> tuple | None:
    exe = "/usr/lib/update-notifier/apt-check"
    if not os.path.exists(exe):
        return None
    try:
        p = subprocess.run([exe], capture_output=True, text=True, timeout=30, check=False)
        a, b = (p.stderr or p.stdout).strip().split(";")[:2]
        q = subprocess.run(["apt", "list", "--upgradable"], capture_output=True, text=True, timeout=30, check=False)
        names = [l.split("/", 1)[0] for l in q.stdout.splitlines() if "-security" in l.split(" ", 1)[0]]
        return int(a), int(b), names
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _hermes_tree_status() -> dict:
    """scripts/hermes-tree.py status — patches applied? edits beyond them? (hyphenated file: load by path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("hermes_tree", ROOT / "scripts" / "hermes-tree.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    return mod.status()


HOOK_CREW = ("crissa", "ada", "bash", "steward", "vera", "lila")


def _hooks_doctor(profile: str) -> tuple[int, str]:
    """`hermes -p <p> hooks doctor` — the NATIVE audit, which nothing was running.

    Returns (issue count, the warning lines). It checks a hook exists, is executable, is
    allowlisted, runs clean on a synthetic payload and returns valid JSON. It does NOT check
    that the hook is doing its job — see the adapter's docstring.
    """
    import re as _re
    import subprocess as _sp
    # krew: the command is KREW_HERMES_BIN, else ~/.local/bin/hermes (as every crew caller, and a timer's PATH has no
    # ~/.local/bin), never a bare `hermes` off PATH: under a test's scratch home that reached the user's real hermes,
    # which provisioned itself there and rewrote the live launcher (2026-10-10).
    exe = os.environ.get("KREW_HERMES_BIN") or str(Path.home() / ".local" / "bin" / "hermes")
    try:
        out = _sp.run([exe, "-p", profile, "hooks", "doctor"],
                      capture_output=True, text=True, timeout=90).stdout
    except (OSError, _sp.SubprocessError) as e:
        return -1, f"{type(e).__name__}: {e}"[:120]
    m = _re.search(r"(\d+)\s+issue\(s\) found", out)
    warns = [ln.strip() for ln in out.splitlines() if "⚠" in ln or "✗" in ln]
    return (int(m.group(1)) if m else 0), "; ".join(w[:150] for w in warns[:3])


def a_hooks(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """Every profile's shell hooks, through hermes' own `hooks doctor`.

    The audit is NATIVE and nothing in the house ran it (the User, 2026-09-22: "i think there
    is a native feature like that in hermes agent" — there is). First run found **3 issues on
    every one of six profiles**, all "script modified since approval", including
    `security/tier0_gate.py` — the T0 security boundary, approved 07-31 and modified 09-19.
    The hooks still RUN; it is the consent record that is stale, so this is a warn, not a bad.

    It deliberately reports HEALTH, not EFFECT. A "the stamp file is old, so it is dead" rule
    was considered and rejected the same morning: `crissa-tool-callout`'s dedup stamps were five
    days old while the hook was working fine (its roll was current), and a heartbeat added to
    this very page watched the wrong home and would have called Crissa's watch dead every day.
    Which artifact means "I acted" is per-tool knowledge, and the roll-call `watch` list is
    where a tool declares it. Kill: KREW_DAILY_HOOKS=0.
    """
    if os.environ.get("KREW_DAILY_HOOKS", "1").strip().lower() in ("0", "false", "no", "off"):
        return slip("hooks", "skipped", "probe off (KREW_DAILY_HOOKS=0)")
    probe = s.hooks_doctor or _hooks_doctor
    bad, worst, unreadable = {}, "", []
    for prof in HOOK_CREW:
        n, detail = probe(prof)
        if n < 0:
            unreadable.append(f"{prof}: {detail}")
        elif n:
            bad[prof] = n
            worst = worst or detail
    broken = []
    if bad:
        broken.append({"status": "warn",
                       "what": f"{sum(bad.values())} shell-hook issue(s) across {len(bad)} profile(s).",
                       "evidence": (", ".join(f"{p} {n}" for p, n in sorted(bad.items()))
                                    + ". " + worst + " — `hermes -p <p> hooks doctor`; "
                                    "a re-approval is `hooks revoke` + re-approve, and it is NOT "
                                    "safe mid-flight on a fail_closed hook: a revoked T0 leaves "
                                    "every NEW worker spawning against an unapproved gate."),
                       "since": "today", "owner": "you"})
    if unreadable:
        return slip("hooks", "unknown", f"could not run hooks doctor for {len(unreadable)} profile(s)",
                    ran_at=now, broken=broken, unverified=unreadable)
    head = (f"{sum(bad.values())} issue(s) over {len(bad)} of {len(HOOK_CREW)} profiles" if bad
            else f"{len(HOOK_CREW)} profiles clean")
    return slip("hooks", "warn" if bad else "ok", head, ran_at=now, broken=broken)


def a_hermes_tree(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """The live hermes install (~/.hermes/hermes-agent) wears the house patches and
    nothing else. 2026-09-17 22:36: a bash worker (t_c7f12942) edited kanban_db.py
    and kanban_watchers.py in place — under a patched file, so the file-level
    "uncovered" check saw nothing, and the running gateway kept the old module
    while every CLI call ran the new one. Kill: KREW_DAILY_HERMES_TREE=0."""
    if os.environ.get("KREW_DAILY_HERMES_TREE", "1").strip().lower() in ("0", "false", "no", "off"):
        return slip("hermes-tree", "skipped", "probe off (KREW_DAILY_HERMES_TREE=0)")
    try:
        st = (s.hermes_tree_status or _hermes_tree_status)()
    except Exception as e:  # noqa: BLE001 — one probe, one line
        return slip("hermes-tree", "unknown", f"could not read the hermes tree: {e!s}"[:160])
    bad = [r["name"] for r in st.get("patches") or [] if r.get("state") != "applied"]
    beyond = st.get("beyond") or {}
    uncovered = st.get("uncovered") or []
    broken = []
    if beyond or uncovered:
        files = sorted(set(beyond) | set(uncovered))
        broken.append({"status": "warn", "what": "The live hermes install was edited beyond the house patches.",
                       "evidence": cut("; ".join([f"{f}: {beyond[f]}" for f in sorted(beyond)]
                                             + [f"{f}: in no patch" for f in uncovered]), 400, "`hermes-tree.py status` lists every file")
                                   + ". A worker wrote there; the gateway runs the old module until restarted, "
                                     "every CLI call the new one. Capture (scripts/hermes-tree.py capture) or drop.",
                       "since": "today", "owner": "unowned", "files": files})
    if bad:
        broken.append({"status": "bad", "what": "A house patch is missing or stale on the hermes tree.",
                       "evidence": f"{', '.join(bad)} — scripts/hermes-tree.py status; the kit re-apply owns it.",
                       "since": "today", "owner": "unowned"})
    status = "bad" if bad else ("warn" if broken else "ok")
    head = (f"{len(st.get('patches') or [])} patches applied, tree clean" if status == "ok"
            else f"{len(bad)} patch(es) not applied, {len(beyond) + len(uncovered)} file(s) edited beyond them")
    return slip("hermes-tree", status, head, ran_at=now, broken=broken)


def a_pipewire(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """PipeWire-pulse client slots, read from the janitor's `pipewire` samples. 2026-09-19:
    46 of 64 taken, 31 by orphaned `pactl subscribe` (tests that loaded the :8099 server);
    at the cap Say does nothing and the wall reads "no tiles". A leak at 60 of 64 is a row,
    not a surprise. Kill: KREW_DAILY_PIPEWIRE=0."""
    if os.environ.get("KREW_DAILY_PIPEWIRE", "1").strip().lower() in ("0", "false", "no", "off"):
        return slip("pipewire", "skipped", "probe off (KREW_DAILY_PIPEWIRE=0)")
    rows: list[dict] = []
    for d in (since.date(), now.date()):
        rows += _jsonl(s.janitor_dir / f"janitor-{d:%Y%m%d}.jsonl")
    rows = [r for r in rows if (_parse_ts(r.get("ts")) or dt.datetime.min) >= since
            and isinstance(r.get("pipewire"), dict) and r["pipewire"].get("clients") is not None]
    if not rows:
        return slip("pipewire", "unknown", "no client counts from the janitor",
                    unverified=["janitor wrote no pipewire samples"])
    last = rows[-1]
    last_ts = _parse_ts(last.get("ts"))
    cap = int(last["pipewire"].get("cap") or 64)
    peak_row = max(rows, key=lambda r: r["pipewire"]["clients"])
    peak = int(peak_row["pipewire"]["clients"])
    reaped = sum(int(r["pipewire"].get("reaped") or 0) for r in rows)
    would = max((int(r["pipewire"].get("would_reap") or 0) for r in rows), default=0)
    broken = []
    status = "ok"
    if peak >= cap - 4:
        status = "bad"
    elif peak >= cap * 3 // 4:
        status = "warn"
    if status != "ok":
        top = ", ".join(f"{k} {v}" for k, v in (peak_row["pipewire"].get("top") or {}).items())
        broken.append({"status": status, "what": f"PipeWire client slots ran to {peak} of {cap}.",
                       "evidence": f"peak at {_hhmm(_parse_ts(peak_row.get('ts')))}: {top}. At the cap Say does "
                                   "nothing and the wall paints no tiles.",
                       "since": _hhmm(_parse_ts(peak_row.get("ts"))), "owner": "unowned"})
    if reaped or would:
        status = "warn" if status == "ok" else status
        broken.append({"status": "warn", "what": "Orphaned audio clients were holding PipeWire slots.",
                       "evidence": (f"janitor reaped {reaped}" if reaped else
                                    f"up to {would} at once, not reaped (dry run; KREW_JANITOR_AUDIO_REAP=1 arms it)")
                                   + ". Something spawned them and died: lib/audio_orphans names each.",
                       "since": "today", "owner": "unowned"})
    head = f"now {last['pipewire']['clients']} of {cap} clients, peak {peak}" + (
        f", {reaped} orphans reaped" if reaped else (f", {would} orphans seen" if would else ""))
    return slip("pipewire", status, head, ran_at=last_ts, broken=broken, facts=[
        fact("pipewire.clients_peak", peak, last_ts, now),
        fact("pipewire.orphans_reaped", reaped, last_ts, now),
    ])


def a_announcer(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """Announcer lines the house dropped without speaking them (the User 2026-09-30). The PA queue holds three;
    a fourth pushes the oldest out to the dump file with reason `overflow`, and until now nobody was told
    (61 on 09-27). The row carries the count AND the text of each dropped line. More than five in one hour
    also files a card, from the drain (lib/announce_overflow.py). Kill: KREW_DAILY_ANNOUNCER=0."""
    if os.environ.get("KREW_DAILY_ANNOUNCER", "1").strip().lower() in ("0", "false", "no", "off"):
        return slip("announcer", "skipped", "probe off (KREW_DAILY_ANNOUNCER=0)")
    import announce_log
    import announce_overflow as ao
    lines = announce_log.read(now.date(), s.inbox / "announcer")
    tally = {e: sum(1 for x in lines if x["event"] == e) for e in announce_log.EVENTS}
    told = ", ".join(f"{n} {e}" for e, n in tally.items() if n) or "no lines logged yet"
    if not s.announce_dump.is_file():
        return slip("announcer", "unknown", "no announcer dump file",
                    unverified=[f"announcer: {s.announce_dump} is missing, so dropped lines cannot be counted"])
    rows = ao.drops(since.timestamp(), s.announce_dump, now.timestamp())
    if not rows:
        return slip("announcer", "ok", f"today: {told}; none dropped for a full queue", ran_at=now,
                    facts=[fact("announcer.overflow_drops", 0, now, now),
                           *[fact(f"announcer.{e}", n, now, now) for e, n in tally.items()]])
    worst, at = ao.worst_hour(rows)
    status = "bad" if worst > ao.PER_HOUR else "warn"
    try:                                             # say which card, or that there is none - never assume
        st = json.loads(s.announce_card.read_text())
        card = f"card {st['card']}" if st.get("day") == f"{now:%Y-%m-%d}" else "no card on file for it"
    except (OSError, ValueError, KeyError, AttributeError):
        card = "no card on file for it"
    shown = [ao.line_of(r) for r in rows[:12]]
    more = f" … and {len(rows) - 12} more" if len(rows) > 12 else ""
    first = dt.datetime.fromtimestamp(rows[0]["ts"])
    broken = [{"status": status,
               "what": f"The announcer dropped {len(rows)} line{'s' if len(rows) != 1 else ''} it never spoke.",
               "evidence": (f"Queue full (it holds {os.environ.get('KREW_ANNOUNCE_QUEUE_MAX', '20')}); worst hour {worst}"
                            f" from {_hhmm(dt.datetime.fromtimestamp(at))}"
                            + (f", over the {ao.PER_HOUR}-an-hour line ({card})" if worst > ao.PER_HOUR else "")
                            + ". Dropped: " + " | ".join(shown) + more),
               "since": _hhmm(first), "owner": "unowned"}]
    return slip("announcer", status, f"today: {told}; {len(rows)} dropped for a full queue, worst hour {worst}",
                ran_at=now, broken=broken, facts=[*[fact(f"announcer.{e}", n, now, now) for e, n in tally.items()],
                    fact("announcer.overflow_drops", len(rows), now, now, status=status),
                    fact("announcer.overflow_worst_hour", worst, now, now)])


def a_code_canary(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """Did the installed Python lose its source (lib/code_canary.py)? 2026-09-22 15:21 every .py
    under the hermes venvs, three plugins and Steward's uv python went, in the same minute a
    worker's scratch /tmp delete ran; the CLI died for 14 h and nothing said so. Read from
    the janitor's `code_canary` samples (the full set, every 5 min). Kill: KREW_DAILY_CODE_CANARY=0."""
    if os.environ.get("KREW_DAILY_CODE_CANARY", "1").strip().lower() in ("0", "false", "no", "off"):
        return slip("code-canary", "skipped", "probe off (KREW_DAILY_CODE_CANARY=0)")
    rows: list[dict] = []
    for d in (since.date(), now.date()):
        rows += _jsonl(s.janitor_dir / f"janitor-{d:%Y%m%d}.jsonl")
    rows = [r for r in rows if (_parse_ts(r.get("ts")) or dt.datetime.min) >= since
            and isinstance(r.get("code_canary"), dict) and "ok" in r["code_canary"]]
    if not rows:
        return slip("code-canary", "unknown", "no counts from the janitor",
                    unverified=["janitor wrote no code_canary samples"])
    last = rows[-1]
    last_ts = _parse_ts(last.get("ts"))
    bad = [r for r in rows if not r["code_canary"]["ok"]]
    if not bad:
        return slip("code-canary", "ok", f"{last['code_canary'].get('trees')} trees hold their source",
                    ran_at=last_ts)
    first = bad[0]
    trees = sorted({d["tree"] for r in bad for d in r["code_canary"].get("drops") or []})
    standing = not last["code_canary"]["ok"]
    sus = ", ".join((first["code_canary"].get("suspects") or [])[:6]) or "none named"
    return slip("code-canary", "bad" if standing else "warn",
                f"{'still ' if standing else ''}short: {', '.join(trees)}",
                ran_at=last_ts, broken=[{
                    "status": "bad" if standing else "warn",
                    "what": f"Installed Python lost its source: {', '.join(trees)}.",
                    "evidence": (f"first seen {_hhmm(_parse_ts(first.get('ts')))}; suspects {sus}. A planned "
                                 "removal is `scripts/code-canary.py rebaseline --tree <name>`; damage is "
                                 "docs/checkpoints/restore-repair-20260923/README.md."),
                    "since": _hhmm(_parse_ts(first.get("ts"))), "owner": "unowned"}])


def a_cua_doctor(now: dt.datetime, since: dt.datetime, s: Sources) -> dict:
    """Does the hands still have a body — the computer-use doctor + the tool tax,
    read the way a session would read them. The 09-27 hermes update (upstream
    5e4a2a3d24) made computer_use available only when hermes's own package
    manager installed cua-driver, or when HERMES_CUA_DRIVER_CMD names it. The
    house driver is neither, so computer_use read "unavailable" and dropped from
    every hands kit with no error, for six days — the last computer_use call was
    09-25 20:39. This row is the standing check the post-update step owes; a
    bad read here is a `bad` row, and lib/cua_doctor.fail_loud owes the alarm
    (a Krew Daily row + one blocked card a day). Kill: KREW_DAILY_CUA=0."""
    if os.environ.get("KREW_DAILY_CUA", "1").strip().lower() in ("0", "false", "no", "off"):
        return slip("cua-doctor", "skipped", "probe off (KREW_DAILY_CUA=0)")
    probe = s.cua_check
    if probe is None:
        try:
            import cua_doctor
            probe = cua_doctor.check
        except Exception as e:  # noqa: BLE001 — one probe, one line
            return slip("cua-doctor", "unknown", f"could not load lib/cua_doctor: {e!s}"[:160])
    try:
        out = probe()
    except Exception as e:  # noqa: BLE001 — one probe, one line
        return slip("cua-doctor", "unknown", f"could not run the cua check: {e!s}"[:160], ran_at=now)
    if out.get("off"):
        return slip("cua-doctor", "skipped", "probe off (KREW_CUA_DOCTOR=0)", ran_at=now)
    if out.get("ok"):
        return slip("cua-doctor", "ok", "computer_use available — the hands have a body", ran_at=now,
                    facts=[fact("computer_use", "available", now, now)])
    why = out.get("why") or ["(no reason recorded)"]
    broken = [{"status": "bad",
               "what": "computer_use is unavailable — the hands lost their body.",
               "evidence": (cut("; ".join(why), 400, "`hermes -p crissa computer-use doctor` gives it whole")
                             + ". The check: `hermes -p crissa computer-use doctor` and "
                               "`scripts/krew-tool-tax.py --profile crissa --platform cli --json` "
                               "(computer_use must not be in `unavailable`). The house driver "
                               "stays on X11 (CLAUDE.md, the User 10-01); the fix is "
                               "HERMES_CUA_DRIVER_CMD in Crissa's .env, not a driver change.")}]
    return slip("cua-doctor", "bad", "computer_use unavailable after a hermes update",
                ran_at=now, broken=broken)


ADAPTERS: list[Callable[[dt.datetime, dt.datetime, Sources], dict]] = [
    a_janitor, a_disk, a_battery, a_ms, a_board, a_board_flow, a_team_eval, a_ideas, a_engines, a_keeper, a_security,
    a_dream, a_mechanical, a_hermes_tree, a_hooks, a_pipewire, a_announcer, a_code_canary, a_cua_doctor]


# --- roll-call ----------------------------------------------------------------

def _unit_show(unit: str, scope: str) -> dict:
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + [
        "show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "Result", "-p", "ExecMainStatus",
        "-p", "ExecMainStartTimestamp", "-p", "ExecMainExitTimestamp"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    d = dict(l.split("=", 1) for l in p.stdout.splitlines() if "=" in l)
    for k in ("ExecMainStartTimestamp", "ExecMainExitTimestamp"):
        raw = d.get(k, "").strip()
        d[k] = None
        if raw and raw != "n/a":
            q = subprocess.run(["date", "-d", raw, "+%s"], capture_output=True, text=True, check=False)
            if q.returncode == 0 and q.stdout.strip():
                d[k] = float(q.stdout.strip())
    if d.get("ExecMainStartTimestamp") is None and d.get("ActiveState") not in ("active", "activating", "deactivating"):
        # 2026-09-18: a finished Type=oneshot unit clears its ExecMain* the moment it
        # goes inactive, so "last ran never" was the page's word for "ran and finished".
        # The journal's systemd lines (by MESSAGE_ID) are the record that survives.
        j = _journal_last_run(unit, scope)
        if j:
            d.update(j)
    return d


_MSG_STARTING = "7d4958e842da4a758f6c1cdc7b36dcc5"
_MSG_FINISHED = "39f53479d3a045ac8e11786248231fbf"
_MSG_FAILED = "be02cf6855d2428ba40df7e9d022f03d"
_MSG_EXITED = "98e322203f7a4ed290d09fe03c09fe15"


def _journal_last_run(unit: str, scope: str, lines: str | None = None, hours: int = 48) -> dict:
    """{ExecMainStartTimestamp, ExecMainExitTimestamp, Result, ExecMainStatus} of the
    unit's last run as the journal recorded it, or {} when it has none."""
    if lines is None:
        cmd = ["journalctl"] + (["--user"] if scope == "user" else []) + [
            "-u", unit, "-o", "json", "--no-pager", "--since", f"-{hours}h"]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return {}
        lines = p.stdout if p.returncode == 0 else ""
    out: dict = {}
    for raw in (lines or "").splitlines():
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue
        mid = str(e.get("MESSAGE_ID") or "")
        try:
            ts = float(e.get("__REALTIME_TIMESTAMP") or 0) / 1e6
        except (TypeError, ValueError):
            ts = 0.0
        if mid == _MSG_STARTING:
            out = {"ExecMainStartTimestamp": ts, "ExecMainExitTimestamp": None, "Result": "", "ExecMainStatus": ""}
        elif mid == _MSG_EXITED and out:
            out["ExecMainStatus"] = str(e.get("EXIT_STATUS") or "")
        elif mid == _MSG_FINISHED and out:
            out.update(ExecMainExitTimestamp=ts, Result="success", ExecMainStatus=out.get("ExecMainStatus") or "0")
        elif mid == _MSG_FAILED and out:
            out.update(ExecMainExitTimestamp=ts, Result=str(e.get("JOB_RESULT") or "failed"),
                       ExecMainStatus=out.get("ExecMainStatus") or "?")
    return out


def _job_due(job: dict, tick: dict, site: dict | None) -> dt.datetime | None:
    """When a window job should have started, in the window the ticker's state is for.
    An `after:` job has no time of its own: it is due by the close."""
    rng = krew_hours.parse(str(tick.get("window") or krew_hours.off_hours(site).get("window") or ""))
    try:
        day = dt.date.fromisoformat(str(tick.get("window_key")))
    except ValueError:
        return None
    if rng is None:
        return None
    opened = dt.datetime.combine(day, dt.time(rng[0] // 60, rng[0] % 60))
    length = (rng[1] - rng[0]) % 1440 or 1440
    if job.get("before_close_min") is not None:
        return opened + dt.timedelta(minutes=length - int(job["before_close_min"]))
    if job.get("after"):
        return opened + dt.timedelta(minutes=length)
    return opened + dt.timedelta(minutes=int(job.get("offset_min", 0)))


def _timer_enabled(timer: str, scope: str, s: Sources) -> bool:
    if s.unit_show is not None:                      # tests: the injected show says it
        return bool((s.unit_show(timer, scope) or {}).get("UnitFileState", "enabled") in ("enabled", "static", "linked"))
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + ["is-enabled", timer]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False).stdout.strip() in ("enabled", "static", "linked", "enabled-runtime")
    except (OSError, subprocess.SubprocessError):
        return False


def load_registry(s: Sources) -> tuple[list[dict], list[dict]]:
    try:
        d = json.loads(s.schedule.read_text())
    except (OSError, ValueError):
        return [], []
    return ([j for j in d.get("jobs") or [] if isinstance(j, dict) and j.get("name")],
            [w for w in d.get("watch") or [] if isinstance(w, dict) and w.get("routine")])


def roll_call(now: dt.datetime, since: dt.datetime, s: Sources, slips: dict[str, dict]) -> dict:
    jobs, watch = load_registry(s)
    try:
        mode = (s.gpu_mode.read_text().splitlines() or ["normal"])[0].strip().lower()
    except OSError:
        mode = "normal"
    try:
        tick = json.loads(s.tick_state.read_text())
    except (OSError, ValueError):
        tick = None
    show = s.unit_show or _unit_show
    rows: list[dict] = []
    if tick is None:
        # No state yet: one row, not one per job. The ticker writes only inside the window,
        # so between its install and the first window this is "waiting", and it says which.
        d = show("krew-window-tick.service", "user")
        ts = d.get("ExecMainExitTimestamp") or d.get("ExecMainStartTimestamp")
        alive = bool(ts) and (now.timestamp() - ts) < 20 * 60
        waiting = [str(j["name"]) for j in jobs if j.get("enabled", True) and j["name"] != "krew-daily"]
        if waiting:
            rows.append({"routine": f"window jobs ({len(waiting)}): " + ", ".join(waiting), "status": "unknown", "when": "—",
                         "evidence": "The ticker is running; no off-hours window has opened since it was installed." if alive
                         else "The window ticker has written no state and is not ticking: it is not installed or has stopped."})
    for j in jobs:                                   # the window's own jobs: the ticker's verdict
        name = str(j["name"])
        if not j.get("enabled", True):
            continue
        rec = ((tick or {}).get("fired") or {}).get(name)
        if name == "krew-daily":
            rows.append({"routine": name, "status": "ok", "evidence": "this page", "when": now.strftime("%H:%M")})
        elif tick is None:
            continue
        elif rec is None and j.get("legacy_timer") and (show(str(j["legacy_timer"]), str(j.get("scope", "user"))) or {}).get("LoadState") not in (None, "", "not-found") \
                and _timer_enabled(str(j["legacy_timer"]), str(j.get("scope", "user")), s):
            # hands-off: its calendar timer still owns it, so the ticker has no verdict — the unit does
            d = show(str(j.get("unit")), str(j.get("scope", "user")))
            ts = d.get("ExecMainExitTimestamp") or d.get("ExecMainStartTimestamp")
            last = dt.datetime.fromtimestamp(ts) if ts else None
            if last is None or (now - last) > dt.timedelta(hours=27):
                rows.append({"routine": name, "status": "unknown", "evidence": f"did not report (its own timer still owns it; last ran {_ago(now, last)})", "when": _hhmm(last)})
            elif d.get("Result") not in (None, "", "success"):
                rows.append({"routine": name, "status": "bad", "evidence": f"last run: {d.get('Result')} exit {d.get('ExecMainStatus')} (its own timer still owns it)", "when": _hhmm(last)})
            else:
                rows.append({"routine": name, "status": "ok", "evidence": "on time, by its own timer (not yet a window job)", "when": _hhmm(last)})
        elif rec is None:
            due = _job_due(j, tick, s.site)
            if due is not None and now < due + dt.timedelta(minutes=30):
                rows.append({"routine": name, "status": "pending", "evidence": f"due {due:%m-%d %H:%M}", "when": f"{due:%H:%M}"})
            else:
                rows.append({"routine": name, "status": "unknown", "evidence": "Not started in its window"
                             + (f" (was due {due:%m-%d %H:%M})." if due else "."), "when": "—"})
        else:
            st = str(rec.get("status") or rec.get("note") or "started")
            # the page never waits on a job: one still going at close is shown as such
            ev = "still running when this page was built — not finished, not failed" if st == "started" else st
            if rec.get("status_line") and st.startswith("failed:") and not st.startswith("failed: result="):
                ev = "silent green (the unit exited 0): " + st[len("failed:"):].strip()   # self-loop step 5
            # self-loop 09-28: `skipped: <reason>` is the job's own status line saying it stood
            # aside on purpose (beast, train-busy) - its own word, not red, not "fine"
            rows.append({"routine": name,
                         "status": "ok" if st in ("ok", "already running") else "unknown" if st == "started"
                         else "skipped" if st.startswith("skipped") else "bad",
                         "evidence": ev, "when": str(rec.get("at") or "")[11:16]})
    for w in watch:
        name = str(w["routine"])
        if mode in (w.get("parked_in") or []):
            rows.append({"routine": name, "status": "parked", "evidence": f"parked by design in {mode}", "when": "—"})
            continue
        limit = dt.timedelta(minutes=int(w.get("every_min", 1440)) + int(w.get("grace_min", 60)))
        last, ok, why = None, True, ""
        if w.get("file"):
            pats = [w["file"].format(day=d.isoformat(), ymd=d.strftime("%Y%m%d"), home=str(HOME), inbox=str(s.inbox))
                    for d in (now.date(), (now - dt.timedelta(days=1)).date())]
            hits = [Path(p) for p in pats if Path(p).is_file()]
            last = max((dt.datetime.fromtimestamp(p.stat().st_mtime) for p in hits), default=None)
            if hits and min(p.stat().st_size for p in hits[:1]) < int(w.get("min_bytes", 1)):
                ok, why = False, "output is near-empty"
        elif w.get("unit"):
            d = show(str(w["unit"]), str(w.get("scope", "user")))
            if d.get("LoadState") == "not-found":
                why = "unit not installed"
            ts = d.get("ExecMainExitTimestamp") or d.get("ExecMainStartTimestamp")
            last = dt.datetime.fromtimestamp(ts) if ts else None
            if last and d.get("Result") not in (None, "", "success"):
                ok, why = False, f"last run: {d.get('Result')} exit {d.get('ExecMainStatus')}"
        if last is None or (now - last) > limit:
            rows.append({"routine": name, "status": "unknown", "evidence": why or f"did not report (last {_ago(now, last)}; expected every {int(w.get('every_min', 1440)) // 60 or 1} h)", "when": _hhmm(last)})
        elif not ok:
            rows.append({"routine": name, "status": "bad", "evidence": why, "when": _hhmm(last)})
        else:
            rows.append({"routine": name, "status": "ok", "evidence": "on time", "when": _hhmm(last)})
    # self-loop step 5: a battery-only M&S night (trail rc 0) is test debt, not a maintenance
    # failure - the row says so and warns; the battery row carries the red
    ms = {f["k"]: f["v"] for f in ((slips.get("ms-nightly") or {}).get("facts") or [])}
    for r in rows:
        if r["routine"] == "ms-nightly" and r["status"] == "bad" and ms.get("ms.trail_rc") == 0 and ms.get("ms.battery_rc"):
            r["status"], r["evidence"] = "warn", (f"trail passed (rc 0); battery rc {ms['ms.battery_rc']}: test debt, "
                                                  f"see the battery row (the ticker keeps: {r['evidence']})")
    # a routine that ran on time and failed at its purpose: the adapter's effect probe outranks the clock
    by = {r["routine"]: r for r in rows}
    for name, r in by.items():
        sl = slips.get(PROBE_OF.get(name, name))
        if sl and r["status"] == "ok" and sl["status"] in ("bad",):
            r["status"], r["evidence"] = "bad", f"ran on time, not working: {sl['headline']}"
    return {"mode": mode, "rows": rows}


# --- build ----------------------------------------------------------------------

def _blocking_parks(limit: int = 3) -> dict:
    """Which parked cards hold live work, and is the board idle BY DEPENDENCY.

    The 09-16 board sat five days with 15 todo cards and ZERO dispatchable — not a
    jam, and no lamp could say so, because every routine reads a card's own state and
    this is a property of the EDGE between two cards. Read-only and fail-quiet: a
    report must never be the reason the page does not build.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "kanban_blast", Path(__file__).resolve().parents[1] / "scripts" / "kanban-blast-radius.py")
        br = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(br)
        db = br.board_db()
        if db is None:
            return {}
        rows, idle = br.rank(db), br.idle_note(db)
        return {"idle": idle,
                "top": [{"id": r["id"], "blocks": r["blocks"], "root": r["root"],
                         "title": r["title"][:60]} for r in rows[:limit]],
                "parks": len(rows)}
    except Exception:                                    # noqa: BLE001
        return {}


def build(now: dt.datetime | None = None, sources: Sources | None = None) -> dict:
    now = now or dt.datetime.now()
    s = sources or Sources()
    since, oh = day_bounds(now, s.site)
    slips: dict[str, dict] = {}
    for fn in ADAPTERS:
        name = fn.__name__[2:]
        try:
            sl = fn(now, since, s)
        except Exception as e:  # noqa: BLE001 — one bad source never costs the page
            sl = slip(name, "unknown", f"adapter failed: {type(e).__name__}: {e}",
                      unverified=[f"{name}: the page could not read its source ({type(e).__name__})"])
        slips[sl["routine"]] = sl
    rc = roll_call(now, since, s, slips)

    all_facts = [f for sl in slips.values() for f in sl["facts"]]
    recovered = []
    show = s.unit_show or _unit_show
    for f in list(all_facts):                        # re-measure what a carried report calls bad
        key = f["k"][7:] if f["k"].startswith("health.") else ""
        if not key or f["status"] != "bad" or f["source"] != "carried" or key not in HEALTH_UNITS:
            continue
        d = show(HEALTH_UNITS[key], "user")
        if d.get("LoadState") == "not-found":
            d = show(HEALTH_UNITS[key], "system")
        if not d.get("ActiveState"):
            continue
        up = d["ActiveState"] == "active"
        all_facts.append(fact(f["k"], up, now, now, status="ok" if up else "bad", source="measured"))
        if up:
            recovered.append({"what": f"{key} is back: its unit is active now.",
                              "evidence": f"{HEALTH_UNITS[key]} active; the nightly trail had it unhealthy at {f['measured_at'][11:16]}."})
    facts, conflicts = merge_facts(all_facts)
    conflicts = [c for c in conflicts if c["a"]["source"] == c["b"]["source"] == "measured"]
    broken = [dict(b, source=sl["routine"]) for sl in slips.values() for b in sl["broken"]]
    for k, f in facts.items():                       # service health: the merged fact, measured over carried
        if k.startswith("health.") and f["status"] == "bad":
            broken.append({"status": "bad" if f["source"] == "measured" else "warn",
                           "what": f"{k[7:]} reads unhealthy.",
                           "evidence": ("Measured " if f["source"] == "measured" else "Carried from a report written ") + _ago(now, _parse_ts(f["measured_at"])) + ".",
                           "since": (f.get("measured_at") or "")[5:16].replace("T", " "), "owner": "M&S", "source": "ms-nightly"})
    for c in conflicts:
        if c["k"].startswith("health."):
            broken.append({"status": "warn", "what": f"Two reports disagree about {c['k'][7:]}.",
                           "evidence": f"{c['a']['source']} {c['a']['v']} vs {c['b']['source']} {c['b']['v']}; showing the {c['kept']['source']} one.",
                           "since": "today", "owner": "unowned", "source": "merge"})
    broken.sort(key=lambda b: (-RANK.get(b["status"], 0), b["what"]))

    trend = _since_last_page(now, s, facts)
    # Worst first, and say what was cut. The page showed the first five in slip-iteration
    # order and dropped the rest in silence — so on a bad day the one ask that mattered could
    # be the sixth. Rank by what the ask actually blocks: her gateway down kills every
    # self-clearing path in the house (2026-09-21), a stale GPU hold parks ops and Crissa, and
    # a capability block is a card literally waiting on his hands.
    all_needs = [n for sl in slips.values() for n in sl["needs_user"]]
    all_needs.sort(key=lambda n: NEED_RANK.get(n.get("id", ""), 50))
    needs, needs_more = all_needs[:NEEDS_SHOWN], max(0, len(all_needs) - NEEDS_SHOWN)
    changed = recovered + trend + [c for sl in slips.values() for c in sl["changed"] if c.get("evidence")]
    unverified = [u for sl in slips.values() for u in sl["unverified"]]
    unverified.sort(key=lambda u: "could not read its source" not in u)      # a source the page failed to read comes first

    def worst(*names: str) -> str:
        return max((slips[n]["status"] for n in names if n in slips), key=lambda x: RANK.get(x, 0), default="unknown")


    exc = [r for r in rc["rows"] if r["status"] in ("bad", "unknown", "warn")]
    pending = [f'{r["routine"]} {r["when"]}' for r in rc["rows"] if r["status"] == "pending"]
    fine = [r["routine"] for r in rc["rows"] if r["status"] == "ok"]
    parked = [r["routine"] for r in rc["rows"] if r["status"] == "parked"]
    skipped = [f'{r["routine"]}: {r["evidence"]}' for r in rc["rows"] if r["status"] == "skipped"]
    n_bad = sum(1 for b in broken if b["status"] == "bad")
    n_warn = sum(1 for b in broken if b["status"] == "warn")
    funnel = (facts.get("ideas.funnel") or {}).get("v") or []
    lamps = [
        {"key": "maintaining", "label": "Maintaining", "status": _lamp(worst("janitor", "keeper", "battery", "ms-nightly", "disk", "board", "engines")),
         "value": f"{n_bad} broken, {n_warn} to watch" if (n_bad or n_warn) else "Nothing broken",
         "detail": slips["keeper"]["headline"] + "; janitor: " + slips["janitor"]["headline"]
                   + ("; " + slips["engines"]["headline"] if slips.get("engines", {}).get("facts") else "") + "."},
        {"key": "improving", "label": "Improving", "status": _lamp(worst("ideas", "team-eval")),
         "value": (facts.get("ideas.value") or {}).get("v") or "No ideas ledger", "detail": slips["ideas"]["headline"] + ". Eval: " + slips["team-eval"]["headline"] + "."},
        {"key": "reporting", "label": "Reporting", "status": "bad" if any(r["status"] == "bad" for r in exc) else "warn" if exc else "ok",
         "value": f"{len(exc)} of {len(rc['rows'])} routines wrong or silent" if exc else f"All {len(rc['rows'])} routines reported",
         "detail": (f"{len(parked)} parked by design in {rc['mode']}. " if parked else "") + "A routine that says nothing is never shown as fine."},
    ]
    if n_bad or n_warn or needs:
        head = (f"{n_bad} broken, {n_warn} to watch. " if (n_bad or n_warn) else "Nothing broken. ") + \
               (f"{len(needs)} thing(s) need you." if needs else "Nothing needs you.")
    else:
        head = "All quiet. Nothing broken, nothing needs you."
    dn, do = (facts.get("crissa.decide_n") or {}).get("v", 0), (facts.get("crissa.decide_oldest_h") or {}).get("v", 0)
    return {"schema": "krew_daily/v1", "built_at": now.isoformat(timespec="seconds"),
            "covers": {"since": _iso(since), "until": _iso(now)},
            "window": {"window": oh.get("window"), "source": oh.get("source"),
                       "quiet": krew_hours.quiet_hours(s.site)},
            "headline": head, "say": head, "lamps": lamps, "needs_you": needs,
            "needs_you_more": needs_more,
            "crissa_queue": {"n": dn, "oldest_h": do}, "blocking": _blocking_parks(),
            "broken": broken[:9], "broken_total": len(broken),
            "changed": changed[:6], "improving": {"funnel": funnel, "note": (facts.get("ideas.note") or {}).get("v", ""),
                          "round": (facts.get("ideas.round") or {}).get("v") or []},
            "rollcall": {"mode": rc["mode"], "exceptions": exc, "fine": fine, "parked": parked, "pending": pending,
                         "skipped": skipped},
            "self": _self_block(now, s),
            "announcer_log": f"announcer/announcer-{now:%Y-%m-%d}.log",
            "unverified": unverified[:8], "slips": slips}


def _self_block(now: dt.datetime, s: Sources) -> dict:
    """Self-loop step 5: the three claims, each with its own denominator and zero rule
    (lib/self_claims.py). Fail-quiet: a report must never be why the page does not build."""
    try:
        import self_claims as sc
        heal = (s.self_healing or (lambda n: sc.healing(n)))(now)
        return {"maintaining": sc.maintaining(s.ms_trail), "healing": heal,
                "improving": sc.improving(s.improvements / "outcomes.json", now),
                "touches": sc.touches(s.improvements / "kpis.json"),
                "silent_greens": sc.silent_greens(s.tick_state)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _since_last_page(now: dt.datetime, s: Sources, facts: dict[str, dict]) -> list[dict]:
    """Does the board clear on its own? (the User 2026-09-17: "As long as it clears on its own
    from now on then we have succeeded.") The page answers from its own dated files:
    blocked, blocked-and-untouched, and wasted runs against the last day's page."""
    today = now.date().isoformat()
    prev = sorted(p for p in s.inbox.glob("krew-daily-20*.json") if p.stem[-10:] < today) if s.inbox.is_dir() else []
    if not prev:
        return []
    try:
        old = {f["k"]: f["v"] for f in (json.loads(prev[-1].read_text()).get("slips") or {}).get("board", {}).get("facts") or []}
    except (OSError, ValueError):
        return []
    bits = []
    for key, label in (("board.blocked", "blocked"), ("board.stale_blocked", "blocked over 48 h"), ("board.wasted_runs", "wasted runs")):
        if key in old and key in facts:
            bits.append(f"{label} {old[key]} → {facts[key]['v']}")
    if not bits:
        return []
    fell = sum(1 for key in ("board.blocked", "board.wasted_runs") if key in old and key in facts and facts[key]["v"] < old[key])
    rose = sum(1 for key in ("board.blocked", "board.wasted_runs") if key in old and key in facts and facts[key]["v"] > old[key])
    verdict = "clearing" if fell and not rose else "growing" if rose and not fell else "mixed"
    return [{"what": f"Board since the last page ({verdict}): " + ", ".join(bits) + ".", "evidence": prev[-1].name}]


def _lamp(status: str) -> str:
    return {"ok": "ok", "parked": "ok", "skipped": "warn", "unknown": "unk", "warn": "warn", "bad": "bad"}.get(status, "unk")


# --- render -----------------------------------------------------------------------

CSS = """
:root{--bg:#0c0d10;--panel:#14161c;--bd:#252833;--inset:#1a1d24;--tx:#e8eaed;--mut:#8b93a7;--acc:#7eb8ff;
--ok:#3dd68c;--warn:#e6b84d;--bad:#f07178;--unk:#a9a3c9}
@media (prefers-color-scheme: light){:root{--bg:#f2f4f7;--panel:#fff;--bd:#d8dde6;--inset:#eaedf2;--tx:#181b22;
--mut:#5a6274;--acc:#2a63a8;--ok:#177a4b;--warn:#8f6a12;--bad:#b23d47;--unk:#5f5a86}}
@media print{:root{--bg:#fff;--panel:#fff;--bd:#bbb;--inset:#f3f3f3;--tx:#111;--mut:#444;--acc:#124}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--tx);margin:0;font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;font-variant-numeric:tabular-nums}
.page{max-width:900px;margin:0 auto;padding-inline:20px;padding-block:28px 72px;display:flex;flex-direction:column;gap:30px}
h1,h2,.k,.who,.since,.n,.num{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:var(--acc)}
.mast{display:flex;flex-direction:column;gap:10px}
.eyebrow{font:11px/1.3 ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);display:flex;flex-wrap:wrap;gap:4px 14px}
h1{font-size:1.55rem;line-height:1.25;margin:0;font-weight:600;text-wrap:balance;max-width:32ch}
.g{display:inline-block;width:11px;height:11px;flex:none;margin-top:.35em}
.g.ok{background:var(--ok);border-radius:50%}
.g.warn{width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:11px solid var(--warn);background:none}
.g.bad{background:var(--bad)}
.g.unk{border:2px solid var(--unk);border-radius:50%;background:none}
.lamps{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--bd);border:1px solid var(--bd);border-radius:8px;overflow:hidden}
.lamp{background:var(--panel);padding:14px 16px;display:flex;flex-direction:column;gap:4px}
.lamp .k{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);display:flex;gap:8px;align-items:flex-start}
.lamp .k .g{margin-top:1px}
.lamp .v{font-size:1.02rem;font-weight:600}
.lamp .d{font-size:13px;color:var(--mut)}
h2{font-size:.8rem;letter-spacing:.12em;text-transform:uppercase;color:var(--acc);margin:0 0 10px;font-weight:600;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}
h2 .n{color:var(--mut);letter-spacing:.04em;text-transform:none;font-weight:400}
section{display:flex;flex-direction:column}
.rows{border-top:1px solid var(--bd)}
.row{display:grid;grid-template-columns:18px minmax(0,1fr) 92px 96px;gap:4px 12px;padding:10px 0;border-bottom:1px solid var(--bd);align-items:start}
.row.claim{grid-template-columns:minmax(0,1fr)}
.row b{font-weight:600}
.ev{display:block;color:var(--mut);font-size:13px}
.since,.who{font-size:12px;color:var(--mut);padding-top:2px}
.who{text-align:right}
.ask{display:grid;grid-template-columns:26px minmax(0,1fr);gap:2px 10px;padding:12px 0;border-bottom:1px solid var(--bd)}
.ask .num{font:600 13px/1.6 ui-monospace,monospace;color:var(--acc)}
.opts{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.opt{font-size:12.5px;border:1px solid var(--bd);background:var(--inset);border-radius:5px;padding:2px 8px}
.opt.rec{border-color:var(--acc);color:var(--acc)}
.funnel{display:grid;grid-template-columns:minmax(120px,190px) minmax(0,1fr) 52px;gap:7px 12px;align-items:center;border-top:1px solid var(--bd);padding-top:12px}
.funnel .lab{font-size:13.5px}
.funnel .lab small{display:block;color:var(--mut);font-size:12px}
.track{height:14px;background:var(--inset);border-radius:3px;overflow:hidden}
.bar{height:100%;background:var(--acc);min-width:2px;border-radius:3px}
.bar.zero{background:none;border:2px solid var(--bad);width:14px;min-width:14px;border-radius:50%}
.funnel .n{text-align:right;font-size:13.5px}
.note{color:var(--mut);font-size:13px;margin:10px 0 0;max-width:72ch}
ul.plain{margin:0;padding:0;list-style:none;border-top:1px solid var(--bd)}
ul.plain li{padding:8px 0;border-bottom:1px solid var(--bd);display:flex;gap:10px}
ul.plain li span.t{min-width:0}
details{border-bottom:1px solid var(--bd);padding:8px 0}
summary{cursor:pointer;color:var(--mut);font-size:13.5px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font:12px/1.4 ui-monospace,monospace;color:var(--mut);border:1px solid var(--bd);border-radius:4px;padding:1px 7px}
.foot{color:var(--mut);font-size:12px;border-top:1px solid var(--bd);padding-top:14px;display:flex;flex-direction:column;gap:4px}
@media (max-width:640px){.lamps{grid-template-columns:1fr}.row{grid-template-columns:18px minmax(0,1fr)}
.since,.who{grid-column:2;text-align:left;padding-top:0}.since::before{content:"since "}.who::before{content:"owner "}}
"""


def _e(x: Any) -> str:
    return html.escape(str(x if x is not None else ""))


def _render_self(sb: dict) -> str:
    """The three claims, one line each, with denominators and the proxies they stand on."""
    if not sb:
        return ""
    if sb.get("error"):
        return f'<section><h2>Self <span class="n">the claims</span></h2><p class="d">not built: {_e(sb["error"])}</p></section>'
    heal = sb.get("healing") or {}
    t = sb.get("touches") or {}
    sg = sb.get("silent_greens") or []
    rows = [
        ("Self-maintaining", (sb.get("maintaining") or {}).get("text", "?"), "M&S applies landed / attempted on the last trail; a rollback is attempted, not landed"),
        ("Self-healing", heal.get("text", "?"), "; ".join(f'{a["actuator"]} {a["cleared"]}/{a["detected"]} ({a["proxy"]})' for a in heal.get("actuators") or [])
         + (f'. Breaker: {(heal.get("breaker") or {}).get("mode")}, false-alarm label {(heal.get("breaker") or {}).get("false_alarm_label")}.' if heal.get("breaker") else "")
         + (f' User touches now {t.get("now")} (down is better; published baseline median {t.get("baseline_median")}).' if t else "")),
        ("Self-improving", (sb.get("improving") or {}).get("text", "?"), "attributable better / shipped (landed with a joined commit), 30 days; a confounded better is not attributable"),
    ]
    body = "".join(f'<div class="row claim"><div><b>{_e(k)}</b><span class="ev">{_e(v)}</span><span class="ev">{_e(d)}</span></div></div>' for k, v, d in rows)
    if sg:
        body += '<div class="row claim"><div><b>Silent greens</b><span class="ev">' + _e("; ".join(f'{x["routine"]}: {x["why"]}' for x in sg)) + "</span></div></div>"
    return f'<section><h2>Self <span class="n">the claims, measured</span></h2><div class="rows">{body}</div></section>'


def render_html(m: dict) -> str:
    built = _parse_ts(m["built_at"]) or dt.datetime.now()
    since = _parse_ts(m["covers"]["since"])
    w = m["window"]
    o: list[str] = []
    o.append(f'<header class="mast"><div class="eyebrow"><span>Krew Daily</span><span>{built:%a %Y-%m-%d}</span>'
             f'<span>built {built:%H:%M}</span><span>covers since {since:%m-%d %H:%M}</span>'
             f'<span>off-hours {_e(w.get("window") or "unset")}</span></div><h1>{_e(m["headline"])}</h1></header>')
    o.append('<div class="lamps" role="list">' + "".join(
        f'<div class="lamp" role="listitem"><div class="k"><i class="g {_e(l["status"])}"></i>{_e(l["label"])}</div>'
        f'<div class="v">{_e(l["value"])}</div><div class="d">{_e(l["detail"])}</div></div>' for l in m["lamps"]) + "</div>")
    o.append(_render_self(m.get("self") or {}))

    zq = m["crissa_queue"]
    asks = "".join(
        f'<div class="ask"><span class="num">{i}</span><div><b>{_e(n["ask"])}</b><span class="ev">{_e(n["evidence"])}</span>'
        '<div class="opts">' + "".join(f'<span class="opt{" rec" if j == n.get("recommend") else ""}">{_e(opt)}</span>'
                                       for j, opt in enumerate(n.get("options") or [])) + "</div></div></div>"
        for i, n in enumerate(m["needs_you"], 1))
    if not asks:
        asks = '<div class="ask"><span class="num">·</span><div><b>Nothing needs you.</b></div></div>'
    if m.get("needs_you_more"):
        asks += (f'<div class="ask"><span class="num">+</span><div><b>{m["needs_you_more"]} more ask(s) '
                 'did not fit.</b><span class="ev">Shown worst first; the rest are in '
                 'krew-daily.json.</span></div></div>')
    if zq["n"]:
        asks += (f'<div class="ask"><span class="num">·</span><div><b>With Crissa, not you: {zq["n"]} software call(s).</b>'
                 f'<span class="ev">The oldest has waited {zq["oldest_h"]} h.</span></div></div>')
    bl = m.get("blocking") or {}
    if bl.get("top"):
        idle = bl.get("idle") or {}
        roots = ", ".join(f'{t["id"]} ({t["blocks"]})' for t in bl["top"] if t.get("root"))
        why = (f'{idle.get("dispatchable", 0)} of {idle.get("todo", 0)} waiting card(s) can start; '
               "the rest are held by a decision, not by a worker.")
        asks += (f'<div class="ask"><span class="num">·</span><div>'
                 f'<b>{bl.get("parks", 0)} parked card(s) are holding live work.</b>'
                 f'<span class="ev">{_e(why)}'
                 + (f' Most held by: {_e(roots)}.' if roots else '')
                 + '</span></div></div>')
    o.append(f'<section><h2>Needs you <span class="n">only what the krew cannot decide</span></h2><div class="rows">{asks}</div></section>')

    rows = "".join(
        f'<div class="row"><i class="g {_lamp(b["status"])}"></i><div><b>{_e(b["what"])}</b><span class="ev">{_e(b["evidence"])}</span></div>'
        f'<span class="since">{_e(b.get("since"))}</span><span class="who">{_e(b.get("owner"))}</span></div>' for b in m["broken"])
    if not rows:
        rows = '<div class="row"><i class="g ok"></i><div><b>Nothing broken or at risk.</b></div><span></span><span></span></div>'
    o.append(f'<section><h2>Broken or at risk <span class="n">worst first · {len(m["broken"])} shown of {m["broken_total"]}</span></h2>'
             f'<div class="rows">{rows}</div></section>')

    if m["changed"]:
        o.append('<section><h2>Changed <span class="n">with evidence on disk</span></h2><ul class="plain">' + "".join(
            f'<li><i class="g ok"></i><span class="t">{_e(c["what"])} <span class="ev">{_e(c["evidence"])}</span></span></li>'
            for c in m["changed"]) + "</ul></section>")

    fun = m["improving"]["funnel"]
    if fun:
        top = max((f["n"] for f in fun), default=0) or 1
        bars = "".join(
            f'<div class="lab">{_e(f["label"])}' + (f'<small>{_e(f["note"])}</small>' if f.get("note") else "") + "</div>"
            + (f'<div class="track"><div class="bar" style="width:{max(0.3, 100 * f["n"] / top):.1f}%"></div></div>' if f["n"]
               else '<div class="track" style="background:none"><div class="bar zero"></div></div>')
            + f'<div class="n">{f["n"]}</div>' for f in fun)
        rnd = m["improving"].get("round") or []

        def _bump(r: dict) -> str:
            if r.get("now") is None:
                return ""
            b = r.get("baseline")
            return f' · {r.get("kpi_id")} {b if b is not None else "?"} → {r.get("now")}'
        round3 = "".join(f'<li><span class="t"><b>{i}.</b> {_e(str(r.get("item") or "")[:90])} '
                      f'<span class="ev">{_e(r.get("stage") or "")}{_e(_bump(r))}'
                      f'{_e(" · " + r["measure"]) if r.get("measure") else ""}</span></span></li>'
                      for i, r in enumerate(rnd, 1))
        o.append(f'<section><h2>Improving <span class="n">ideas to measured gains</span></h2><div class="funnel">{bars}</div>'
                 + (f'<p class="note">{_e(m["improving"]["note"])}</p>' if m["improving"]["note"] else "")
                 + (f'<ul class="plain">{round3}</ul><p class="note">The whole round, ranked: '
                    '<a href="team-daily.html">team-daily.html</a> · The round.</p>' if round3 else "") + "</section>")

    rc = m["rollcall"]
    exc = "".join(
        f'<div class="row"><i class="g {_lamp(r["status"])}"></i><div><b>{_e(r["routine"])}</b><span class="ev">{_e(r["evidence"])}</span></div>'
        f'<span class="since">{_e(r.get("when"))}</span><span class="who">{"not working" if r["status"] == "bad" else "half ok" if r["status"] == "warn" else "did not report"}</span></div>'
        for r in rc["exceptions"])
    chips = "".join(f'<span class="chip">{_e(n)}</span>' for n in rc["fine"])
    pk = "".join(f'<span class="chip">{_e(n)}</span>' for n in rc["parked"])
    o.append('<section><h2>Roll-call <span class="n">did each routine run, and did it work</span></h2>'
             + (f'<div class="rows">{exc}</div>' if exc else "")
             + f'<details><summary>{len(rc["fine"])} routine(s) reported on time and passed the page\'s own check</summary><div class="chips">{chips}</div></details>'
             + (f'<details><summary>{len(rc.get("pending") or [])} window job(s) not due yet</summary><div class="chips">'
                + "".join(f'<span class="chip">{_e(n)}</span>' for n in rc["pending"]) + "</div></details>" if rc.get("pending") else "")
             + (f'<details><summary>{len(rc["parked"])} parked by design in {_e(rc["mode"])}</summary><div class="chips">{pk}</div></details>' if pk else "")
             + (f'<details><summary>{len(rc["skipped"])} skipped on purpose (the job said why)</summary><div class="chips">'
                + "".join(f'<span class="chip">{_e(n)}</span>' for n in rc["skipped"]) + "</div></details>" if rc.get("skipped") else "")
             + "</section>")

    unv = m["unverified"] or ["Nothing: every source was read."]
    o.append('<section><h2>Could not verify</h2><ul class="plain">' + "".join(
        f'<li><i class="g unk"></i><span class="t">{_e(u)}</span></li>' for u in unv) + "</ul></section>")
    q = w.get("quiet") or {}
    if m.get("announcer_log"):
        o.append('<section><h2>Announcer <span class="n">every line it was given today</span></h2><p class="note">'
                 f'<a href="{_e(m["announcer_log"])}">{_e(m["announcer_log"].rsplit("/", 1)[-1])}</a> · spoken, skipped, '
                 'summarised into a roundup, or dropped - one line each, with why. '
                 f'{_e(((m.get("slips") or {}).get("announcer") or {}).get("headline") or "")}</p></section>')
    o.append('<footer class="foot"><span>Each routine keeps its own detail file; this is the only page you need to open. '
             f'The krew speaks up outside {_e(q.get("window"))} ({_e(q.get("source"))}).</span>'
             '<span>● fine · ▲ watch · ■ broken · ○ did not report. A routine that says nothing is never shown as fine, '
             'and one that says "fine" is still checked.</span></footer>')
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Krew Daily</title>\n'
            f"<style>{CSS}</style>\n</head><body><div class=\"page\">\n" + "\n".join(o) + "\n</div></body></html>\n")


def write(m: dict, inbox: Path) -> list[Path]:
    """krew-daily.{html,json} + the dated pair, each written atomically."""
    day = (_parse_ts(m["built_at"]) or dt.datetime.now()).date().isoformat()
    body, js = render_html(m), json.dumps(m, indent=2, default=str) + "\n"
    out = []
    inbox.mkdir(parents=True, exist_ok=True)
    for name, text in ((f"krew-daily-{day}.html", body), (f"krew-daily-{day}.json", js),
                       ("krew-daily.html", body), ("krew-daily.json", js)):
        p = inbox / name
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
        out.append(p)
    return out
