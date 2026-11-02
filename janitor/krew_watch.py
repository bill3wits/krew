"""krew_watch: the janitor's extra watch list and open gaps, and the Control Panel's Janitor tile.

The owner 2026-10-06: "We cannot operate like this" - an off switch nobody can see and a unit that fails
silently. The watch list is no longer typed by hand (2026-10-06, "if it's worth setting up, it's worth
watching"): it is every unit in THE unit registry (lib/krew_registry.py, discovered from systemd, refreshed
by the night's refresh job and a cheap check on each janitor tick), described by $KREW_HOME/unit-notes.json (what it
does, what of Crissa's leans on it, its pause switch). scripts/krew-janitor.py checks it every tick, cards a red
one through its usual path, and writes one snapshot (SNAPSHOT) that the panel's Janitor tile paints:
every watched unit and probe, why each red one is red, every pause switch with Pause/Resume, the gaps, and
the registry's new / removed notes.

Nothing here restarts anything. Pause/Resume only writes or removes a declared off_file.
Kill: KREW_JANITOR_WATCH=0 (the janitor skips the list; its built-in UNITS are untouched).
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import sys as _sys
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))
import krew_settings  # noqa: E402


def _cfg(section: str) -> dict:
    """One section of $KREW_HOME/janitor.yaml, or of $KREW_JANITOR_CONFIG (config/janitor.example.yaml lists every key)."""
    return krew_settings.load(section, path=os.environ.get("KREW_JANITOR_CONFIG") or krew_settings.krew_home() / "janitor.yaml")


def _p(v) -> Path:
    return Path(os.path.expanduser(str(v)))

_W = _cfg("watch")
ROOT = _p(_W.get("gaps_root") or "~")   # where a gap's `grep` condition looks
CONFIG = Path(os.environ.get("KREW_UNIT_NOTES") or _p(_cfg("registry").get("notes") or krew_settings.krew_home() / "unit-notes.json"))
SNAPSHOT = Path(os.environ.get("KREW_JANITOR_SNAPSHOT") or _p(_cfg("paths").get("metrics_dir") or krew_settings.krew_home() / "janitor" / "metrics") / "janitor-latest.json")
ORDERS = Path(os.environ.get("KREW_RENDER_ORDERS") or _p(_W.get("render_orders") or krew_settings.krew_home() / "render-orders"))
STALE_S = 15 * 60          # three missed 5-min ticks: the tile says the janitor itself is not reporting
EXPECTS = ("running", "exited", "any", "gpu", "timer", "path", "socket", "off")


def on() -> bool:
    return os.environ.get("KREW_JANITOR_WATCH", "1").strip().lower() not in ("0", "false", "no", "off")


def _registry():
    import krew_registry
    return krew_registry


def load(path: Path | None = None, *, reg: dict | None = None, skip: frozenset = frozenset()) -> dict:
    """{watch, gaps, notes}: watch = every registry unit (krew_registry.watch_entries) described by its
    note; gaps from the notes file. A test or caller may pass a ready `watch` in a dict instead."""
    try:
        notes = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        notes = {}
    notes.setdefault("notes", {})
    notes.setdefault("gaps", [])
    try:
        hr = _registry()
        watch = hr.watch_entries(hr.load() if reg is None else reg, notes, skip=set(skip))
    except Exception:  # noqa: BLE001 - a broken registry must not stop the janitor's own UNITS
        watch = []
    return {"watch": watch, "gaps": notes["gaps"], "notes": notes["notes"]}


def entries(cfg: dict | None = None) -> list[dict]:
    out = []
    for e in (cfg or load()).get("watch") or []:
        if not isinstance(e, dict) or not str(e.get("unit") or "").strip():
            continue
        e = dict(e)
        e.setdefault("scope", "user")
        e.setdefault("expect", "running")
        e["kind"] = "timer" if e["unit"].endswith((".timer", ".path", ".socket")) else "service"
        out.append(e)
    return out


def off_path(e: dict) -> Path | None:
    p = str(e.get("off_file") or "").strip()
    return Path(os.path.expanduser(p)) if p else None


def pause_state(e: dict) -> dict | None:
    """{since, reason} when the entry's off_file exists, else None."""
    p = off_path(e)
    if p is None or not p.exists():
        return None
    try:
        st = p.stat()
        reason = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()[:1] if p.is_file() else []
    except OSError:
        return {"since": None, "reason": ""}
    return {"since": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "reason": (reason[0][:160] if reason else "")}


