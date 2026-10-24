# SOUL templates

One `SOUL.md` per Hermes profile, plus `TRUNK.md`: how everyone on the crew talks and works. Persona lives in each
SOUL; code and skills own the rest. `scripts/krew-install.py --souls` writes `TRUNK.md` + the profile's SOUL into
`$HERMES_HOME/profiles/<name>/SOUL.md` only where none exists yet (yours is never overwritten).

| Template | Role |
|----------|------|
| `zoey` | Front door: the voice you talk to (Crissa's avatar). Decides software calls, keeps you informed, files projects for Ada. |
| `lila` | Designer and researcher: the crew's browser, specs, design docs, render minting. |
| `ada` | Orchestrator: owns the board; decomposes, assigns, routes. Never implements. |
| `bash` | Coder 1: terminal, files, builds, tests, render jobs that need a shell. |
| `steward` | Coder 2 and default assignee: equal peer of Bash, plus M&S (the machine map and nightly report), host security, architect of record. |
| `vera` | Reviewer: reads diffs and deliverables against the brief, passes or sends back. |
| `scout` | Researcher: writes research notes (overnight or on request); never a board assignee for implementation. |
| `judge` | One call, one verb: answers a diagnosed card with a single `VERB:` line; never assigned work. |
| `main` | The Bridge: your console on the machine (Hermes's default profile, host reach), not crew. Hands lasting work to Ada as a card. `--souls main` writes it to `$HERMES_HOME/SOUL.md` (no TRUNK). |

Each template is the reference install's SOUL for that profile (2026-10-08) minus what is that machine's alone:
household names, its ports and paths, its presence feed. The laws stay: a child's summary is a **self-report**;
a human-only blocker goes to Crissa as **needs-zoey**, never an owner card; an applied upgrade needs an
`unlock=` disposition (**land-only** for security and hygiene); Lila and Vera are **spawn-only** workers (no
gateway). Crissa's template carries her AUTH states (skill **zoey-auth**) and the pointers to her skills
(zoey-wall, zoey-map, zoey-list, zoey-marketing, zoey-look, zoey-needs, zoey-spotcheck); the coders' and
reviewer's maps (bash-map, lila-map, vera-map) ship beside the SOULs.

Keep the names. They are the crew's profile names, and the SOULs, skills, plugin defaults (render allow-list `lila,bash`,
render cards on `lila`/`bash`/`vera`, default card assignee `ada`, hands on `zoey`... see `config/krew.example.yaml`) and board assignees all
key on them. A second coder of your own: `--souls bash:bash-2` copies Bash's SOUL into a new profile.
Fill in the `{{...}}` placeholders (your name, your deliverables folder) or delete the lines.
