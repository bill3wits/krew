---
name: scout-team-research
description: Midnight TV Time revival — per-role improvement research, real sources only.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Scout, team, research]
---

# Scout — team morning research (museum TV Time, rails)

Write `~/Desktop/krew-deliverables/team-research-YYYY-MM-DD.md` **early**
(stub then fill). A thin sourced note beats a thinking-only session.

Read **one at a time**:

1. `~/.krew/memory/ms/architect-collect/YYYY-MM-DD/team.md`
2. the krew docs.json` (trusted list)
3. Today's architect-world note if present (do not duplicate it — add *role* lens)
4. `~/.krew/memory/ms/kanban-snapshots.jsonl` last line if present
5. On-disk Hermes docs listed in the collect `hermes-docs` section — open 1–2 files that match a role, never invent doc claims

## Roles (write a heading for each; empty is a pass)

ada (board), lila (design), bash (host implement), vera (review), steward (architect/M&S), crissa (front door), scout (notes), **team-in-general**.
Do **not** write a heading for a retired profile. The door is the presence source of truth.

## Rules

- Concrete findings only: name, version, date, one line why it matters **here**.
- No hype. Thin night = thin note. Never invent sources.
- WORK (check-offable on this box) → bullet `team-improve: <short> bucket=<improve-ops|expand-caps|improve-existing|cool-fun>`
- KNOWLEDGE (useful to know) stays in the note, not a card.
- Do not scrape full podcasts. Titles + why-listen only.
- **NNT ("The Next New Thing") is the week's top GitHub repos.** The video
  TITLES do not carry the repos; the DESCRIPTIONS do . Read the
  `## NNT repos (from the video descriptions)` section of today's collect
  `team.md` (also `architect-collect/<today>/nnt-repos.md`): each video with its
  GitHub repos, one-line description, stars, last push, licence — already
  looked up, free, NOT on the extraction meter. You **pick** the repos that
  touch this install (brains, ears, mouth, render, memory, board, ops), and
  write them for the team to debate — no verdicts from you. Section
  `## NNT picks — for the team`, one line per REPO (never a video or the
  channel name):
  `nnt-pick: <owner/repo> — <github url> — <piece> — <why it matters here>`.
  A video with no repo link is not a pick. If the section says the feed is
  missing or STALE, say so in the header and pick from what it lists.
  Roles score the picks at team-eval; the ranker files what every role
  agrees on as a `team-improve:` card on the green queue and appends it to
  the top improvements list (`improvements/proposals.jsonl`).
- **Extraction is metered: 100 lookups a MONTH, shared across every night.**
  That is ~3 a night. `web_extract` is Firecrawl (cloud); search is local
  searxng and is free. Read a page only when the title alone cannot carry
  the finding and you intend to cite it. Two or three deliberate reads beat
  ten skims, and a thin night is still a legal note — say what you did not
  open rather than spending the month's budget proving it was thin.

Proposed cards section at the end. Steward files them. You do not.