def _show(unit: str, scope: str, props: tuple[str, ...], runner=None) -> dict:
    cmd = ["systemctl"] + (["--user"] if scope == "user" else []) + ["show", unit] + [f"-p{p}" for p in props]
    try:
        r = (runner or subprocess.run)(cmd, capture_output=True, text=True, timeout=10)
        text = r.stdout or ""
    except Exception:  # noqa: BLE001
        return {}
    return dict(ln.split("=", 1) for ln in text.splitlines() if "=" in ln)


def log_verdict(unit: str, scope: str, lc: dict, *, runner=None, row: dict | None = None) -> str:
    """The unit's own last word on its work, from the journal: e.g. the places watcher's last
    `build rc=N`. "" when the last matching line carries `ok` (or there is none yet)."""
    cmd = ["journalctl"] + (["--user"] if scope == "user" else []) + ["-u", unit, "-n", str(int(lc.get("lines", 300))),
                                                                      "-o", "cat", "--no-pager"]
    try:
        r = (runner or subprocess.run)(cmd, capture_output=True, text=True, timeout=10)
        lines = (r.stdout or "").splitlines()
        rx = re.compile(lc["pattern"])
    except Exception:  # noqa: BLE001
        return ""
    hit = next((ln for ln in reversed(lines) if rx.search(ln)), None)
    if hit is None:
        return ""
    if row is not None:
        row["last_log"] = hit.strip()[-160:]
    return "" if str(lc.get("ok") or "") in hit else f"last {lc.get('name') or 'run'}: {hit.strip()[-120:]}"


# ---- a red unit's own reason (the User 2026-10-06: a failure is NAMED, never just a red tile) ------------
# krew-crissa-screen went red as "failed (exit-code)" while the real answer - the isolation check that failed - sat in
# ~/.hermes/crissa-screen-check.json. Any entry may name a check/result file ("reason_file": "<path>" or
# {"path": ...}); when the unit is red its reason is read from it and put in `why` (so the card title and the
# Control Panel tile say it). Shapes read: {"checks": [{"check"|"name", "ok", "why"}]} (the failing names),
# {"reason"|"error"|"why"|"message": ...}, or plain text (its last line). Never makes anything red by itself.
REASON_KEYS = ("reason", "error", "why", "message")


def file_reason(e: dict) -> str:
    spec = e.get("reason_file")
    if isinstance(spec, str):
        spec = {"path": spec}
    if not isinstance(spec, dict) or not str(spec.get("path") or "").strip():
        return ""
    p = Path(os.path.expanduser(str(spec["path"])))
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        when = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
    except OSError:
        return f"no reason file at {spec['path']}"
    tag = f" [{p.name} {when}]"
    try:
        d = json.loads(text)
    except ValueError:
        d = None
    if isinstance(d, dict):
        checks = d.get("checks")
        if isinstance(checks, list):
            bad = [c for c in checks if isinstance(c, dict) and c.get("ok") is False]
            if bad:
                parts = [str(c.get("check") or c.get("name") or "?")
                         + (f" ({str(c['why'])[:100]})" if c.get("why") else "") for c in bad[:4]]
                more = f" +{len(bad) - 4} more" if len(bad) > 4 else ""
                return f"failed check{'s' if len(bad) > 1 else ''}: " + "; ".join(parts) + more + tag
        for k in REASON_KEYS:
            if d.get(k):
                return str(d[k]).strip()[:200] + tag
        if d.get("ok") is True:
            return f"its record says ok{tag}"
        return ""
    if d is not None:
        return ""
    last = next((ln.strip() for ln in reversed(text.splitlines()) if ln.strip()), "")
    return (last[:200] + tag) if last else ""


