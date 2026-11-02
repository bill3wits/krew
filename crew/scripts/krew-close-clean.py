#!/usr/bin/env python3
"""pre_tool_call hook on kanban_complete: a crew card may not close while its worker leaves uncommitted

(KREW_CLOSE_CLEAN overrides); see lib/close_clean.py. Fails OPEN on any error - it never blocks by crashing.

  as a hook:   reads the hermes hook payload on stdin, prints {} / a context line / a block decision
  by hand:     krew-close-clean.py --card t_xxxxxxxx [--profile <name> ...]   (report only, any mode)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import close_clean as cc  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402



def _profile() -> str:
    return (os.environ.get("HERMES_PROFILE") or Path(os.environ.get("HERMES_HOME", "")).name or "").strip().lower()


def hook() -> int:
    if cc.mode() == "off":
        print("{}")
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        tool = payload.get("tool_name") or ""
        prof = _profile()
        card = (os.environ.get("HERMES_KANBAN_TASK") or "").strip()
        sid = str(payload.get("session_id") or "")
        if tool != "kanban_complete" or prof not in cc.crew_profiles() or not sid:
            print("{}")
            return 0
        db = KH.hermes_home(passwd=True) / "profiles" / prof / "state.db"
        paths = cc.worker_dirty(db, sid)
        if not paths:
            print("{}")
            return 0
        m = cc.mode()
        cc.log({"mode": m, "profile": prof, "card": card, "session": sid, "paths": paths})
        msg = cc.refusal(paths)
        if m == "enforce":
            print(json.dumps({"decision": "block", "reason": msg}))
        else:
            print(json.dumps({"context": "[close-clean report] would refuse: " + msg}))
    except Exception as e:  # noqa: BLE001 - fail open
        cc.log({"error": f"{type(e).__name__}: {e}"[:300]})
        print("{}")
    return 0


def by_hand(card: str, profiles: list[str]) -> int:
    hits = 0
    for prof in profiles:
        db = KH.hermes_home(passwd=True) / "profiles" / prof / "state.db"
        if not db.is_file():
            continue
        for sid in cc.card_sessions(db, card):
            paths = cc.worker_dirty(db, sid)
            if paths:
                hits += 1
                print(f"{card} {prof} {sid}: would refuse the close - {len(paths)} path(s): {', '.join(paths)}")
    if not hits:
        print(f"{card}: clean - no worker session leaves an uncommitted live-tree path")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--card")
    ap.add_argument("--profile", action="append")
    a = ap.parse_args(argv)
    if a.card:
        return by_hand(a.card, a.profile or list(cc.crew_profiles()))
    return hook()


if __name__ == "__main__":
    raise SystemExit(main())
