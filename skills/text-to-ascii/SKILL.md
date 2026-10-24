---
name: text-to-ascii
description: "Text → glyph ASCII for the Wall's glass. Lightweight (LLM mono + pyfiglet). Lila catalogs; Crissa may place if light."
version: 1.0.0
metadata:
  hermes:
    tags: [ASCII, glyph, Lila, Crissa, face-shell, text-to-art, VO]
    related_skills: [ascii-art, zoey-surface-art, lila-glyph-catalog]
---

# Text → glyph ASCII (not a heavy render)

**Goal:** From a short **text prompt**, produce **original** mono glyph art for the VO wall.  
**Not:** stock rips, Shutterstock, diffusion/image models, video.

**Product surface:** the Wall's glass (the player's `/v1/screen`)  
**Craft tools:** Hermes `creative/ascii-art` + pyfiglet (venv) + this prompt recipe.  
**Style refs (taste only):** `zoey-surface-art/examples/LOOKBOOK.md`

---

## Who runs what

| Job | Who | When |
|-----|-----|------|
| Curate languages, invent catalog seeds, fun float pieces | **Lila** | Design cards; quiet-time queue |
| Live her-box identity glyph (light) | **Crissa** | Room mood shift — **not** every TTS line |
| Surface mechanics | **Grok** | face-shell only |

If the request is heavy (many variants, new language, lookbook expand) → **Lila**.  
If one small glyph for “User is home” / “night hold” → **Crissa** OK.

---

## Inputs

```text
prompt:   short English (or CJK) description — e.g. "loki the dog soft", "desk at dusk"
language: robot | aa | portrait | crane | hills | night-figure | error-hex | open | roster
zone:     left | float
kicker:   optional label e.g. "fun · loki"
tone:     gold | soft | dawn | night
```

Default language from motif: person/pet → `roster`/`portrait`; outdoor → `hills`; error → `error-hex`; abstract dark → `night-figure`.

---

## Hard limits (must pass)

| Zone | Max cols | Max lines | Notes |
|------|----------|-----------|--------|
| **left** (her box) | **36** | **14** (prefer 6–10) | Glyph identity |
| **float** (Lila window) | **48** | **22** | Still partial panel — not full screen |

- Monospace only. No ANSI color codes.  
- No watermarks, no celebrity faces, no photo-real “renders”.  
- Prefer **black stage + light glyphs**.

---

## Pipeline (lightweight)

### 1) Word / name banner (tool)

```bash
python3 \
  $HERMES_HOME/skills/zoey-surface-art/scripts/render_banner.py \
  "hush" -c quiet
# or: python -m pyfiglet -f small -w 34 "loki"
```

Categories: `quiet|slant|script|block|cyber|play|display` — see `zoey-surface-art/FONTS.md`.

### 2) Scene / character glyph (LLM mono)

Use this **system recipe** (do not invent a heavy pipeline):

```text
You draw ORIGINAL ASCII/Unicode art only (no markdown fences in the art body).
Language: {language} from the lookbook.
Canvas: max {cols} columns × {lines} lines. Monospace. Light on dark.
Motif: {prompt}
Motifs OK: door, dial, ear, coffee steam, stars, desk, companion, pet.
Density languages:
  robot=# machine face; aa=2ch/kana face; portrait=$S@% fill;
  crane=bird silhouette; hills=$SXx landscape; night-figure=sparse *+@ on dots;
  error-hex=hex noise block; open=thin stars/dusk; roster=simple person/pet.
Output ONLY the art lines (no commentary). Stay inside the canvas.
```

Generate 1–2 variants; pick the cleaner one. If wider than limit, trim or regenerate.

### 3) Optional tool assist (ascii-art skill)

- Borders: `boxes` (if installed) then crop  
- Reference subject ideas: ascii.co.uk — **inspire only**, rewrite original  
- Image→ASCII only for **local** refs (never stock URLs)

### 4) Publish

**Left her box (Crissa / hold):**
```json
{"action":"resting","art":"<lines>","kicker":"…","tone":"night"}
```

**Right tumble window (Lila / stick then blow off):**
```json
{"action":"resting","moment":"scene","art":"<lines>","kicker":"lila · …","tone":"soft"}
```

**Fun image render Lila already built** (loopback only):
```json
{"action":"resting","moment":"scene","image":"http://127.0.0.1:…/x.png","kicker":"lila · render"}
```

---

## Quality bar (before publish)

- [ ] Fits col/line limits  
- [ ] Readable at a glance from a few feet  
- [ ] Looks **made here** (not a known stock piece)  
- [ ] Correct zone (glyph left vs scene float)  
- [ ] Kicker set; tone matches band if auto  

Fail → regenerate, don’t ship.

---

## Catalog handoff (Lila)

When you invent a keeper piece:

1. Save `zoey-surface-art/examples/<name>.txt`  
2. Note language id + zone in `examples/LOOKBOOK.md` or `FONTS.md`  
3. Comment on kanban: path + sample `team_display` JSON for Crissa  

Crissa does not need to re-invent languages — she picks from catalog.

---

## Out of scope (too heavy)

- Training models, ComfyUI, video ASCII, full-screen wallpapers  
- Scraping Shutterstock assets  
- Editing face-shell.html (Grok / implement card only)

---

## Related paths

| Path | Role |
|------|------|
| `zoey-surface-art/FONTS.md` | Figlet categories + density languages |
| `zoey-surface-art/categories.json` | Machine catalog |
| `zoey-surface-art/examples/` | Seeds + the lookbook |
| `lila-glyph-catalog` | Lila ownership skill |
| `creative/ascii-art` | Generic tools |
