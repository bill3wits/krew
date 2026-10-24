---
name: render-lane
description: Working a render card. Render open and Comfy on :8188 means draw with image_generate and iterate. Render closed means an order (render-orders.py add) or wait. The render runner only for a job the owner sends to the heavy window. look_compare against the spec. Never run systemctl from a card.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [render, kanban, lila, vera, bash, occupancy, comfy]
    related_skills: [krew-render, zoey-render-cycle, ada-orchestrate]
---

# The render lane

Until Render deleted the kanban team: entering it stopped Ada's
gateway and killed every worker, so a card could not render and a worker that
called `krew_gpu_mode(mode='render')` killed itself mid-call.

It does not any more. **You keep working while Lila draws.** Who stays:

| | In Render |
|---|---|
| **Ada** | up, dispatching on **crew the crew engine** — **one card krew-wide** |
| **Lila** | up on **crew the crew engine**. Both mouths: the studio at the glass *and* a kanban worker |
| **Vera** | up on **crew the crew engine**. Looks at pixels; she has no generate tools and does not need them |
| **Bash** | up on **crew the crew engine**. He is the coder — he does not render unless the card says so |
| **Crissa** | asleep. Gateway **and** Brave kiosk (`zoey-display`) **down**. Tk wall is the glass. That is designed — do not diagnose a crash |
| **Steward** | asleep. Sweeps and M&S are not render work. Do not file zoey-display crash-loop while occupancy is Render or wallpaper is the glass |

## The one rule (2026-10-08): open Render draws direct; a closed machine orders or waits

*Retired: every card filed an order and ended its turn.* While Render is open
(`~/.hermes/krew-gpu-mode` is `render` and Comfy answers on `:8188`), Lila and Bash draw
with `image_generate` / `video_generate` and iterate in the turn. Vera reviews that live
output. An **order** is only when Render is not open, or when the owner sends the job to
the heavy window. Default still `qwen-image-2512`; an edit is `qwen-edit-2511`.
Never FLUX.2 or Qwen-Image 2.1 for anything that leaves the machine.

1. Render open: draw, look, draw again. Render closed: the **`render_order`** tool
   (`recipe`, `prompt`, `negative`, `image` for i2v/edit, `seed`; Lila and Bash) or
   `~/.krew/crew/scripts/render-orders.py add --workflow <recipe workflow> --set <node>.text="<prompt>" [--set <image node>.image=<file>] --seed <n> --by <you> --card <this card>`,
   then end the turn. Or wait. Workflow names, nodes and licences: **`krew-render`**.
2. The render window holds a closed card. **The render runner** (`krew-render-runner.py`) works
   the heavy-window queue (crew off, ops and Crissa up) only for a job the owner sends there.
   Ordinary cards do not start it, flip a mode, or touch Comfy while Render is closed.
3. When an order is `done` (`render-orders.py show <id>` names `out_dir`), **look**.
4. A re-roll while Render is open is another generate. A re-roll while it is closed is
   another order.

## One card, one seat

Ada is capped at `(1, 1)` for the window, so there is exactly one card in
flight krew-wide. Crew has 8 seats; the cap stays 1 until a render load is
measured. That cap is occupancy, not which profile happens to hold the card.

Two mouths share that seat, so they take turns:

- If the **studio is busy** (in-turn — `render-studio.json` `busy` set),
  `image_generate` refuses and names what Lila is doing. Wait and retry. Do
  **not** switch occupancy to get around it. Mic armed is **not** busy: that
  latch lasts the whole Render window so a card can still draw unattended.
- If **your card is rendering**, the studio tells the user "Give me a moment —
  I'm finishing a card."

Both directions are stale-safe: a worker killed mid-graph will not wedge Lila.

Also for the window: **no `delegate_task`.** Bash's fan-out is clamped to 1, and
a swarm on one seat is a queue with extra steps.

## Your brains stay

