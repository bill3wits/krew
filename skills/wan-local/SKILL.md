---
name: wan-local
description: >
  Local Wan-style stills and clip-from-still on krew-comfy, not DashScope.
  Use when Lila or Bash would have used wan2.7-image-skill (text-to-image,
  edit a still, sequential stills, then WAN i2v from an approved PNG).
  Render occupancy. Absolute host paths. Look after every file.
version: 0.1.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [render, wan, comfy, lila, bash]
    related_skills: [krew-render, render-avatars, krew-vlm, pose-flourish]
---

# Wan local (krew-comfy, not API)

Upstream [Wan-skills/wan2.7-image-skill](https://github.com/Wan-Video/Wan-skills)
is DashScope (`DASHSCOPE_API_KEY`, OSS). This install does **not** call that.
Same three modes, local recipes. Occupancy: skill **`krew-render`**. Look:
**`krew-vlm`**. Face plates: **`render-avatars`** §2 (four calls; no one-shot
two-pic).

Tools: `image_generate` / `video_generate` provider `krew-comfy`. Paths are
absolute files under `~/Desktop/` or `display/` — never `oss://`.

| Wan-skills | Here |
|------------|------|
| Mode 1 文生图 | `image_generate` `model=qwen-image-2512` (or `sdxl`) |
| Mode 2 图生图 / 编辑 | `image_generate` `model=qwen-edit-2511` `image_url`=canvas, optional `reference_image_urls` |
| Mode 3 组图 | **n separate** Mode 1 or 2 calls. Look after each. No `enable_sequential` |
| then video | `video_generate` `model=wan22-i2v` `image_url`=**approved** still. First-only — not a wall pose |

Krew plate size is **1296×704** (`aspect_ratio=landscape`). Do not emit 1K
square unless the user asked.

## Workflow

1. Occupancy already `render`. If the tool refuses, **stop** — do not start Comfy.
2. Pick the mode. Local files stay files (`file_to_oss.py` is not used).
3. Generate. Open the PNG (`krew-vlm`). A 200 is not a look.
4. Optional: WAN i2v from that PNG only if the still **passed**.
5. Wall loops/slides: **`pose-flourish`** (`ltx25-flf2v`), not WAN.

## Mode 2 (edit) — the one that shipped chimeras

`qwen-edit-2511` keeps **picture 1** framing, clothes, hair. Prompt "replace
the woman with the man from picture 2" in **one** call is FAIL (her ponytail
on his face). For a new face on a mate's plate, run **`render-avatars` §2**
(four calls, paint the white torso, look after each). Identity FAIL =
blonde ponytail / her earrings / white blouse on a man.

June: real still → Qwen-edit **in place** → WAN from the approved still.

## CLI (optional)

```bash
python3 ~/.krew/packs/skills/wan-local/scripts/local_image.py \
  --mode 1 --prompt "warm desk, lid along the bottom, 1296x704"
python3 ~/.krew/packs/skills/wan-local/scripts/local_image.py \
  --mode 2 --prompt "replace only the head" \
  --canvas ~/Desktop/LilaPoses/lila-hub-comms-src.jpg \
  --ref ~/Desktop/BashPoses/renders-bust/take-8.png
```

Prints the Hermes tool args. Does not mint. Mint is `image_generate`.
