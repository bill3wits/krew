---
name: crissa-surface-art
description: Publish roll ASCII and coffee on VoiceOnly $CRISSA_PLAYER/v1/screen.
version: 1.4.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, voiceonly, VO, text-art, mono, team_display, fonts]
    related_skills: [ascii-art, crissa-her-wall]
---

**Fonts & categories:** see **`FONTS.md`** + **`categories.json`**.  
Lila owns the catalog; you pick a category and publish to the wall.

# Crissa surface art (product = VoiceOnly $CRISSA_PLAYER/v1/screen)

**Target UI:** VoiceOnly glass **$CRISSA_PLAYER/v1/screen**.  
Do **not** write **:8088**.

Pages and notes live in **`crissa-her-wall`**. This skill is **art on the roll + coffee**.

## Layout law (do not freestyle)

| Zone | What | Who updates |
|------|------|-------------|
| **the roll** | Speech + her ASCII riding with it | Crissa `resting` / `art=`. One landing. |
| **headline / note / notes** | Short takeaway or long scroll | `crissa-her-wall` (`chalk`) — not art. |
| **Claimed page** | Whole glass | `team_display` `view name=` — **yields theater** |
| **Coffee** | Cup only | `moment=coffee` |
| **right (dial / seats)** | Room clock + seats | System — never cover. |

### Theater vs Display

- Conversation does **not** stop theater. The **current piece yields on speech**.
- Only **Claimed Display** (`view name=` / a page he asked to watch) pauses theater.
- `{"action":"home"}` → delight resumes.

### Glyph language (the roll)

Crissa publishes original mono on the **roll**. Lila uses the same `resting`
call. There is no right paper plate and no face-shell tumble.

```json
{"action":"resting","moment":"scene","art":"<ascii>","kicker":"lila · dusk","tone":"soft"}
```

```json
{"action":"resting","moment":"scene","image":"http://…/render.png","kicker":"lila · fun"}
```

Publish landscape `art` the same way (`resting` + `art=`). It rides the roll.

## Craft: pull Hermes `ascii-art`, then crop for her box

Crissa already has **`creative/ascii-art`** (pyfiglet · boxes · cowsay · image→ascii · unicode palette).  
Use it to **make** art; this skill is **where it goes** and **how big**.

### Her-box constraints (hard)

| Limit | Value | Why |
|-------|--------|-----|
| Max width | **≤ 36 columns** (prefer 28–34) | Left ~⅓ VO column; surface fits densest font that still fits |
| Max height | **≤ 14 lines** (prefer 8–12) | Rides the roll; taller pieces shove speech off the glass |
| Font | Monospace only | `IBM Plex Mono` on surface |
| Color | Via `tone=` only | `gold` · `soft` · `dawn` · `night` — no ANSI |
| Frames | Light box-draw OK | `╭╮╰╯─│` preferred over heavy `╔╗` spam |
| No | Logs, JSON, tool dumps, full pyfiglet banners that wrap | Ban |

### Original work pattern (team —

**Do not ship stock / Shutterstock / watermarked rips.** Density languages are **references**; pieces must be **krew-original**.

| Density language | Feel | Krew motifs to invent |
|------------------|------|-------------------------|
| **Robot / glyph face** | Dense `#` companion | Crissa face, antenna, ear, dial eyes |
| **Landscape `$SXx`** | Hills, trees, stream | Door yard, desk window, night trees |
| **Portrait glyphs** | Heavy fill `S$@%#` face | Soft companion mark, not a celebrity |
| **Open mono** | Stars, dusk, thin lines | Night watch, steam, quiet room |

**Recipe for original pieces**

1. Pick **motif** (door · coffee · dial · stars · companion · hills).  
2. Pick **density language** from the table (match the user’s taste, don’t clone a file from the web).  
3. Stay inside her-box limits (≤36×14).  
4. Title with `kicker` (e.g. `night · crissa`, `day · hills`).  
5. Publish via `team_display`; if it looks generic stock, redo.

Lila/Crissa: vary silhouettes and fill patterns; keep a **krew lookbook** under `examples/` (robot, hills, companion-glyph) as style seeds only.

### Recommended craft path

1. **Mood / open mono** — Unicode palette or light `╭╮` (no heavy picture frames).  
2. **Short word** (dawn / hush) — `python3 -m pyfiglet -f small -w 34 "hush"`. Prefer **`small` / `mini` / `slant`**.  
3. **Dense companion / landscape** — LLM or terminal tools in the **density language** above; **original**, krew motifs.  
4. **ascii-art skill** — for tools (pyfiglet, boxes); not a license to paste external art.

### Unicode palette (LLM fallback)

```
Box:  ╭ ╮ ╰ ╯ │ ─ ├ ┤ ┬ ┴ ┼ · ═ ─
Fill: · ‧ ∘ ○ ● ◦ ░ ▒ █ ▄ ▀
Star: ✦ ✧ ⋆ ★ ☆ ·
```

### Good example (her box)

```
  ╭─ evening ────────────╮
  │  steam · quiet desk  │
  │     ·  ✦  ·          │
  ╰──────────────────────╯
```

### Bad examples

- 80-col `doom` banner  
- Full `wttr.in` dump  
- Omni log paste  
- Coffee cup **plus** an essay in the art slot  

## How to publish (`team_display`)

```json
{"action":"resting","art":"<multiline mono>","kicker":"evening","tone":"gold","line":"optional soft caption"}
```

```json
{"action":"resting","moment":"coffee"}
```

```json
{"action":"view","name":"kanban"}
```

```json
{"action":"view","url":"https://www.youtube.com/watch?v=VIDEO_ID"}
```

`url=` only for an address someone actually handed you. A claimed page takes the whole glass.

```json
{"action":"clear","target":"resting"}
```

```json
{"action":"home"}
```

Identical `art` string → surface **holds** (no scroll thrash). Change only when the room mood actually shifts (hour band, celebration, goodbye).

## Cadence

| When | What |
|------|------|
| Hourly auto | Surface may set gallery art — **do not fight** with same piece |
| You set art | Wins until you clear or set a **different** piece (`_crissa`) |
| Every TTS line | **Never** post new art |
| Speak confirm | One short line: “On the wall.” |

## Related

- Hermes skill: `creative/ascii-art` (tools + decision flow)  
- Pages / notes / claimed display: skill `crissa-her-wall`  
- Surface: VoiceOnly **$CRISSA_PLAYER/v1/screen** (`display/voiceonly-home.html`)  
- Do not write :8088

## Lookbook (`examples/`)

The owner's style refs (8–12) stay with the install that collected them (not shipped). **Style only**; generate originals.

| Ref | Language | Krew seed | Zone |
|-----|----------|------------|------|
| 8 | Crane / bird silhouette | `krew-crane.txt` | roll |
| 9 | Kana AA / 2ch face | `krew-aa-companion.txt` | **roll** |
| 10 | Dense `$` portrait | invent; see companion-glyph | **roll** |
| 11 | Hex-noise ERROR | `krew-error-hex.txt` | conflict / surprise |
| 12 | White-on-black sparse figure | `krew-night-figure.txt` | preferred dark VO |
| — | Robot `#` | `crissa-robot.txt` | **roll** |
| — | Hills `$SXx` | `landscape-hills.txt` | **roll** |

See `examples/LOOKBOOK.md`. Preferred stage: **black + white glyphs** (ref 12).
