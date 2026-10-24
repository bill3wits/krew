#!/usr/bin/env python3
"""The front door's tool-call watch (crew/lib/zoey_tool_watch.py; the User 2026-10-02). Opt-in.

  krew-tool-watch.py tick      # the 5-min timer runs this (crew/units/krew-tool-watch.timer)
  krew-tool-watch.py status    # the last tick's numbers

Cards only with KREW_ZOEY_TOOL_WATCH_CARDS=1 (report-only until then: `would_card`).
Kill: KREW_ZOEY_TOOL_WATCH=0 or ~/.hermes/zoey-tool-watch.off.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))


def _own_lib(name: str):
    """crew/lib/<name>.py by its path (krew: never a same-named module from another tree on sys.path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"krew_own_{name}", Path(__file__).resolve().parent.parent / "lib" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


w = _own_lib("zoey_tool_watch")

# the lib's readers, by name, so a caller that loads this script sees them (tests/test_clean_install.py)
classify, rates, cards_on, enabled, read_new, tick = w.classify, w.rates, w.cards_on, w.enabled, w.read_new, w.tick
STATE, LOG, PROFILE, STATE_DB = w.STATE, w.LOG, w.PROFILE, w.STATE_DB


def main(argv=None) -> int:
    cmd = (argv or sys.argv[1:] or ["tick"])[0]
    if cmd == "status":
        try:
            print(json.dumps(json.loads(w.STATE.read_text()).get("last"), indent=1))
        except (OSError, ValueError):
            print("no tick yet")
        return 0
    print(json.dumps(w.tick()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
