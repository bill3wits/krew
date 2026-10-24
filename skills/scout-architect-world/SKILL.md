---
name: scout-architect-world
description: Daily local-AI world note — GitHub, models, Next New Thing, HN, r/LocalLLaMA.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Scout, architect, local-ai]
---

# Scout — world note

Write `~/Desktop/krew-deliverables/architect-world-YYYY-MM-DD.md`.
Collect bundle (no LLM): `~/.krew/memory/ms/architect-collect/YYYY-MM-DD/`
(`INDEX.md`, `github.md`, `hf.md`, `youtube.md`, `hn.md`, `localllama.md`, `x.md`, `dedupe.md`).

## Procedure

1. Read `INDEX.md`, then **one section file at a time**. Do not load the whole bundle.
2. Read `dedupe.md`. Do not re-headline a repo already listed unless stars/release changed.
3. Ground every GitHub `owner/name` you print — it is already resolved in `github.md` (DROPPED lines stay dropped).
4. Write the note with these headings, in order:

```
# Local-AI world — YYYY-MM-DD
## Repos
## New local models
## Inference / serving
## Next New Thing
## HN / r/LocalLLaMA / X
## Expansion / unlocks
## Pointers for the stack job
## Proposed cards
```

5. **New local models is required.** If none: `_none worth naming_` — that is a pass.
6. Expansion/unlocks even if we will not adopt.
7. Under Proposed cards, optional bullets:
   `- architect-watch: <short>`
   Steward files them. You do not.
8. Every number: **measured** or **ASSUMED**.

YouTube transcripts: only if a Next New Thing URL looks krew-relevant, and only
via the skill script `scripts/fetch_transcript.py` (file read of its stdout if
the collect already quoted titles — prefer titles over full transcripts).

No implement. No clone. No spike.
