#!/usr/bin/env python3
"""krew: tmp-checkout-report.py (lifted from the reference install; see crew/README.md)."""









from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sqlite3
import subprocess
from pathlib import Path

HOME = Path.home()
OUT = HOME / "Desktop" / "krew-deliverables" / "tmp-checkout"
STATE = HOME / ".hermes" / "tmp-checkout-report.state.json"
OFF = HOME / ".hermes" / "tmp-checkout-report.off"
REAP_LOG = HOME / ".hermes" / "logs" / "tmp-worktree-reap.jsonl"
T0_LOG = HOME / ".hermes" / "security" / "tier0-hits.jsonl"
BOARD = HOME / ".hermes" / "kanban" / "boards" / "daily-2026-09-16" / "kanban.db"
CARD = "t_42d6ef2e"
ROUTES = ("tmp-checkout", "krew-mode-tests")
START = dt.date(2026, 10, 1)
LAST_DAY = dt.date(2026, 10, 8)
TIMER = "tmp-checkout-report.timer"
GIB = 1024 ** 3


def _jsonl(path: Path):
    try:
        with path.open() as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def reaper_days(log: Path | None = None) -> dict:
    log = log or REAP_LOG
    days: dict = collections.OrderedDict()
    for rec in _jsonl(log):
        day = str(rec.get("ts") or "")[:10]
        if not day:
            continue
        d = days.setdefault(day, {"runs": 0, "modes": set(), "candidates": 0, "would_free": 0, "reap_rows": {},
                                  "keeps": collections.Counter()})
        d["runs"] += 1
        d["modes"].add(rec.get("mode"))
        d["candidates"] = max(d["candidates"], int(rec.get("candidates") or 0))
        d["would_free"] = max(d["would_free"], int(rec.get("would_free") or 0))
        for r in rec.get("rows") or []:
            if r.get("verdict") == "reap":
                d["reap_rows"][r.get("path")] = r
            elif r.get("verdict") == "keep":
                d["keeps"][r.get("why")] += 1
    return days


def card_status(db: Path | None = None, card: str = CARD) -> tuple[str, float | None]:
    db = db or BOARD
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)   # never immutable=1 (stale WAL)
        row = con.execute("SELECT status, completed_at FROM tasks WHERE id = ?", (card,)).fetchone()
        con.close()
    except sqlite3.Error as e:
        return f"unreadable ({e})", None
    return (row[0], row[1]) if row else ("not on the board", None)


def t0_days(since: dt.datetime, log: Path | None = None) -> dict:
    """Hits per day from the MOMENT the card landed (not its date: hits made while it was being done are not
    what the crew does after it)."""
    log = log or T0_LOG
    days: dict = collections.defaultdict(lambda: {r: [] for r in ROUTES})
    for h in _jsonl(log):
        route, ts = h.get("route"), str(h.get("ts") or "")
        if route in ROUTES and ts >= since.isoformat(timespec="seconds"):
            days[ts[:10]][route].append(h)
    return dict(sorted(days.items()))


