#!/usr/bin/env python3
"""Render worker GPU memory pre-check and blocking logic (the User 2026-09-17).

Background (frame spec, t_52fb1371 -> this card): the freelance render run of
2026-09-17 died with 94,193 MiB of 97,887 MiB resident — 3,694 MiB free — and
the worker exited 0 without a terminal kanban call. Framing from the reframe:
the GPU budget is a HARD CONSTRAINT that must be checked BEFORE the render is
attempted, and a shortfall is a BLOCK with the stats in the reason, not a
silent crash.

What this module does
=====================
It is the pre-render resource guard for the render worker. scripts/
render-submit.py imports :func:`precheck` and calls it once, at the top of
the render task (before staging, before the POST to Comfy) — scoped 2026-09-19
(t_6260608e, User note "check VRAM only when the job will not park ops") to
--no-park runs only; a run that parks ops is gated by the post-park 24 GiB
wait in render-submit.py, because the park frees the VRAM the engines hold:

    import gpu_budget
    verdict = gpu_budget.precheck(a.card)
    if not verdict.ok:
        rc, detail = verdict.block(a.card)   # kanban_block with the stats
        sys.exit(gpu_budget.EXIT_INSUFFICIENT)

Two deliberately different "minimum free VRAM" numbers live in this pipeline
(attack 2 of the 2026-09-17 adversarial review — stated split, not a bug):

  precheck threshold   = max(KREW_RENDER_MIN_FREE_MIB, MIN_FREE_FRAC * total)
                         defaults 4096 MiB / 10% of total.  QUESTION IT ANSWERS:
                         "is there enough room left to even ATTEMPT the park
                         path and start a render?"  Cheap, runs first, blocks
                         early with the full stats.
  render-submit NEED_GIB = 24.0 GiB (render-submit.py:60).  QUESTION IT ANSWERS:
                         "can this render actually RUN without OOM?"  Checked
                         again, after the park has had its chance to free VRAM.
  A 3,700 MiB free reading (the 2026-09-17 crash state) fails the PRECHECK
  and never reaches the park; a 20 GiB reading passes the precheck and is
  then correctly refused by NEED_GIB after park. Neither gate is weaker
  than the other's job.

VRAM sources
============
1. nvidia-smi — source of truth (per the house rails law): its TOTAL and its
   FREE are the kernel's own numbers.
2. Comfy /system_stats — the same card's free as Comfy sees it.
Merge rule: free = the LOWER number across sources (a disagreement means
trust what can actually be handed out); total = nvidia-smi's when it
answers, Comfy's only as a fallback, and a total mismatch is recorded in the
reason.

FAIL-CLOSED
===========
A guard that cannot measure VRAM must NOT silently allow the render. If no
source answers, precheck() returns ok=False and the worker blocks with
"VRAM could not be measured" (exit EXIT_UNMEASURABLE) — the render window
sweep / an operator unblocks once the driver recovers. The recovery path is
deliberate, not a code path: a blocked card is visible on the board, so a
stuck node is a visible stuck card, not a wedge nobody sees.

Blocking — one owner
====================
verdict.block() shells out to `hermes kanban block <card> <reason>` with the
full stats (total/used/free MiB, threshold, per-process table) — matching
the acceptance criterion "kanban_block with 'Insufficient GPU memory' and
the current usage stats". The WORKER import path is the only thing that
blocks. The standalone probe (below) is READ-ONLY: it prints the budget and
exits by verdict code (0 ok / 4 insufficient / 5 unmeasurable) and never
touches the board — running the probe can no longer flip a live card.

No wait/retry
=============
The card says "defaulting to block is safer per the frame spec" — so there
is NO built-in retry loop. KREW_RENDER_WAIT_SEC is a single re-poll after
the given seconds (clamped to 60 s — the kanban terminal wall is 600 s and
the worker must stay well inside it), default 0 = block immediately.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field

__all__ = ["GpuBudget", "precheck", "query_vram",
           "MIN_FREE_MIB", "MIN_FREE_FRAC",
           "EXIT_INSUFFICIENT", "EXIT_UNMEASURABLE", "EXIT_OK"]

#: Minimum free VRAM in MiB, absolute floor (env: KREW_RENDER_MIN_FREE_MIB).
#: t_724afc97 (2026-09-20): the card's own guard number — "fails fast with a
#: clear error message if <6GB is available" — is 6 GB = 6096 MiB, and the
#: card ALSO says optimize to fit "~6.5GB free VRAM". The pre-park precheck
#: floor therefore moves 4096 -> 6096 (env override keeps the old default
#: reachable). The 24 GiB NEED_GIB run gate in render-submit.py is untouched:
#: it answers the different question ("can this recipe RUN") AFTER the park,
#: and raising the precheck floor never weakens it.
MIN_FREE_MIB = float(os.environ.get("KREW_RENDER_MIN_FREE_MIB", "6096"))
#: Minimum free fraction of total VRAM (env: KREW_RENDER_MIN_FREE_FRAC).
MIN_FREE_FRAC = float(os.environ.get("KREW_RENDER_MIN_FREE_FRAC", "0.10"))
#: Comfy base URL (same env render-submit.py honors).
COMFY_URL = os.environ.get("KREW_COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
#: One optional re-poll delay in seconds (default: none — block immediately).
#: Clamped to 60 s: the kanban terminal wall is 600 s and the worker must
#: stay far inside it.
WAIT_SEC = min(float(os.environ.get("KREW_RENDER_WAIT_SEC", "0")), 60.0)

EXIT_INSUFFICIENT = 4
EXIT_UNMEASURABLE = 5
EXIT_OK = 0


@dataclass
class GpuBudget:
    """One VRAM reading, with the threshold it was measured against."""
    total_mib: float | None
    used_mib: float | None
    free_mib: float | None
    threshold_mib: float | None
    source: str                       # "nvidia-smi" | "comfy" | "nvidia-smi+comfy" | "none"
    per_process: list[str] = field(default_factory=list)
    total_mismatch: float | None = None   # nvidia-smi vs comfy total drift, MiB

    @property
    def ok(self) -> bool:
        if self.free_mib is None or self.threshold_mib is None:
            return False
        return self.free_mib >= self.threshold_mib

    @property
    def reason(self) -> str:
        if self.free_mib is None or self.threshold_mib is None:
            return (
                "Insufficient GPU memory — VRAM could not be measured "
                f"(sources: {self.source or 'none'} answered nothing); "
                "refusing to render blind. Unblock once the driver/GPU "
                "recovery is confirmed."
            )
        used = (f"{self.used_mib:.0f} MiB" if self.used_mib is not None else "?")
        total = (f"{self.total_mib:.0f} MiB" if self.total_mib is not None else "?")
        extra = ""
        if self.total_mismatch:
            extra = f" [note: VRAM total differs across sources by {self.total_mismatch:.0f} MiB; nvidia-smi trusted]"
        return (
            f"Insufficient GPU memory: {self.free_mib:.0f} MiB free of {total} "
            f"({used} used) < required {self.threshold_mib:.0f} MiB "
            f"(max({MIN_FREE_MIB:.0f} MiB floor, {MIN_FREE_FRAC:.0%} of total), "
            f"sources={self.source}).{extra} Not rendering — unblock when VRAM recovers."
        )

    def block(self, card: str | None) -> tuple[int, str]:
        """Issue `hermes kanban block <card> <reason>`. Returns (rc, detail).

        The WORKER path calls this. The standalone probe does not.
        """
        if not card:
            return 1, "gpu_budget.block: no card id to block"
        reason = self.reason
        if self.per_process:
            reason += " Residents: " + "; ".join(self.per_process[:6])
        try:
            r = subprocess.run(
                ["hermes", "kanban", "block", card, reason],
                capture_output=True, text=True, timeout=60,
            )
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except Exception as exc:  # noqa: BLE001 - the reason is the deliverable
            return 1, f"hermes kanban block failed: {exc}"


def _nvidia_smi() -> GpuBudget | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip().splitlines()
        line = next((l for l in out if l.strip()), None)
        if line is None:
            return None
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            return None
        total, used, free = (float(p.replace("MiB", "")) for p in parts[:3])
        procs: list[str] = []
        try:
            p = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                 "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip().splitlines()
            for l in p:
                f = [x.strip() for x in l.split(",")]
                if len(f) == 3:
                    procs.append(f"{f[1] or f[0]}: {f[2]}")
        except Exception:
            pass
        return GpuBudget(total, used, free, None, "nvidia-smi", procs)
    except Exception:
        return None


def _comfy_stats() -> GpuBudget | None:
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/system_stats", timeout=5) as r:
            data = json.loads(r.read().decode())
        devs = data.get("devices") or []
        if not devs:
            return None
        d = devs[0]
        total = d.get("total_memory")
        free = d.get("free_memory")
        if isinstance(total, (int, float)) and isinstance(free, (int, float)):
            # Comfy reports bytes
            total_mib, free_mib = total / (1024 * 1024), free / (1024 * 1024)
            used_mib = max(0.0, total_mib - free_mib)
            return GpuBudget(total_mib, used_mib, free_mib, None, "comfy")
    except Exception:
        pass
    return None


def query_vram() -> GpuBudget:
    """Merge every VRAM source. Free = the LOWER number (conservative — what
    can actually be handed out). Total = nvidia-smi's when it answers
    (source of truth per the rails law), Comfy's only as fallback; a total
    mismatch is recorded so the reviewer sees it.
    """
    nv = _nvidia_smi()
    cf = _comfy_stats()
    sources = [s for s in (nv, cf) if s is not None]
    if not sources:
        return GpuBudget(None, None, None, None, "none")
    known = [s for s in sources if s.free_mib is not None]
    merged = min(known or sources,
                 key=lambda s: s.free_mib if s.free_mib is not None else float("inf"))
    total_source = nv or merged
    merged.total_mib = total_source.total_mib
    if nv is not None and cf is not None and nv.total_mib and cf.total_mib:
        drift = abs(nv.total_mib - cf.total_mib)
        if drift > 1.0:
            merged.total_mismatch = drift
    merged.source = "+".join(sorted({s.source for s in sources}))
    return merged


def precheck(card: str | None = None) -> GpuBudget:
    """The pre-render resource guard. Call BEFORE any render work.

    Returns a :class:`GpuBudget` whose ``ok`` says whether to proceed. When
    ``ok`` is False the worker must call ``verdict.block(card)`` and exit
    with ``EXIT_INSUFFICIENT`` / ``EXIT_UNMEASURABLE`` (the guard's reason
    text is the block message). Honors KREW_RENDER_WAIT_SEC (clamped to
    60 s) as a single re-poll (default 0 = block immediately, per the frame
    spec).

    t_724afc97 (2026-09-20): the default floor is now 6096 MiB (the card's
    own "<6GB" number) instead of 4096 — the render loop's judge (the 27B
    crew vision call) cannot fit under less, so a machine with that little
    free refuses HERE, pre-loop, with a clear message, instead of OOMing
    mid-render.
    """
    g = query_vram()
    if WAIT_SEC > 0 and not g.ok:
        import time
        time.sleep(WAIT_SEC)
        g = query_vram()
    if g.total_mib is not None:
        g.threshold_mib = max(MIN_FREE_MIB, MIN_FREE_FRAC * g.total_mib)
    elif g.free_mib is not None:
        # total unknown but free known: fall back to the absolute floor.
        g.threshold_mib = MIN_FREE_MIB
    return g


if __name__ == "__main__":
    # READ-ONLY probe (2026-09-17 adversarial review, attack 3): print the
    # budget and exit by verdict code. NEVER touches the kanban board —
    # blocking is the worker's job (render-submit.py import path).
    #   exit 0  ok to render
    #   exit 4  insufficient VRAM (worker would block)
    #   exit 5  VRAM unmeasurable (worker would block, fail-closed)
    v = precheck(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({
        "ok": v.ok,
        "total_mib": v.total_mib,
        "used_mib": v.used_mib,
        "free_mib": v.free_mib,
        "threshold_mib": v.threshold_mib,
        "source": v.source,
        "per_process": v.per_process,
        "reason": v.reason,
    }, indent=1))
    if v.ok:
        sys.exit(EXIT_OK)
    print(f"[gpu_budget] {v.reason} (worker path would kanban_block; probe is read-only)",
          file=sys.stderr)
    sys.exit(EXIT_UNMEASURABLE if v.free_mib is None else EXIT_INSUFFICIENT)
