#!/usr/bin/env python3
"""Would the lean hands kit have held the tools each stored hands run actually called? Read-only.

  scripts/hands-kit-backtest.py                 # every stored run
  scripts/hands-kit-backtest.py --days 7 --json --out <file>

A hands run is a Crissa session whose first message is the hands prompt; its job is the text after
"Job:". The router is scripts/crissa-hands.py's own `kit_for(ask, lean=True)`; the tool -> toolset map is
her CLI kit as scripts/krew-tool-tax.py resolves it today. A MISS is a run the router would dress
lean (or display) that called a tool outside that kit.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB = KH.hermes_home() / "profiles" / "crissa" / "state.db"
ALIASES = {"browser_cdp": "browser", "browser_dialog": "browser", "browser_exec": "browser",
           "computer_use": "computer_use"}


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hands_runs(db: Path, since: float = 0.0) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    runs = []
    try:
        for sid, started in con.execute("select id, started_at from sessions where source in ('oneshot','cli') "
                                        "and started_at >= ? order by started_at", (since,)):
            first = con.execute("select content from messages where session_id=? and role='user' order by id limit 1",
                                (sid,)).fetchone()
            if not first or not any(p in (first[0] or "") for p in ("You are Crissa's hands", "You are Crissa's hands")):   # runs from before the rename (2026-10-10) too
                continue
            m = re.search(r"\nJob: (.*)\Z", first[0], re.S)
            tools = []
            for (tc,) in con.execute("select tool_calls from messages where session_id=? and role='assistant' "
                                     "and tool_calls is not null", (sid,)):
                try:
                    tools += [(t.get("function") or {}).get("name") for t in json.loads(tc)]
                except (ValueError, TypeError, AttributeError):
                    pass
            runs.append({"session": sid, "at": started, "ask": (m.group(1) if m else "").strip(), "tools": tools})
    finally:
        con.close()
    return runs


def backtest(runs: list[dict], toolset_of: dict[str, str], kit_for) -> dict:
    misses, worn = [], collections.Counter()
    for r in runs:
        kit = kit_for(r["ask"], lean=True)
        worn[kit or "full"] += 1
        if kit is None:
            continue
        have = set(kit.split(","))
        need = {toolset_of[t] for t in r["tools"] if t in toolset_of}
        lacking = sorted(need - have - ({"skills"} if "skill_read" in have and set(r["tools"]) & {"skill_view"}
                                        and not set(r["tools"]) & {"skills_list", "skill_manage"} else set()))
        if lacking:
            misses.append({"session": r["session"], "kit": kit, "lacking": lacking, "ask": r["ask"][:200]})
    return {"runs": len(runs), "worn": dict(worn), "misses": len(misses),
            "lacking": dict(collections.Counter(",".join(m["lacking"]) for m in misses).most_common()), "rows": misses}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=0.0)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    hands = _load("crissa_hands_bt", "crissa-hands.py")
    tax = _load("krew_tool_tax_bt", "krew-tool-tax.py")
    kit = tax.measure("crissa", "cli")
    if "rows" not in kit:
        print(f"cannot size her CLI kit: {kit.get('error')}", file=sys.stderr)
        return 2
    toolset_of = {r["tool"]: r["toolset"] for r in kit["rows"]}
    toolset_of.update(ALIASES)
    lean = tax.measure("crissa", toolsets=hands.LEAN_TOOLSETS)
    rep = backtest(hands_runs(Path(a.db), time.time() - a.days * 86400 if a.days else 0.0), toolset_of, hands.kit_for)
    rep.update({"made": time.strftime("%Y-%m-%dT%H:%M:%S"), "lean_toolsets": hands.LEAN_TOOLSETS,
                "full_kit": {"tools": kit["sent_tools"], "bytes": kit["sent_bytes"]},
                "lean_kit": {"tools": lean.get("sent_tools"), "bytes": lean.get("sent_bytes")}})
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        n = rep["runs"] or 1
        print(f"{rep['runs']} hands runs; kit worn under the lean default: {rep['worn']}")
        print(f"full kit {rep['full_kit']['tools']} tools / {rep['full_kit']['bytes']:,} B; "
              f"lean kit {rep['lean_kit']['tools']} tools / {rep['lean_kit']['bytes']:,} B")
        print(f"misses (a tool outside the kit was called): {rep['misses']} ({100 * rep['misses'] / n:.2f} %)  {rep['lacking']}")
        for m in rep["rows"][:12]:
            print(f"  {m['lacking']} | {m['ask'][:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
