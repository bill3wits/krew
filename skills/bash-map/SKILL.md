---
name: bash-map
description: One lookup, then the skill to load and act on the host. Not a hands child.
version: 1.1.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Bash, index, map]
---

# The map (one lookup, then act)

## Rules (checkouts and tests)

- **Comparison checkouts go under the card's workspace on disk, never /tmp** (tmpfs is RAM, against the worker's memory cap): `git worktree add $HERMES_KANBAN_WORKSPACE/baseline <ref>`, and `git worktree remove` it before the card ends.
- **Name the test files your change touches**: never a whole `tests/` run, never a tests/ glob.

| They say | Load | Then `act` |
|---|---|---|
| implement · diff · land the card | — | terminal + file; complete with a path under the deliverables folder |
| render · still · clip · Comfy | **krew-render** · Local Comfy stills and short video in Render occupancy | as the skill says |
| third try · same failure · frame | **krew-reframe** · Step up a level before the third try | as the skill says |
| adversarial · non-trivial refactor | **adversarial-gate** · Single-stream or dual hot/cold adversarial review | as the skill says |
| ops tok/s | **ops-toks** · Measure the engines' tok/s | as the skill says |
| crystal · deliverable · complete the card | **krew-deliverable** · Land work in the deliverables folder as md + html | as the skill says |

## Who does what

- **zoey** — Mouth and Chief of Staff: front door, last judgment before the owner
- **ada** — orchestrator and kanban dispatcher
- **steward** — architect of record, M&S, security; coder 2
- **scout** — low-privilege world/stack notes
- **bash** — coder 1
- **lila** — designer / researcher
- **vera** — board reviewer

## Where things live

- Finished work: the deliverables folder your SOUL names (`KREW_DELIVERABLES`)
- Crew scripts: `~/.krew/crew/`
- Board: the kanban board Ada runs (`team_board`)

## Rule

One lookup, then act. Terminal and file stay on the belt. You are the hands: do not spawn a nested hermes CLI of yourself.
