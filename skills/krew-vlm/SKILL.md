---
name: krew-vlm
description: >
  Look at a still or clip with vision. Lila, Bash, and Vera only — the
  renderers and reviewers. A 200 is not success. /krew-vlm
version: 0.2.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [render, vision, lila, bash, vera]
    related_skills: [krew-render, pose-flourish, render-lane, mouth-place-and-shape]
---

# Krew VLM (look at the file)

**Who:** Lila (taste / on-spec), Bash (his own mint), Vera (pass / stop /
rewrite). Not Crissa, Ada, or Steward.

**Tools (Hermes native):**

| File | Tool | Toolset |
|------|------|---------|
| a render AGAINST its target | **`look_compare`** | `vision` (krew-vlm plugin) |
| still (png/jpg), on its own | `vision_analyze` | `vision` |
| clip (mp4/webm/mov), on its own | `video_analyze` | `video` |

## The look that decides: `look_compare` 

A render is judged **beside its target**, never on its own. `vision_analyze`
and `video_analyze` take ONE file: they describe it, and a description of a
handsome picture is not a verdict on whether it matches.

```
look_compare(target=<absolute path of the reference still>,
             render=<absolute path of the rendered still OR clip>,
             spec=<what it was asked to be, in your words>)
```

It answers `PASS` or `FAIL` with the visible differences, the defects (with the
second, for a clip) and **the one fix** for the next render. A clip gets two
looks: its first and last frame beside the target (does it start and end on
the hub), and the whole clip beside the target (does identity hold).

**The loop, every render:**

1. Render.
2. `look_compare` it against the target.
3. `FAIL` -> change what `fix` names, render again, look again. Do not describe
   the failure on the card and stop: the fix is the next render's prompt.
4. `PASS` -> hand off with the two paths and the verdict.

`UNREADABLE` and `NOT RUN` are **not** a pass. Look again; if it repeats, say
so on the card and ask Vera. Three renders that fail on the same difference is
a recipe problem, not a seed problem: say which difference, stop re-rolling.

**Vera:** your verdict on a render rests on `look_compare` of the FINAL file
against the target named on the card, run by you, not on the builder's report
of its own look.

Open the **path**. A Comfy 200 is not a look. Do **not** call `vision_analyze`
on an mp4 — it only loads images (`source is not a recognized image`). Do
**not** ffmpeg frames and do **not** spawn a subagent for the look. Auxiliary
`vision` and `video` are the same brain: **crew the crew engine**. Occupancy does not
move that pin. Crissa's look is ops `:8000` and is a different skill
(`crissa-look`). `auxiliary.video` is the native `video_analyze` slot.

`video_analyze(video_url=<absolute mp4>, question=<look>)` hits
`auxiliary.video` (`task=video`), not `auxiliary.vision`. Then
`kanban_complete` with the path + GREEN, or fail notes from what you saw.

| You | Generate? | Look? |
|-----|-----------|-------|
| Lila | yes (`image_generate` / `video_generate`) | yes — taste, on-spec |
| Bash | yes (implementer, idle studio) | yes — his output |
| Vera | **no** | yes — pass / stop / rewrite |

Recipes and Law 1: **`krew-render`** (studio) and **`pose-flourish`** (wall).
Mouth paste / wrap sheets: **`mouth-place-and-shape`**. This skill is only
the look.

## What to fail

- **Identity plate (Bash / any new face).** `vision_analyze` the PNG with
  this brief, temperature does not matter — the question does:

  > Bash is a man: short dark curly hair, black hoodie, no ponytail, no
  > long earrings, no white blouse. Lila is a woman: long blonde ponytail,
  > long earrings, white top. (1) who: Bash, Lila, chimera (his face her
  > hair/clothes), or canvas-echo (just Lila). (2) one visual fact. (3)
  > PASS only if hair and hoodie match take-8; else FAIL.

  A man with a long blonde ponytail is **FAIL**. Crew 27B already names
  this when asked (: take-8 PASS, `qwenedit_00115` chimera,
  `qwenedit_00119` canvas-echo). Do not complete a chimera. Do not load
  Muse or 32B VL for this look — occupancy is dual-27B; leftover Muse
  does not fit. Skill **render-avatars** §2 is the mint chain.

- **Down/work clip — fingers in front of the laptop lid.** Hands sit
  behind the screen (`MONITOR` / `clip-work`). Fail notes-style hands
  over the glow. Skill **pose-flourish** § Hands vs the lid. Sip / tuck
  / stretch may leave the keys; typing must not.
- Beige / noise still — starved Comfy; restore occupancy, do not ship
- Pose clip that does not close on the hub it started on (first-only WAN or
  LTX-2.5 I2V) — wrap or reject, not an inventory row
- Still + wavy screen — guide too tight (strength 1.0 / CRF 18)
- A long 2.5 movie that merely looks like her, treated as a fidget
- Mouth chewing on 2.5 FLF audio — the mouth owns speech; prompt needed **No dialogue**
- **Mouth sheet — dest lips still showing (row 3 doubles).** Soft `|t|`
  painted on top of the filmed mouth. Fail unless `hide_clip_jaw` /
  `inpaint_plane` ran before the paste. Not more blur. Spec:
  `mouth-place-and-shape`.
- **Mouth sheet — gray speck under dest nose on affine.** `dest_nose_keep`
  punch or TELEA in `INPAINT_GAP_S`. Affine does not need that cap.
- **Mouth sheet — hop, boxed harvested AABB, profile shear, Gaussian-smear
  incisors, or someone fitted `a1/a3/a5`.** Path call is in
  the krew docs. Do not pass those.
- **Mouth sheet — banana teeth, crawling arch, or unsealed MBP after
  warp.** Pixel sag *and* vertex bend on the same frame, or a different
  `p(x)` per class. Digest: `mouth-place-and-shape` / `DENTAL-ARCH.md`.
