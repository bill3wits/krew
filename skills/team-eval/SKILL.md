---
name: team-eval
description: Score tonight's research picks for YOUR job (0–3 each, one line why) so the krew can rank them; items that help every role go to the top.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [team, research, eval, improve]
    related_skills: [scout-team-research, krew-deliverable]
---

# Team eval — score the picks for your job

The user: "schedule the team eval of all items as it relates to their
job and rank and aggregate scores into a ranked list. Any items improving
them all goes to top." This runs at **01:00**, forty minutes after Scout's
research note, one run per role. You are one voice; the ranker adds them up.

## Read

1. `~/Desktop/krew-deliverables/team-research-<today>.md` — the section
   `## NNT picks — for the team` (`nnt-pick:` lines) and the `team-improve:`
   bullets under your role heading and `team-in-general`.
2. `~/.krew/memory/ms/improvements/kpis.json` — the house's KPIs (`kpis.<id>`:
   name, unit, direction, current). You name one of these for an item you score 2 or 3.
3. Nothing else. Do not research the items again; Scout did. Do not open
   the web unless a pick's line is unreadable.

## Write — one file, one score per item

`~/Desktop/krew-deliverables/<your-profile>-improvement-ideas-<today>.md`

```
# <role> — eval of tonight's picks (<today>)
lede: <one sentence: the item that would change your week most, and why>

## Scores
score: <item as written on the pick line, verbatim up to the first " — "> = <0|1|2|3> — <one line why, for YOUR job> — kpi: <kpi_id>
score: ...
```

- **0** does not touch my job · **1** nice to have · **2** a real gain for my
  job · **3** removes a pain I hit this week (name the card or the run).
- Every pick gets a line, including the 0s — silence is not a score.
- **A 2 or a 3 names the KPI it should move** (the user: tie improvements to
  the team's KPIs): end the line `— kpi: <kpi_id>` with an id from `kpis.json`
  (e.g. `kanban_blocked`, `kanban_oldest_ready_sec`, `kanban_open`, `ops_tok_s`), the
  one you expect this item to push in its direction. If no KPI would show it, write
  `— kpi: none` — that is an honest answer, and a card with no KPI is filed as such.
  The krew measures the named KPI after the card lands and reports whether it moved.
- **The ranker discounts a faked voice**: one number down the whole column,
  a 3 with no reason, or fewer than half the items scored — that file does
  not count that night and the brief says so by name. This list is your
  research time now, bounded: Scout found the items, you say what each does
  for your job.
- The item text must match the pick's first field verbatim so the ranker can
  add your score to the others'. Copy, do not paraphrase.
- A `team-improve:` pick's item is its id, the first word after the colon:
  `score: kanban-worker-no-terminal-call = 3 — stalled this way`.
  Score the listed picks; a line under a name of your own reaches nobody's sum.
- Under 200 words total. No proposals, no cards — the ranker files.

## Do not

- Score your own lane's `team-improve:` bullets higher because they are yours;
  score what it does for your job.
- Skip the file on a thin night — write the header and `## Scores` with the
  0s; the ranker reads the absence of a file as "did not vote".
