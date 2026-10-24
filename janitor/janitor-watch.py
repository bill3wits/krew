#!/usr/bin/env python3
"""janitor-watch - who watches the janitor (krew-janitor-watch.timer) (the User 2026-10-06: "one more service to watch the janitor ... a
redundant, robust system that could be self-healing").

Every 10 min (krew-janitor-watch.timer): the janitor must have written a tick row to <metrics_dir>/janitor-<day>.jsonl (janitor.yaml paths.metrics_dir)
within STALE_S, its timer must be active and its last run must not have failed. It writes its own heartbeat
(~/.hermes/janitor-watch.beat); the janitor reads that beat every tick (watcher_tick in krew-janitor.py), so each
watches the other.

Report (default): one card per episode on Zoey's list + ~/.hermes/janitor-watch.jsonl.
Act (KREW_JANITOR_WATCH_ACT=1): also starts ONE janitor run (`systemctl --user start krew-janitor.service`, a oneshot -
not a restart of anything long-running), once per episode. Kill: KREW_JANITOR_WATCH=0 or ~/.hermes/janitor-watch.off.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"

# krew: the janitor's metrics folder (janitor.yaml paths.metrics_dir; its default here)
METRICS = Path(os.path.expanduser(os.environ.get("KREW_JANITOR_METRICS") or
                                  os.path.join(os.environ.get("KREW_HOME") or "~/.krew", "janitor", "metrics")))
BEAT = HERMES_HOME / "janitor-watch.beat"
STATE = HERMES_HOME / "janitor-watch.json"
LOG = HERMES_HOME / "janitor-watch.jsonl"
OFF = HERMES_HOME / "janitor-watch.off"
HERMES = os.environ.get("HERMES_BIN", str(Path.home() / ".local/bin/hermes"))
STALE_S = int(os.environ.get("KREW_JANITOR_STALE_S", str(15 * 60)))      # janitor ticks every 5 min


def on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_JANITOR_WATCH", "1")).strip().lower() not in ("0", "false", "no", "off") \
        and not OFF.exists()


def act_on(env: dict | None = None) -> bool:
    e = os.environ if env is None else env
    return str(e.get("KREW_JANITOR_WATCH_ACT", "0")).strip().lower() in ("1", "true", "yes", "on")


def last_tick(metrics: Path = METRICS, today: date | None = None) -> float | None:
    """Epoch of the janitor's newest tick row (today's file, else yesterday's), or None."""
    d = today or date.today()
    for day in (d, d - timedelta(days=1)):
        p = metrics / f"janitor-{day:%Y%m%d}.jsonl"
        try:
            with p.open("rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 65536))
                tail = f.read().decode("utf-8", "replace").strip().splitlines()
        except OSError:
            continue
        for line in reversed(tail):
            try:
                ts = json.loads(line).get("ts")
                return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, TypeError, AttributeError):
                continue
    return None


def unit_facts(run=subprocess.run) -> dict:
    def show(unit: str, *props: str) -> dict:
        r = run(["systemctl", "--user", "show", unit, *[f"-p{p}" for p in props]], capture_output=True, text=True,
                timeout=20)
        return dict(line.split("=", 1) for line in (r.stdout or "").splitlines() if "=" in line)
    t = show("krew-janitor.timer", "ActiveState")
    s = show("krew-janitor.service", "Result", "ActiveState")
    return {"timer": t.get("ActiveState", ""), "result": s.get("Result", ""), "service": s.get("ActiveState", "")}


def verdict(now: float, tick: float | None, facts: dict) -> list[str]:
    why = []
    if tick is None:
        why.append("no janitor tick row found")
    elif now - tick > STALE_S:
        why.append(f"last janitor tick {int((now - tick) / 60)} min ago (it ticks every 5)")
    if facts.get("timer") != "active":
        why.append(f"krew-janitor.timer is {facts.get('timer') or 'unknown'}")
    if facts.get("result") not in ("success", "", None) and facts.get("service") != "activating":
        why.append(f"last janitor run result: {facts.get('result')}")
    return why


def card(title: str, body: str, key: str, run=subprocess.run) -> str:
    r = run([HERMES, "-p", "zoey", "kanban", "create", title, "--assignee", "zoey", "--body", body[:3500],
             "--created-by", "janitor-watch", "--idempotency-key", key], capture_output=True, text=True, timeout=60)
    m = re.search(r"\bt_[0-9a-f]{8}\b", (r.stdout or "") + (r.stderr or ""))
    if r.returncode != 0 or not m:
        return ""
    run([HERMES, "-p", "zoey", "kanban", "block", m.group(0), "The house janitor looks stopped (details in the card).",
         "--kind", "needs_input"], capture_output=True, text=True, timeout=60)
    return m.group(0)


def main(now: float | None = None, run=subprocess.run) -> int:
    now = time.time() if now is None else now
    if not on():
        print("janitor-watch: switched off")
        return 0
    BEAT.write_text(str(round(now, 1)), encoding="utf-8")
    why = verdict(now, last_tick(), unit_facts(run))
    try:
        st = json.loads(STATE.read_text())
    except (OSError, ValueError):
        st = {}
    row = {"ts": round(now, 1), "ok": not why, "why": why}
    if not why:
        if st.get("episode"):
            row["cleared"] = st["episode"]
        st = {}
    else:
        ep = st.setdefault("episode", {"since": now})
        if act_on() and not ep.get("kicked"):
            r = run(["systemctl", "--user", "start", "--no-block", "krew-janitor.service"], capture_output=True,
                    text=True, timeout=30)
            ep["kicked"] = round(now, 1)
            row["kicked"] = r.returncode == 0
        if not ep.get("card"):
            ep["card"] = card(f"Janitor stopped? {why[0][:80]}",
                              "janitor-watch (the janitor's watcher): " + "; ".join(why) + ".\n"
                              + ("Started one janitor run." if ep.get("kicked") else
                                 "Report-only: nothing was started (KREW_JANITOR_WATCH_ACT=1 to start one run).")
                              + "\nCheck: systemctl --user status krew-janitor.timer krew-janitor.service; "
                                "journalctl --user -u krew-janitor -n 50.",
                              f"janitor-watch:{time.strftime('%Y-%m-%d-%H', time.localtime(ep['since']))}", run=run)
        row["card"] = ep.get("card")
        print("janitor-watch RED: " + "; ".join(why))
    STATE.write_text(json.dumps(st), encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    if not why:
        print("janitor-watch ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
