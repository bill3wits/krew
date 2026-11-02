# Krew glyph fonts & density categories

**Who does what**

| Role | Owns | Hands off |
|------|------|-----------|
| **Lila** | Categories, lookbook, original seeds, font shortlists | Catalog JSON + `examples/` pieces |
| **Crissa** | Live wall expression via `team_display` | Uses catalog + `crissa-surface-art` law |
| **Grok** | VoiceOnly $CRISSA_PLAYER/v1/screen surface | Surface only |

**Tools already available to both (Hermes venv)**

- `python3 -m pyfiglet` — **571** fonts; curated herbox-safe list below  
- `creative/ascii-art` skill — banners, boxes, unicode palette  
- System mono: **IBM Plex Mono** (UI) + **Noto Sans Mono CJK** (AA / 2ch)

```bash
# render a short word for her box (≤36 cols)
~/.hermes/hermes-agent/venv/bin/python -m pyfiglet -f small -w 34 "hush"
# or krew helper
python3 ~/.hermes/skills/crissa-surface-art/scripts/render_banner.py hush --font small
```

---

## A. Figlet categories (Lila curates; Crissa picks by mood)

All listed fonts fit **≤36×12** for the word `crissa` at width 36. Prefer short words (3–6 letters).

| Category | Mood | Fonts (herbox-safe) |
|----------|------|---------------------|
| **quiet** | Soft, small | `mini` `small` `short` `straight` `threepoint` `pepper` `digital` |
| **slant** | Modern, fast | `slant` `smslant` `lean` `speed` |
| **script** | Soft handwritten | `script` `smscript` `stampatello` `rounded` |
| **block** | Bold presence | `block` `doom` `big` `standard` `ogre` `puffy` |
| **cyber** | Tech / night | `cybermedium` `cyberlarge` `sblood` `rev` |
| **play** | Whimsy | `bubble` `whimsy` `weird` `pawp` `rectangles` |
| **display** | Title (use sparingly) | `larry3d` `starwars` `univers` `roman` `nancyj` |

**Avoid on left:** `doh`, tall isometric, anything that wraps past 36 cols.

**Roll:** keep ≤36 cols. Taller pieces still trim; they ride the roll, not a right panel.

---

## B. Density languages (glyph “fonts” — not TTF)

These are **character palettes** for hand/LLM art. Lila invents pieces; Crissa places them.

| ID | Language | Zone | the user ref |
|----|----------|------|----------|
| `robot` | Dense `#` robot / machine | left | robot face |
| `aa` | Kana AA / 2ch face (CJK OK) | left | ref 9 |
| `portrait` | Heavy `$S@%#` fill | left | ref 10 |
| `crane` | Bird silhouette density | roll | ref 8 |
| `hills` | `$SXx` landscape | **roll** | landscape |
| `night-figure` | Sparse white-on-black | roll | ref 12 |
| `error-hex` | Hex noise / conflict | error states | ref 11 |
| `open` | Stars, dusk, thin mono | left | night watch |
| `roster` / **grandpa art** | Low-res simple glyphs | roll | your people · pets · coffee · dial |

**Grandpa art**: chunky low-res faces/pets/icons — mix **hard** with denser pieces on the roll.  
Seeds: your own `examples/roster/` (the reference install's roster of its people stays with it) + gallery kickers `grandpa · *`. Lila invents more in this language.

Seeds: `examples/*.txt`. Style reference images stay with the install that collected them (do not ship stock).

---

## C. Surface CSS fonts (already on machine)

The wall's CSS font stack should include:

```css
--mono: "IBM Plex Mono", "Noto Sans Mono CJK JP", "Noto Sans Mono", ui-monospace, monospace;
```

- Latin banners → Plex Mono  
- AA / CJK → Noto Sans Mono CJK (full-width ≈ 2 cells)

---

## D. Lila → Crissa handoff

1. Lila adds/edits category entries in this file or `categories.json`.  
2. Lila drops original `.txt` seeds under `examples/` with `kicker` in filename or header.  
3. Crissa reads `FONTS.md` + `examples/LOOKBOOK.md`, picks category by room mood, publishes:

```json
{"action":"resting","art":"…","kicker":"night · aa","tone":"night"}
```

```json
{"action":"resting","moment":"scene","art":"…","kicker":"lila · hills","tone":"soft"}
```

Crissa does **not** thrash every TTS line. Lila does **not** write voiceonly-home.html.

---

## E. Install note

Pyfiglet: Hermes agent venv (`~/.hermes/hermes-agent/venv`).  
If missing: `venv/bin/pip install pyfiglet`.  
Optional: `apt install boxes cowsay toilet` for more tools (see ascii-art skill).
