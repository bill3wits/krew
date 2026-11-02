---
name: crissa-look
description: >
  Look at the room and at clips. team_look aims the camera. Stills use
  vision_analyze. Mp4s use Hermes native video_analyze (auxiliary.video).
version: 0.1.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, look, camera, video_analyze, team_look]
    related_skills: [crissa-auth, krew-vlm]
---

# Crissa look

The camera is for the **room**, not only the door. Rest is still door so
walk-ins are not missed. When you need to *see*, use the tools — do not
guess from presence JSON alone.

## Camera — `team_look`

| Need | Call |
|------|------|
| Who is on a beam / speaker | `team_look` `what=hear` |
| Body count vs SoT | `team_look` `what=room` |

Inspector aims, grabs, returns door. Do **not** call grabber
`/v1/look_around` or `:8192` yourself.

## See the file — Hermes native

| File | Tool | Aux slot | Brain |
|------|------|----------|-------|
| still (jpg/png) | `vision_analyze` | `auxiliary.vision` | ops `:8000` |
| clip (mp4/webm/mov) | `video_analyze` | `auxiliary.video` | ops `:8000` |

`video_analyze(video_url=<absolute path>, question=<what to notice>)`.
Do **not** point `vision_analyze` at an mp4. Do **not** ffmpeg frames.

Your mouth and your look stay **ops `:8000`**. Kanban look is crew the crew engine
(Lila/Vera/Bash, skill `krew-vlm`). Occupancy parks engines; it does not
move either pin.

## After a look

Write what you learned (`memory`). Glance is not AUTH-off. Skill
**crissa-auth**.
