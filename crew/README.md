# crew: the machinery that makes the crew run on its own

krew's plugins give each profile its tools. `crew/` is what runs between turns: the kanban loop and its gates,
the hooks every crew profile calls before a tool, the steward sweep, and their systemd unit templates. It was
lifted as it runs on the reference install (the machine krew comes from), with that machine's names, paths and
ports moved into settings. Crew profile names (crissa, lila, ada, bash, steward, vera, scout, judge) stay: SOULs,
skills, board assignees and these scripts key on them.

## Install

    python3 scripts/krew-install.py                       # plugins (krew-kitting, krew-skill-preload, ...) + skills
    python3 crew/scripts/krew-crew-install.py             # crew/ -> ~/.krew/crew, ~/.krew/crew.example.yaml; prints the rest
    python3 crew/scripts/krew-crew-install.py --units     # + systemd user unit templates (not started)
    python3 crew/scripts/krew-crew-install.py --enable    # + start the timers
    python3 crew/scripts/krew-crew-install.py --profiles ada,bash,judge,lila,scout,steward,vera,crissa   # + the hooks

Settings: `~/.krew/crew.yaml` (`env:` mapping; the example lists every key, with the reference install's values on
`# ref:` lines). Every unit and hook runs through `crew/scripts/krew-crew-run <script>`, which exports those
settings first and puts `crew/lib` (and any other `~/.krew/*/lib`) on PYTHONPATH. Per-tree values that are lists
live in `~/.krew/crew/config/*.json` (close-clean's live repos, Crissa's hands switches, which profile gets which hook).

## Kanban loop and gates

| piece | runs | what it does |
|---|---|---|
| `krew-close-clean.py` + `lib/close_clean.py` | hook on `kanban_complete` | a worker may not close a card while it leaves uncommitted edits in a live tree (off / report / enforce) |
| `krew-review-route.py` | hook on `kanban_request_review` | every review names a reviewer who is not the builder (vera; steward reviews vera) |
| `krew-tool-count.py` | hook on every tool | per-profile tool-call counts (`--report`) |
| `kanban-loop.py` (+ `kanban_cause`, triage-close, crashed-exit) | `krew-kanban-loop.timer`, 20 min | rules greens into triage, reframes repeated failures, retreats a card to Crissa |
| `kanban-board-gate.py` (+ liveness, orphans, cron streak, skills filter, salad tripwire, skills-add) | `krew-hold-sweep.timer`, 2 min | routes blocked cards, files reviews, liveness, orphan reaping, breakers |
| `kanban-chatter-atomic.py` | `krew-kanban-chatter.timer`, 60 s | board events to the log roll |
| `kanban-day-close.py` | started by the window ticker at night | archive closed done cards, gc the board |
| `steward-sweep` (+ `sweep-ram-guard`, `kanban-blast-radius.py`) | `steward-sweep.timer`, hourly | one goal card for Steward with open cards, diagnostics, blocking parks |
| `tmp-reap`, `tmp-worktree-reap.py`, `tmp-checkout-report.py` | worktree reap timer, 30 min (report-only) | leftover /tmp checkouts |
| `crissa-hands.py` + `lib/hands_confirm.py` | on demand | Crissa's hands runner with claim checking (krew-hands is the in-plugin runner) |
| `kanban-lean.sh`, `kanban-env-clean`, `krew-tool-tax.py`, `kanban-snapshot.py`, `kanban_kpis` | by hand / by other parts | worker kit and measurement helpers |

Report-only runs: `krew-crew-run kanban-loop.py --dry-run`, `krew-crew-run kanban-board-gate.py --dry-run`,
`krew-crew-run kanban-day-close.py --dry-run`, `krew-crew-run kanban-chatter-atomic.py --dry-run`.

**Coach, don't cap.** Text that reaches an agent or a card is never cut silently: `lib/krew_cut.py` is the one way
crew code shortens it, and a cut says how much was kept and where the rest is.
Two markers ship, and both are fine: crew/ (hermes-tree.py's stack check included, whose `why` lands on restart-window
cards) uses `krew_cut.cut` (`[cut at N of M chars; <where>]`), and the off-hours window uses its own
`window/lib/text_cut.py` (`cut at N of M chars, full text: cat <file>`, which also saves the whole text under
`$KREW_HOME/logs/cut-text`). Same rule, two lanes; neither is a silent cut.

## Docs index night + memory

| piece | what it does | runs |
|---|---|---|
| `scripts/krew_memory.py` | day files (`append`, `today`), day and 3-day rolls, `workspace/TODAY.md`, and `dream-night`: roll + Hindsight retain / consolidate / reflect for the closed day | `krew-dream-night.service`, `krew-dream-reflect.service` (the window ticker starts both; idempotent by hand) |
| `scripts/krew-dream.py` | the innate rollup: Hermes sessions + kanban runs of the last N days into `rolling/<n>day.md` and Hindsight | called by the dream night |
| `scripts/nightly-krew-index.py` | the night's incremental docs-index rebuild (`docs_index.py build`), in a CPU-capped scope; off until `KREW_NIGHTLY_INDEX=1` | `krew-docs-index-night.service` (the night starts it) |
| `lib/krew_places.py`, `scripts/krew-places-watch.py`, `config/krew-places.json` | the places map: watched folders described from disk into `~/.krew/places/PLACES.md`; index that folder as docs source `places` | `krew-places-watch.service` (enabled by `--enable`) |
| `lib/crissa_docs.py`, `scripts/crissa-docs-review.py`, `config/crissa-docs.json` | Crissa's docs lookup for her hands runner, and the review queue for references she suggests | in-process / by hand |
| `hindsight/` | the Hindsight unit (opt-in), env example (keys only), upgrade pattern | `krew-hindsight.service` (never auto-enabled) |

Memory lives under `KREW_MEMORY_ROOT` (default `~/.krew/memory`). No memory, index or Hindsight data ships.
