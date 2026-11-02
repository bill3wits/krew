---
name: krew-skill-builder
description: Turn a job you keep doing by hand into a skill of your own - draft it, try it once on a real case, install it to yourself, log it and index it.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [krew, skills, builder]
    related_skills: [krew-tool-builder]
---

# Make a skill out of a repeated job

Use it when you have done the same job by hand two or three times, the owner or Crissa asks you to "remember how
to do this", or a review says the same steps should not be re-derived again. A skill is instructions (when to
use it, the steps, how you know it worked). If the job needs NEW CODE the profile cannot already run, that is a
tool: use **krew-tool-builder** instead.

The helper is `scripts/skill_lab.py` beside this file (run it with `python3`). Its drafts live in
`$KREW_HOME/skill-lab/<name>/` until installed.

## Steps

1. **Name and draft it.**
   `python3 scripts/skill_lab.py new <name> --profile <you> --description "<when to use it, in one line>" --why "<the repeated job and who asked>"`
   The name is lowercase with dashes. The description is how you and every other agent will pick it later: say
   WHEN, not what.
2. **Write the body** in the draft: `## When to use` (the words people use when they want it), `## Steps` (each one
   names the exact tool or command), `## Done when` (the result and where it lands). Take the steps from the last
   time you really did the job, not from memory.
3. **Check it.** `python3 scripts/skill_lab.py check <name>` must say ok (frontmatter name = folder, a real
   description, no placeholder left).
4. **Try it once on a real case.** `python3 scripts/skill_lab.py trial <name> --profile <you> --case "<a real
   instance of the job>"` runs one one-shot of your profile with the draft as its guide. It must end `TRIAL OK`. If it
   fails, fix the steps where it went wrong and try again; never record a skill that has not passed a trial.
5. **Install it to yourself** with `skill_manage` (create, the draft's whole text, your own profile). Never into
   another profile: a profile that wants it makes its own request.
6. **Record it.** `python3 scripts/skill_lab.py record <name> --profile <you> --why "<same why>"` checks it landed
   in your skills, logs who / why / when to `$KREW_HOME/skill-lab/log.jsonl`, copies it to the catalog
   (`$KREW_HOME/built/skills/`) and rebuilds the docs index's `built` source, so `krew_docs` finds it for the crew
   and for Crissa.
7. Say in one line what you made and when it applies (your card or the chat that asked).

## Done when

`skill_lab.py list` shows the row, `krew_docs lookup "<its job>"` finds it, and the next time the job comes up you
load the skill instead of working it out again.

Off: `KREW_SKILL_LAB=0` (every verb refuses).
