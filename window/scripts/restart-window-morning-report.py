#!/usr/bin/env python3
"""Morning report: which owed units last night's restart window restarted, held, failed or left owed (the User
2026-10-01). Read-only - it restarts nothing.

  restart-window-morning-report.py [--since "2026-10-01 19:55"] [--units u1,u2,...]

For each unit: `restarted HH:MM` when it (re)entered active inside the window (systemd ActiveEnterTimestamp), the
executor's OK/FAILED line from the journal, any `deferred <unit>: <why>` it wrote, and whether the window's own
facts still list it as owed. Plus the window's busy_now holds (counted) so a quiet night reads as such.
Writes $KREW_DELIVERABLES/restart-window/<day>.md and LATEST.md (default ~/Desktop/krew-deliverables).

--confirm-clear (the User 2026-10-01: everything owed was restarted by hand that morning and the night's window was
cancelled with $KREW_HOME/restart-window.off): put the window back on (remove the flag it was cancelled with) and
report only whether the owed list - the window's facts and $KREW_HOME/state/restart-owed.json - is still clear, plus any
restart the window made overnight anyway.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import restart_window as rw  # noqa: E402
import text_cut  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# krew: the deliverables inbox is KREW_DELIVERABLES (the house kept it on the Desktop)
OUT = Path(os.path.expanduser(os.environ.get("KREW_DELIVERABLES") or "~/Desktop/krew-deliverables")) / "restart-window"
# krew: the units a night left for the window are the machine's own: KREW_MORNING_REPORT_UNITS (comma list),
# else the window's current owed list. (The house named its thirteen here.)
LEFT_FOR_TONIGHT = [u.strip() for u in os.environ.get("KREW_MORNING_REPORT_UNITS", "").split(",") if u.strip()]


def _run(cmd: list[str], timeout: int = 120) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def active_enter(unit: str) -> dt.datetime | None:
    out = _run(["systemctl", "--user", "show", f"{unit}.service", "-p", "ActiveEnterTimestamp", "--value"]).strip()
    if not out:
        return None
    try:
        m = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", out)   # systemd: "Day YYYY-MM-DD HH:MM:SS TZ"
        return dt.datetime.strptime(m.group(0), "%Y-%m-%d %H:%M:%S") if m else None
    except ValueError:
        return None


def _hhmm(stamp: str) -> str:
    """HH:MM out of a journal "YYYY-MM-DD HH:MM:SS" stamp (a time, not a cut of text)."""
    m = re.search(r"(\d{2}:\d{2}):\d{2}", stamp)
    return m.group(1) if m else stamp


def journal(since: str) -> str:
    return _run(["journalctl", "--user", "--since", since, "--no-pager", "-o", "cat"], timeout=180)


def still_owed() -> dict[str, str] | None:
    out = _run([sys.executable, str(ROOT / "scripts" / "krew-restart-window.py"), "facts"], timeout=180)
    try:
        d, _ = json.JSONDecoder().raw_decode(out[out.index("{"):])
    except ValueError:
        return None
    return {o["unit"].removesuffix(".service"): o.get("why", "") for o in d.get("facts", {}).get("owed", [])}


def classify(units: list[str], since: dt.datetime, log: str, enter: dict, owed: dict | None) -> list[dict]:
    rows = []
    for u in units:
        ok = re.findall(rf"^(\S+ \S+) {re.escape(u)}\.service: (OK|FAILED)\b(.*)$", log, re.M)
        deferred = re.findall(rf"deferred {re.escape(u)}\.service: (.*)$", log, re.M)
        t = enter.get(u)
        restarted = t is not None and t >= since
        if ok and ok[-1][1] == "FAILED":
            state = f"FAILED {_hhmm(ok[-1][0])}: " + text_cut.clip(ok[-1][2].strip(), 80, label="morning-report-failed", hard=False)
        elif restarted:
            state = f"restarted {t:%H:%M}" + (" (executor OK)" if ok else "")
        elif deferred:
            state = "held: " + text_cut.clip(deferred[-1].strip(), 80, label="morning-report-held", hard=False)
        else:
            state = "not restarted"
        rows.append({"unit": u, "state": state,
                     "still_owed": None if owed is None else (owed.get(u) or "")})
    return rows


def holds(log: str) -> collections.Counter:
    c = collections.Counter()
    for m in re.finditer(r"user busy: (.+)$", log, re.M):
        c[text_cut.clip(re.sub(r"\d+ s ago", "N s ago", m.group(1).strip()), 70, label="morning-report-hold", hard=False)] += 1
    return c


def render(day: str, since: dt.datetime, rows: list[dict], hold: collections.Counter, owed_ok: bool) -> str:
    L = [f"# Restart window report - night of {since:%Y-%m-%d}", "",
         f"Units the User left for the window (since {since:%Y-%m-%d %H:%M}). Read-only.", "",
         "| unit | last night | still owed now |", "|---|---|---|"]
    for r in rows:
        so = "?" if not owed_ok else (r["still_owed"] or "no")
        L.append(f"| {r['unit']} | {r['state']} | {so} |")
    L += ["", "busy_now holds during the night:" if hold else "No busy_now holds logged."]
    L += [f"- {n}x {why}" for why, n in hold.most_common(6)]
    return "\n".join(L) + "\n"


# krew: the window's own flag and owed file (window/lib/restart_window.py), under $KREW_HOME
OFF_FLAG = rw.OFF_FLAG
OWED_FILE = rw.CONFIG_OWED


def confirm_clear(since: dt.datetime, log: str, owed: dict | None, owed_rows: dict | None, flag_was: bool) -> str:
    ran = re.findall(r"^(\S+ \S+) (\S+)\.service: (OK|FAILED)\b", log, re.M)
    L = [f"# Restart window - is the owed list clear? ({dt.date.today().isoformat()})", "",
         "The User restarted every owed unit by hand and cancelled that night's window. Read-only.", "",
         f"- window flag: {'was off (cancelled for the night); removed now - the window is back on' if flag_was else 'was not off - the window was on all night'}",
         f"- the window's own facts: " + ("unreadable" if owed is None else ("CLEAR - nothing owed" if not owed else
            f"{len(owed)} owed: " + "; ".join(f"{u} ({w})" for u, w in sorted(owed.items())))),
         f"- {OWED_FILE}: " + ("unreadable" if owed_rows is None else ("CLEAR" if not owed_rows else
            f"{len(owed_rows)} row(s): " + ", ".join(sorted(owed_rows)))),
         f"- restarts by the window since {since:%m-%d %H:%M}: " + (", ".join(f"{u} {s} {_hhmm(t)}" for t, u, s in ran) or "none")]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=os.environ.get("KREW_MORNING_REPORT_SINCE") or (dt.date.today() - dt.timedelta(days=1)).strftime("%Y-%m-%d 19:55"))
    ap.add_argument("--units", default=",".join(LEFT_FOR_TONIGHT))
    ap.add_argument("--confirm-clear", action="store_true")
    a = ap.parse_args(argv)
    since = dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M")
    if a.confirm_clear:
        flag_was = OFF_FLAG.exists()
        OFF_FLAG.unlink(missing_ok=True)
        try:
            rows = json.loads(OWED_FILE.read_text() or "{}")
        except FileNotFoundError:
            rows = {}
        except (OSError, ValueError):
            rows = None
        text = confirm_clear(since, journal(a.since), still_owed(), rows, flag_was)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{dt.date.today().isoformat()}.md").write_text(text)
        (OUT / "LATEST.md").write_text(text)
        print(text)
        return 0
    units = [u.strip().removesuffix(".service") for u in a.units.split(",") if u.strip()]
    owed = still_owed()
    if not units and owed is not None:
        units = sorted(owed)
    log = journal(a.since)
    rows = classify(units, since, log, {u: active_enter(u) for u in units}, owed)
    text = render(dt.date.today().isoformat(), since, rows, holds(log), owed is not None)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{dt.date.today().isoformat()}.md").write_text(text)
    (OUT / "LATEST.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
