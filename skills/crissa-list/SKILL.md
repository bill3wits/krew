---
name: crissa-list
description: Crissa's list on the Control Panel. Ask him yes/no, or keep a task with steps. Ada and the crew share the tool.
version: 1.1.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, Ada, approvals, list]
    related_skills: [crissa-needs, crissa-her-wall, ada-orchestrate]
---

# Crissa's list

Control Panel tile **Crissa's list** (`:8898`). Tool **`team_list`**.
Crissa, Ada, and the crew (bash/lila/steward/vera) all carry it.
`show_me {"what":"approvals"}` puts the page on the glass.

Crew **kanban** stays the board. Do not copy a card onto this list.

## Two kinds

| He means | You call | Who moves it |
|----------|----------|--------------|
| Needs his yes/no | `team_list {"action":"ask","title":"…","text":"…"}` | **He** taps YES/NO |
| A job with steps | `team_list {"action":"task","title":"…","steps":"1. …\n2. …"}` | Owner `note`s, then `done` |

`list` / `show id=` to read. Never dump ids out loud. `source` is who filed it.

## When

- He says put it on Crissa's list / the list / Approvals / your todos.
- A king-bar choice (skill **crissa-needs**): file `ask`, one simple question.
- He (or Ada) gives work with steps: `task`, then do it.

Software the krew can decide → decide (crissa-needs). Do not `ask`.
**Ruling a card on your Decide queue** (try · park · later · done · archive) is skill **crissa-needs**, not this list.
Need Crissa / `needs_input` cards **are** this list. Close them only with
`team_board {"action":"answer","id":"t_…","text":"<his words>"}` — that
lands the answer, unblocks, and clears the to-do. Do not `done` a
card-linked ask.

## Do not

- Mark `done` on an `ask` — that waits on him.
- File krew work here (`team_board` / Ada's board).
- Read the queue aloud. Show the page, one spoken line.
