#!/usr/bin/env python3
"""krew: zoey-docs-review.py (lifted from the reference install; see crew/README.md)."""








from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import zoey_docs  # noqa: E402


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "reference"


def card(row: dict, approved_by: str) -> str:
    return (f"# {row['title']}\n\n"
            f"- where: {row['where']}\n- why: {row['why']}\n"
            + (f"- tags: {', '.join(row['tags'])}\n" if row.get("tags") else "") +
            f"- suggested by: {row.get('by', 'zoey')} on {time.strftime('%Y-%m-%d', time.localtime(row['ts']))}\n"
            f"- approved by: {approved_by} on {time.strftime('%Y-%m-%d')}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("approve"); a.add_argument("id"); a.add_argument("--no-index", action="store_true")
    r = sub.add_parser("reject"); r.add_argument("id"); r.add_argument("why", nargs="?", default="")
    args = ap.parse_args(argv)
    cfg = zoey_docs.settings()
    rows = zoey_docs.read_queue(Path(cfg["queue"]))
    if args.cmd == "list":
        pend = [x for x in rows if x.get("status") == "pending"]
        for x in pend:
            tags = f"  #{' #'.join(x['tags'])}" if x.get("tags") else ""
            print(f"{x['id']}  [{x.get('by', 'zoey')}]  {x['title']}{tags}\n    {x['where']}\n    why: {x['why']}")
        print(f"{len(pend)} pending")
        return 0
    hit = [x for x in rows if x.get("id") == args.id]
    if not hit:
        print(f"no suggestion {args.id}"); return 2
    row = hit[0]
    if args.cmd == "reject":
        row.update(status="rejected", decided=time.time(), note=args.why)
        zoey_docs.write_queue(rows, Path(cfg["queue"])); print(f"rejected {args.id}"); return 0
    out = Path(os.path.expanduser(cfg.get("approved_dir") or "~/.krew/references"))
    out = out if out.is_absolute() else ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{slug(row['title'])}.md"
    path.write_text(card(row, "the user"), encoding="utf-8")
    row.update(status="approved", decided=time.time(), file=str(path))
    zoey_docs.write_queue(rows, Path(cfg["queue"]))
    print(f"approved {args.id} -> {path}")
    if not args.no_index:
        subprocess.run([cfg["index_python"] if Path(cfg["index_python"]).exists() else "python3",
                        cfg["index_script"], "build", "--source", cfg.get("approved_source") or "references"], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
