---
name: ada-ops-efficiency
description: Sunday ops-efficiency narrative. KPI-tied action items only.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Ada, ops, kpi]
---

# Ada — weekly ops efficiency

Write **`{{deliverables_dir}}/ops-efficiency-YYYY-MM-DD.md`** (the owner's inbox; `{{deliverables_dir}}` is
the folder your SOUL names, `KREW_DELIVERABLES` in `crew.yaml`). Read nothing outside it for this report.

Read, in order:

1. `{{deliverables_dir}}/ops-efficiency-YYYY-MM-DD.json` — per-KPI `history_n`,
   `status` (`ok` or `insufficient history`), open `ops_action_id`s
2. `{{deliverables_dir}}/ops-efficiency-YYYY-MM-DD.html` — charts + tracked-changes
   table (same facts, pictures)

## Required sections (markdown)

1. Charts pointer (dated HTML filename).
2. Tracked changes this week (cite `history_n`, `added`, last snapshot ts).
3. **Action items** — only if tied to a named KPI. Format exactly:

   `ops-action: <verb> — <kpi_id> — <why>`

   Verbs: `keep-baseline`, `stop-tracking`, `investigate`.
   Example: `ops-action: keep-baseline — kanban_blocked — new tool; baseline established`.
4. Last week's open `ops_action_id`s: still open or closed?

Vera unlock dispositions (ledger `unlock=`, not a new verb here):

- **keep** → a `kpi_id` is on this JSON. You may `keep-baseline` or `investigate`.
- **toss** → `stop-tracking` only after `history_n` ≥ 3; if never registered, nothing to stop.
- **land-only** → not a KPI. Do not invent one for security/hygiene lands.

Ad-hoc probes (`ops_tok_s`, bash `scripts/ops-toks.py`) show `sample=adhoc`
and `insufficient history` until bash records a run. That is not stop-tracking.
Crissa reports the number; she does not take the sample.

## Laws

- **No KPI history ≠ healthy.** If `status` is `insufficient history` or
  `history_n` < 3, say `insufficient history` — do **not** `stop-tracking`.
- Thin week (no movement, enough history): `no actions` is a pass.
- Do not implement. Steward files and closes `ops-action:` cards
  (assignee **steward**). Do not take those cards.
- Do not invent numbers. Cite the JSON fields.
