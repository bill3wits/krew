---
name: steward-map
description: One lookup — M&S / security / architect skill, then act.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Steward, index, map]
---

# The map (one lookup, then act)

## Krew rules (from a worker OOM)

- **Comparison checkouts go under the card's workspace on disk, never /tmp** (/tmp is tmpfs = RAM, against the worker's 4 GB cap): `git worktree add $HERMES_KANBAN_WORKSPACE/baseline <ref>`, and `git worktree remove` it before the card ends (`--force` + `prune` if dirty — the OOM left a 3 GB tree behind).
- **Name the test files your change touches** — never `pytest tests/`, never a tests/ glob, never `test_krew_mode.py` together with another test file (it starts Crissa's parked gateway).

| He says | Load | Then `act` |
|---|---|---|
| M&S · trend · dead upstream · pin | — | read the krew docs and the krew docs |
| reframe · third try | **krew-reframe** · Step up a level before the third try. Write the… | as the skill says |
| kit · hermes update · tree patches | — | the krew docs; hermes-tree.py status |
| search · what is the latest | — | web_search / search — those schemas stay on the belt |

## Who does what

- **zoey** — Mouth + Chief of Staff: front door, last krew judgment before the user
- **ada** — ORCHESTRATOR + kanban dispatcher
- **steward** — architect of record + M&S + security
- **scout** — low-priv world/stack SOTA notes
- **bash** — coder 1
- **lila** — designer / researcher
- **vera** — board reviewer

## Where things live

- M&S: `~/.krew/memory/ms/`
- Repo: `~/.krew/crew/`
- Board: `~/.hermes/kanban/boards/<today>/`

## Rule

One lookup, then act. Terminal and file stay on the belt.
