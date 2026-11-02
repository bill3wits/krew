#!/usr/bin/env python3
"""Run vLLM so EngineDeadError / CUDA IMA cannot exit 0.

2026-08-21 08:46–08:48: both dual-27B engines died at FlashInfer
seq_lens.to("cpu") (cudaErrorIllegalAddress). The APIServer swallowed
EngineDeadError and exited 0, so systemd Restart=on-failure never fired.

Parked occupancy paths (Beast ops, Render crew) still exit 0 *before*
this wrapper — they never exec it.

Scan only the last TAIL_LINES of log at death. A lifetime buffer would
false-positive a clean park after an incidental IMA mention, and grow
without bound (dump_input.py dumps are multi-KB).
"""
from __future__ import annotations

import re
import signal
import subprocess
import sys
from collections import deque

DEAD_RE = re.compile(
    r"EngineDeadError|cudaErrorIllegalAddress|illegal memory access",
    re.IGNORECASE,
)
TAIL_LINES = 200


def mapped_exit(rc: int | None, text: str) -> int:
    """0 + a fatal engine line in the *exit tail* → 1. Else keep vLLM's code."""
    code = 0 if rc is None else int(rc)
    if code == 0 and DEAD_RE.search(text or ""):
        return 1
    return code


def run(argv: list[str]) -> int:
    if not argv:
        return 2
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _fwd(signum: int, _frame: object) -> None:
        if proc.poll() is None:
            proc.send_signal(signum)

    signal.signal(signal.SIGTERM, _fwd)
    signal.signal(signal.SIGINT, _fwd)
    buf: deque[str] = deque(maxlen=TAIL_LINES)
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        buf.append(line)
    rc = proc.wait()
    return mapped_exit(rc, "".join(buf))


def main(argv: list[str]) -> int:
    if argv[1:2] == ["--"]:
        cmd = argv[2:]
    else:
        cmd = ["vllm", "serve", *argv[1:]]
    if not cmd:
        return 2
    return run(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
