"""The three self-* numbers, each with its own denominator and zero rule (self-loop step 5, the User 09-28).

The house wants to say it is self-maintaining, self-healing and self-improving. Each claim is a
number the house measures about itself, traceable to a file a run really wrote. The House Daily's
"Self" block and the weekly self-review both call these functions; `self-claims.json` holds only
their output (private; nothing leaves the machine until the User publishes).

  maintaining  M&S applies LANDED / ATTEMPTED on the last trail (a rollback is attempted, not landed:
               09-27 hindsight = 0 of 11). Denominator 0 = "nothing owed".
  healing      faults cleared by a NAMED actuator / faults detected (lines the house already writes).
               The breaker's false-alarm label stays "unassigned" until the 10-05 read.
               kanban_user_touches is the residual (direction down) against the published series.
  improving    ATTRIBUTABLE better / SHIPPED over 30 days (shipped = landed with a joined commit;
               a confounded better is not attributable). Denominator 0 = "nothing shipped".

A silent green (a job that exited 0 while its status line said failed) is a REPORTING fault: it is
counted beside the roll-call, never folded into the maintaining ratio.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

# kanban_user_touches, the published series through 09-21 (CLAUDE.md, 09-16..09-21). The 09-28
# samples of 43/44 are one incident, not a baseline.
TOUCHES_BASELINE = {"2026-09-16": 1, "2026-09-17": 3, "2026-09-18": 2, "2026-09-19": 8, "2026-09-20": 5, "2026-09-21": 30}


def _read(p: Path) -> dict:
    try:
        d = json.loads(Path(p).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def tally_applied(applied: dict) -> dict:
    """landed / attempted / rolled from a trail report's `applied` (the same rule as
    ms-trail-run.apply_tally, for reports written before that key existed)."""
    landed = attempted = rolled = 0
    named: list[str] = []
    for env, a in (applied or {}).items():
        if not isinstance(a, dict):
            continue
        n_land = int(a.get("changed_n") or 0)
        outs = [o for o in (a.get("outcomes") or []) if isinstance(o, dict)]
        rb = [o for o in outs if o.get("outcome") == "rolled-back"]
        failed = list(a.get("failed") or [])
        n_rb = len(rb) or (len(failed) if a.get("ok") is False else 0)
        n_other = max(0, len(failed) - n_rb)
        landed += n_land
        rolled += n_rb
        attempted += n_land + n_rb + n_other
        if n_rb:
            named.append(f"{env} {n_rb}")
    return {"landed": landed, "attempted": attempted, "rolled": rolled, "rollbacks": named}


def maintaining(trail_path: Path) -> dict:
    d = _read(trail_path)
    if not d:
        return {"claim": "maintaining", "value": None, "text": "M&S trail unreadable", "source": str(trail_path)}
    t = d.get("apply_tally")
    t = ({"landed": t["landed"], "attempted": t["attempted"], "rolled": t["rolled"],
          "rollbacks": [f"{k} {len(v['rolled'])}" for k, v in (t.get("envs") or {}).items() if v.get("rolled")]}
         if isinstance(t, dict) and "attempted" in t else tally_applied(d.get("applied") or {}))
    if not t["attempted"]:
        text = "nothing owed (no apply attempted)"
    else:
        text = f"{t['landed']} of {t['attempted']} applies landed" + (
            f"; rolled back: {', '.join(t['rollbacks'])}" if t["rollbacks"] else "")
    return {"claim": "maintaining", "value": (t["landed"] / t["attempted"]) if t["attempted"] else None,
            "landed": t["landed"], "attempted": t["attempted"], "rolled": t["rolled"], "rollbacks": t["rollbacks"],
            "text": text, "day": d.get("day"), "source": str(trail_path)}


def improving(outcomes_path: Path, now: dt.datetime, days: int = 30) -> dict:
    d = _read(outcomes_path)
    cut = (now - dt.timedelta(days=days)).timestamp()
    rows = [r for r in (d.get("rows") or []) if isinstance(r, dict)]
    shipped = [r for r in rows if r.get("stage") == "landed" and r.get("sha") and float(r.get("landed_at") or 0) >= cut]
    better = [r for r in shipped if r.get("measure") == "better" and not r.get("confounded")]
    unmeasured = [r for r in shipped if r.get("measure") not in ("better", "worse", "flat") or r.get("confounded")]
    if not shipped:
        text = f"nothing shipped in {days} days"
    else:
        text = f"{len(better)} of {len(shipped)} shipped changes measured better (attributable)" + (
            f"; {len(unmeasured)} shipped, unmeasured" if unmeasured else "")
    return {"claim": "improving", "value": (len(better) / len(shipped)) if shipped else None,
            "better": len(better), "shipped": len(shipped), "unmeasured": len(unmeasured), "days": days,
            "items": [{k: r.get(k) for k in ("item", "sha", "kpi_id", "measure", "confounded", "review")} for r in shipped],
            "text": text, "source": str(outcomes_path)}


def silent_greens(tick_state: Path) -> list[dict]:
    """Window jobs whose unit exited 0 but whose own status line said failed (or wrote none)."""
    d = _read(tick_state)
    out = []
    for name, rec in (d.get("fired") or {}).items():
        st = str((rec or {}).get("status") or "")
        if st.startswith("failed:") and not st.startswith("failed: result=") and rec.get("status_line"):
            out.append({"routine": name, "why": st[len("failed:"):].strip(), "at": rec.get("at")})
    return out


def touches(kpis_path: Path) -> dict:
    k = (_read(kpis_path).get("kpis") or {}).get("kanban_user_touches") or {}
    base = sorted(TOUCHES_BASELINE.values())
    med = base[len(base) // 2]
    return {"now": k.get("current"), "baseline_series": TOUCHES_BASELINE, "baseline_median": med,
            "direction": "down", "source": str(kpis_path)}


# --- healing: four named actuators, from the lines they already write -------------------------
# Only the salad release writes a "cleared" line. The others are read by the proxy each names
# (the page prints it): a janitor restart CLEARED when the unit is active on the next 5-min row;
# an orphan CLEARED when a TERM/KILL was sent and its pid is never reported again; the way home
# CLEARED when a launch is not followed by another within 10 minutes (09-27 22:34-22:54 was five
# launches, one clear). Report-only numbers; nothing acts on them.
HOME = Path.home()
GATE_LOG = HOME / ".hermes" / "logs" / "kanban-board-gate.log"
SALAD_LOG = HOME / ".hermes" / "logs" / "kanban-salad-tripwire.log"
HOLD_LOG = HOME / ".hermes" / "krew-gpu-hold.jsonl"
JANITOR_DIR = Path(os.path.expanduser(os.environ.get("KREW_METRICS_ROOT") or "~/.krew/metrics"))
BREAKER = HOME / ".hermes" / "kanban-breaker.json"

import re  # noqa: E402

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def _lines_since(p: Path, since: dt.datetime) -> list[tuple[dt.datetime, str]]:
    out = []
    try:
        for line in Path(p).read_text(errors="replace").splitlines():
            m = _TS.match(line)
            if not m:
                continue
            try:
                t = dt.datetime.fromisoformat(m.group(1))
            except ValueError:
                continue
            if t >= since:
                out.append((t, line))
    except OSError:
        pass
    return out


def heal_salad(since: dt.datetime, gate_log: Path = GATE_LOG, salad_log: Path = SALAD_LOG) -> dict:
    det = sum(1 for _, l in _lines_since(gate_log, since) if re.search(r"\ssalad: \d+ session", l))
    clr = sum(1 for _, l in _lines_since(salad_log, since) if re.search(r"release: t_[0-9a-f]+ unblocked", l))
    return {"actuator": "salad release", "detected": det, "cleared": clr,
            "proxy": "salad: lines (gate) vs `release: <id> unblocked` (tripwire)"}


def heal_orphans(since: dt.datetime, gate_log: Path = GATE_LOG) -> dict:
    seen: dict[str, dt.datetime] = {}
    sent: dict[str, dt.datetime] = {}
    for t, l in _lines_since(gate_log, since):
        m = re.search(r"orphan t_[0-9a-f]+ pid=(\d+)", l)
        if not m:
            continue
        pid = m.group(1)
        if "leftover of run" in l:
            seen[pid] = t
        elif re.search(r"-> SIG(TERM|KILL) sent", l):
            sent.setdefault(pid, t)
    cleared = sum(1 for pid, t in sent.items() if seen.get(pid, t) <= t)
    return {"actuator": "orphan reap", "detected": len(seen), "cleared": cleared,
            "proxy": "leftover pids vs TERM/KILL sent and never reported again"}


def heal_janitor(since: dt.datetime, d: Path = JANITOR_DIR) -> dict:
    rows = []
    day = since.date()
    while day <= dt.date.today():
        p = d / f"janitor-{day:%Y%m%d}.jsonl"
        try:
            for line in p.read_text(errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                rows.append(r)
        except OSError:
            pass
        day += dt.timedelta(days=1)
    rows = [r for r in rows if str(r.get("ts") or "") >= since.isoformat(timespec="seconds")]
    rows.sort(key=lambda r: str(r.get("ts") or ""))
    det = clr = 0
    for i, r in enumerate(rows):
        for unit, u in (r.get("units") or {}).items():
            if not isinstance(u, dict) or u.get("action") not in ("restart", "card"):
                continue
            det += 1
            if u.get("action") == "restart":
                nxt = next((x for x in rows[i + 1:] if unit in (x.get("units") or {})), None)
                if nxt and (nxt["units"][unit].get("active") == "active") and not nxt["units"][unit].get("red"):
                    clr += 1
    return {"actuator": "janitor restart", "detected": det, "cleared": clr,
            "proxy": "restart or card vs the unit active on the next 5-min row"}


def heal_way_home(since: dt.datetime, hold_log: Path = HOLD_LOG, window_min: int = 10) -> dict:
    ts = []
    try:
        for line in Path(hold_log).read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("event") == "flag_home_launched":
                try:
                    t = dt.datetime.fromisoformat(str(r.get("ts")))
                except ValueError:
                    continue
                if t >= since:
                    ts.append(t)
    except OSError:
        pass
    ts.sort()
    clr = sum(1 for i, t in enumerate(ts) if not (i + 1 < len(ts) and ts[i + 1] - t <= dt.timedelta(minutes=window_min)))
    return {"actuator": "hold-sweep way home", "detected": len(ts), "cleared": clr,
            "proxy": f"launches vs no relaunch within {window_min} min"}


def healing(now: dt.datetime, days: int = 7, kpis_path: Path | None = None) -> dict:
    since = now - dt.timedelta(days=days)
    acts = [heal_way_home(since), heal_janitor(since), heal_orphans(since), heal_salad(since)]
    det = sum(a["detected"] for a in acts)
    clr = sum(a["cleared"] for a in acts)
    br = _read(BREAKER)
    text = (f"{clr} of {det} faults cleared by a named actuator ({days} days)" if det
            else f"no fault detected by a named actuator in {days} days")
    return {"claim": "healing", "value": (clr / det) if det else None, "cleared": clr, "detected": det,
            "days": days, "actuators": acts, "text": text,
            "breaker": {"mode": br.get("mode"), "trips": len(br.get("trips") or []),
                        "false_alarm_label": "unassigned (the 10-05 read)"},
            **({"touches": touches(kpis_path)} if kpis_path else {})}
