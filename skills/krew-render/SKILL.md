---
name: krew-render
description: "Local Comfy stills and short video in Render occupancy. When to use each studio renderer. Talking clips — do not bake a dental arch; paste is mouth-place-and-shape."
version: 0.3.3
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [render, comfy, lila, bash, vera]
    related_skills: [pose-flourish, krew-vlm, render-lane, mouth-place-and-shape]
---

# Krew render (Render occupancy)

Use when the user wants a still or a short clip.

**While Render is open, draw in the turn** (2026-10-08). When `~/.hermes/krew-gpu-mode` is `render` and
Comfy answers on `:8188`, Lila and Bash draw directly with
`image_generate` / `video_generate` provider **krew-comfy** (`model` = the
recipe id) and iterate live: look, then another generate. Vera reviews that
live output in the same turn. An **order** is only
when Render is not open, or when the owner sends the job to the heavy window.
Then `render_order` (Lila, Bash: `recipe` + `prompt` [+ `negative`, `image` for
i2v/edit, `width`/`height`, `seed`]) or the same thing from a shell:
`~/.krew/crew/scripts/render-orders.py add --workflow <workflow> --set <prompt node>.text="<prompt>" [--set <image node>.image=<file in ~/ComfyUI/input>] --seed <n> --by <you> --card <card id>`
and end the turn. The render window holds a closed card. **The render runner**
(`krew-render-runner.py`) works the heavy-window queue (crew off, ops and Crissa up).
Ordinary cards do not use it. `render-orders.py show <id>` names `out_dir` when an
order is `done`. Then **look** (below). A re-roll while Render is open is another
generate. A re-roll while it is closed is another order.

Default still: **`qwen-image-2512`**. Edit: **`qwen-edit-2511`**. Never
FLUX.2 or Qwen-Image 2.1 for anything that leaves the machine.

Those tools **refuse unless occupancy is already Render** (panel **Render**,
`krew_gpu_mode(mode="render")`, or `krew-mode render <recipe>`). They do
not park ops themselves. Leave with `krew_gpu_mode(mode="normal")` / panel
**Standard** / `krew-mode normal` when the card is done. Do not start
Comfy next to ops by hand. Do not `nvidia-smi` a 24 GiB gate. Do not
`systemctl stop` ops.

## Two mouths, one seat

