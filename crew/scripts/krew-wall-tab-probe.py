#!/usr/bin/env python3
"""Does the Wall's page still answer? Report only (opt-in).

    krew-wall-tab-probe.py tick     probe, keep the count, file ONE card per dead episode (the timer runs this)
    krew-wall-tab-probe.py probe    one reading, nothing written, nothing filed
    krew-wall-tab-probe.py cdp-tick | cdp-probe   the same through the browser's CDP (crew/lib/wall_tab_probe.py: the
                                    page itself answers one Runtime.evaluate; glass mode `brave` only)

Through the Wall's player only (ZOEYOS_PLAYER, default http://127.0.0.1:8100): GET /v1/state carries `glass_lag`,
one entry per glass that paints the player's frames, each with `fresh` (the page answered within its window) and
`age_s`. A player that answers with no fresh glass is a dead page; a player that does not answer is "Crissa not
installed" (nothing to probe, nothing filed). Never reloads, navigates, raises or restarts anything.

A dead episode is DEAD_TICKS consecutive dead readings; one card per episode (assignee the front door, blocked
needs_input), closed by this probe when the page answers again. Cards only with KREW_WALL_TAB_PROBE_CARDS=1 (else
`would_card`). Kill: KREW_WALL_TAB_PROBE=0 or $KREW_HOME/krew-wall-tab-probe.off (the launcher honours it).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KREW_HOME = Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))
PLAYER = (os.environ.get("ZOEYOS_PLAYER") or "http://127.0.0.1:8100").rstrip("/")
STATE = KREW_HOME / "wall-tab-probe.json"
DEAD_TICKS = int(os.environ.get("KREW_WALL_TAB_PROBE_DEAD_TICKS", "3"))
PROFILE = os.environ.get("KREW_WALL_TAB_PROBE_PROFILE", "zoey")
NOT_INSTALLED = "Crissa not installed"


def enabled() -> bool:
    return str(os.environ.get("KREW_WALL_TAB_PROBE", "1")).strip().lower() not in ("0", "false", "no", "off")


def cards_on() -> bool:
    return str(os.environ.get("KREW_WALL_TAB_PROBE_CARDS", "0")).strip().lower() in ("1", "true", "yes", "on")


def probe(player: str | None = None, timeout: float = 4.0) -> dict:
    """{installed, alive, glasses, why}: one reading of the player."""
    base = (player or PLAYER).rstrip("/")
    try:
        with urllib.request.urlopen(base + "/v1/state", timeout=timeout) as r:   # noqa: S310 - a local URL
            st = json.loads(r.read(512 * 1024).decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"installed": True, "alive": False, "glasses": {}, "why": f"the player said {e.code}"}
    except (OSError, ValueError) as e:
        return {"installed": False, "alive": False, "glasses": {}, "why": f"{NOT_INSTALLED} (no player at {base}): {e}"[:200]}
    lag = st.get("glass_lag") if isinstance(st, dict) else None
    if not isinstance(lag, dict) or not lag:
        return {"installed": True, "alive": False, "glasses": {},
                "why": "the player reports no glass (glass_lag empty): no page is painting its frames"}
    glasses = {k: {"fresh": bool(v.get("fresh")), "age_s": v.get("age_s")} for k, v in lag.items() if isinstance(v, dict)}
    alive = any(g["fresh"] for g in glasses.values())
    return {"installed": True, "alive": alive, "glasses": glasses,
            "why": "" if alive else "no glass has painted a fresh frame: " + ", ".join(
                f"{k} {g['age_s']}s" for k, g in glasses.items())}


def _hermes() -> str:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
        import krew_settings  # noqa: E402
        return krew_settings.hermes_bin()
    except Exception:  # noqa: BLE001
        return os.environ.get("KREW_HERMES_BIN") or os.path.expanduser("~/.local/bin/hermes")


def _kanban(*args: str, timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run([_hermes(), "-p", PROFILE, "kanban", *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def file_card(reading: dict, now: float, kanban=None) -> str:
    k = kanban or _kanban
    rc, out = k("create", "The Wall's page is not answering", "--assignee", PROFILE, "--created-by", "krew-wall-tab-probe",
                "--body", f"krew-wall-tab-probe at {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}: {reading['why']}\n"
                          f"The player answers; no glass has painted a fresh frame for {DEAD_TICKS} readings. "
                          f"This probe never reloads; `park` if the page is meant to be down.",
                "--idempotency-key", f"krew-wall-tab-probe:{int(now // 3600)}")
    m = re.search(r"\b(t_[0-9a-f]{8})\b", out)
    if rc != 0 or not m:
        return ""
    tid = m.group(1)
    rc2, _ = k("block", tid, "The Wall's page stopped answering; see the body.", "--kind", "needs_input")  # reason FIRST
    if rc2 != 0:
        k("archive", tid)
        return ""
    return tid


def tick(now: float | None = None, *, reading: dict | None = None, kanban=None) -> dict:
    now = now or time.time()
    if not enabled():
        return {"skipped": "off"}
    r = probe() if reading is None else reading
    try:
        st = json.loads(STATE.read_text())
    except (OSError, ValueError):
        st = {}
    if not r["installed"]:
        st.update(dead=0, last=dict(r, ts=now, action=""))
        _save(st)
        return dict(r, action="")
    dead = 0 if r["alive"] else int(st.get("dead") or 0) + 1
    ep = st.get("episode")
    action = ""
    if dead >= DEAD_TICKS and not ep:
        if cards_on():
            card = file_card(r, now, kanban)
            ep = {"since": now, "card": card}
            action = f"card {card or 'FAILED'}"
        else:
            ep = {"since": now, "card": ""}
            action = "would_card"
    elif ep and r["alive"]:
        if ep.get("card"):
            (kanban or _kanban)("complete", ep["card"], "--summary", "The page answers again.")
        action = "episode closed"
        ep = None
    st.update(dead=dead, episode=ep, last=dict(r, ts=now, action=action))
    _save(st)
    return dict(r, dead=dead, action=action)


def _save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, STATE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("verb", choices=("tick", "probe", "cdp-tick", "cdp-probe"))
    a = ap.parse_args(argv)
    if a.verb.startswith("cdp-"):
        # the house's own probe (crew/lib/wall_tab_probe.py): asks the Wall page itself one Runtime.evaluate through
        # the browser's CDP while the glass mode is `brave`; the player path above needs no browser access
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "krew_own_wall_tab_probe", Path(__file__).resolve().parents[1] / "lib" / "wall_tab_probe.py")   # by path
        wall_tab_probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wall_tab_probe)
        out = wall_tab_probe.tick() if a.verb == "cdp-tick" else wall_tab_probe.probe()
    else:
        out = tick() if a.verb == "tick" else probe()
    print(time.strftime("%H:%M:%S"), "krew-wall-tab-probe:", json.dumps(out, sort_keys=True), flush=True)
    return 0                       # a dead page is a REPORT, never a failed unit


if __name__ == "__main__":
    raise SystemExit(main())
