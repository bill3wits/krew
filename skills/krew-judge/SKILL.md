---
name: krew-judge
description: The Judge protocol — read one diagnosed card, answer with ONE `VERB:` line on the source card, complete your own. Pinned on every judge card (`--skill krew-judge`); Ada's registry carries it so the loop/gate can pin it.
version: 1.0.0
---

# krew-judge — one call, one verb

A judge card is filed by kanban-loop or the board gate when a detector has already
diagnosed a card and only a judgment is missing. Its body names the source card and carries
the evidence. You answer once.

## Read (one `kanban_show`, at most one `read_file`)

- `source: t_xxxxxxxx` — the card you rule on. `kanban_show` it: body, last run summary,
  `last_failure_error`, comments (a ruler's word — crissa, user, a person — outranks yours; if one is
  there, your verb repeats it).
- `cause:` — what the detector saw: `verb` (the worker exited rc=0 with tools called and no
  terminal verb: the work may be done), `ownerless` (a typed-less block nobody reads),
  `dup` (a normalised-title twin), `frame` (a reframe that needs a project decision).
- `evidence:` — log tail, deliverable paths, run ids. If a deliverable path is named,
  `read_file` its first 80 lines: a file that exists and says what the card asked = done.

## Rule (the grammar the board gate lands — first line of ONE comment on the SOURCE card)

| situation | line |
|---|---|
| work done, proof on disk or in the run summary | `VERB: complete — <the proof, one clause>` |
| not done; only a human can supply the missing thing | `VERB: block capability — <what>` |
| not done; a choice is needed, not a fact | `VERB: block needs_input — <the question>` |
| an engine/harness fault, the work never started | `VERB: block transient — wake: engine-ok` |
| duplicate or superseded | `VERB: archive — dup of <id>` / `VERB: archive — superseded by <id>` |
| wrong profile | `VERB: reassign <bash\|steward\|lila\|vera> — <why>` |
| wait for a date | `VERB: park later <YYYY-MM-DD> — <why>` |

Then `kanban_complete` your own card with the same line as the result.

## Never

- a second comment, a plan, prose before the verb, a `terminal` call, a file edit, a spawn,
  a `kanban_complete`/`kanban_block` on the source card (the gate does that from your line).
- a verb you cannot back with one clause of evidence: when the evidence is not there, the
  honest line is `VERB: block needs_input — <what would settle it>`.
