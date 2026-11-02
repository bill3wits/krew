#!/usr/bin/env python3
"""Run one restart-window after-check and post its RESULT line on the window's card (the User 2026-10-03).

Launched by krew-restart-window.py as its own transient unit, so a check that waits for a conversation to end never
holds the window's 5-minute tick.   restart-after-check.py --card t_x --unit ear-bridge.service

krew: the rows are $KREW_HOME/restart-window-after.json (shape: config/restart-window-after.example.json; none by
default), the state $KREW_HOME/state/restart-after-checks.json. Off: KREW_RESTART_AFTER_CHECKS=0.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "restart-window-after.json"
sys.path.insert(0, str(ROOT / "lib"))
import krew_window_settings as _KWS  # noqa: E402
import text_cut  # noqa: E402
CONFIG = _KWS.settings_file("restart-window-after")
STATE = _KWS.krew_home() / "state" / "restart-after-checks.json"


def enabled(env=None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_RESTART_AFTER_CHECKS", "1")).strip().lower() not in ("0", "false", "no", "off")


def rows(path: Path | None = None) -> dict:
    try:
        return json.loads(Path(path or CONFIG).read_text()).get("units") or {}
    except (OSError, ValueError):
        return {}


def load_state(path: Path | None = None) -> dict:
    try:
        return json.loads(Path(path or STATE).read_text())
    except (OSError, ValueError):
        return {}


def due(unit: str, *, cfg: dict | None = None, state: dict | None = None) -> bool:
    row = (cfg if cfg is not None else rows()).get(unit)
    if not row:
        return False
    st = state if state is not None else load_state()
    return not (row.get("once") and (st.get(unit) or {}).get("done"))


def result_line(out: str) -> str:
    lines = [l for l in (out or "").splitlines() if l.startswith("RESULT:")]
    return lines[-1] if lines else ("RESULT: FAIL - the check printed no RESULT line: "
                                     + text_cut.clip((out or "").strip(), 200, label="after-check-output", tail=True, hard=False))


def run(unit: str, card: str, *, runner=subprocess.run, comment=None) -> str:
    row = rows().get(unit) or {}
    if not row:
        return ""
    cmd = [os.path.expanduser(c) for c in row["cmd"]]
    env = dict(os.environ, **{k: str(v) for k, v in (row.get("env") or {}).items()})
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=float(row.get("timeout_s", 600)), env=env)
        line = result_line((p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        line = "RESULT: FAIL - the check timed out"
    text = f"after-check {row.get('name', unit)} ({unit}): {line}"
    if comment is None:
        import restart_window as rw
        hermes = _KWS.hermes_bin()
        subprocess.run([hermes, "-p", "ada", "kanban", "comment", card, text_cut.clip(text, 4000, label="after-check-comment"), "--author", rw.AUTHOR],
                       capture_output=True, text=True, timeout=60)
    else:
        comment(card, text)
    st = load_state()
    # HELD/OFF did not test anything: only a PASS or FAIL spends a once-row
    st[unit] = {"ts": time.time(), "card": card, "result": line,
                "done": line.startswith(("RESULT: PASS", "RESULT: FAIL"))}
    STATE.parent.mkdir(parents=True, exist_ok=True); tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(st, indent=1)); os.replace(tmp, STATE)
    return line


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--card", required=True); ap.add_argument("--unit", required=True)
    a = ap.parse_args(argv)
    if not enabled():
        print("after-checks off"); return 0
    print(run(a.unit, a.card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
