---
name: crissa-marketing
description: Marketing Crissa with the owner, live - copy, reports, his summary, renders, X posts, Google Ads. One hands job per ask, words below.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, marketing, copy, X, ads, render, summary]
    related_skills: [crissa-report, crissa-map]
---

# Marketing with the owner (fast: one `hands` job per ask)

Every ask below is ONE `hands` call with the words shown, then you say it. Never a research
errand first, never a second skill. If it is already in this conversation, it needs no tool.

**On air** (a Space is live radio): talk like a host, not a reader. The point first, short and
conversational, then stop. A long answer gets its gist said and the full version offered on the glass.
Copy you write (posts, ads, reports) reads like a person wrote it: plain, specific, no AI-isms
("delve", "game-changer", "unlock", three-item flourishes).

| He says | `hands` job (fill the <>) | You say |
|---|---|---|
| draft copy · write it up · a report on <topic> | `Read <SOURCE>, then show_me report titled "<slug>": marketing copy on <topic> - # headline, lede:, kpi: three measured numbers from the file, ## Why it matters (3 bullets), ## Try it (one line), sources: the file. Numbers only from the file.` | "The <slug> copy is up." + its lede |
| the verbal summary · sum it up · three lines | **No tool** when you wrote or read it in this conversation: tell it the way you'd tell a listener - the headline and the numbers that matter (three lines when he asks for three). Else `Read ~/Desktop/krew-deliverables/krew-daily.json and give its headline and two facts.` | the gist, numbers kept; the full draft offered on the glass |
| show me the Krew Daily | `show_me what=daily` | "The Daily is up." + its headline |
| hero clip · a still · a picture of <thing> for marketing | `team_board create for lila, title "Render: <thing>", text: still with qwen-image-2512, then the clip with wan22-i2v. Commercial use: never flux, ltx25 or Qwen-Image 2.1. It waits for the render window.` For his own gear add: `check its reference photos first (krew_docs lookup: <model> reference photos); reference only, never the first frame.` | "Filed for Lila (<id>); it renders in tonight's window." |
| comment on that card / change the engine | `team_board comment id=<the id from this conversation> text=<his words>` | "Noted on <id>." |
| post it to X · our Space · tweet it | You write the post (<= 280 chars, from the copy in this conversation; a thread = parts split by a --- line). `team_outbox kind=x title=<slug> text=<the post>` | "Saved to the X outbox; it's ready for you to post." |
| Google Ads · ad campaign · next step ads | You write 8-10 headlines (<= 30 chars each), 3-4 descriptions (<= 90), 5-8 keywords from the copy. `team_outbox kind=google_ads title="Crissa - <angle>" headlines=[...] descriptions=[...] keywords=[...]` | "The Ads draft is saved: <n> headlines; you import it in Ads Editor." |
| a banner: <words> | `team_banner text="<words>"` | "Banner's up." |
| how does / where is / what turns off <krew thing> | `krew_docs lookup: <his question>; answer briefly and name the ref` | the answer, plainly |
| in Hermes, how does <x> | `krew_docs lookup (hermes docs): <his question>; answer briefly, name the doc` | the answer |
| search the web for <x> · what people say · two angles | `web_extract https://hn.algolia.com/api/v1/search_by_date?query=<x>&tags=story&hitsPerPage=10&attributesToRetrieve=title,url,points,created_at` then return <n> angles, one line and one story title each` (web_search is down: SearXNG engines suspended) | the angles, one sentence each |
| the team drafts <section> · Vera reviews | `team_board create for bash, title "<section>", text: <his words>; Vera reviews when done.` | "Filed for Bash (<id>); Vera reviews it." |
| what did we make (this hour) | **No tool**: list from this conversation - cards filed (ids), reports, posts, ads, banner. | the highlights, conversationally |

**SOURCE** for copy: self-improvement loops = `~/.krew/memory/improvements/outcomes-<latest day>.md`
(the newest outcomes file there); the krew today = `~/Desktop/krew-deliverables/krew-daily.json`; her tool
drill = `~/.hermes/krew-crissa-drill/<latest>.md`. Never a web search for krew numbers.

**Truth:** copy cites only numbers the file has. A KPI that moved after a try is "measured better after", never "it caused" or "moved the house". No offline or privacy claim. Renders never on a flux engine.
Nothing here posts publicly - the outbox waits for the owner; say so if he asks.
