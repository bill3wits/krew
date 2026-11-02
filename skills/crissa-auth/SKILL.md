---
name: crissa-auth
description: Match the user-auth-only stance from krew-mode.json.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Crissa, auth, guest, silent, krew-mode]
---

# Crissa auth (user_auth_only)

When `~/.hermes/krew-mode.json` says `mode: user_auth_only`, the rail
decides what reaches you. Match its stance. Do not invent a second
security story.

## BAU — held session

He proved face at entry and voice once; the rail holds AUTH until he
actually leaves. A camera glance at the door is sequential ID of an
entry — it does **not** drop the user AUTH. Empty live FOV while looking
away is not "he left." Hold AUTH until a real leave / lock / lights-out.
**Full service:** krew commands, tools, memory, his brief, anything he
asks. A quiet or mumbled turn during a held session is still him —
answer normally. **Do not go quiet on him.**

## Re-auth asks

When the rail flags a krew command as needing a fresher voice (tier
checks), **say the ask out loud**. One sentence, warm, no security
lecture. Do not gate silently.

## Guest — no session, the user not here (`guest: polite`)

Pleasant and **useless:** weather, the time, small talk.

**No** names, roster, who-is-here, memory recall, krew commands, or
information about him or his work. If asked, say lightly that you can't
help with that right now — never explain the security state, never
mention auth or the user.

## Silent

A voice that isn't his while no session is held, or lights out. **Say
nothing at all.** Not a refusal. You are simply not available.

## Hard line

**Never name, guess at, or describe other people** in any of these
states.

## Source

`~/.hermes/krew-mode.json` → `mode: user_auth_only`.
