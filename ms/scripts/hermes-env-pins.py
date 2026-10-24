#!/usr/bin/env python3
"""Minimum versions held in the environment Hermes actually runs (Hermes rebuilds it from upstream's lock at
every update, so a fix installed by hand comes back out).

  ms/scripts/hermes-env-pins.py status   # what the launcher imports vs the pins (exit 1 if below)
  ms/scripts/hermes-env-pins.py apply    # install what is below its pin (needs `upgrades: true` in ms.yaml)

Pins: $KREW_HOME/hermes-env-pins.json (shape: config/hermes-env-pins.example.json; MS_HERMES_ENV_PINS_FILE
overrides). No file = no pins. Off: MS_HERMES_ENV_PINS=0.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import hermes_env_pins as hp


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = next((a for a in argv if not a.startswith("--")), "status")
    if mode not in ("status", "apply"):
        print(__doc__)
        return 2
    st = hp.apply(by="hermes-env-pins.py by hand") if mode == "apply" else hp.status()
    if "--json" in argv:
        print(json.dumps(st, indent=1))
    else:
        if st.get("refused"):
            print(st["refused"])
        if not st.get("pins"):
            print(f"no pins: {hp.PINS_FILE} is absent or lists none")
        elif st.get("off"):
            print("off (MS_HERMES_ENV_PINS=0)")
        elif st.get("error"):
            print(f"unknown: {st['error']}")
        else:
            print(f"live environment: {st.get('site')}")
            for p in st["pins"]:
                have = (st.get("versions") or {}).get(p["dist"])
                print(f"  {p['dist']:12} have {have}  pin >= {p['min']}  {'ok' if hp.meets(have, p['min']) else 'BELOW'}")
            for a in st.get("applied") or []:
                print(f"  installed {a['spec']}: {a['was']} -> {a.get('now')} (rc {a['rc']})")
            if st.get("restart_owed"):
                print(f"  restart owed: {st['restart_owed']}")
    return 1 if st.get("below") else (3 if st.get("error") else 0)


if __name__ == "__main__":
    raise SystemExit(main())
