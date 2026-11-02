---
name: ada-map
description: One lookup — the skill to load, then act with your board tools. Not a hands child.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Ada, index, map]
---

# The map (one lookup, then act)

| He says | Load | Then `act` |
|---|---|---|
| decompose · assign · what's on the board · dispatch | — | kanban list / decompose / assign — you own the board |
| page Crissa · he must decide · needs_input | — | kanban_block kind=needs_input with the question, then team_page |
| crystal · deliverable · complete the card | **krew-deliverable** · Land work in ~/Desktop/krew-deliverables as ma… | as the skill says |
| third try · same failure · step back | **krew-reframe** · Step up a level before the third try. Write the… | as the skill says |
| consultant · docker · risky · untrusted | **ada-orchestrate** · How Ada runs the board — crystal, fan-out, T0,… | as the skill says |
| kit drift · pack vs live · pin-check | — | read the krew docs; do not cp whole live YAML |

## Who does what

- **crissa** — Mouth + Chief of Staff: front door, last krew judgment before the user
- **ada** — ORCHESTRATOR + kanban dispatcher
- **steward** — architect of record + M&S + security
- **scout** — low-priv world/stack SOTA notes
- **bash** — coder 1
- **lila** — designer / researcher
- **vera** — board reviewer

## Where things live

- Finished work: `~/Desktop/krew-deliverables/`
- Repo: `~/.krew/crew/`
- Board: `~/.hermes/kanban/boards/<today>/`
- Who does what: `~/.krew/crew/config/crew.manifest.yaml`

## Rule

One lookup, then act with your tools. No second skill_view unless the skill you loaded names one. You are the hands — do not spawn hermes -p ada.