def check(e: dict, *, runner=None, parked=None) -> dict:
    """One entry's verdict: {unit, label, state, level ok|paused|parked|bad, red, why}."""
    unit, scope = e["unit"], e.get("scope", "user")
    row = {"unit": unit, "scope": scope, "label": e.get("label") or unit, "what": e.get("what", ""),
           "feeds": e.get("feeds", ""), "kind": e.get("kind") or ("timer" if unit.endswith((".timer", ".path", ".socket")) else "service"),
           "off_file": e.get("off_file") or "", "pause": e.get("pause") or ""}
    kv = _show(unit, scope, ("LoadState", "ActiveState", "SubState", "Result", "Unit"), runner)
    load_state, active, sub = kv.get("LoadState", ""), kv.get("ActiveState", ""), kv.get("SubState", "")
    row["state"] = f"{active or '?'}/{sub or '?'}"
    paused = pause_state(e)
    if paused:
        row["paused"] = paused
    why = ""
    if not kv:
        why = "systemctl did not answer"
    elif load_state in ("not-found", "") or load_state == "masked":
        why = f"unit {load_state or 'unknown'}"
    elif e.get("expect") == "off":
        why = f"failed ({kv.get('Result') or 'exit'})" if active == "failed" else ""
        row["note"] = "installed but disabled - off by choice"
    elif row["kind"] == "timer":
        if active != "active":
            why = f"{unit.rsplit('.', 1)[1]} {active}/{sub}"
        elif not unit.endswith(".timer"):
            why = ""
        else:
            svc = kv.get("Unit") or unit[: -len(".timer")] + ".service"
            s = _show(svc, scope, ("ActiveState", "Result", "ExecMainStatus"), runner)
            row["last_run"] = f"{s.get('ActiveState', '?')} result={s.get('Result', '?')}"
            if s.get("ActiveState") == "failed" or s.get("Result") not in (None, "", "success"):
                why = f"last run of {svc} {s.get('Result') or s.get('ActiveState')}"
    else:
        expect = e.get("expect", "running")
        if active == "failed":
            why = f"failed ({kv.get('Result') or 'exit'})"
        elif expect == "any":
            why = ""
        elif expect == "exited":
            why = "" if active == "active" else f"{active}/{sub}"
        elif active != "active":
            why = f"{active}/{sub}"
        if not why and active == "active" and isinstance(e.get("log_check"), dict):
            why = log_verdict(unit, scope, e["log_check"], runner=runner, row=row)
        if why and expect == "gpu" and parked is not None and parked(unit):
            row.update(level="parked", red=False, why="parked by the GPU mode (by design)")
            return row
    if paused and not why:
        # a paused switch is not a failure, but it is never hidden: amber, named, with its age
        row.update(level="paused", red=False,
                   why=f"paused since {paused['since'] or '?'}" + (f": {paused['reason']}" if paused["reason"] else ""))
        return row
    if why and e.get("reason_file"):
        reason = file_reason(e)
        if reason:
            row["reason"] = reason
            why = f"{why}: {reason}"
    row.update(level="bad" if why else "ok", red=bool(why), why=why)
    return row


def check_all(cfg: dict | None = None, *, runner=None, parked=None, skip: frozenset = frozenset()) -> list[dict]:
    rows = []
    for e in entries(cfg or load(skip=skip)):
        r = check(e, runner=runner, parked=parked)
        if e.get("new_since"):
            r["new_since"] = e["new_since"]      # the tile says `new` for a week
        rows.append(r)
    return rows


# ---- gaps ---------------------------------------------------------------------------------------------
def _grep(cond: dict, root: Path) -> bool:
    try:
        rx = re.compile(cond["pattern"])
    except (KeyError, re.error):
        return False
    glob = cond.get("glob") or "*"
    for rel in cond.get("paths") or []:
        base = root / rel
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for f in files:
            if not fnmatch.fnmatch(f.name, glob):
                continue
            try:
                if rx.search(f.read_text(encoding="utf-8", errors="replace")):
                    return True
            except OSError:
                continue
    return False


def _order(cond: dict, orders: Path) -> bool:
    try:
        rx = re.compile(cond.get("workflow") or ".")
    except re.error:
        return False
    for f in (orders / (cond.get("state") or "done")).glob("*.json"):
        try:
            o = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if cond.get("card") and o.get("card") != cond["card"]:
            continue
        if not rx.search(str(o.get("workflow") or "")):
            continue
        if cond.get("after") and str(o.get("created") or "") < cond["after"]:
            continue
        return True
    return False


