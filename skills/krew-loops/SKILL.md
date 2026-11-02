---
name: krew-loops
description: "Deliver a SERVICEABLE hub loop or flourish from one anchor still: the written words per loop class, the one picture a buyer gives, the gates every frame (head band, floor of life, ends on the plate, still mouth, identity), automatic retakes inside the price, and the sidecars the painter reads. The Krew's job is a loop that ships, never a prompt."
version: 0.1.0
author: krew
license: MIT
platforms: [linux, darwin]
metadata:
  hermes:
    tags: [render, loop, flourish, anchor, wan, ltx, qc, bash, lila, vera]
    related_skills: [krew-render, render-avatars, render-lane, krew-vlm, wan-local]
---

# Krew loops: a serviceable loop from one anchor

The owner's goal (2026-10-20): **the Krew delivers serviceable loops**, on the owner's own pictures and on a buyer's,
with no human in the loop until the owner's eye. A serviceable loop is one that *the mouth painter can speak on all
day*: it starts and ends on the one anchor still, her head stays inside the band every frame, there is life in it,
her lips are closed and still, and she is still herself. Everything below is a gate with a number; a take that misses
one is retaken, automatically, inside the fixed price. **Never write loop words by hand**; never ask the buyer for a
prompt; never judge a retake by eye before the numbers.

## 1. The one input: the picture

The buyer (or the owner) gives ONE still per hub: the anchor. Before any render, QC the picture itself:
one face, frontal (|yaw| <= 8, |roll| <= 4 by the face engine), lips closed, the face 0.30-0.40 of the frame height,
eyes on the lens (up anchor) or on the phone / screen (down anchor), no hand near the mouth. A picture that fails is
sent back with the reason, never rendered. Cut it to the output's exact aspect first (`render-loop.py cut-anchor`,
or `crissa-render.py loop` does it): 704x1280 (portrait, LTX) / 720x1280 (hosted), 1280x704 (desk).

## 2. The words are written, per class

`modules/render/specs/<class>.json` (the render module ships them; the reference install keeps them in `config/render-loop-specs/`): the
exact words a provider is sent, slots filled from the order (`{subject}`, `{look}`, pronouns). Classes: `listen-loop`,
`work-loop`, `work-loop-up`, `work-loop-multitask`, `scroll-loop` (the phone's heads-down loop), and the flourishes
`fl-nod-settle`, `fl-hair-tuck`, `fl-laugh-held`, `fl-earring-touch`, `fl-smile-blink`, `fl-hair-clump-up/-down`
(talking-safe). Every spec's words carry: the locked camera, **the head band in words** ("within a few degrees ... no
turn and no tilt beyond a few degrees ... any glance with the eyes only") AND **the life** (breath, slow blinks, a slight
nod, a little to one side and back, a weight shift) so it does not freeze, lips closed and still, the last second still
in the pose, **"the first frame and the last frame are exactly this photo"**. The loader refuses words that freeze her
("head steady", "barely move") and words with no life.

```bash
CRISSA_RENDER_LOOPS=1 python3 modules/render/scripts/crissa-render.py loop --words --type listen-loop --frame portrait [--subject ... --look ... --pronoun she]
```

## 3. The render, by route

| route | call | first AND last frame | cost (measured 2026-10-20) |
|---|---|---|---|
| local LTX 2.5 flf2v (Comfy) | `crissa-render.py loop --type <class> --frame <desk|portrait> --anchor A` (one render order; `orders run` draws it) | yes, guide 1.0 | electricity (~0.004 kWh a take at 736x384; the full-size number is in the review doc) |
| Wan 3.0 hosted, first/last frame | `wan frame2video --site intl --duration 10 --resolution 720P --audio-output=false --first-frame A --last-frame A --prompt "<words>"` then `wan task wait <id> --save` | yes | 30 credits a 10 s clip on the web plan ($0.60; hand-made use only by its terms); the API route is Alibaba Model Studio at 0.10-0.14 $/s |
| Grok Imagine 1.5 | the house client the Imagine client (`image` + `last_image` = A, 10 s, 720p, 9:16) | yes | $1.42 a 10 s clip (0.142 $/s) |

Fresh draws each take (no fixed seed) unless a retake must hold a look. Hosted takes are 30 fps (Wan) / 24 (Grok); the
intake fits them. Never put a provider's words in the card: the spec is the words.

## 4. The gates, every frame, by machine (the reference install's `compliance.py`, the public face engine; the krew copy ships with the render module when it lands)

| gate | number | what fails it |
|---|---|---|
| head CEILING, every frame, vs the anchor's pose | interim: pitch dev <= 8, yaw <= 12, roll <= 10 (the owner 2026-10-20); the phone path holds yaw to 17; the record band 8/8/4 | one frame out = retake |
| head FLOOR (life) | pitch or yaw travels >= 2 deg with >= 2 distinct moves, >= 1 blink, motion >= 0.30 | "too still" = retake |
| the ends | first and last vs the aspect-true anchor >= 0.995; own seam >= 0.97; AND the cut against the hub loop's own first frame (the anchor PNG can sit ~5 px and 3-4 grey levels off the loop it joins: 2026-10-21) | not on the plate = retake |
| the mouth | lip aperture <= 0.03, step p95 <= 0.002 (still, painted over) | a moving mouth = retake |
| identity | median >= 0.60, min >= 0.50 vs the portrait (the avatar's own bar when measured) | drift = retake |
| camera | the top corners hold (NCC >= 0.97 or change <= 3 grey levels); face height drift <= 5 %, centre <= 5 % | a zoom or a pan = retake |
| the asked beats | the eye-glance (listen), the blinks, the flourish's own beat | a missed beat = retake |
| talking flourish | mouth covered at most one window of <= 1.0 s; the window goes in `<take>.occlusion.json`; cover means ANYTHING over the lips, a strand of hair included (the 2026-10-21 hair-clump read hands only and missed a strand at f75-105), so look at the frames of the window | longer = retake |

Measured error rates (five takes each, listen loop, the v3 words, 2026-10-20) are in the owner's review (RENDERS-LOOP-REVIEW-20261020)
section 3c; size the retake allowance from them, never from a guess.

## 5. Retakes and delivery

A failed gate retakes the SAME words and picture (a fresh draw), up to the spec's allowance (`retakes.limit`,
`order_spend_cap` in the pack rule); past it the clip is HELD with its numbers, never shipped and never judged by a
human first. A passing take gets the anchor held 10 frames in front and 20 at the end (the pack rule's tags), its
`.score.json` / `.compliance.json` / `.occlusion.json` sidecars, a contact sheet, and goes through the normal intake
(`pose-slide-intake.py` with `paint: true` for a loop; a flourish stamped as a flourish). The owner's eye closes it
(`owed-verdicts.py add`); the numbers are what you post on the card, three lines, never "looks good".

## 6. Never

- Never "head steady / no turn / barely move" in words: the 2026-10-15 set froze. Never a bare "looks up".
- Never a seed, size or word change mid-batch: a batch is a measurement.
- Never enter Render by hand in the owner's day; never a hosted call without the spend in the card and the owner's go.
- Never pass a take on a still contact sheet: motion, the band and the mouth are read from the moving film.
