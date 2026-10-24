#!/usr/bin/env python3
"""How often do Crissa's hands run out of steps, and how many of their tool calls does a tool refuse?

  scripts/hands-step-cap.py --days 3                       # since the canonical schemas (2026-09-30)
  scripts/hands-step-cap.py --days 3 --out <file.json>     # and append a line to <file>.md

Read-only. A hands run is a Crissa session whose first message is the hands prompt; it hit the cap when
hermes handed it "You've reached the maximum number of tool-calling iterations". A refused call is a
tool reply that says nothing changed / no such action / empty ask (lib/hands_confirm.REFUSAL).
Baseline, 7 days to 09-30: 923 runs, 127 capped (13.8 %), 18.1 % of tool calls refused, 68 of the 127
capped runs had at least one refused call. Raising KREW_HANDS_TURNS is the User's call; this only counts.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import hands_confirm as hc  # noqa: E402

DB = Path.home() / ".hermes" / "profiles" / "zoey" / "state.db"
BASELINE = {"runs": 923, "capped_pct": 13.8, "refused_pct": 18.1}
CAP_NOTICE = "You've reached the maximum number of tool-calling iterations%"


def measure(db: Path, since: float) -> dict:
    spec = importlib.util.spec_from_file_location("hands_kit_backtest_sc", ROOT / "scripts" / "hands-kit-backtest.py")
    bt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt)
    runs = bt.hands_runs(db, since)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    calls = refused = capped = capped_with_refusal = 0
    by = collections.Counter()
    try:
        for r in runs:
            facts = hc.run_facts(db, r["session"])
            bad = [f for f in facts if hc.REFUSAL.search((f["reply"] or "")[:400])]
            calls += len(facts)
            refused += len(bad)
            hit = con.execute("select 1 from messages where session_id=? and role='user' and content like ?",
                              (r["session"], CAP_NOTICE)).fetchone()
            if hit:
                capped += 1
                capped_with_refusal += bool(bad)
                for f in bad:
                    by[f"{f['tool']}:{(f['args'] or {}).get('action', '')}"] += 1
    finally:
        con.close()
    n = len(runs) or 1
    return {"runs": len(runs), "capped": capped, "capped_pct": round(100 * capped / n, 1), "tool_calls": calls,
            "refused": refused, "refused_pct": round(100 * refused / max(calls, 1), 1),
            "capped_with_a_refused_call": capped_with_refusal, "refused_in_capped_by_tool": dict(by.most_common(10))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rep = measure(Path(a.db), time.time() - a.days * 86400)
    rep.update({"made": time.strftime("%Y-%m-%dT%H:%M:%S"), "days": a.days, "baseline_7d_to_0930": BASELINE})
    line = (f"{rep['made']}: {rep['runs']} hands runs in {a.days:g} days, {rep['capped']} hit the step cap "
            f"({rep['capped_pct']} %, was {BASELINE['capped_pct']} %); {rep['refused_pct']} % of tool calls refused "
            f"(was {BASELINE['refused_pct']} %); {rep['capped_with_a_refused_call']} capped runs had a refused call. "
            f"Raising KREW_HANDS_TURNS is the User's call.")
    print(line)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
        with out.with_suffix(".md").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