def gap_done(cond: dict, *, root: Path = ROOT, orders: Path | None = None) -> bool:
    kind = (cond or {}).get("kind")
    if kind == "grep":
        return _grep(cond, root)
    if kind == "order":
        return _order(cond, orders or ORDERS)
    if kind == "any":
        return any(gap_done(c, root=root, orders=orders) for c in cond.get("of") or [])
    return False


def gaps(cfg: dict | None = None, prev: dict | None = None, *, root: Path = ROOT, orders: Path | None = None,
         now: str | None = None) -> list[dict]:
    """Each gap open or closed. `closed` (by hand in the config, or its done_when met) is sticky in the
    snapshot: the first time it was seen closed is kept as closed_at."""
    prev_closed = {g["id"]: g.get("closed_at") for g in (prev or {}).get("gaps") or [] if g.get("closed_at")}
    out = []
    for g in (cfg or load()).get("gaps") or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        row = {k: g.get(k) for k in ("id", "title", "owner", "why", "fix", "done", "card", "opened") if g.get(k)}
        closed = bool(g.get("closed")) or gap_done(g.get("done_when") or {}, root=root, orders=orders)
        if closed:
            if isinstance(g.get("closed"), str):
                row["closed_at"] = g["closed"]
            else:
                row["closed_at"] = prev_closed.get(g["id"]) or now or dt.datetime.now().isoformat(timespec="seconds")
        row["status"] = "closed" if closed else "open"
        out.append(row)
    return out


# ---- snapshot -----------------------------------------------------------------------------------------
def write_snapshot(snap: dict, path: Path | None = None) -> bool:
    p = Path(path or SNAPSHOT)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".janitor-latest.")
        with os.fdopen(fd, "w") as fh:
            json.dump(snap, fh, indent=1)
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def read_snapshot(path: Path | None = None) -> dict | None:
    try:
        return json.loads(Path(path or SNAPSHOT).read_text())
    except (OSError, ValueError):
        return None


def panel_view(*, path: Path | None = None, cfg: dict | None = None, now: float | None = None) -> dict:
    """What the Janitor tile paints. Staleness is judged HERE (the panel), because a janitor that stopped
    cannot say so itself."""
    snap = read_snapshot(path)
    now = now or time.time()
    if not snap:
        return {"ok": False, "level": "bad", "headline": "The janitor has not written a report yet",
                "reds": [], "rows": [], "gaps": [], "switches": switches(cfg)}
    age = now - float(snap.get("epoch") or 0)
    view = dict(snap)
    view["ok"] = True
    view["age_s"] = int(age)
    view["switches"] = switches(cfg)
    try:
        hr = _registry()
        reg = hr.load()
        view["registry"] = {"n": len(reg.get("units") or []), "updated": reg.get("updated"),
                            "changes": hr.recent_changes(reg)[-40:],
                            "notes_without_units": hr.notes_without_units(reg)}
    except Exception as e:  # noqa: BLE001
        view["registry"] = {"error": f"{type(e).__name__}: {e}"[:160]}
    if age > STALE_S:
        view["level"] = "bad"
        view["headline"] = f"The janitor has not reported for {int(age // 60)} min (krew-janitor.timer)"
        view["stale"] = True
    return view


def switches(cfg: dict | None = None) -> list[dict]:
    """Every declared pause switch and whether it is set, read live (a press shows at once)."""
    out = []
    for e in entries(cfg):
        if e.get("off_file"):
            out.append({"unit": e["unit"], "label": e.get("label") or e["unit"], "off_file": e["off_file"],
                        "paused": pause_state(e)})
    return out


def set_pause(unit: str, paused: bool, *, by: str = "the Control Panel", cfg: dict | None = None) -> dict:
    """Write or remove a declared off_file. Only units in the list that declare one; nothing else."""
    e = next((x for x in entries(cfg) if x["unit"] == unit), None)
    if e is None or off_path(e) is None:
        return {"ok": False, "error": f"{unit} has no pause switch (off_file) in unit-notes.json"}
    p = off_path(e)
    try:
        if paused:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"paused from {by} at {dt.datetime.now():%Y-%m-%d %H:%M}\n", encoding="utf-8")
        elif p.exists():
            p.unlink()
    except OSError as err:
        return {"ok": False, "error": f"{type(err).__name__}: {err}"}
    return {"ok": True, "unit": unit, "paused": pause_state(e)}
