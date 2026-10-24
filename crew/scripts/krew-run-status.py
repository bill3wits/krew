#!/usr/bin/env python3
"""krew-run-status: how a timer-driven crew job's last run ended, as a report the Crissa Control Panel paints.

A timer stays `active (waiting)` while the job it starts fails every run, so the timer's state never says the job
is down. The job's unit runs this as `ExecStopPost=-` (it never fails the job): systemd hands it SERVICE_RESULT,
EXIT_CODE, EXIT_STATUS and INVOCATION_ID, and it writes $KREW_HOME/run-status/<unit>.json in the panel's report
shape {level, headline, epoch}. krew's crew/kanban tile reads it (panel/krew-panel-tiles.py); the panel calls a
report that stops arriving stale and names how long ago it last came.

    krew-run-status.py <unit> [--home KREW_HOME] [--off FILE]
        --home: where run-status/ goes (the unit passes %h/.krew, which krew-crew-install rewrites with
                the install's own home); else $KREW_HOME, else ~/.krew
        --off:  a switch file that, when it exists, means "off by choice"

level: ok (exit 0), warn (off by its switch), bad (anything else). The headline names systemd's result and the
error line of that run's journal (the last Traceback/Error line, else its last line), never just "failed".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

RESULT_WORDS = {   # the Crissa panel's words for systemd's Result, so a tile reads the same either way
    "exit-code": "exit {status}", "signal": "killed by {status}", "core-dump": "crashed ({status}, core dumped)",
    "timeout": "timed out", "watchdog": "stopped answering systemd's watchdog",
    "start-limit-hit": "gave up: restarted too often (start-limit-hit)", "oom-kill": "killed: out of memory",
    "resources": "could not start: a file, folder or user it needs is missing (resources)",
    "exec-condition": "its start condition said no", "condition": "its start condition said no",
}
_ERR = re.compile(r"(Traceback|Error\b|Exception\b|error:|ERROR|FATAL|refused|denied|No such file|not found)", re.I)


def krew_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


def status_file(unit: str, home: Path | None = None) -> Path:
    return (home or krew_home()) / "run-status" / f"{unit}.json"


def pick_reason(lines: list[str]) -> str:
    lines = [ln.strip() for ln in lines if ln.strip()]
    for ln in reversed(lines):
        if _ERR.search(ln) and not ln.startswith("Traceback"):
            return ln
    return lines[-1] if lines else ""


def journal_lines(unit: str, invocation: str) -> list[str]:
    argv = ["journalctl", "--user", "-o", "cat", "--no-pager", "-n", "80"]
    argv += [f"_SYSTEMD_INVOCATION_ID={invocation}"] if invocation else ["-u", unit]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return r.stdout.splitlines() if r.returncode == 0 else []


def report(unit: str, env: dict, *, off: str = "", now: float | None = None, lines=None) -> dict:
    now = time.time() if now is None else now
    result = env.get("SERVICE_RESULT", "")
    code, status = env.get("EXIT_CODE", ""), env.get("EXIT_STATUS", "")
    at = time.strftime("%H:%M", time.localtime(now))
    base = {"epoch": now, "unit": unit, "result": result, "exit_code": code, "exit_status": status,
            "invocation": env.get("INVOCATION_ID", ""), "by": "krew-run-status"}
    if off and Path(os.path.expanduser(off)).exists():
        return dict(base, level="warn", headline=f"off by choice: {off} exists (last tick {at})")
    if result in ("", "success") and code in ("", "exited") and status in ("", "0"):
        return dict(base, level="ok", headline=f"last run ok at {at}")
    words = RESULT_WORDS.get(result, result or "failed").format(status=status or "?")
    lines = journal_lines(unit, env.get("INVOCATION_ID", "")) if lines is None else lines
    why = pick_reason(lines)
    return dict(base, level="bad", headline=f"last run failed at {at}: {words}" + (f" · {why}" if why else
                                                                                 " · its journal has no line"))


def write(unit: str, rep: dict, home: Path | None = None) -> Path:
    f = status_file(unit, home)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, f)
    return f


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("unit")
    ap.add_argument("--home", default="")
    ap.add_argument("--off", default="")
    a = ap.parse_args(argv)
    home = Path(os.path.expanduser(a.home)) if a.home else None
    f = write(a.unit, report(a.unit, dict(os.environ), off=a.off), home)
    print(f"krew-run-status: {a.unit} -> {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
