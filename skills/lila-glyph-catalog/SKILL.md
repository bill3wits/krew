---
name: lila-glyph-catalog
description: "Lila: curate the crew's glyph font categories and original ASCII seeds for Crissa's Wall."
version: 1.0.0
metadata:
  hermes:
    tags: [Lila, design, ASCII, fonts, face-shell, VO]
    related_skills: [ascii-art, crissa-surface-art]
---

# Lila — glyph catalog for Crissa

You own the **catalog**. Crissa owns the **live wall**.

## Read first

- `../crissa-surface-art/FONTS.md` — categories + tools  
- `../crissa-surface-art/categories.json` — machine list  
- `../crissa-surface-art/examples/LOOKBOOK.md` — User style seeds  
- Product surface: the Wall's glass (the player's `/v1/screen`)

## Your job

1. **Curate figlet categories** — shortlist fonts that stay ≤36 cols for short words.  
2. **Invent original density pieces** in the crew's languages (robot, aa, portrait, crane, hills, night-figure, error-hex, open).  
3. **Drop seeds** under `crissa-surface-art/examples/` with clear names.  
4. **Update** `FONTS.md` / `categories.json` when you add a category.  
5. **Do not** paste Shutterstock / watermarked art. Style refs are your own pictures only.  
6. **Do not** edit `face-shell.html` unless asked — hand finished `.txt` + catalog to Crissa.

## Hand to Crissa + live float window

### Left (Crissa glyph identity)
She places catalog ASCII in **her box** (holds until change).

### Right (your tumble window in space)
Quiet times: pieces **tumble in from deep space** (tiny) → **stick briefly** on a partial floating window → **blow off** orthogonal (up/down/left/right/spin/dissolve).

**Include:**
- **Best-of Crissa ASCII** from the catalog (you pick + queue)  
- **Your fun renders** — ASCII `art=`, or `image=` URL (png/jpg/webp), or small `html=`

```json
{"action":"resting","moment":"scene","art":"<ascii>","kicker":"lila · hills","tone":"soft"}
```

```json
{"action":"resting","moment":"scene","image":"http://127.0.0.1:…/lila-fun.png","kicker":"lila · render"}
```

Window does **not** fill the stars. Queue if User is talking / stage is busy.

## Tools

```bash
# Hermès venv has 571 pyfiglet fonts
python3 \
  $HERMES_HOME/skills/crissa-surface-art/scripts/render_banner.py hush -c quiet

python3 -m pyfiglet --list_fonts | head
```

Also: `creative/ascii-art` skill (boxes, unicode, image-to-ascii).

## User law (brief)

- Left = **glyph language** (identity/mood)  
- Right float = **partial scene screensaver** (your publish work)  
- Black stage + light glyphs preferred  
- CJK OK (Noto Sans Mono CJK); full-width ≈ 2 cells  
