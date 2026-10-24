---
name: zoey-wall
description: Put pages and notes on Zoey's wall.
version: 1.0.1
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Zoey, wall, team_display, voiceonly]
    related_skills: [zoey-surface-art]
---

# Zoey wall (VoiceOnly $ZOEYOS_PLAYER/v1/screen)

**Product:** VoiceOnly glass on **$ZOEYOS_PLAYER/v1/screen**. Do **not** write **:8088**.

**One verb:** `show_me {"what": …}` — a screen name, `report` (+ `text=`
markdown, skill **zoey-report**), `note` (+ `text=`), a handed URL or file,
`home`, `clear`, `tabs`, `shot`. `team_display {"action": …}` is the same
tool in its old shape; every example below still works in either.

## When to Use

These words mean put it on the wall **this turn** — not read it out, not ask first:

> show me · put up · display · pull up · bring up · let me see · on screen ·
> on the wall · I want to see · throw it up · have a look

Also put it up (then one spoken line) when the answer is a list, table, log,
diff, report, or more than about two sentences.

**Never ask permission** for your own screen. **Never** `team_board list` first
for a display verb — the page shows him the board better than you can describe
it. `team_board list` is for when *you* need facts to answer a question.

## Four landings

Pick by shape. Prefer 1, 2 and 4.

1. **A named krew screen he asked for** → `{"action":"view","name":"<that
   page>"}`. Live page, one glass, crossfade. `kanban` only when he asked
   for the board / Work. Do **not** put the board up as a default fidget.
2. **Chalk / notes** → `{"action":"chalk","title":"Last night","text":"<body>"}`.
   Lands as an **8.5×11 letter sheet** on the right of the **homescreen**.
   If the glass is on Control Panel, the tool **switches to home first** and
   the return says so — speak that, or he will still be looking at CP.
   Always that size, even for two lines. Summaries, card notes, completions.
3. **A tab** → `{"action":"tab","name":"dashboard"}` only when he wants it
   *alongside* something already up. `{"action":"activate","target":"…"}`
   switches. Tabs are a hard cut — not the normal way to show one thing.

4. **A finding** (summary, audit, comparison, "what happened") →
   `show_me {"what":"report","title":"…","text":"<markdown>"}`. Renders with
   her template, saves md + html in `krew-deliverables`, and the page is on
   the glass. Skill **zoey-report** has the shape. Say the lede, one line.

`{"action":"home"}` (or `show_me {"what":"home"}`) gives the face back and
closes pages you opened.

## Curate — take it down when the look is over

The glass is his. You put things up; you take them down **during the
conversation**, same turn the topic moves. Do not wait for the chat to die,
and do not wait for the leftover timer.

- He asked a new question, said thanks, or you moved on → `{"action":"home"}`.
  VoiceOnly face, no sheet, no document.
- A letter sheet whose job is done, face already up →
  `{"action":"clear","target":"notes"}`.
- He is still reading it → leave it. Hover × and a 15‑minute forgotten fade
  are backstops, not your cue.
- Do not announce the clear unless he would miss the page.

## Names, not URLs

Pass a **name**. Do not invent a URL.

