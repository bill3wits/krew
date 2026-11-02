---
name: glyph-density
description: "Procedural glyph art (+ * # @ on black) for the room screen. CPU only — draws in any occupancy. Tool: team_glyph_art."
version: 1.1.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [ASCII, glyph, art, wall, CPU, Crissa, Lila]
    related_skills: [crissa-surface-art, picture-to-glyph, text-to-ascii]
---

# Glyph Density Singularity — Japanese Figure System

## Purpose

Create dense ASCII/glyph art in the martial-artist silhouette style using a limited character palette to render high-density figures against a black background.

## Character Set

- Space ( ): transparent / background fill
- + (plus): lightest shadow / edge highlight
- * (asterisk): mid-light fill / shoulder highlights
- # (hash): mid-dark body / shadow depth
- @ (at sign): darkest form / core silhouette

## Density Requirements

- Minimum 20 characters wide, minimum 30 characters tall
- Use ALL four glyphs (`+ * # @`) — never use just one or two
- The `@` character defines the figure form (arms, legs, torso, head, weapons)
- `#` fills shadow areas and depth
- `*` adds highlight planes
- `+` outlines edges and fine detail

## Style Rules

1. **Figure-first**: The character silhouette must be clear and recognizable
2. **Density**: Every character position must serve a purpose — no empty space within the figure bounds
3. **Black background**: All characters render on a black field (no spacing characters outside the figure)
4. **Original only**: Generate from scratch, never copy copyrighted characters

## Output Format

Each line uses only `+ * # @` (and space for background). Lines are roughly the same width.

Example:
```
+@+++#@+
@@**###@
+#@*#@*+
@#@@@#@#
```

## Prompt Format

Input: "Generate a [description] in glyph density style"

Examples:
- "Generate a martial artist in a fighting stance"
- "Generate a kunoichi character with a tanto blade"
- "Generate a samurai with a katana in a bow pose"

## Tool

A companion CLI lives at the team-crissa plugin's `glyph_density.py` (`$HERMES_HOME/plugins/team-crissa/glyph_density.py`):
13 built-in shapes, freeform subject parsing, multi-pass rendering, 4 palette
modes, inversion, and seeded randomness.

From an agent, call the tool **`team_glyph_art`** instead of shelling out — it
runs that CLI, posts the result to the room screen, and reads `/v1/screen` back
so what it tells you is what is actually on the glass. `post=false` returns the
art without touching the wall. It is CPU-only, so it works in every occupancy,
including Render and All-OS-Off when krew-comfy will not run.

## References

- User ref 12 (white-on-black martial-artist silhouette style reference)
