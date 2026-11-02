#!/usr/bin/env python3
"""Did the nightly trail's security scan stop listing a package? exit 0 = gone, 1 = still listed, 2 = no fresh scan.
Reads <state_dir>/hermes/security-LATEST.json (MS_OUT, else $KREW_HOME/ms). Report only."""







from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

LATEST = Path(os.environ.get("MS_OUT") or Path(os.environ.get("KREW_HOME") or Path.home() / ".krew").expanduser() / "ms") / "hermes" / "security-LATEST.json"


def check(package: str, path: Path = LATEST, newer_than: float = 0.0) -> tuple[int, str]:
    try:
        stamp = path.stat().st_mtime
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return 2, f"scan file unreadable ({type(e).__name__})"
    when = dt.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
    if not d.get("measured", True):
        return 2, f"scan of {when} did not complete (unmeasured)"
    if stamp <= newer_than:
        return 2, f"no scan since the change: the newest is {when}"
    rows = [f for f in d.get("findings") or [] if str(f.get("package", "")).lower() == package.lower()]
    if rows:
        ids = ", ".join(sorted({str(f.get("vuln_id")) for f in rows})[:6])
        return 1, f"scan of {when} STILL lists {package} {sorted({str(f.get('version')) for f in rows})}: {len(rows)} row(s) ({ids})"
    return 0, f"scan of {when} lists no {package} finding ({d.get('finding_count')} findings in all)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package")
    ap.add_argument("--newer-than", default="", help="YYYY-MM-DD HH:MM: the scan must be newer than this")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    newer = dt.datetime.strptime(a.newer_than, "%Y-%m-%d %H:%M").timestamp() if a.newer_than else 0.0
    rc, line = check(a.package, newer_than=newer)
    line = f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M')}: {line}"
    print(line)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