`kanban` · `panel` · `approvals` (Zoey's list, `:8898`) · `composer` ·
`omni` · `dashboard` · `sessions` · `logs` ·
`presence` · `enroll` · `home` · `vllm` (`:8086` Crew tok/s) ·
`morning` · `efficiency` · `ms` · `architect` · `inbox` (INDEX.html)

Morning pages are a **sheet beside her** (skill **zoey-morning**). Wallpaper
does not paint HTML; `show_me report` posts `$ZOEYOS_PLAYER/v1/screen` doc-window. Do not steal
the Brave tab while the wall is the picture.

`panel` is Crissa's Control Panel (`show_me panel`: team-zoey puts `$KREW_PANEL_URL` on her card through `$ZOEYOS_PLAYER/v1/screen`). Never the STT service's own `/panel` (retired rail-stt HQ).

`url=` only for an address someone actually handed you. Guessing a URL puts
the wrong screen on the glass.

## Bookmarks bar (Hybrid Brave)

Her display browser (`~/.hermes/display`) already opens Wall · Krew ·
Composer · Panel on start. Those four (plus Clip Doctor) sit on the
**bookmarks bar**. If a krew screen is already a tab, **activate it** —
do not open a second copy. `team_display` is still the tool for the
glass; bookmarks are how *she* finds the krew pages in the tab strip.

## Live glass ($ZOEYOS_PLAYER/v1/screen)

| Place | Holds |
|-------|--------|
| **headline / note** | News cron only. Do not write or clear this band. |
| **the roll** | What the room said + her ASCII riding with it. Art lives here. |
| **notes** | 8.5×11 letter sheet on the **right** (Kindle page). Always full sheet. You clear it; 15 min is only if you forget. |
| **right / dial** | Room clock. The letter sheet covers it while notes are up; it comes back when they clear. |
| **claimed page** | Whole glass (`view name=`). `home` gives the room back. |

Chalk is the letter sheet, even for two lines. **Tables do not fit the
news band** — they never did. Long text stays on the sheet; the sheet
does not shrink to the summary.

## Notes — you clear them

You take the sheet down when its job is done. The **15 minute** fade is a
forgotten leftover, not the plan. Pass a shorter `notes_ttl` (seconds) if
you already know it is a glance. the user can close it with the hover ×
(mouse, same as Hybrid). You can also clear:

`{"action":"clear","target":"notes"}`

A PDF or a long script (Comma Gets a Cure, a paper) is a **tab**, not a
50-line chalk dump:

`{"action":"tab","url":"/path/to/file.pdf"}` or `url=https://…`

After `view name=kanban`, **click the glass**: `browser_snapshot`, then
`browser_click` "Show archived". Do not reprint the board into `text=`.

Art rides the **roll**: `{"action":"resting","art":"…"}` or `moment=coffee`.
Craft: **`zoey-surface-art`**. A spoken line releases the current piece;
conversation does not shut theater.

## Read the return

The tool reports the page that really loaded. If it says **NOTHING CHANGED**,
say you could not put it up. Never announce a screen you have not been told
is there.

Once a page is up: `browser_snapshot`, then `browser_click` /
`browser_type` / `browser_scroll`. `{"action":"shot"}` looks at your glass.
`{"action":"tabs"}` / `activate` / `close` manage extra tabs.

Confirm in **one short spoken line**.

## Worked examples

> **He says:** "show me the kanban board."
> **You call:** `show_me {"what":"kanban"}` (same as `team_display {"action":"view","name":"kanban"}`) — and nothing else.
> **You say:** "It's up on the screen."

> **He says:** "what did you find last night?" / "write it up" / "brief me."
> **You call:** `show_me {"what":"report","title":"Last night","text":"# Last night\nlede: …\nkpi: …\n## What I found\n- …"}`
> **You say:** the lede, one line. The page is on the glass; the file is in krew-deliverables.

> **He says:** "put up the panel."
> **You call:** `team_display {"action":"view","name":"panel"}`
> **You say:** "Control panel's up."

> **He says:** "what's on the board?"  ← no display word: this is a question
> **You call:** `team_board {"action":"list"}`
> **You say:** the one-line answer, out loud.

> **He looked at the board, then asks who is at the door.**
> **You call:** `team_display {"action":"home"}` — then presence.
> **The glass is his face again.** Do not leave the board up through the next topic.

## Delight

Conversation does **not** stop her box. Yield the glass only for a **claimed
page** (`view name=`). `home` resumes the scene. Discretion — not art every
turn.

## Anti-patterns

- "Would you like me to put that up?"
- `team_board list` then reading the table after a display verb
- Inventing a URL for a named krew screen
- Cramming a table into `note` (~160)
- Claiming the wall moved when the tool said NOTHING CHANGED
- Leaving the board up after the look is over
- Writing :8088
- Opening the STT service's `/panel` — that is the old ear HQ, not Control Panel
