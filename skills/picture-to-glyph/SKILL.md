---
name: picture-to-glyph
description: "Any picture → wall ASCII. team_wall_photo for a face (crops to it, so it is recognised); img2glyph.py for a piece in the crew's glyph style. Measured ramp table."
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [ASCII, glyph, photo, face, wall, CPU, Crissa, Lila]
    related_skills: [crissa-surface-art, glyph-density, text-to-ascii, crissa-her-wall]
---

# Picture → glyph

Two renderers live here. They are not rivals — they answer different questions.

| | **A face** | **A picture as a piece** |
|---|---|---|
| Question | "will my family recognise it?" | "does it look like the crew's style?" |
| Tool | **`team_wall_photo`** | `scripts/img2glyph.py` (CLI) |
| Script | the team-crissa plugin's `photo-ascii.py` | this skill's `scripts/` |
| Does | finds faces, crops to them, dithers | tone + sub-cell ramps, the crew's `glyph` style |

Both are CPU only, so both work in **every occupancy** — Render, Standard, even
All-OS-Off, when krew-comfy will not run at all.

## A face → `team_wall_photo`

The bar is the owner's, from 2026-08-13: **"I want my family to recognize it."** Not
"looks arty" — *recognisable*. A naive brightness ramp fails that every time.

```
team_wall_photo photo="latest" label="fort lauderdale"
```

`photo=` a path, a bare filename, or `latest` / omitted for the newest picture
in `~/Downloads` or `~/Pictures`. It posts to the room screen and reads
`/v1/screen` back, so what it tells you is what is on the glass.

## A picture as a piece → `img2glyph.py`

Use it when the subject is not a face — a door, a landscape, a still — and you
want the Glyph Density look rather than maximum likeness. It owns the
`glyph` ramp (`" ··+*#@"`, white figure on a dark wash) that `photo-ascii.py`
does not have, plus `--ground` for a border and `--gamma`/`--clip` for tone.

```bash
S=$HERMES_HOME/skills/picture-to-glyph/scripts/img2glyph.py

python3 $S photo.jpg --ramp glyph --cols 60 --ground 2      # look at it first
python3 $S photo.jpg --ramp glyph --cols 60 --ground 2 \
        --post --label "night watch" --hold 90              # then put it up
```

Render one picture of your own at the four ramps to see them side by side (the reference install's examples were its own door camera and do not ship).

## Why it is not just a brightness ramp

Four things decide whether a face survives the trip down to ~60 columns, and
the script already handles all four. Know them so you can tell when one failed:

1. **Crop to the face.** A wide beach selfie at 60 columns spends most of its
   glyphs on sand. Faces are found and cropped to, so the whole budget goes on
   the part that carries identity. `face="off"` renders the whole frame — use
   it for landscapes and buildings, never for people.
2. **Local contrast.** A backlit shot puts the face in the bottom third of the
   histogram. CLAHE on the L channel pulls features apart without blowing out
   the sky.
3. **Cell aspect.** A terminal cell is about twice as tall as it is wide, so
   `rows = cols × cell × H/W`. Skip it and everyone comes out squashed — the
   single most common reason ASCII portraits look like nobody.
4. **Dithering.** Quadrant and braille glyphs are on/off per subcell.
   Thresholding gives a harsh stencil; Floyd–Steinberg gives tone, and **tone
   is what the eye reads as a face.**

## Picking a ramp

Ramp names match the wall's `ART_RAMPS` exactly, so whatever you post renders
in the right font. Ink and resolution were **measured on this box** (2026-08-13),
not guessed — the surprises below are why you read the table instead of trusting
intuition.

| Ramp | Ink | Res | Use for |
|---|---|---|---|
| `quadrant` | 0.00–1.00 | 2×2 | **Best all-round.** Detail *and* true black. Default for faces |
| `eighth` | 0.00–1.00 | 1×1 | Best tonal ladder — 9 linear steps. Strong for graphics, **poor on faces** |
| `shade` | 0.00–1.00 | 1×1 | Flat tone, 4 steps. Softer when quadrant reads harsh |
| `glyph` | 0.00–0.27 | 1×1 | The crew's style: white figure on a dark wash. `img2glyph` only |
| `ascii` | 0.00–0.27 | 1×1 | Plain letters. Renders in any font; safest, lowest detail |
| `braille` | 0.06–0.25 | 2×4 | Most detail available, but **cannot go dark**, is poor on faces, and needs Unifont on the glass or it renders as tofu |
| `cjk` | 0.00–0.30 | 1×1 | Full-width texture. **NOT denser than ascii**, despite the intuition |

Default `cols` is 72 for `team_wall_photo`, 90 for `img2glyph.py`. Wider is not
automatically better — past the subject's own pixel budget you are dithering
noise.

## Look at it

`photo-ascii.py --compare` prints the ramps side by side before you commit.
The reference install kept a stock reference beside it; it does not ship (a watermarked stock image). Use your own picture.

Then **look at the result**. The tool reports what is actually on the glass; if
it says the piece did not land, it did not land — say so rather than claiming
the photo is up.

## Which renderer

- A **photo** → this skill, `team_wall_photo`.
- A **shape or mood** (spiral, wave, figure) → `glyph-density`, `team_glyph_art`.
- A **single word** big on screen → `crissa-surface-art`, `team_banner`.
- A **generated picture** → that is Comfy and needs Render occupancy: `krew-render`.

## History

`img2glyph.py` lived only inside a live Crissa profile until 2026-08-28, under the
name `glyph-density` — which collided with the *procedural* skill of that name.
It is in the pack now. The procedural generator (`glyph_density.py`, shapes and
figures, no input image) is a different thing and keeps the `glyph-density` name.
