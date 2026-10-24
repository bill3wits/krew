---
name: scout-architect-stack
description: Map today's local-AI world onto krew stack pieces; try/watch/ignore.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Scout, architect, stack]
---

# Scout — stack note

Write `~/Desktop/krew-deliverables/architect-stack-YYYY-MM-DD.md`.

Read, in order, one at a time:

1. Today's (else yesterday's) `architect-world-YYYY-MM-DD.md`
2. Collect `INDEX.md` + at most two section files you need
3. the krew docs (pins)
4. `~/.krew/memory/ms/architect-coverage.jsonl` (starve: any piece untouched 7 days **must** be included)
5. Top of the krew docs if present (open hazards)

## Pieces (2–3 / day, plus hazards, plus starved)

brains, hermes, presence, camera, face, voice_emb, stt, tts, wall, train,
render, security, memory.

**render** pin is the krew docs Comfy `:8188` (parked except Render).
M&S already surveys git vs origin + krew-comfy recipes. Your note is the
world/SOTA overlay (new video models, Comfy tags, LTX), not a second inventory.
Verdict `try` becomes an Ada `architect-try` card — Steward/Ada run it; the user
does not. Never mint pose clips from a try.

Sunday or first run after skips: full scorecard.

## Note shape

```
# Krew stack vs SOTA — YYYY-MM-DD
## Pieces this run
### <id>
- pin: (from KREW-BUILD)
- world: …
- verdict: try | watch | ignore
## Callouts
## spike-candidate: <slug-or-none>
## Proposed cards
- architect-try: …
- architect-watch: …
```

Under `## Proposed cards`, write a line only for a real card: a named piece with a
verdict of `try` or `watch`. On a night with nothing to try, the section holds the one
line `_no cards this run_`; the Callouts say why.

`spike-candidate: none` unless one cheap throwaway bench is obvious. Steward
decides whether rails pass. You never spike.

Append one JSON line per verdict to
`~/.krew/memory/ms/architect-verdicts.jsonl`:
`{"date","piece","name","verdict","resolved_url"}`.

Append coverage lines to
`~/.krew/memory/ms/architect-coverage.jsonl`:
`{"date","piece"}`.

Callouts required. Empty callouts = `_none worth trying_`. Pass.

Interesting ≠ adopt. No engine swap. No weight download.
