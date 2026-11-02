---
name: krew-bring-to-life
description: The render-and-judge loop for a wall loop (submit, wait for the collector, judge with vision) and the mouth-tile intake a take needs. Use on Render / DELETE / Repair cards and on any card that names a take or a loop.
version: 0.1.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [render, comfy, vera, lila, bash, mouth, takes]
    related_skills: [krew-render, render-lane, pose-flourish, krew-deliverable]
---

# Bring a loop to life (crew do-list)

The the user's page is the krew docs. This is your side of it.

## Render a loop (Render card)

1. Read the card once. It names the seed still, the plate, the prompt file
   and the seconds. Do not re-derive any of them.
2. Submit, one call, then **end your turn**:
   **15 s life loops** (eyes, mouse): first-only, then wrap:
   `python3 ~/.krew/crew/scripts/render-submit.py --card <id> --name <loop> --recipe ltx25-i2v --seed <hub.png> --prompt-file <file> --seconds 15`
   Do not pass `--last`. **Short scoots** stay `--recipe ltx25-flf2v` with seed and plate. `wan22-i2v` only when the card says so.
   It parks ops itself, starts Comfy if it is down, queues the job and
   comments the job id on the card. Never poll Comfy. Never build a graph.
   Never change the seed.
3. The collector wakes the card when the file lands. Your next turn is
   **judging only**: `vision_analyze` `take-N.f0.png`, `take-N.last.png`
   and the mp4 in the take folder the comment names. Say what the bun, the
   eyes and the landing do, in three lines.
4. Both ends at or above NCC 0.97 and the eyes right → `kanban complete`
   with that verdict. Otherwise submit take N+1 with **one** changed
   sentence in the prompt, and end the turn. Three takes, then stop and
   say why.
5. Never install a take, never touch `inventory.json` or a playlist. The
   the user looks, then intake is by hand.

## Delete / repair card

1. Look at the clip (`vision_analyze` a frame) before you act; say what you
   saw on the card.
2. Delete = `python3 scripts/pose-freelance.py shelve <key>` — the mp4 and
   its mouth sidecar go to `_shelved/`, the inventory row goes. Repair =
   the card says which tool.
3. Complete with the shelf rows or the repaired file as the crystal. The
   deliverable record in `krew-renders/` is written for you when the card
   closes.

## A talking take (only when a card asks)

`pose-intake.py <key> --line "<sentence>" --apply`, then
`pose-head-frame.py --clips clip-<key> --ctx 0`, then
`pose-loop-playlist.py clip-<key>.mp4 --harvest --ctx 0`, each inside
`systemd-run --user --scope -p CPUQuota=300%`. Stamping the mouths is the
the user's, on the Takes page. Do not stamp.

## Why it is shaped like this

Four 40-call budgets went to polling a two-hour render on, and
its hold kept the user's brain down for two hours. Submit-and-leave is the
fix; the collector and the sweeps are the way back.
