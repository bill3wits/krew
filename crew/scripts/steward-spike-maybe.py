#!/usr/bin/env python3
"""If today's stack note names a spike candidate and rails pass, fire steward-spike."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import (  # noqa: E402
    INBOX,
    SPIKE_CEILING_BYTES,
    SPIKES,
    USAGE,
    allow_spike,
    append_jsonl,
    read_gpu_mode,
    today,
)

CAND = re.compile(r"spike-candidate:\s*(\S+)", re.I)
HERMES = Path.home() / ".local" / "bin" / "hermes"


def load_avg() -> float:
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


def mem_available_kb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        return 0
    return 0


def rails_ok() -> str | None:
    if not allow_spike():
        return f"occupancy_or_train mode={read_gpu_mode()}"
    if load_avg() > 8.0:
        return f"loadavg={load_avg():.1f}"
    if mem_available_kb() < 4 * 1024 * 1024:
        return f"mem_available_kb={mem_available_kb()}"
    SPIKES.mkdir(parents=True, exist_ok=True)
    # ceiling across all spike dirs
    total = 0
    for p in SPIKES.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    if total > SPIKE_CEILING_BYTES:
        return f"spikes_dir {total}B over ceiling"
    return None


def main() -> int:
    note = INBOX / f"architect-stack-{today().isoformat()}.md"
    if not note.is_file():
        print("no stack note")
        return 0
    text = note.read_text(encoding="utf-8", errors="replace")
    m = CAND.search(text)
    if not m:
        print("no spike-candidate")
        return 0
    slug = m.group(1).strip().strip("`")
    why = rails_ok()
    if why:
        skipped = INBOX / f"spike-{slug}-{today().isoformat()}.md"
        skipped.write_text(
            f"# spike skipped\n\ncandidate={slug}\nreason={why}\n",
            encoding="utf-8",
        )
        append_jsonl(USAGE, {"job": "spike", "event": "skip", "reason": why, "slug": slug})
        print("skip", why)
        return 0
    env = os.environ.copy()
    env["SPIKE_SLUG"] = slug
    env["HF_HOME"] = str(SPIKES / f"{today().isoformat()}-{slug}" / "hf")
    rc = subprocess.run(
        [str(HERMES), "-p", "steward", "cron", "run", "steward-spike"],
        env=env,
        check=False,
    ).returncode
    print("spike rc", rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
