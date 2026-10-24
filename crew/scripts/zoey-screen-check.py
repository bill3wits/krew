#!/usr/bin/env python3
"""Check that Zoey's private screen (her Bot Desktop) is isolated from the User's desktop, and record it.

    crew/scripts/zoey-screen-check.py        # prints each check; writes ~/.hermes/zoey-screen-check.json

computer_use returns to her hands, and the drill's CUA tasks run, only on a passing record for the display
her env names now (crew/lib/zoey_screen.py). Kill: KREW_ZOEY_SCREEN_CHECK=0.
KREW_ZOEY_SCREEN_WAIT_S=<s> (default 0 = off) waits first for his display to be readable (the boot race, 10-04).
When that wait ends with his display still unreadable, and the only failed lines are his pointer and his
focused window, the record is deferred and this process exits 0 so her screen stays up (10-07 04:00).
Isolation is not claimed. Kill: KREW_ZOEY_SCREEN_DEFER=0.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import zoey_screen  # noqa: E402


def main() -> int:
    waited = None
    limit = zoey_screen.wait_limit()
    if limit > 0:
        waited = zoey_screen.wait_for_his_display(limit, log=lambda m: print(m, flush=True))
        print(f"{'ok  ' if waited['ok'] else 'FAIL'} his display readable - {waited['display']} after "
              f"{waited['waited_s']} s, {waited['tries']} tr{'y' if waited['tries'] == 1 else 'ies'}", flush=True)
        if waited["ok"]:
            os.environ["DISPLAY"] = waited["display"]
            if waited["xauthority"]:
                os.environ["XAUTHORITY"] = waited["xauthority"]
    rec = zoey_screen.check()
    if waited is not None:
        rec["waited"] = waited
    stays = zoey_screen.screen_stays_up(rec, waited)
    if stays:
        rec["deferred"] = "his display unreadable after the wait; her screen stays up"
    for c in rec["checks"]:
        print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['check']}{(' - ' + c['why']) if c['why'] else ''}")
    path = zoey_screen.write_record(rec)
    if stays:
        print(f"DEFERRED {rec['display'] or '(no display)'} his display unreadable, her screen stays up -> {path}")
        return 0
    print(f"{'ISOLATED' if rec['ok'] else 'NOT ISOLATED'} {rec['display'] or '(no display)'} -> {path}")
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
