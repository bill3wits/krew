---
name: krew-deliverable
description: >-
  Land work in ~/Desktop/krew-deliverables as markdown plus an HTML sibling
  the user can open. Renderer: scripts/krew-report-html.py --file. Use when
  crystallizing a card, writing a Desktop note, or putting an ask on the glass.
version: 1.1.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [crystal, deliverable, html, desktop, house]
    related_skills: [ada-orchestrate, crissa-needs, crissa-her-wall]
---

# Krew deliverable (the user inbox)

the user folder: **`~/Desktop/krew-deliverables/`** (Ada docker: `/deliverables`).
May be a symlink; write through that path. Markdown is the record. **HTML
beside it is what he reads.** He scans **INDEX.html**.

| Place | Write here |
|-------|------------|
| *(root)* | today's daily notes only (`ms-`, `logs-`, `architect-`, …) |
| `ask/` | king-bar briefs `ask-YYYY-MM-DD-<slug>.md` |
| `ideas/` | curated improvement-ideas |
| `process/` | how the krew is run |
| `shelf/` | other lasting crystals |
| `scratch/` | probes — not the user-facing |

Do not dump the user-facing notes only in `docs/` or a card comment. Do not
leave probes at the root.

## Write

1. Write the `.md` first (the crystal).
2. Render the sibling:

```bash
python3 ~/.krew/crew/scripts/krew-report-html.py --file /abs/path/<slug>.md
```

Ada (no host shell): page bash to run the renderer on `{{deliverables_dir}}/<shelf>/<slug>.md`
(`<shelf>` is `ask`, `process`, `shelf`, or empty for today's daily notes).

No terminal (Crissa): write the `.md`, then the `.html` with the **file** tool
(same stem), or page bash to run the renderer. Do not skip the sibling.

3. Complete with `artifacts=["/abs/path/<slug>.md","/abs/path/<slug>.html"]`.
   Plugin **krew-crystal** refuses a Desktop `.md` that has no `.html`.

Pin / host-smoke crystals do not need HTML. File crystals do.
A deliverable that **runs** (a `.py`/`.sh`, or a folder of them) needs one
more line in the completion summary, the exact command and its exit:
`ran: python3 recon_harness.py --self-test rc=0`. The gate refuses the
claim without the command (: "runs cleanly (rc=0)" over a
builder that produced `python3 /usr/bin/python3 …`).

## Present (when he should see it now)

Crissa: `team_display {"action":"tab","url":"/abs/path/<slug>.html"}`.
Headline popup (`team_needs_sheet`) is not this file.

## Ask briefs (king-bar)

Write `ask/ask-YYYY-MM-DD-<slug>.md` plus the HTML sibling. Every option
has **Tradeoffs** and **Risks**. Last option is always **Leave it**.
