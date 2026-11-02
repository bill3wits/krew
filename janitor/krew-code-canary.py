#!/usr/bin/env python3
"""code-canary — did the house's installed Python lose its source? (lib/code_canary.py)

  code-canary.py check [--card t_x] [--source worker|janitor] [--json]
      count .py per tree against the baseline; exit 0 clean, 3 on a drop.
      In KREW_CODE_CANARY=act a drop also comments the suspects and asks Crissa once.
  code-canary.py rebaseline [--tree NAME ...]
                                     accept today's counts (after a repair / a planned removal);
                                     --tree accepts one tree and leaves the rest standing
  code-canary.py record [PATH ...]   deep check: every file each dist-info RECORD lists exists
                                     (default: both hermes-agent venvs); exit 3 on a gap
  code-canary.py status              the baseline and the last events

Never repairs anything. Kill: KREW_CODE_CANARY=off.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import code_canary as cc  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--card", default="")
    c.add_argument("--source", default="worker")
    c.add_argument("--json", action="store_true")
    c.add_argument("--scope", choices=("fast", "full"), default="full",
                   help="fast = no service envs (worker exit); full = everything (janitor)")
    rb = sub.add_parser("rebaseline")
    rb.add_argument("--tree", action="append", default=[],
                    help="accept only this tree (repeatable); default: every tree")
    r = sub.add_parser("record")
    r.add_argument("paths", nargs="*")
    sub.add_parser("status")
    a = ap.parse_args(argv)

    if a.cmd == "check":
        if cc.mode() == "off":
            print("[code-canary] off (KREW_CODE_CANARY=off)")
            return 0
        res = cc.check(card=a.card, source=a.source, scope=a.scope)
        # Only the check that FIRST sees an incident acts: when the drop was already standing,
        # the card exiting now did not cause it and must not be named on the board.
        if not res["ok"] and not res.get("repeat"):
            res["acted"] = cc.act(res)
        if a.json:
            print(json.dumps(res))
        elif res["ok"]:
            print(f"[code-canary] ok {res['trees']} trees in {res['ms']} ms")
        elif res.get("repeat"):
            since = time.strftime("%m-%d %H:%M", time.localtime(res.get("clean_at") or 0))
            print(f"[code-canary] standing drop since {since} (reported then; not this card): "
                  + "; ".join(d["tree"] for d in res["drops"]))
        else:
            print(f"[code-canary] DROP ({res['mode']}): {cc.describe(res)}")
        return 0 if res["ok"] else 3

    if a.cmd == "rebaseline":
        try:
            st = cc.rebaseline(only=a.tree)
        except KeyError as e:
            print(f"[code-canary] {e.args[0]} — see `code-canary.py status`")
            return 2
        for k, v in sorted(st["trees"].items()):
            print(f"{v['baseline']:>7}  {k}")
        return 0

    if a.cmd == "record":
        targets = a.paths or [str(cc.HERMES_DIR / "hermes-agent" / v) for v in ("venv", ".venv")]
        bad = 0
        for sp in cc.site_packages_of(targets):
            res = cc.record_check(sp)
            flag = "ok " if res["ok"] else "GAP"
            print(f"{flag} {res['packages']:>4} dists, {res['missing_files']:>6} files missing, "
                  f"{len(res['no_record'])} without RECORD  {sp}")
            for b in res["broken"][:12]:
                print(f"      {b['dist']}: {b['missing']} missing")
            for n in res["no_record"][:12]:
                print(f"      {n}: no RECORD (its install record is gone — rebuild it to verify it)")
            bad += not res["ok"]
        return 3 if bad else 0

    st = cc._load()
    for k, v in sorted((st.get("trees") or {}).items()):
        print(f"{v.get('baseline'):>7} (high {v.get('high')})  {k}")
    try:
        for line in cc.EVENTS.read_text().splitlines()[-5:]:
            print(line[:300])
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
