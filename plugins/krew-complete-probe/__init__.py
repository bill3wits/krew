"""Spike probe: capture raw kanban_task_completed hook payload.

Steward spike 2026-09-16. Throwaway — enabled on no profile; enable it for a spike, disable it after.
Appends one JSON line per hook fire to SPIKE_DIR/hook-<pid>.jsonl
(KREW_KANBAN_HOOK_SPIKE_DIR, else $KREW_HOME/spikes/kanban-complete-hook).
Observer-only: never raises, never returns.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SPIKE_DIR = Path(os.environ.get(
    "KREW_KANBAN_HOOK_SPIKE_DIR",
    str(Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew")) / "spikes" / "kanban-complete-hook"),
))


def _log(**kwargs):
    try:
        SPIKE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "argv0": sys.argv[0] if sys.argv else None,
            "hook_kwargs": {k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else repr(v))
                            for k, v in kwargs.items()},
        }
        path = SPIKE_DIR / f"hook-{os.getpid()}.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass  # observer must never break the transition


def initialize(ctx):
    try:
        ctx.register_hook("kanban_task_completed", _log)
    except Exception:
        pass


register = initialize   # krew's plugins expose register(ctx); the reference probe named it initialize
