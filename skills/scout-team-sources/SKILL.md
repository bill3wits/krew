---
name: scout-team-sources
description: Monthly involved scan for new trusted research sources; self-healing list.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Scout, sources]
---

# Scout — monthly source scan

Write `~/Desktop/krew-deliverables/team-sources-YYYY-MM-DD.md` **in the first
few tool calls** — a stub is better than a thinking-only turn.

Read the krew docs.json` first, then today's
`~/.krew/memory/ms/architect-collect/YYYY-MM-DD/team.md` (already resolved).

If search/web is down: still write the note from collect + JSON. Propose only
candidates that already appear resolved in collect (HN titles, GH repos, YT
videos). Do not stall.

## Hunt (involved)

Use search/web. For each candidate: name, URL, kind, why it would help *this*
house, one sample item from the last 30 days (must resolve). Drop unresolved.

## Self-heal

- `proposed[]` in the JSON: append candidates (`trust` omitted; status proposed).
- Do **not** pin. Do not delete `pinned` sources.
- `watch` sources with misses≥3 are already `stale` from collect — note them.
- Propose dropping stale watches: bullet `team-source-drop: <id>`.

JSON writes are allowed under `~/.krew/docs/` **only**
for `TEAM-RESEARCH-SOURCES.json` `proposed` array. If write is denied, put the
JSON block in the note for Steward.

End with `team-source:` bullets Steward will file to Ada.
