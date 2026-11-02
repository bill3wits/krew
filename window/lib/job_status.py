"""A window job's own word on whether it did its work (self-loop, the User 09-28).

systemd's `success` only says the process exited 0. On 09-27 the world job ran
20:40:09-20:41:21 rc 0 and wrote a note saying its bundle was missing; the logs job
ran three seconds rc 0 and wrote no note at all. The ticker, systemd and the Daily all
said ok. hermes itself returned 0, so no exit code could have told them.

So the job's shell, which knows what it did (the collect's rc, the note it expected,
the stub it chose to write), writes ONE status line per run:

    ~/.hermes/job-status/<key>-<KREW_DAY>.json
    {"status": "ok" | "failed" | "skipped", "reason": "...", "artifact": "...", "ts": ...}

and scripts/krew-window-tick.py reads it in the same tick that records the verdict,
before it starts anything chained behind the job. No phrase-matching of the note:
the shell decides from facts it holds.

CLI (for the bash runners):
    job_status.py write <key> <day> <ok|failed|skipped> [reason] [artifact]
    job_status.py read  <key> <day>

krew: the line lives under $KREW_HOME/job-status/ (KREW_JOB_STATUS_DIR overrides), not ~/.hermes.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date as _date
from datetime import timedelta as _timedelta
from pathlib import Path

STATUSES = ("ok", "failed", "skipped")


def status_dir() -> Path:
    return Path(os.environ.get("KREW_JOB_STATUS_DIR", str(Path(os.environ.get("KREW_HOME") or Path.home() / ".krew").expanduser() / "job-status")))


def path_for(key: str, day: str, d: Path | None = None) -> Path:
    return (d or status_dir()) / f"{key}-{day}.json"


def write(key: str, day: str, status: str, reason: str = "", artifact: str = "",
          d: Path | None = None) -> Path:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, not {status!r}")
    p = path_for(key, day, d)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"key": key, "day": day, "status": status, "reason": reason,
                               "artifact": artifact, "ts": time.time()}) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def clear_stale(key: str, day: str, d: Path | None = None) -> dict:
    """A recovered window must not stay red on the previous day's failed line
    (09-29, t_96f37501): after TODAY's run wrote ok, the previous day's failed
    line is the window's history, not tonight's verdict — remove it and report
    what it said. Any other line (or none) is left as it is."""
    y = (_date.fromisoformat(day) - _timedelta(days=1)).isoformat()
    rec = read(key, y, d=d)
    if rec is None or rec.get("status") != "failed":
        return {"key": key, "day": y, "stale": None}
    path_for(key, y, d).unlink()
    return {"key": key, "day": y, "stale": rec}


def read(key: str, day: str, since: float = 0.0, d: Path | None = None) -> dict | None:
    """The status line for this run, or None: missing, unreadable, or written before
    `since` (an earlier run's line is not tonight's word). `since` is epoch seconds;
    two minutes of slack, as the ticker allows for its own start stamp."""
    p = path_for(key, day, d)
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    ts = float(rec.get("ts") or 0) or p.stat().st_mtime
    if since and ts < since - 120:
        return None
    return rec


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "write":
        try:
            p = write(argv[1], argv[2], argv[3], argv[4] if len(argv) > 4 else "",
                      argv[5] if len(argv) > 5 else "")
        except (OSError, ValueError) as e:
            print(f"job_status: {e}", file=sys.stderr)
            return 2
        print(f"status {argv[3]}: {p}")
        return 0
    if len(argv) == 3 and argv[0] == "read":
        print(json.dumps(read(argv[1], argv[2])))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
