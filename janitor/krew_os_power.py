#!/usr/bin/env python3
"""krew's All OS Off / On (G7, the owner 2026-11-01: "How is someone supposed to shut down completly?").

    python3 janitor/krew_os_power.py status            what krew runs now, and the last Off
    python3 janitor/krew_os_power.py off [--dry-run]    stop every running unit of krew's (the registry's krew- and
                                                         hermes- units, user and system scope); the snapshot names them
    python3 janitor/krew_os_power.py on [--dry-run]     start exactly what the last Off stopped, then forget it

The units are THE registry's (krew_registry.refresh: the same discovery the janitor and the polkit rule read), so a unit
krew installs later is covered without an edit here. The Crissa kit's own units (crissa-*) are the kit's to stop: with
the kit installed, its Control Panel's All OS Off already stops krew too, through krew's panel manifest; this is the
switch for a machine that runs krew alone, and the one the kit's Off can call. System-scope units stop and start
through the polkit rule (config/polkit/51-krew-os-power.rules, krew_registry.py polkit); a refusal is named, never
silent.

Order: Off stops timers, paths and sockets first (nothing new starts behind it), then the services; On starts the
services first, then what triggers them. Every stop and start is checked by the unit's ActiveState afterwards.
The snapshot: $KREW_HOME/state/os-power.json. Off never overwrites an Off that has not been turned back On (a second
Off adds what is still running to it). Kill: KREW_OS_POWER=0 refuses both verbs.

Tests never reach the real service manager: under pytest the default runner refuses (pass a runner).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_registry as R  # noqa: E402
import krew_settings  # noqa: E402

SNAPSHOT = Path(os.environ.get("KREW_OS_POWER_SNAPSHOT") or krew_settings.krew_home() / "state" / "os-power.json")
OURS = ("krew-", "hermes-")                     # the kit's crissa-* units are the kit's Off
ROLES = ("service", "timer", "path", "socket")
FIRST = (".timer", ".path", ".socket")          # stopped first, started last


def on() -> bool:
    return os.environ.get("KREW_OS_POWER", "1").strip().lower() not in ("0", "false", "no", "off")


def safe_run(argv: list, timeout: float = 60.0):
    """The real runner, refused under a test (a test passes its own)."""
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("KREW_OS_POWER_REAL") != "1":
        raise RuntimeError("under a test, a real %s is refused (pass a runner)" % (argv[0] if argv else "command"))
    return R.default_run(argv, timeout)


def ours(reg: dict) -> list:
    return [r for r in R.units(reg, roles=ROLES) if str(r.get("unit") or "").startswith(OURS)]


def active(rows: list, run) -> dict:
    """{(scope, unit): ActiveState} for the rows, one show per scope."""
    out = {}
    for scope in ("user", "system"):
        names = [r["unit"] for r in rows if r.get("scope") == scope]
        if names:
            for u, p in R.show(scope, names, run=run, props=("Id", "ActiveState")).items():
                out[(scope, u)] = p.get("ActiveState") or "unknown"
    return out


def _order(rows: list, stopping: bool) -> list:
    first = [r for r in rows if r["unit"].endswith(FIRST)]
    rest = [r for r in rows if not r["unit"].endswith(FIRST)]
    return first + rest if stopping else rest + first


def read_snapshot(path: Path | None = None) -> dict:
    try:
        return json.loads(Path(path or SNAPSHOT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _act(verb: str, rows: list, run, dry: bool) -> list:
    done = []
    for r in rows:
        argv = R.sc(r["scope"]) + [verb, r["unit"]]
        if dry:
            done.append({"unit": r["unit"], "scope": r["scope"], "would": " ".join(argv)})
            continue
        rc, _out, err = run(argv, 90.0)
        done.append({"unit": r["unit"], "scope": r["scope"], "rc": rc, "err": (err or "").strip()[:200]})
    return done


def off(reg: dict | None = None, run=safe_run, *, dry: bool = False, snapshot: Path | None = None) -> dict:
    if not on():
        return {"ok": False, "refused": "KREW_OS_POWER=0"}
    reg = R.load() if reg is None else reg
    rows = ours(reg)
    st = active(rows, run)
    up = [r for r in rows if st.get((r["scope"], r["unit"])) in ("active", "activating", "reloading")]
    acts = _act("stop", _order(up, True), run, dry)
    rep = {"ok": True, "dry": dry, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "stopped": acts, "failed": []}
    if dry:
        return rep
    after = active(up, run)
    rep["failed"] = [{"unit": r["unit"], "scope": r["scope"], "state": after.get((r["scope"], r["unit"]))}
                     for r in up if after.get((r["scope"], r["unit"])) not in ("inactive", "failed")]
    rep["ok"] = not rep["failed"]
    snap_path = Path(snapshot or SNAPSHOT)
    snap = read_snapshot(snap_path)
    keep = {(x["scope"], x["unit"]) for x in snap.get("stopped") or []}
    snap_rows = list(snap.get("stopped") or []) + [{"unit": r["unit"], "scope": r["scope"]} for r in up
                                                   if (r["scope"], r["unit"]) not in keep]
    _write(snap_path, {"schema": "krew-os-power/1", "at": rep["at"], "stopped": snap_rows, "failed": rep["failed"]})
    return rep


def turn_on(run=safe_run, *, dry: bool = False, snapshot: Path | None = None) -> dict:
    if not on():
        return {"ok": False, "refused": "KREW_OS_POWER=0"}
    snap_path = Path(snapshot or SNAPSHOT)
    snap = read_snapshot(snap_path)
    rows = [{"unit": x["unit"], "scope": x["scope"]} for x in snap.get("stopped") or []]
    if not rows:
        return {"ok": True, "started": [], "note": "no Off to undo"}
    acts = _act("start", _order(rows, False), run, dry)
    rep = {"ok": True, "dry": dry, "started": acts, "failed": []}
    if dry:
        return rep
    after = active(rows, run)
    def bad(r) -> bool:
        s = after.get((r["scope"], r["unit"]))
        # a service may have run and exited (a oneshot): only `failed` is a failure; a timer/path/socket must be up
        return s == "failed" if r["unit"].endswith(".service") else s not in ("active", "activating")
    rep["failed"] = [{"unit": r["unit"], "scope": r["scope"], "state": after.get((r["scope"], r["unit"]))}
                     for r in rows if bad(r)]
    rep["ok"] = not rep["failed"]
    if rep["ok"]:
        snap_path.unlink(missing_ok=True)
    else:
        _write(snap_path, dict(snap, failed_on=rep["failed"]))
    return rep


def status(reg: dict | None = None, run=safe_run, snapshot: Path | None = None) -> dict:
    reg = R.load() if reg is None else reg
    rows = ours(reg)
    st = active(rows, run)
    return {"running": sorted(u for (s, u), a in st.items() if a in ("active", "activating")),
            "units": len(rows), "last_off": read_snapshot(snapshot)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("verb", choices=("status", "off", "on"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-refresh", action="store_true", help="read the registry as it is (default: refresh it first)")
    a = ap.parse_args(argv)
    reg = None
    if a.verb != "on" and not a.no_refresh:
        R.refresh(R.default_run, by="os-power")
    if a.verb == "status":
        out = status(reg)
    elif a.verb == "off":
        out = off(reg, dry=a.dry_run)
    else:
        out = turn_on(dry=a.dry_run)
    print(json.dumps(out, indent=1))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