def missed(state: dict, today: dt.date) -> list[str]:
    done = set(state.get("days") or [])
    first = max(START, dt.date.fromisoformat(min(done))) if done else today
    out, d = [], first
    while d < today:
        if d.isoformat() not in done:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def render(today: dt.date, reaper: dict, status: str, landed: float | None, t0: dict, gaps: list[str]) -> str:
    L = [f"# /tmp-checkout report - {today.isoformat()}", "",
         "Read-only. Arming the reaper and enforcing the two T0 rules are the user's call.", ""]
    if gaps:
        L += [f"**MISSED:** no report on {', '.join(gaps)} (the timer did not run those days).", ""]
    L += ["## Reaper (report-only; krew + both hermes-agent trees)", "",
          "| day | runs | mode | candidates | would free | kept (why) |", "|---|---:|---|---:|---:|---|"]
    clean_days = 0
    for day, d in reaper.items():
        modes = ",".join(sorted(m for m in d["modes"] if m))
        keeps = "; ".join(f"{n}x {w}" for w, n in d["keeps"].most_common(3)) or "-"
        L.append(f"| {day} | {d['runs']} | {modes} | {d['candidates']} | {d['would_free'] / GIB:.2f} GiB | {keeps} |")
        clean_days += int(d["candidates"] == 0)
    for day, d in reaper.items():
        for path, r in d["reap_rows"].items():
            L.append(f"- {day} candidate: `{path}` ({r.get('repo')}, {r.get('age_h')} h, "
                     f"{int(r.get('bytes') or 0) / GIB:.2f} GiB)")
    if any("reap" in d["modes"] for d in reaper.values()):
        L.append("- NOTE: a run was in reap mode.")
    L += ["", f"Clean days so far: {clean_days}. "
          + ("Two or more clean days: arming is a drop-in `Environment=KREW_TMP_WORKTREE_REAP=1` on "
             "tmp-worktree-reap.service - the user's call." if clean_days >= 2 else "Not enough clean days yet."), ""]
    L += ["## T0 report-only rules (tmp-checkout, krew-mode-tests)", ""]
    if status != "done":
        L.append(f"{CARD} not landed yet (status {status}); both rules stay report-only.")
    else:
        since = dt.datetime.fromtimestamp(landed) if landed else dt.datetime.combine(START, dt.time())
        t0 = t0_days(since)
        L += [f"{CARD} landed {since:%Y-%m-%d %H:%M}. Flags per day since:", "",
              "| day | tmp-checkout | krew-mode-tests | sample |", "|---|---:|---:|---|"]
        rows = dict(t0)
        if not rows:
            L.append(f"| {today.isoformat()} | 0 | 0 | - |")
        for day, v in rows.items():
            sample = next((f"{h.get('profile')}: `{(h.get('snippet') or '')[:90]}`" for r in ROUTES for h in v[r]), "-")
            L.append(f"| {day} | {len(v['tmp-checkout'])} | {len(v['krew-mode-tests'])} | {sample} |")
        last = list(rows.values())[-1] if rows else {r: [] for r in ROUTES}
        total = sum(len(last[r]) for r in ROUTES)
        L += ["", "Rule fix 2026-10-01 06:3x (before any enforce decision): krew-mode-tests matches only a pytest that "
              "is actually RUN - in command position, quote-aware. A grep, echo, prose or a file that merely contains "
              "`pytest tests/` no longer flags (the 05:55 steward cases). 7-day replay: 175 -> 194 = 10 text-only "
              "matches gone, 29 real multi-line runs (`cd krew` then `$PY -m pytest tests`) now caught. "
              "Hits before the fix used the old pattern."]
        L += ["", "Near zero on the latest day: enforcement is the user's decision." if total <= 1
              else f"Latest day still flags {total}: not near zero yet."]
    if today >= LAST_DAY:
        L += ["", f"**This is the last report: it expires after {LAST_DAY.isoformat()} and its timer is now disabled.**"]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now")
    a = ap.parse_args(argv)
    if OFF.exists():
        print(f"tmp-checkout-report: off ({OFF})")
        return 0
    today = dt.date.fromisoformat(a.now) if a.now else dt.date.today()
    if today > LAST_DAY:
        print(f"tmp-checkout-report: expired after {LAST_DAY}")
        subprocess.run(["systemctl", "--user", "disable", "--now", TIMER], capture_output=True)
        return 0
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    gaps = missed(state, today)
    status, landed = card_status()
    text = render(today, reaper_days(), status, landed, {}, gaps)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{today.isoformat()}.md").write_text(text)
    (OUT / "LATEST.md").write_text(text)
    state["days"] = sorted(set(state.get("days") or []) | {today.isoformat()})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    print(text)
    if today >= LAST_DAY:
        subprocess.run(["systemctl", "--user", "disable", "--now", TIMER], capture_output=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
