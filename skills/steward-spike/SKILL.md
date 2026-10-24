---
name: steward-spike
description: Throwaway local-AI bench under spike rails. Never promote.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Steward, spike]
---

# Steward — bounded spike

`SPIKE_SLUG` is in the environment. Work only in
`~/spikes/YYYY-MM-DD-$SPIKE_SLUG/` (create it). `HF_HOME` is already set
inside that tree.

Write `~/Desktop/krew-deliverables/spike-$SPIKE_SLUG-YYYY-MM-DD.md` with
commands and output. Cap ~20 minutes then stop.

## Allowed

- clone into the spike dir
- scratch `uv` venv **in the spike dir**
- CPU smoke `nice`d; stop if load climbs

## Forbidden (deny globs will block; do not rephrase)

- restart/stop ops, crew, the voice server, rails, audio8
- `vllm serve`, `start-ops.sh` / `start-crew.sh` / the long-context start script
- the model store (`*/hf/hub-local`), `human_gold`, `chattr`
- `uv pip` into the service envs
- promote anything to live

If a rail stops you, write `spike skipped: <reason>` and stop. Never retry a
denied command.