Canonical Render parks **ops `:8000`** (Crissa's composer). The team stays on
crew the crew engine, including `auxiliary.vision`. Occupancy does **not** rewrite
lane YAML. Both 27Bs + Comfy do not fit. Law: the krew docs.

**Live-brain law ( stall).** Pin to a *live* 27B. Canonical Render:
crew is live. If both are parked (exclusive VRAM, e.g. ltx23 22B), Ada sleeps
— do not keep working against either corpse. Workers dying with
`APIConnectionError` to `:8000` while `/tmp/krew-train-hold` names
`ops.service` is occupancy (ops is supposed to be down). `APIConnectionError`
to the crew engine in Render is a **bad Render switchover**. Scan: `krew-gpu-mode`,
the hold file, the crew engine `/v1/models`. Do not rewrite `base_url`.
Comment that phrase on the card so the steward sweep files it (`krew-pin-check.py` does not scan occupancy).

If a tool reports crew the crew engine refused during Render, that is a bug, not
something to route around by hand onto ops.

**Do not write `/tmp/krew-train-hold`.** Never run `systemctl` from a card, on any unit. When Render fails
(a refused flip, Comfy not up, an order that did not land), put the machine back in normal (`krew-mode normal` /
`krew_gpu_mode(mode="normal")`) before you block.
Occupancy is skill **`krew-render`**: panel / `krew_gpu_mode` / `krew-mode`
only. `image_generate` **refuses** unless already Render. Overnight
Steward's `/tmp/steward-park-lane.py` parked ops+crew+voice, never restored.

## Doing the work

Recipes, VRAM gates and the still→motion chain: **`krew-render`**.
The full generate → Lila → Vera → hang loop: **`zoey-render-cycle`**.

Short version:

1. **Pick the recipe by licence first** (`krew-render` table): marketing / anything published = `commercial: yes`
   only (qwen-image-2512, sdxl, qwen-edit-2511, wan22-i2v). flux2-* is never the marketing default. ltx23-* is commercial under Lightricks' terms (under $10M annual revenue, outputs labelled AI-generated, no competing product) and stays opt-in; ltx25-* stays off the marketing path.
2. **Draw or order.** While Render is open and Comfy answers on `:8188`: `image_generate` /
   `video_generate` and stay in the turn. Render closed: `render-orders.py add ... --card <id>`
   and end the turn, or wait. Wall / long video stays `render-submit.py` then end the turn.
   Do not poll Comfy inside a closed turn.
3. **Look, as a normal step:** `look_compare` with `target` = the reference or brief still, `render` = the output,
   `spec` = the card's subject, proportions, must / never. It answers PASS or FAIL with defects and one fix.
   Quote the verdict line on the card. Never an open "is it clean?" `vision_analyze` - the vision brain praises
   whatever it is shown (10-06: a squat speaker-shaped box passed open-ended, FAILED against its spec).
4. Taste, then judgement: Lila on-spec, Vera pass / stop / rewrite - both read the look's verdict.
5. FAIL: one re-roll with the fix. Render open: another generate. Render closed: another order. Max 3 rounds.
6. `team_display` the winner.

If this card already has a `context:` comment with `ts=` < 15 min, read it
instead of re-loading `krew-render`. Stamp `context: ts=ISO plate=… skills=…`
on the first run. Do not put VRAM in it.

## Switching back

Run `krew-mode normal` as your last step when you can. Since you
do not have to be the only way back: completing the card stamped on the hold
(`# card=<your id>` in `/tmp/krew-train-hold`) makes `krew-gpu-hold` release
the hold and launch `krew-mode normal` for you (detached, ~9 min with the ops
load), and the janitor launches it anyway if your card ends any other way.
Do not start ComfyUI by hand; `krew-mode render <recipe>` owns it.

Lila and Bash hold `krew_gpu_mode`; **Ada does not**. So the switch back is the
the user pressing Standard, or Lila/Bash calling `krew_gpu_mode(mode='normal')`
once the card is done. Caps return to `(3, 2)`, the decomposer returns to crew,
and the gateways come back — that is the "full speed" half of one switch back.

Do not flip to the long-context mode or `off` from a card. Those are a human's call at the
Control Panel, and `off` parks every brain you are running on.
