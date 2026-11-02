---
name: kanban-terminal-watchdog
description: Watchdog wrapper enforcing kanban terminal-signal protocol (complete/block) on any worker process; catches rc=0 no-signal exits.
version: 1.0.0
author: bash
platforms: [linux]
metadata:
  hermes:
    tags: [kanban, watchdog, protocol, wrapper]
---

# kanban-terminal-watchdog

One-shot script, no dependencies beyond python3 stdlib. Lives at
`~/Desktop/krew-deliverables/process/kanban-terminal-watchdog`
(next to its acceptance test `test_kanban_terminal_watchdog.py`).

## Use

```bash
python3 ~/.krew/crew/scripts/kanban-terminal-watchdog \
    [--log PATH] [--signal TOKEN ...] [--no-stream] \
    <worker-command> [worker-args ...]
```

## Decision table (at child exit)

| child rc | signal on child stdout | wrapper rc | verdict log code |
|----------|------------------------|------------|------------------|
| 0        | yes                    | 0          | `code=ok`        |
| 0        | no                     | **1**      | **`code=protocol_violation`** |
| != 0     | any                    | child rc   | `code=child_failed` |

Signal = any chunk of combined stdout+stderr containing `kanban_complete` or
`kanban_block` (plus any extra `--signal` tokens). Verdict log line (default
`/tmp/kanban-terminal-watchdog.log`):

```
<iso-ts> level=ERROR code=protocol_violation worker=<cmd> rc=0 signals_seen=none
  elapsed_s=<n> wrapper_rc=1 reason='worker exited cleanly (rc=0) without
  emitting any of: kanban_complete, kanban_block'
```

## Verification (ran)

```
python3 test_kanban_terminal_watchdog.py  ->  rc=0, "RESULT: PASS — all 3 cases behaved as specified"
```

Cases: (1) rc=0 + no signal -> wrapper rc=1 + protocol_violation logged;
(2) rc=0 + `kanban_complete` printed -> wrapper rc=0 + `code=ok`;
(3) rc=3 + no signal -> wrapper rc=3 propagated + `code=child_failed`.
Plus edge probes: unknown worker path -> rc=127; custom `--signal card_closed`
-> passes when token printed.

## Honest scope note (read before wiring into anything)

This wrapper only sees the worker process's **stdout/stderr stream**. If a
worker calls `kanban_complete`/`kanban_block` as a tool call whose output
does NOT flow to its own stdout (e.g. hermes CLI where tool results go to the
session log, not the child's stdout), the wrapper will flag protocol_violation
even though the card transitioned. It is correct and useful as a
standalone/gateway guard for processes where tool signals do print (or when
the worker is scripted to echo the token), but it is NOT a substitute for
inspecting the kanban DB / session log for real hermes workers. The edge test
confirming this: worker that wrote `kanban_complete` to a side file, not
stdout, was correctly flagged rc=1.

## Not installed into the dispatcher

Deliberately not placed on PATH or wired into the kanban dispatcher. The
existing dispatcher already logs `protocol_violation` events (see run 272 metadata). This is a reusable guard the board can opt
into, not a live change to how the krew runs.
