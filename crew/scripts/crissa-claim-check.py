#!/usr/bin/env python3
"""Did Crissa say something was filed, ruled or shown with no tool reply behind it? REPORT ONLY.

  scripts/crissa-claim-check.py                       # her voice turns, last 7 days
  scripts/crissa-claim-check.py --since "2026-09-30" --json --out <file>

For every turn of her mouth (api_server sessions) it reads what she said last and the replies her
tools have handed her in that session so far. A claim is confirmed by a `confirmed:` line in a hands
reply (since 09-30), or, for older turns, by the tool replies of the hands run that reply names. It
changes nothing and speaks nothing. The check itself is ARMED on the voice path since 09-30
(rail-voice ear_bridge `claim_checked`, kill EAR_BRIDGE_CLAIM_CHECK=0); this is the audit of it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import hands_confirm as hc  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

DB = KH.hermes_home() / "profiles" / "crissa" / "state.db"
RUN_ID = re.compile(r"session_id:\s*(\S+)")


def turns(con: sqlite3.Connection, since: float, source: str = "api_server"):
    """(session, at, said, [tool replies]) per user turn: `said` is the turn's last assistant text."""
    q = ("select m.session_id, m.role, m.content, m.timestamp from messages m join sessions s on s.id = m.session_id "
         "where s.source = ? and s.started_at >= ? order by m.session_id, m.id")
    cur, out = None, []
    for sid, role, content, ts in con.execute(q, (source, since)):
        if role == "user" or cur is None or cur["session"] != sid:
            if cur and cur["said"]:
                out.append(cur)
            earlier = list(cur["replies"]) if cur and cur["session"] == sid else []
            cur = {"session": sid, "at": ts, "said": "", "replies": earlier}     # proof is the session's, not the turn's
            if role == "user":
                continue
        if role == "tool":
            cur["replies"].append(content or "")
        elif role == "assistant" and (content or "").strip():
            cur["said"] = content.strip()
    if cur and cur["said"]:
        out.append(cur)
    return out


def check_turn(turn: dict, db: Path) -> dict | None:
    kinds = hc.claims(turn["said"])
    if not kinds:
        return None
    effects = hc.effects_in(turn["replies"])
    for reply in turn["replies"]:
        for run in RUN_ID.findall(reply):
            effects += hc.confirmed(hc.run_facts(db, run))
    miss = hc.unconfirmed(turn["said"], effects)
    return {"session": turn["session"], "at": dt.datetime.fromtimestamp(turn["at"]).isoformat(timespec="seconds"),
            "claims": kinds, "unconfirmed": miss, "confirmed": [e["kind"] for e in effects],
            "said": turn["said"][:300],
            "would_say": (hc.strip_claims(turn["said"], miss) + " I could not confirm that.").strip() if miss else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    since = (dt.datetime.strptime(a.since, "%Y-%m-%d").timestamp() if a.since and len(a.since) == 10
             else dt.datetime.strptime(a.since, "%Y-%m-%d %H:%M").timestamp() if a.since else time.time() - 7 * 86400)
    db = Path(a.db)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        all_turns = turns(con, since)
    finally:
        con.close()
    rows = [r for r in (check_turn(t, db) for t in all_turns) if r]
    bad = [r for r in rows if r["unconfirmed"]]
    report = {"made": dt.datetime.now().isoformat(timespec="seconds"), "turns": len(all_turns),
              "turns_with_a_claim": len(rows), "unconfirmed": len(bad), "mode": "report-only", "rows": bad}
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(report, indent=1), encoding="utf-8")
        tmp.replace(out)
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"{len(all_turns)} voice turns, {len(rows)} made a claim, {len(bad)} had no tool reply behind it (report only)")
        for r in bad:
            print(f"  {r['at']} {r['session']} claims {r['unconfirmed']}: {r['said'][:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
