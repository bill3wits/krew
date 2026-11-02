#!/usr/bin/env python3
"""krew-decide-sheet-watch: run the Decide sheet's privacy watch (`crissa-decide-list.py watch`, team-crissa's plugin)
from the Hermes home the plugin was installed into, so the unit goes through krew-crew-run like every other.

The plugin lives at $KREW_HERMES (else $HERMES_HOME, else ~/.hermes)/plugins/team-crissa. No plugin there: say so and
exit 0 (krew without team-crissa has no sheet to guard). Off: ~/.krew/krew-decide-sheet-watch.off.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402


def plugin_script() -> Path:
    home = os.environ.get("KREW_HERMES") or os.environ.get("HERMES_HOME") or str(KH.hermes_home())
    return Path(os.path.expanduser(home)) / "plugins" / "team-crissa" / "crissa-decide-list.py"


def main() -> int:
    script = plugin_script()
    if not script.is_file():
        print(f"krew-decide-sheet-watch: no team-crissa plugin at {script.parent}; no sheet to guard", flush=True)
        return 0
    os.execv(sys.executable, [sys.executable, str(script), "watch"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
