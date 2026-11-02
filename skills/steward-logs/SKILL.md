---
name: steward-logs
description: Daily log anomaly judgment, M&S snapshot, archive already done by script.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Steward, logs, M&S]
---

# Steward — logs + M&S snapshot

The wrapper already archived yesterday and wrote
`~/.krew/memory/ms/architect-collect/YYYY-MM-DD/logs.md`.

Write:

- `~/Desktop/krew-deliverables/logs-YYYY-MM-DD.md`
- `~/Desktop/krew-deliverables/ms-YYYY-MM-DD.md`
- append one dated paragraph to the krew docs

## logs note

Judge **silent** failure in last 24h: repeating CUDA illegal-mem, WS storms,
AUTH sticky, ghost IDs, disk, OOM with host-OOM=0, doctor HIGH, T0
sudo/credential families.

Do **not** re-card units janitor already carded this window (janitor JSONL).
Do **not** restart services. Do **not** browse GitHub.

One kanban card per distinct anomaly, assignee **ada**, crystal = this logs
note + later `mitigation-<slug>-YYYY-MM-DD.md`. Page Crissa only for NEEDS HUMAN
/ gray software (skill steward-sweep).

Healthy blocked cards stay healthy.

## M&S snapshot

Copy headline facts from `~/.krew/memory/ms/reports/ms-trail-LATEST.md`
plus your first-person hazards. Same paragraph into the living log.

If occupancy skipped the LLM, the wrapper already wrote a stub — do not invent.

**Comfy / render tools** are a health lane (`comfy`) plus a `render` survey
block: git tag vs origin, custom nodes, krew-comfy recipes wired. Parked
`:8188` in normal is **IDLE**, not RED. `:8188` up in normal is **LEAK**.
Render + `:8188` down is **RED**.

Pip greens in the comfyui env already apply on the nightly trail (skip
unpinned torch). **Git tag upgrades** are not auto-checkout: if
`render.sota.behind` (or an open `M&S render SOTA:` card), Ada assigns
bash/steward — `krew-mode render`, smoke `qwen-image-2512` + `ltx23-flf2v`
+ `ltx25-flf2v` (glance at the user/camera, **no inventory**), Vera the change gate,
checkout the origin tag, restore with **`krew-mode normal`**. Never write
`/tmp/krew-train-hold` or park ops/voice by hand. T0 the change gate. **the user does not.**
Stuck → bash/steward or Vera. **needs-crissa** last resort.
