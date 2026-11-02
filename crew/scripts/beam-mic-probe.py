#!/usr/bin/env python3
"""Is the room's beam mic still there, and does the ear still get a beam? Report only (the User 2026-10-01).

    beam-mic-probe.py tick     probe, keep the episode, file ONE card per 5-min fault (the timer runs this)
    beam-mic-probe.py probe    one reading, nothing written, nothing filed

Never restarts, reloads or changes anything; the rules are in lib/beam_mic_probe.py.
Kill KREW_BEAM_MIC_PROBE=0 or ~/.hermes/beam-mic-probe.off.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import beam_mic_probe  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("verb", choices=("tick", "probe"))
    a = ap.parse_args(argv)
    out = beam_mic_probe.tick() if a.verb == "tick" else beam_mic_probe.probe()
    print(time.strftime("%H:%M:%S"), "beam-mic-probe:", json.dumps(out, sort_keys=True), flush=True)
    return 0                       # a missing mic is a REPORT, never a failed unit


if __name__ == "__main__":
    raise SystemExit(main())
