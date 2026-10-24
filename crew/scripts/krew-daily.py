#!/usr/bin/env python3
"""Build the Krew Daily (lib/krew_daily.py): the one report the User opens.

  scripts/krew-daily.py                 # build into ~/Desktop/krew-deliverables
  scripts/krew-daily.py --out DIR       # build somewhere else (a dry look)
  scripts/krew-daily.py --say           # print only the headline Crissa speaks

Started by the window ticker just before the User's off-hours close
(config/krew-window-schedule.json, job `krew-daily`). Off: KREW_DAILY=0 or
~/.hermes/krew-daily.off. Exit 0 = written; 1 = could not write; the page itself
never fails on one unreadable source — that source shows as "could not verify".
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import krew_daily  # noqa: E402

OFF = Path.home() / ".hermes" / "krew-daily.off"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="")
    ap.add_argument("--say", action="store_true")
    a = ap.parse_args(argv)
    if OFF.is_file() or os.environ.get("KREW_DAILY", "1").strip().lower() in ("0", "false", "no", "off"):
        print("krew-daily: off switch set; nothing written")
        return 0
    src = krew_daily.Sources()
    m = krew_daily.build(sources=src)
    if a.say:
        print(m["say"])
        return 0
    try:
        paths = krew_daily.write(m, Path(a.out) if a.out else src.inbox)
    except OSError as e:
        print(f"krew-daily: could not write: {e}", file=sys.stderr)
        return 1
    print(f"krew-daily: {m['headline']} -> {paths[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
