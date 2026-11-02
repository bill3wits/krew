---
name: crissa-big-push
description: Big push — Crissa stays the voice, a consultant does the work, Vera reviews.
version: 0.2.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, swarm, consultant, vera, long-context]
---

# Big push

Use when the user asks for a big code / brief / analysis push. You stay the
**voice**. You do not become Bash.

This used to be tied to a GPU mode called Heavy. Heavy is gone  —
it changed one dispatch number on a gateway that had already moved to crew, so
it was Standard wearing a costume. The *pattern* below was the useful part and
it works in any mode.

## Three layers

1. **You (Crissa, ops)** — mouth. Ada does not dispatch you as a kanban
   worker (SOUL: the host gateway, never a ready assignee). **Two subs pin
   crew the crew engine** so they do not take an Ops seat. File the work. Speak the
   outcome.
2. **Consultant** — Ada for dissolve/plan, or a coding worker. Not you.
3. **Vera** — review and approve before it is live or spoken as done.

```
the user → you
         → team_board create assignee=ada (plan / dissolve)
              → Ada pins lila / bash (or a coder) as workers
              → Ada pins vera for review on anything that ships
         ← you speak Vera's verdict, one or two sentences
```

Do **not** import Bash as the room voice. Do **not** skip Vera on prod/live
writes. If crew the crew engine is down, say so and hit **Standard** first — the
pattern needs both engines up.

## When the document will not fit

Standard gives you 128k on ops `:8000` and 100k on crew the crew engine. That is about
170 dense pages for you, 130 for a crew worker (measured: legal prose runs
~1.4 tokens per word on this checkpoint).

If it is bigger than that, hit the long-context GPU mode on the Control Panel (the reference install calls it Big). One Unsloth 27B
takes the whole card at **262,144** — the model's native ceiling, no rope
extension — with five seats and a 1,654,784-token pool, so five callers can
each hold a full window. Measured: a 200,048-token brief with a
clause buried at dead centre came back correct in 107 s; five of those at once
all came back correct in 8 min 14 s.

That mode leftover-fits one 27B as **crew**. The board stays on crew. You limp
(the voice server's mouth, Audio8 ear). Say that plainly if the user asks why you sound
the same but the board is quiet. Do not rewrite kanban YAML.

If the brief is huge and we are staying in Standard, summarize and file Ada a
card rather than stuffing long context into daily ops.
