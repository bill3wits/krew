---
name: steward-sweep
description: Hourly board sweep — archive, stop churn, escalate to Crissa not the user.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Steward, kanban, sweep, needs-crissa]
---

# Steward sweep

Hourly card from `steward-sweep`. Additive layer only: archive, orphan
repair, **one** human escalation. Dispatcher already retries workers.

## Human-only / thread-churn

If one logical change burned 3+ cards without landing, **stop**. Software
loops get a **question to Crissa on the parked source** (`team_board ask
id=<source> …`), never a new work card. **Never file a card whose
prerequisite is the park it will release** — "deploy …,
then unblock" was filed under (its `--parent`), so the todo
could not promote and the park could not close: a cycle nothing read for five
days. If the fix is a deploy the lane already has a card for, say so on the
source and stop; a systemic card is Ada's to cut from Crissa's ruling.

**NEEDS HUMAN** (physical world only — unplugged camera, a body in the chair,
hardware). File **one** card:

- Title **`NEEDS HUMAN: <short>`**, assignee: **crissa**, **`--initial-status blocked`**.
- **Page her:** `team_page` (or `scripts/crissa-page.py`).
- body: one paragraph, the card ids, what a human must do with their hands
- crystal: Crissa writes `~/Desktop/krew-deliverables/needs-human-YYYY-MM-DD.md`

Do **not** file `needs-<person>`, `needs-user`, or `needs-crissa` for sudo, vLLM,
deps, or unit restarts. Files go **the change gate → Vera → apply**. Venv goes host
`uv` + smoke / Steward `M&S land`. Stuck after that? **needs-crissa last
resort** — she decides. the user does not. A card with `repeated_failures`
(3+) or a worker `frame:` is **kanban-loop's** (every 20 min): it cuts a
`reframe:` triage card and parks the source `dependency`. Do not escalate
one of those by hand before the loop has had a tick; if the loop is off
(`~/.hermes/kanban-loop.off`), say so on the sweep card.

**Breaker parks (`gave_up`, `block_kind` usually NULL).** The crash/verb
wall writes `status=blocked` and never stamps a kind — NULL is leftover,
not a class. Untyped worker `kanban_block` is also NULL. The class is
**last event `gave_up`** (not unblocked after). Patch 70 made that park
sticky: the dispatcher will **not** retry. Do **not** `unblock` (re-burns
salad). Do **not** archive. When able (salad tripwire cold — last reset
≥30 min or no open hits; crew the crew engine answers; this turn is
actually calling tools) and the card has no `frame:` / `reframe-cut:`
yet, comment a mechanical `frame:` from `last_failure_error` so the next
loop tick cuts a reframe. A single crash used to be "leave to the
dispatcher" — that is wrong now. Do **not** assign a person,
`grok`, or `fable`. Do **not** telegram-subscribe.

A blocked **NEEDS HUMAN** waiting for a body in the room is healthy. Leave it.
A blocked "needs the user to approve a pip bump" is a routing error — unblock
and send it to Vera (files) or bash/steward (venv), not Crissa.

Same for **Comfy / render SOTA**. A card titled `M&S render SOTA:` is software
(checkout + **orderly Render swap** + smoke + `krew-mode normal` + the change gate).
Do not ad-hoc stop ops/crew to free VRAM. Do not `NEEDS HUMAN` it. Do not ask
the user. Occupancy is skill **`krew-render`**: **`krew-mode` /
`krew_gpu_mode` / the Control Panel**. Not a body in the chair, not
`/tmp/krew-train-hold`, not `/tmp/steward-park-*.py`. A card blocked on
“VRAM &lt; 24 GiB in Standard” skipped the switcher: not healthy-transient.
Unblock onto bash/lila with the switcher **first**, then mint. Do not
leave it overnight for the user.

A hold leftover while the flag reads `normal` is the overnight
failure: park never restored. `systemctl start crew` while
the hold still names `crew.service` exits 0 and the unit stays dead. Release
is `krew-mode normal` (or `krew-gpu-hold` on `kanban_complete` for a
`# card=` you own). Do not delete a human/training hold (no `# card=` line).

**Bad Render switchover ( stall).** Protocol_violation /
`APIConnectionError` to the crew engine while workers are in Render is occupancy
(crew should be up). `:8000` refused in Render is expected (ops parked).
Scan these flags directly (`crew/scripts/krew-pin-check.py` checks the Hermes-side pins, not occupancy):

- `~/.hermes/krew-gpu-mode`
- `/tmp/krew-train-hold`
- the crew engine (kanban). `:8000` down in Render is expected.
- Ada `max_in_progress` and `hermes-ada-gateway`
Do not rewrite `base_url`. Crissa is ops; the board is crew.

If Ada is dispatching onto a parked 27B, the diagnosis is **bad Render
switchover**. Comment that phrase on the crashing cards, leave them for
janitor's occupancy card (or file one if janitor has
not). Do not spawn another worker at the corpse. Pin-on-fail for a
*package upgrade* is the M&S report routine — different from this.

## Close

When the subject work is already on disk, **archive** the escalation
(and its notify/reassign clones). Do not `kanban_complete` an unclaimed
escalation.

## Stranded ready on grok / fable / a person

Those names are not Hermes profiles. Ada will never claim them. Reassign
to Ada, then stop. Do not file a the user ask.

## Research routing

Scout writes notes. Lila researches on the board. Ada does not.

If a ready/blocked card is `assignee=ada` and (title starts `research:` or
`skills` contains `research`), that is a routing error, not a missing pin.
Reassign **lila** (research) or **bash** (the body is already an implement).
Comment that `research` is a category folder, not a skill. Do not `--skill
research`. Do not assign **scout** (no kanban toolset).

## Hermes update / kit RED

Pin-check RED on toolsets or `krew-kanban-lean` plugin link is occupancy/kit,
not a the user ask. Ritual: the krew docs. Do not bounce
ops/crew. Do not `--skill research`. Workers carry `kanban_lean` (ten verbs);
Ada the full fourteen; Crissa `team_board` only. Measure tax with
`scripts/krew-tool-tax.py`, not `hermes prompt-size`.
