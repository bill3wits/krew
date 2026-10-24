---
name: ms-maintenance
description: Keep this machine's Python environments current and safe with M&S - read its nightly report, record decisions, register an env.
---

# M&S: maintenance for your Python environments

M&S surveys every environment it knows, upgrades only what is safe, and tests each change with that
environment's own smoke test. A change that breaks the smoke test is rolled back.

## Where things are
Run `ms-trail-run.py --where`. It prints the state directory, the settings file (`ms.yaml`), the reports
directory and the decisions ledger. Never hard-code these paths.

## What M&S will and will not do
- An environment is upgraded only if it has a smoke test AND its settings say `apply: green`.
  Everything else is report-only: listed, never changed.
- `promote: {after_clean_nights: N}` turns a report-only environment green after N clean nightly runs.
- If a promoted environment is rolled back two nights in a row, M&S raises it to you and keeps it green.
  Decide: keep it green, set `apply: report`, or fix what the upgrade breaks.

## Common tasks
- Register an environment you installed:
  `ms-trail-run.py --register-env NAME --env-python /path/to/python --smoke-url http://127.0.0.1:PORT/health`
  (add `--apply green`, or `--promote-after 7` to let it earn green).
- Record a decision about a package (the only sanctioned way to write the ledger):
  `ms-trail-run.py --record-decision --env ENV --package PKG --decision hold --reason "why"`.
- See what a restore would do (prints, never runs): `ms-trail-run.py --restore-plan ENV`.

## Rules
- Never edit the ledger or the freeze manifests by hand.
- Never upgrade an environment in place outside M&S; a hand change is reported as drift.
- A check M&S could not run says "not measured" - that is not the same as healthy.

## In krew
- Run it as `python3 ms/scripts/krew-ms.py` from the krew checkout: discovery first (the map, with new / removed /
  not scanned notes), then the report. `--discover-only` stops after the map. Every `ms-trail-run.py` flag passes through.
- Settings: `$KREW_HOME/ms.yaml` (start from `config/ms.example.yaml`). With no settings M&S is report-only and finds
  the machine by discovery. `upgrades: false` is the default: apply, canary, rollback and restart flags are refused
  with a printed line, and nothing changes.
- A folder or env M&S did not inventory is listed under `dropped` in the report with the reason; a check discovery
  could not run is a NOT SCANNED line on the map. Neither is ever left out silently.
