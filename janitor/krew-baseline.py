#!/usr/bin/env python3
"""krew-baseline: measure the machine before and after anything temporary; say what did not come back.

    krew-baseline.py snapshot <name>                 one reading -> ~/.hermes/krew-baseline/<name>.json
    krew-baseline.py diff <before> [<after>]          <after> omitted = now; names or paths
    krew-baseline.py diff <before> --record <label>   also write findings.json (needs KREW_BASELINE_FINDINGS=1)
    krew-baseline.py run <label> -- <command ...>     snapshot, run the command, snapshot, report
    krew-baseline.py start <label> | end <label>      the hook pair call sites use (KREW_BASELINE_HOOKS=1)

REPORT ONLY: reads, compares, writes its own report. It never stops, frees or restarts anything.
A probe that cannot be read is UNKNOWN - listed, never counted as "came back". See lib/krew_baseline.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_baseline as hb  # noqa: E402


def _resolve(name: str) -> dict:
    p = Path(name)
    if not p.is_file():
        p = hb.STATE / f"{hb._safe(name)}.json"
    return hb.load(p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("name")
    s.add_argument("--print", action="store_true", dest="show")
    d = sub.add_parser("diff")
    d.add_argument("before")
    d.add_argument("after", nargs="?")
    d.add_argument("--record", metavar="LABEL", default="")
    d.add_argument("--json", action="store_true")
    for verb in ("start", "end"):
        h = sub.add_parser(verb)
        h.add_argument("label")
        if verb == "end":
            h.add_argument("--settle", type=float, default=0.0)
    r = sub.add_parser("run")
    r.add_argument("label")
    r.add_argument("--settle", type=float, default=5.0, help="seconds to wait after the command before measuring")
    r.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)

    if a.cmd == "snapshot":
        snap = hb.snapshot(a.name)
        path = hb.save(snap, a.name)
        print(f"{path}  ({len(snap['probes'])} probes read, {len(snap['unknown'])} unknown)")
        if a.show:
            print(json.dumps(snap, indent=1))
        return 0
    if a.cmd == "diff":
        before = _resolve(a.before)
        after = _resolve(a.after) if a.after else hb.snapshot(before.get("label") or a.before)
        result = hb.diff(before, after)
        if a.record:
            if hb.findings_on():
                result["findings"] = hb.record(a.record, result)
            else:
                print("findings are off (KREW_BASELINE_FINDINGS=1 to write findings.json); report only", file=sys.stderr)
        print(json.dumps(result, indent=1) if a.json else hb.render(result))
        return 0
    if a.cmd == "start":   # the hook pair: silent no-ops unless KREW_BASELINE_HOOKS=1
        path = hb.start(a.label)
        print(path if path else "hooks are off (KREW_BASELINE_HOOKS=1)")
        return 0
    if a.cmd == "end":
        result = hb.end(a.label, settle_s=a.settle)
        print(hb.render(result) if result else "hooks are off, or no start for this label")
        return 0
    cmd = [c for c in a.command if c != "--"] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("run needs a command after --")
    before = hb.snapshot(a.label)
    hb.save(before, f"{a.label}.before")
    rc = subprocess.call(cmd)
    time.sleep(a.settle)
    after = hb.snapshot(a.label)
    hb.save(after, f"{a.label}.after")
    result = hb.diff(before, after)
    if hb.findings_on():
        result["findings"] = hb.record(a.label, result)
    hb.save(result, f"{a.label}.report")
    print(hb.render(result))
    return rc  # the command's own exit code: the report never changes the outcome


if __name__ == "__main__":
    sys.exit(main())
