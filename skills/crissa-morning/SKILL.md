---
name: crissa-morning
description: >
  Morning report on the glass while she speaks: M&S, architecture, Ada
  efficiency, yesterday's completions, curated improvement ideas. Pages
  sit beside her (sheet / doc-window). Wallpaper does not paint HTML.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, morning, report, wall, cua]
    related_skills: [crissa-her-wall, crissa-report, crissa-cua]
---

# Morning report — she stays on the glass

He says *morning report · morning brief · what happened last night ·
team daily*. You are on screen speaking. Pages sit **beside** you.

The brief is **pre-built at 02:00** (`krew-morning-brief.timer`) from
**yesterday's** reports, so the dates are a complete day. If
`team-daily.html` exists, pop it. Do not wait to assemble.
`show_me {"what":"inbox"}` is INDEX.html when he wants to browse.

## What goes in it

Gatherer (bash if missing): `python3 ~/.krew/crew/scripts/crissa-team-daily.py`

The HTML is `~/Desktop/krew-deliverables/team-daily.html`. It already
rolls up:

- M&S yesterday (`ms-YYYY-MM-DD.md`)
- Architecture yesterday (world + stack)
- Ada efficiency (`ops-efficiency.html`)
- Completions from yesterday's board
- Curated `*-improvement-ideas-*.md`
- Yesterday's role notes (from team-research)
- Other dated reports from that day (sweeps, reviews, land notes)

Do not invent numbers. If a section says missing, say that.

## How it lands (both glasses)

Wallpaper **does not paint HTML**. Yesterday a Brave tab showed the page
and the wall background did not — CDP navigated a browser that is not
the picture.

`show_me {"what":"report", "title":"Morning", "text":"<the markdown>"}`
posts the letter sheet + doc-window on `$CRISSA_PLAYER/v1/screen`. She stays on the ASCII
(wallpaper) or the VoiceOnly face (Brave). `show_me home` takes the
sheet down.

Named pages (same sheet path, not a full-screen steal):

- `show_me {"what":"morning"}` — living `team-daily.html`
- `show_me {"what":"efficiency"}` — Ada's charts
- `show_me {"what":"ms"}` — living `ms.html`
- `show_me {"what":"architect"}` — living world note

## The spoken loop

1. `show_me` the morning report (or `what=morning` if the HTML exists).
2. Speak the **lede**, one or two sentences. You are on camera.
3. Walk the sections. When you change topic, `show_me` that named page
   (efficiency, ms) — still a sheet, not a stolen face.
4. When he moves on: `show_me {"what":"home"}`.

Do **not** `browser_click` through the HTML on wallpaper. CUA
(`browser_*`) is for the Brave glass tab. Wallpaper: the sheet is the
page.

Ops tok/s is bash (`ops-toks`). You report the number from the table.

## Anti-patterns

- Navigating her Brave tab to `file://` while wallpaper is the glass.
- Reading the whole report out loud.
- Claiming the wall moved when the tool said NOTHING CHANGED.
