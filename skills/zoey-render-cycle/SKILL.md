---
name: zoey-render-cycle
description: ONLY while the krew is already in Render. "Have the team do a still/clip" in normal mode is skill zoey-marketing - a card for Lila, never ask him to flip Render.
version: 0.1.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, render, comfy, lila, vera, judge]
---

# Render cycle (Render occupancy only)

**Not in Render?** Stop: load **zoey-marketing** and file the card for Lila (one `hands` job). Never check the GPU mode for him, never ask him to flip Render. While Render is closed, Lila's card files a render order (`render-orders.py add`) or waits for the window. Once Render is open and Comfy answers on `:8188`, she draws directly and iterates; Vera reviews that live output. The render runner (`krew-render-runner.py`) works only a job the owner sends to the heavy window. When it is done, the krew's `look_compare` verdict against the spec is on the card - say it, PASS or FAIL, never "it looks great". Default still `qwen-image-2512`; an edit is `qwen-edit-2511`. Never FLUX.2 or Qwen-Image 2.1 for anything that leaves the machine.

Use when `~/.hermes/krew-gpu-mode` is **`render`** and the user wants a picture
taken all the way (draw, iterate, on the wall). In Render you **sleep**;
Lila’s studio is the loop. Recipe catalog + tools: skill **`krew-render`**.

**Silo:** do not start Muse. Render parks **ops** (your composer). The live
27B is **crew the crew engine**. **You** and Steward sleep; Ada stays up to dispatch
and Lila/Vera/Bash keep working cards, one at a time (skill `render-lane`).
The voice server stays if Voice is ON — you limp (Audio8 ear, `lila` voice on the mouth). Bash
does **not** render (he is the coder). Steward does **not** render (sweeps /
M&S). You hold `image_generate` on the room path. **Lila** holds the same
tools (HTTP studio, not a spawn). Vera **looks** on crew. The Comfy tool
refuses unless occupancy is Render.

## Loop (no swaps)

1. `image_generate` - recipe by licence: `qwen-image-2512` (the default) for anything published or marketing;
   `qwen-edit-2511` if they handed a photo. An opt-in draft engine only when the owner turned it on.
2. Look at the **file** with `look_compare` against the spec (PASS / FAIL + one fix). A 200 is not success.
3. **Lila** (taste, on-spec) then **Vera** (pass / stop / rewrite).
4. If Vera fails and there is a rewrite, generate again. Max 3 rounds.
5. `team_display` the winner. Speak one line.

Helper (same loop, no gateway):

```bash
python3 ~/.krew/crew/scripts/render-cycle.py "SPEC" --model qwen-image-2512   # the default; opt-in drafts only when turned on
```

**Interactive (family Friday):** the user talks to Lila on the Control Panel studio
(the panel's `/panel` page, Render only). She orients on engage (named helm line) and the
kiosk fronts that panel. the user may inject a turn anytime — Talk, Mic, or CLI;
they are not waiting to be asked. Ada is awake, so a kanban comment does reach
her — but the studio and a card share one seat and take turns, so a card in
flight makes Lila answer "give me a moment". Voice-Only: Mic is armed, speech
*is* the turn.
Or:

```bash
python3 ~/.krew/crew/scripts/render-studio "warmer light, keep the red cup"
```

Lila speaks (`lila` voice, Voice ON) while Comfy draws, then she and Vera look.
You do not need to `image_generate` yourself if they are in that studio.

If the tool says not Render / Comfy parked: one line — hit **Render** on the panel.
Do not invent a second occupancy.
