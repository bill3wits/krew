#!/usr/bin/env python3
"""What a report-only T0 rule would have refused (the User 2026-09-30).

Reads ~/.hermes/security/tier0-hits.jsonl and lists the hits of ONE rule (by route, and a reason
substring) since a time, so the User can decide from the list whether to switch the rule to enforce.
Read-only: it changes no rule and no file but --out.

  t0-report-hits.py --route window --since "2026-09-30 14:10" [--reason unit] [--out FILE]
                    [--safe-root-db PROFILE/state.db]

--safe-root-db (2026-10-01) adds the writes hermes itself refused under HERMES_WRITE_SAFE_ROOT, read from a
profile's session store (mode=ro), so the enforce decision sees the allow list's hits beside the T0 rule's.
Exit 0 = no hits, 1 = hits listed, 2 = the log could not be read.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

LOG = Path(os.environ.get("KREW_T0_LOG", KH.hermes_home() / "security/tier0-hits.jsonl"))


def hits(log: Path, route: str, since: dt.datetime, reason: str = "") -> list[dict]:
    out = []
    with log.open() as fh:
        for line in fh:
            try:
                h = json.loads(line)
                ts = dt.datetime.fromisoformat(h.get("ts") or "")
            except (ValueError, TypeError):
                continue
            if ts >= since and h.get("route") == route and reason in (h.get("reason") or ""):
                out.append(h)
    return out


SAFE_ROOT_MARK = "outside HERMES_WRITE_SAFE_ROOT"


def safe_root_hits(db: Path, since: dt.datetime) -> list[dict]:
    """hermes's own safe-root refusals (tool results) since a time; read-only, never `immutable`."""
    import sqlite3
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("SELECT timestamp, tool_name, content FROM messages WHERE role = 'tool' "
                           "AND timestamp >= ? AND content LIKE ? ORDER BY timestamp",
                           (since.timestamp(), f"%{SAFE_ROOT_MARK}%")).fetchall()
    finally:
        con.close()
    out = []
    for ts, tool, content in rows:
        text = str(content or "")
        i = text.find("denied:")
        out.append({"ts": dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds"), "tool": tool or "?",
                    "what": text[i if i >= 0 else 0:][:160]})
    return out


def render_safe_root(rows: list[dict]) -> str:
    lines = ["", "## hermes safe-root refusals (HERMES_WRITE_SAFE_ROOT, already enforced)", ""]
    if not rows:
        return "\n".join(lines + ["None.", ""])
    lines += [f"{len(rows)} refusal(s).", "", "| time | tool | what hermes said |", "|---|---|---|"]
    for h in rows[-60:]:
        lines.append(f"| {h['ts']} | {h['tool']} | {h['what'].replace(chr(10), ' ').replace('|', chr(92) + '|')} |")
    return "\n".join(lines + [""])


def render(rows: list[dict], route: str, since: dt.datetime) -> str:
    lines = [f"# T0 report-only hits: route `{route}` since {since:%Y-%m-%d %H:%M}",
             "", f"Written {dt.datetime.now():%Y-%m-%d %H:%M} by scripts/t0-report-hits.py (read-only).", ""]
    if not rows:
        return "\n".join(lines + ["No hits: nothing the rule would have refused.", ""])
    by = collections.Counter((h.get("profile") or "?", h.get("mode") or "?") for h in rows)
    lines += [f"{len(rows)} hit(s).", "", "| profile | mode | hits |", "|---|---|---:|"]
    lines += [f"| {p} | {m} | {n} |" for (p, m), n in sorted(by.items())]
    lines += ["", "| time | profile | tool | what it saw |", "|---|---|---|---|"]
    for h in rows[-60:]:
        what = (h.get("snippet") or "").replace("\n", " ").replace("|", "\\|")[:140]
        lines.append(f"| {h.get('ts')} | {h.get('profile')} | {h.get('tool')} | {what} |")
    return "\n".join(lines + [""])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--route", required=True)
    ap.add_argument("--since", default="")
    ap.add_argument("--since-env", default="", help="krew: read --since from this environment variable (a unit drop-in sets it); unset = nothing to review, exit 0")
    ap.add_argument("--reason", default="")
    ap.add_argument("--out")
    ap.add_argument("--log", default=str(LOG))
    ap.add_argument("--safe-root-db")
    a = ap.parse_args(argv)
    raw = a.since or (os.environ.get(a.since_env, "") if a.since_env else "")
    if not raw:
        print(f"t0-report-hits: no --since (and {a.since_env or 'no --since-env'} is unset): nothing to review", flush=True)
        return 0
    since = dt.datetime.fromisoformat(raw)
    try:
        rows = hits(Path(a.log), a.route, since, a.reason)
    except OSError as e:
        print(f"t0-report-hits: cannot read {a.log}: {e}", file=sys.stderr)
        return 2
    text = render(rows, a.route, since)
    extra = []
    if a.safe_root_db:
        try:
            extra = safe_root_hits(Path(a.safe_root_db), since)
            text += render_safe_root(extra)
        except Exception as e:  # noqa: BLE001 - the T0 list still lands; say the store was unreadable
            text += f"\n## hermes safe-root refusals\n\nCould not read {a.safe_root_db}: {e}\n"
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)   # krew: $KREW_HOME/docs may not exist yet
        Path(a.out).write_text(text)
    print(text)
    return 1 if (rows or extra) else 0


if __name__ == "__main__":
    raise SystemExit(main())