Control Panel HTTP studio (the panel's `/panel` page) is Lila’s mouth at the glass. Since she is **also** a kanban worker in Render — the lane no longer
sleeps. Both ride **crew** 27B, one card (`1/1`), so they take turns: the tool
refuses while studio `busy` is set, and the studio says "give me a moment"
while a card is drawing. Wait; do not switch occupancy to get around it.
Card-side rules live in skill **`render-lane`**.

**Renderers and reviewers are Lila, Bash, and Vera only.** Crissa, Ada, and
Steward do not mint or judge pixels.

Bash: implementer, not the room voice. Same generate tools when occupancy is
Render and the studio is idle. He does not `delegate_task` in Render. He
**looks** at his own output (`vision` + skill **`krew-vlm`**).

Vera looks only — no generate tools. Skill **`krew-vlm`**. Her `vision` aux
stays on crew the crew engine. Canonical Render parks ops, not crew.

## Occupancy protocol (one switcher)

**Enter and leave with the switcher. Never by hand.**
Sleep/wake of ops is **inside Render mode**. The switch is the panel,
`krew_gpu_mode`, or `krew-mode`. `render-submit.py` calls `krew-mode`
then Comfy. `image_generate` / `video_generate` **do not** enter — they
refuse if the flag is not `render`. Workers do not calculate whether
parking ops would free 24 GiB. If the switcher refuses, **stop**.
Standard free-VRAM is not a render gate.

| Who | How |
|-----|-----|
| Lila / Bash | `krew_gpu_mode(mode="render")` then later `mode="normal"` |
| Panel | **Render** / **Standard** |
| Grok / Steward CLI | `python3 ~/.krew/crew/scripts/krew-mode render turbo` then `krew-mode normal` |

Ada does **not** have `krew_gpu_mode`. Steward on a mint card uses the CLI, not a new `/tmp/*.py`.

Canonical Render (law: the krew docs):

- systemd-stops **ops** `:8000` (Crissa's composer). Never `--enable-sleep-mode` on ops.
- Starts Comfy **without** `--highvram`.
- **Crew stays.** Ada/Lila/Vera/Bash, decomposer, aux vision, goal judge.
- **The voice server stays if Voice ON** (Lila speaks). Audio8 ear always. Crissa limps.
- **Crissa Brave kiosk (`krew-crissa-display`) DOWN.** Tk wallpaper is the glass.
  A stopped or failed gateway/kiosk in Render is **law**, not a crash.
  Do **not** file `krew-crissa-display` crash-loop or "Crissa is down" cards.
  Do **not** `systemctl start krew-crissa-display` or her gateway. Control Panel
  **crissa kiosk** readout: `parked in ZOS Desktop ON` (same style as Ops
  parked in render).
- No YAML pin swap. Ada dispatch `(1, 1)`. Crissa and Steward sleep.
- Leave: SIGKILL Comfy, start ops (cold boot ~2 min).

`krew-mode` writes `/tmp/krew-train-hold` itself (`# card=<id>` when `HERMES_KANBAN_TASK` is set) and **releases it on the way out**. Plugin `krew-gpu-hold` only cleans a leftover if you `kanban_complete` without leaving.

**Forbidden.** These are how Standard died overnight:

- Do **not** write `/tmp/krew-train-hold` yourself.
- Do **not** `systemctl stop` ops, crew, or the voice server.
- Do **not** park the voice server while Voice is ON. Ops parks in Render on purpose.
- Do **not** start Comfy next to both 27Bs, and do **not** pass `--highvram`.
- Do **not** invent `/tmp/steward-park-*.py`. The switcher is the park.

**When Render fails, put the machine back in normal** (the owner 2026-10-08): a refused flip, a Comfy that did not
come up, an order that did not land, a look that failed three times: `krew_gpu_mode(mode="normal")` (or
`krew-mode normal`) before you block, then block with what you measured. Never leave the flag on `render` with
nothing rendering: the engines stay parked and the front door stays down. **Never run `systemctl` from a card**
(start, stop, restart, kill, on any unit): the switcher parks and wakes the engines, the restart window restarts
units, and a card that reaches for systemctl is a card that gets blocked.

If `krew_gpu_mode` / `krew-mode render` **refuses** (Voice ON and still short of the recipe after ops parks): **stop**. Report measured VRAM. Do not widen the park. Exclusive dual-park (ops **and** crew down, Ada 0/0) is occupancy's call when the recipe and Voice OFF allow it — not a worker script.

**Before `kanban_complete`:**

```bash
cat ~/.hermes/krew-gpu-mode
cat /tmp/krew-train-hold 2>/dev/null || echo "clear"
```

Flag must be **normal** and the hold **clear** (or a `# card=` that is not yours and not done). A hold stamped with a **done** card, or units listed while the flag says `normal`, is a crashed switch — say so loudly. Do not `systemctl start` crew while the hold still names `crew.service`: `start-crew.sh` will exit 0 and the unit stays dead.

**Before you mint:** occupancy is already `render` (the switcher refuses otherwise). Do **not** `nvidia-smi`, curl Comfy, or read `render-submit.py` as a pre-flight — the tools refuse with the reason. If `~/.hermes/krew-render-hold` exists, stop. Kanban workers outside the user's off-hours (`site.json training_window`) are refused; `render-now` in a Crissa/the user comment exempts. Control Panel / a the user CLI is not gated.

**Re-dispatch:** if this card already has a `context:` comment with `ts=` younger than 15 minutes, read that instead of re-loading this skill. Stamp `context: ts=ISO plate=… skills=krew-render` on the first run. Do not put VRAM in it.

## Look at the file (a normal step, every render)

A 200 from Comfy, or an order marked `done`, is not success. **`look_compare`** (skill **`krew-vlm`**):
`target` = the reference or the brief's still, `render` = the output PNG/MP4, `spec` = the card's subject,
proportions and must / never (e.g. "NR200P case, 292 tall x 378 long = 0.77, wedge feet, reads as a PC case, no
logos"). It answers PASS or FAIL with defects and one fix. Quote the verdict line on the card. FAIL = one re-roll
with the fix: another generate while Render is open, another order while it is closed. Never an open "is it clean?" `vision_analyze`: the vision brain praises whatever it sees
(10-06: the same squat render read "clean, ready for marketing" open-ended and FAIL - "reads as a speaker" -
against its spec).

## When to use each renderer

SoT for workflows / VRAM / wired: `plugins/krew-comfy/recipes.json`
(the copy next to the installed plugin). `wired` is “workflow file exists under
`~/ComfyUI/workflows/`” — do not trust a remembered boolean.

**Licence first.** Marketing or anything published uses `commercial: yes` only. The default still is
`qwen-image-2512`; an edit is `qwen-edit-2511`. Never FLUX.2 or Qwen-Image 2.1 for anything that
leaves the machine. flux2-* and ltx25-* are opt-in draft engines (`opt_in: true` in recipes.json)
that stay on this machine.

| Id | Licence | Commercial | When |
|----|---------|------------|------|
| `qwen-image-2512` | Apache 2.0 | **yes** | **Marketing / product still** (no Lightning LoRA - not a speed pick) |
| `qwen-edit-2511` | Apache 2.0 | **yes** | **Edit a still** - `image_url` / image node attached |
| `sdxl` | open | **yes** | Publishable still, lower quality |
| `wan22-i2v` | open | **yes** | **Clip-from-still** (~2 s). Needs the still. Studio chain step 2. **First-only** - not a wall pose |
| `ltx23-nvfp4` | Lightricks LTX community licence: commercial use allowed under $10M annual revenue, outputs labelled AI-generated, no competing product | yes, under $10M annual revenue; outputs labelled AI-generated; no competing product (opt-in) | Text-to-video (~2 s). No still. **Forbidden for poses** - redraws the room |
| `ltx23-flf2v` | Lightricks LTX community licence: commercial use allowed under $10M annual revenue, outputs labelled AI-generated, no competing product | yes, under $10M annual revenue; outputs labelled AI-generated; no competing product (opt-in) | **Retired** for new wall mints. Leave existing library clips |
| `flux2-turbo` | BFL non-commercial | no (opt-in) | Draft still (fast photoreal). Never published; needs your own workflow file |
| `flux2-klein` | BFL non-commercial | no (opt-in) | Draft - cheap try of a composition |
| `flux2-max` | BFL non-commercial | no (opt-in) | Quality draft still; may refuse if VRAM short |
| `flux2-dev` / `flux1-dev` | BFL non-commercial | no (opt-in) | Older Flux; skip |
| `ltx25-flf2v` | LTX-2 Community | no (legal track) | Short scoot / slide. First **and** last = hub PNGs. Not studio |
| `ltx25-i2v` | LTX-2 Community | no (legal track) | CP chip LTX 2.5 - ~15 s from the current still. Kanban: `render-submit`, wrap the tail |

Not krew product: xAI Imagine, WAN t2v (no weights), generic `creative/comfyui`.

## Wall vs movie

Law 1 still wants tails on the hub. **15 s life loops** mint on
`ltx25-i2v` (first-only), then wrap. **Short scoots** stay `ltx25-flf2v`.
Prompt **No dialogue**; the mouth owns speech.

| Path | Pins | Use |
|------|------|-----|
| `ltx25-i2v` | hub PNG as frame 0 | **15 s life loops** (eyes, mouse). Wrap the tail |
| `ltx25-flf2v` | first and last hub PNGs | Short scoot / slide. ≲5 s |
| `ltx23-flf2v` | first and last | Retired for new mints |
| `ltx23-nvfp4` | neither | Forbidden for poses |

Do **not** treat a 2.5 movie-length I2V as a fidget because it looks like her.

## Still then motion

Two **different** tool calls, in order — not one call. This is the **studio**
chain (a still, then a short clip). It is **not** the pose kit.

1. A still per the brief. Render open: `image_generate`. Render closed: an order, or wait. Default `qwen-image-2512`; an edit is `qwen-edit-2511`. `flux2-turbo` only for a draft that stays on this machine. Never FLUX.2 or Qwen-Image 2.1 for anything that leaves the machine. `look_compare` it against the spec.
2. Studio motion, look in the turn: `video_generate` model `wan22-i2v` (~2 s) or `ltx25-i2v` (Control Panel chip **LTX 2.5**, ~15 s) with `image_url` = that still (an order: the still copied into `~/ComfyUI/input`, image node set). `look_compare` the MP4. A kanban worker must not `video_generate` `ltx25-i2v` — that is submit/collect.
3. **Wall FLF / a pose scoot** is **not** `video_generate`. One call then end the turn:

```
python3 ~/.krew/crew/scripts/render-submit.py --card <id> --name <name> \
  --recipe ltx25-flf2v --seed <hub.png> --plate <plate.png> --prompt-file <txt>
```

The script blocks the card. `render-collect` (2 min timer) lands the mp4, scores the ends, unblocks for the judging turn. Do **not** poll `:8188/history`.

WAN without a still fails. The bread table on the wall is wallpaper, not a still to animate unless the user said so.

`video_generate` model `ltx23-nvfp4` is text-to-video and is **forbidden for poses**. Wall pose clips → skill **`pose-flourish`**, mint via **`render-submit.py --recipe ltx25-flf2v`** (first+last hub PNG; wrap a miss).

## Viseme / jaw arch (not a Comfy knob)

Comfy mints the clip. It does not paste visemes and it does not retune
`ARCH6`. Filmed talking already has her occlusal curve. Do **not** prompt
a banana smile, extra dental arch, or a bent mouth card into a still or
FLF take. Harvest does not bake `p(x)` into tiles.

After mint, tiles **place** with affine and **shape** with at most one
wrap. Motion picks the wrap — inherit this on every talking / mouth-visible
card:

| Dest motion | After mint |
|-------------|------------|
| Nod, talk, laptop tilt | Affine paste, dental r0 hole. Rest pins through `frame_matrix`. |
| Front viseme tile, smile / tooth line | Pixel sag inside the rectangle (`warp_dental_maps`). Same `p` every class. |
| 3D mouth card / top-down arch | Vertex bend (`warp_dental_plane_maps`). Not live. Not also pixel sag. |
| `\|yaw\| ≥ ~35°` | Affine + triad. Not wrap. |
| Rest / MBP | Dest-pixel OK; warp must still seal. |
| Wide AH | Tile + affine. Geometry does not invent a cavity. |

Full inherit: skill **`mouth-place-and-shape`**. Digest:
`hermes-packs/skills/mouth-place-and-shape/DENTAL-ARCH.md`.
A card that is only warp / paste / diamond is **not** a Comfy mint —
Ada routes it to that skill, occupancy stays off Render unless the card
is also a pose mint.
