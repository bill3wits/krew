#!/usr/bin/env python3
"""The night's unit-registry refresh: a full discovery into $KREW_HOME/units/registry.json, new and removed
units noted (janitor/krew_registry.py). The window ticker starts it after the M&S nightly
(window-schedule.json row `units-refresh`); by hand: `python3 janitor/krew-units-refresh.py`.
Report only: it changes nothing but the registry file and its changes log.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_registry  # noqa: E402


def main() -> int:
    res = krew_registry.refresh(by="units-refresh")
    print(json.dumps(res))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
