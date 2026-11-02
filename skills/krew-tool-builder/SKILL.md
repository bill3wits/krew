---
name: krew-tool-builder
description: Build a new tool (a script with its JSON schema, as a small plugin) in the tool lab - tested, reviewed by Vera, read by T0 and approved by the owner before any profile gets it.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [krew, tools, builder, T0]
    related_skills: [krew-skill-builder]
---

# Build a tool, the owner's way

Use it when a job needs code no profile can already run as a tool, the owner asks for a new tool, or the same
shell recipe keeps being retyped. If the job is only steps over tools you already have, make a skill instead
(**krew-skill-builder**).

The helper is `scripts/tool_lab.py` beside this file. The tool lives in `$KREW_HOME/tool-lab/<name>/` and NEVER
enables itself: nothing reaches a profile until the owner has said yes on his card.

## The route (each step is stamped with the files' sha; an edit re-opens the steps after it)

1. **Scaffold.** `python3 scripts/tool_lab.py new <name> --desc "<what it does, when to use it>" --profiles <a,b>
   --by <you> --why "<who asked and for what>"` writes `plugin/` (plugin.yaml, __init__.py, tool.py,
   schema.json), `tests/` and `tool.json`.
2. **Write it.** The work goes in `plugin/tool.py` `run(args)`; its arguments in `plugin/schema.json`; its own
   tests in `tests/`. List in `tool.json` every path it **reads**, every path it **writes** and every command it
   **runs**: T0 reads exactly that list, and Vera checks the code does no more.
3. **Test.** `python3 scripts/tool_lab.py test <name>` (its own tests, on the lab copy). Green before anything else.
4. **Review.** `python3 scripts/tool_lab.py submit <name>` files one `review: tool <name>` card to Vera. She
   completes it with PASS or FAIL as the first word. A FAIL: fix, test, submit again.
5. **T0.** `python3 scripts/tool_lab.py t0 <name>` asks the T0 gate, as each named profile, about the tool's own
   code and every declared read, write and command. Any block is a finding; a finding means the tool changes, never
   the policy.
6. **The owner.** `python3 scripts/tool_lab.py ask-owner <name>` (only after PASS + T0 clean) files one card blocked
   on the owner. His comment `approve` or `no` is the answer; nobody answers it for him.
7. **Install.** `python3 scripts/tool_lab.py install <name>` refuses unless tests, PASS, T0 and the owner's approve
   all stand for the files as they are now; then it copies the plugin into each named profile, enables it there,
   writes its catalog page (`$KREW_HOME/built/tools/<name>.md`) and rebuilds the docs index's `built` source. A
   running gateway loads it at its next start.
8. `python3 scripts/tool_lab.py status <name>` shows every step at any time.

## Taking it out

`python3 scripts/tool_lab.py remove <name> [--profiles a,b]` disables it and deletes the copy in each profile (all of
them by default) and takes its catalog page down. Use it the moment a tool misbehaves; installing again goes the whole
route.

## Done when

`status` says nothing is missing, the named profiles list the tool, and `krew_docs lookup "<its job>"` finds its page.
Every verb is logged to `$KREW_HOME/tool-lab/log.jsonl` (who, when, what happened).

Off: `KREW_TOOL_LAB=0` (every verb refuses).
