---
name: krew-panel
description: Read the machine from a card — unit lamps, journals, unit scripts, log lanes, security, health. Plus the four safe buttons.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [panel, units, journal, logs, health, diagnose, ada, steward, bash]
    related_skills: [steward-logs, steward-sweep, krew-render]
---

# The Control Panel, from a card

The Control Panel (`panel.url` in krew.yaml) is what a human reads when the machine feels wrong.
These tools are that screen. Same HTTP routes the panel's own JavaScript calls,
so you can see exactly what the owner sees at the glass — no more, and no less.

## Look before you blame

Order matters. Most "X is broken" turns end at step 1 or 2.

1. **`panel_units`** — all 21 lamps at once. `trouble_only=true` when you just
   want what is lit. A tile is `ok` / `warn` / `bad`; a lit tile is asking for
   you. Note the `restarts` count — a unit that is `active` with 40 restarts is
   not healthy, it is flapping.
2. **`panel_journal unit=<name>`** — the unit's own stderr. Get the name from
   `panel_units`; anything not on the panel is refused by the server, which is
   the allowlist doing its job, not a bug to work around.
3. **`panel_script unit=<name>`** — how it is actually launched: flags, env,
   working directory. Read this *before* proposing a change to how something
   runs. Bodies are cut at 6000 chars; raise `max_chars` or open the path.
4. **`panel_log lane=…`** — these are rail-written *files*, not journals:
   - `camera` — door and PTZ activity
   - `voice` — what was heard and what was spoken
   - `security` — face stamps and refusals
5. **`panel_security`** — who was seen, what was turned away.

`panel_health` reads the last battery for free (it is already in crew state).
`run=true` starts a new one. **A started job is not a verdict** — the answer you
report is `battery.worst`, not "I started a health check."

## The four buttons

Everything else on the panel stays a human's call. These four you may press:

| Tool | Use it when |
|---|---|
| `panel_camera_home` | The camera is pointed somewhere odd and no walk is running |
| `panel_kiosk` | The room screen is on the wrong page |
| `panel_announcer` | The machine is talking over someone, or a quiet-hours job is starting |
| `panel_health` | Something feels wrong and you want a real probe |

**`panel_camera_home` will refuse and name the holder** if a sweep, an Inspector
walk, a tool hold or a pause is in flight. That refusal is correct — someone is
mid-tour and the camera is theirs. Wait, do not retry in a loop.

**`panel_kiosk dest=wall` is refused in Render.** the GPU-mode switcher pins the studio
panel up on the way in and would undo you on the next tick. Leave Render first
if the wall is really what is wanted.

## Not here on purpose

- **Restarting a unit** and the **Voice master** — a human's call at the glass.
- **`who_is_here` / error_check** — those take the Inspector lease.
- **Occupancy** — that is the machine's GPU-mode tool. One switcher, not two.

If you need one of those, say which and why, and let the owner press it.

## Report the glass

Every one of these reads the real state back after acting and answers with what
landed. So does your sentence to the owner. If a tool says the camera is not at
the door, or the piece did not land, **say that** — do not upgrade it to "done"
because the POST returned 200. A 200 is not success anywhere in this machine.
