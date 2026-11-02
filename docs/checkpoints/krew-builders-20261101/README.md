# krew-skill-builder and krew-tool-builder (2026-11-01)

**Asked:** the owner ~14:3x EST: "how about one skillbuilder skill and tool builder skill".
Branch `krew-builders-20261101` off krew 1.0.6 (02672b7). Nothing pushed.

## What

**krew-skill-builder** (`skills/krew-skill-builder/SKILL.md`, helper `scripts/skill_lab.py`): any profile turns a job it
keeps doing by hand into a skill of its own.
1. `new` drafts `$KREW_HOME/skill-lab/<name>/SKILL.md`: the frontmatter (name, and a description that says when to use
   it) and the When to use / Steps / Done when sections.
2. `check` refuses a draft with a wrong name, a thin description or a placeholder left.
3. `trial` runs ONE one-shot of the asking profile on a real case, with the draft inlined as its guide, and keeps the
   answer. It passes on `TRIAL OK`.
4. The agent installs the skill to itself with its own `skill_manage`.
5. `record` refuses without a passing trial or the installed copy. Then it logs who / why / when
   (`$KREW_HOME/skill-lab/log.jsonl`), copies the skill to the catalog (`$KREW_HOME/built/skills/<name>.md`) and rebuilds
   the docs index's `built` source.

**krew-tool-builder** (`skills/krew-tool-builder/SKILL.md`, helper `scripts/tool_lab.py`): a new tool is a small Hermes
plugin in `$KREW_HOME/tool-lab/<name>/`. It holds `plugin/` (plugin.yaml, an `__init__.py` that registers one tool,
`tool.py` `run(args)`, `schema.json`), `tests/`, and `tool.json` (what it is, who asked and why, the profiles, and what it
reads, writes and runs). The route:
1. `test`: its own tests.
2. `submit`: one `review: tool <name>` card to Vera. PASS or FAIL is the first word of her completion or her comment.
3. `t0`: the T0 gate, run exactly as Hermes runs it, in enforce mode, as each named profile. It is asked about the
   tool's own code and every declared command, write and read. A block is a finding; no gate or no policy is a finding
   too.
4. `ask-owner`: refused until tests, PASS and a clean T0 stand. Then it files one card on the front door, blocked
   `needs_input` on the owner.
5. `install`: refused unless all four stand for the files as they are now. Then it copies the plugin into each named
   profile's `plugins/`, runs `hermes -p <p> plugins enable`, writes a catalog page (`$KREW_HOME/built/tools/<name>.md`)
   and rebuilds the `built` index.
6. `remove`: disables the tool, deletes the copies and takes the page down.

More on the tool route:
- `status` shows every step.
- Every step is stamped with the sha of `plugin/` + `tests/`, so an edit after any step re-opens every step after it.
- Only a comment by an author in `KREW_OWNER_AUTHORS` (default `user,dashboard`; T0 refuses a crew profile signing
  as one) approves. The tool never enables itself.
- Every verb is logged to `$KREW_HOME/tool-lab/log.jsonl`.

**krew-docs:** the default sources gain `built` (`$KREW_HOME/built`), so `krew_docs` finds what the builders made for
the crew and for Crissa. A box that sets `docs.sources` adds it by hand (`config/krew.example.yaml` shows it).

## Switches

- `KREW_SKILL_LAB=0` and `KREW_TOOL_LAB=0` refuse every verb (exit 3).
- Nothing arms on its own. The skills are instructions plus a helper; no unit and no timer.
- `KREW_REVIEWER` (vera), `KREW_OWNER_PROFILE` (crissa, the card's assignee), `KREW_OWNER_AUTHORS`, `KREW_T0_GATE`
  (default `$KREW_HOME/rails/tier0_gate.py`), `KREW_SKILL_TRIAL_TURNS` / `_S`, `KREW_TOOL_LAB_TEST_S`.

## Proof

`tests/test_builders.py`, 7 tests. Setup: a stand-in `hermes` that records every argv and answers cards from a file,
scratch HERMES_HOME and KREW_HOME, and the shipped `rails/tier0_gate.py` on a one-rule policy.
- **Whole tool route:** nothing installs before the owner's word. A crew profile writing "approve" does not count, and
  no `plugins enable` call happens until `user` says approve. Install copies and enables for the named profile only and
  writes the catalog page. Remove disables, deletes the copy and the page. Log rows carry tool, verb, by and ts.
- **An edit after approval** re-opens tests, review and owner.
- **A T0 finding** (`sudo systemctl` in `runs`) stops the route before the owner's card.
- **No gate installed** is not clean.
- **The kill switch** writes nothing.
- **Skill route:** a placeholder fails check; record refuses without a trial and without the installed copy; a
  `TRIAL FAILED` answer fails. Record logs who / why / when and writes the catalog.
- **The docs index's default sources** include `built`.

Run: `python3 -m pytest -q -p no:cacheprovider tests/test_builders.py tests/test_krew_packages.py tests/test_scrub.py
tests/test_crissa_skills.py tests/test_docs.py tests/test_install.py tests/test_install_parity.py tests/test_plugins_parity.py
tests/test_clean_install.py tests/test_t0_gate.py tests/test_crew_docs.py` gives 151 passed, 8 skipped, 1 failed. The failure,
`test_install_parity[steward]`, fails the same way on clean 02672b7 (a line-wrapped path in the steward SOUL under a
long basetemp); it is not this landing.

**Not proven live:** a real profile running the skill and the trial through a real one-shot, a real Vera card and a real
owner card on a board. Those need a krew install (a copy of crissatest), never the house.

## Files

- `skills/krew-skill-builder/SKILL.md`, `skills/krew-skill-builder/scripts/skill_lab.py`
- `skills/krew-tool-builder/SKILL.md`, `skills/krew-tool-builder/scripts/tool_lab.py`
- `plugins/krew-docs/docs_index.py` (the `built` source), `config/krew.example.yaml` (the `built` line)
- `config/krew-packages.d/builders.json` (package `skills-builders`)
- `tests/test_builders.py`
