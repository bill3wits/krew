---
name: crissa-report
description: Write a finding as a beautiful HTML report on the glass — show_me report.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, report, html, show_me, deliverable]
    related_skills: [crissa-her-wall, krew-deliverable, crissa-cua]
---

# Crissa report — findings as a page, not a paragraph

**When:** the answer is a summary, a comparison, a table, a log digest, an
audit, a "what happened last night" — anything past two sentences that he
would rather read than hear. He says *report · summary · write it up ·
findings · what did you find · brief me · show me the numbers*.

**One call:**

```json
show_me {"what":"report","title":"<title>","text":"<markdown in TEMPLATE.md's shape>"}
```

The tool renders your markdown with **her template** (masthead · KPI tiles ·
callouts · tables · sources), saves `<slug>.md` + `<slug>.html` in
`~/Desktop/krew-deliverables/`, and opens the HTML on **your glass** as a
page. It returns the page that really loaded — read the return, say one line.

## Write it like this (TEMPLATE.md)

```
# Title — the finding in one line
lede: the sentence he can act on
kpi: dead runs = 76; profiles hit = 3; fix routed = half
## What I found
- one idea per bullet, a number if it changes what he does
> the one thing that matters most
## Detail
| item | now | before | note |
## What I could not verify
## Next
---
sources: where the numbers came from
```

- **Lead with the answer.** The lede is the report; the rest is evidence.
- **KPI line = tiles.** Three to five `label = value` pairs. Numbers live
  here or in the table, not in prose.
- **`> ` quote = callout.** One per report. His words, back to him.
- **Table for anything with two or more dimensions.** Never a list of
  numbers in sentences.
- **"What I could not verify"** is not optional. An empty section reads as
  "everything is verified" — write "nothing" only if that is true.
- **Sources** at the foot, always: a log path, a page, a file, who said it.
- No em-dashes, no "in summary", no closing offer. Stop when the content stops.

## One hands job, read and write together (fast)

A report or copy on krew data is ONE `hands` job, never a research errand first: `Read <SOURCE>, then show_me report titled "<slug>" with <what he asked> - numbers only from the file.` SOURCE: improvement loops = `~/.krew/memory/improvements/outcomes-<newest day>.md`; the krew today = `~/Desktop/krew-deliverables/krew-daily.json`; marketing copy, posts, ads = skill **crissa-marketing**.
Say what was MEASURED, never what caused it: a KPI that rose after a try "measured better after it", never "it moved", "the effect of" or "the big win". Say KPI names in words (ops tokens per second, not ops_tok_s).

## After it is up

Say **one** spoken line: the lede. Then curate as in **crissa-her-wall**: when the
topic moves, `show_me {"what":"home"}`. The file stays in
`krew-deliverables` (INDEX.html lists it) — he can open it later.

## Daily team summary / morning report

Skill **crissa-morning**. He says morning report / brief / last night.

`show_me {"what":"morning"}` or `what=report` with the gatherer markdown.
The sheet sits **beside** her (doc-window on wallpaper, letter sheet on
Brave). Wallpaper does not paint HTML — do not CDP-navigate a file:// onto
a wall that is not Brave.

Gatherer: `python3 ~/.krew/crew/scripts/crissa-team-daily.py` (default: **yesterday**).
M&S, architecture, Ada efficiency, that day's completions, improvement
ideas. Built at 02:00. Page bash if the HTML is missing.

**Ops tok/s:** you do **not** run the probe. `show_me {"what":"vllm"}` or
read `ops_tok_s` from the table. Bash records it.

## When NOT to use it

- A one-line answer → say it.
- A live krew page (board, panel, presence) → `show_me {"what":"kanban"}`.
- Two lines for the corner of the wall → `show_me {"what":"note","text":"…"}`.
- A picture or a chart he handed you → `show_me {"what":"<path or url>"}`.
