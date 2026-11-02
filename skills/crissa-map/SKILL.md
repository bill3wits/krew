---
name: crissa-map
description: One lookup — words → the skill to load and the act. Load this when no trigger line fits, then act.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, index, map]
---

# The map (one lookup, then act)

| He says | Load | Then `hands` |
|---|---|---|
| show me · put up · display · on screen · let me see | **crissa-her-wall** · Put pages and notes on Crissa's wall. | put <what> on the wall |
| his list · ask him yes/no · a task with steps · what's on my list | **crissa-list** · Crissa's list on the Control Panel. Ask him yes/n… | team_list <ask|task> <words> |
| what's the krew doing · how is the board · what is Ada doing · what got done | — | kanban report: <his words> — list the board, say counts and the cards that matter |
| something needs doing that outlives this chat · can we · could you get | **crissa-needs** · Crissa decides software calls; NEEDS HUMAN is phy… | file a triage card for ada: <one sentence> |
| Decide queue · rule a card · krew stuck · should this reach him · green this | **crissa-needs** · Crissa decides software calls; NEEDS HUMAN is phy… | rule the Decide card: try, park, later, or answer — do not file another card |
| who is here · is he authenticated · guest · re-auth | **crissa-auth** · Match the user-auth-only stance from krew-mode.jso… | as the skill says |
| look at the door · look at the room · who is that | **crissa-look** · Look at the room and at clips. team_look aims t… | as the skill says |
| morning brief · what happened overnight · M&S | **crissa-morning** · Morning report on the glass while she speaks: M… | as the skill says |
| draft copy · marketing · a still/clip · hero clip · post to X · Space · Google Ads · verbal summary | **crissa-marketing** · Marketing Crissa with the owner, live - copy, report… | as the skill says (In Render mode: crissa-render-cycle.) |
| write it up · a page on the glass · a report | **crissa-report** · Write a finding as a beautiful HTML report on t… | as the skill says |
| click · open the page · drive the desktop · type into | **crissa-cua** · Drive web pages in your own browser and native… | as the skill says |
| krew health · lamps · journals · is a unit down | **krew-panel** · Read the krew from a card — unit lamps, journa… | read the krew panel: <what> |
| big job · take this on · consultant | **crissa-big-push** · Big push — Crissa stays the voice, a consultant d… | as the skill says |
| give me the file · deliver · leave it on my desktop | **krew-deliverable** · Land work in ~/Desktop/krew-deliverables as ma… | write <name> to ~/Desktop/krew-deliverables/ |
| we keep failing · step back | **krew-reframe** · Step up a level before the third try. Write the… | as the skill says |
| who does what · who is Steward · which profile | — | read hermes-packs/crew.manifest.yaml and answer: <question> |
| who is at the door right now · how many people | — | GET $CRISSA_PLAYER/v1/rail/presence/v1/presence and say who |
| search · look up · what is the latest on · what people say | — | search the web: <question> |
| a banner · ASCII art | — | team_banner text=<his words> |
| remember · what did I say · did we | — | recall from memory: <topic> |
| where is · how do I · what did we decide · why is it like this | — | krew_docs lookup: <question>; name the ref |
| look this up · read up on · find the paper · what are they saying | — | research it and cite what you read (arxiv · grounded-citations · blogwatcher) |
| what can you do here · is there a tool for | — | name the two or three that fit, then do the one he picks |
| a deck · slides · an infographic | — | build it, on the glass, say where |
| check the krew · judge the judge | **crissa-spotcheck** · Spot-check the krew — sample what they shipped,… | as the skill says |

## Who does what

- **crissa** — Mouth + Chief of Staff: front door, last krew judgment before the user
- **ada** — ORCHESTRATOR + kanban dispatcher
- **steward** — architect of record + M&S + security
- **scout** — low-priv world/stack SOTA notes
- **bash** — coder 1
- **lila** — designer / researcher
- **vera** — board reviewer

## Where things live

- Finished work for him: `~/Desktop/krew-deliverables/`
- The krew docs: `~/.krew/docs/`
- Who does what: `hermes-packs/crew.manifest.yaml`
- Your kit: `~/.hermes/profiles/crissa/` (skills in `skills/house/`)
- The board: `~/.hermes/kanban/boards/<today>/` — never by hand; `hands` reads it
- Presence: `$CRISSA_PLAYER/v1/rail/presence/v1/presence` · glass `$CRISSA_PLAYER/v1/screen` · his list `:8898`

## Rule

One lookup, then act. Load the row's skill (if any) and give `hands` the job in plain words. No second `skill_view` in the same turn unless the skill you loaded names one. No row fits: `hands` with his words as they were. Say filed, ruled or on the glass only when the `hands` reply has a `confirmed:` line for it; otherwise say you could not confirm it.
