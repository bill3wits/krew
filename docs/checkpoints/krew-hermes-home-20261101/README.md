# krew asks one place where Hermes lives (2026-11-01)

**Asked:** the owner, 2026-11-01. 31+ krew scripts hard-coded `Path.home() / ".hermes"`:
- kanban-loop's BOARDS / CURRENT, kanban-chatter, krew-keeper, krew_memory, krew-dream, team-eval-rank,
  improvement-outcomes, crissa-hands, render-cycle, hermes-tree and more;
- krew-crew-run's `KREW_HERMES_PYTHON` defaulted to `~/.hermes/hermes-agent/venv/bin/python`, which a fresh Hermes
  install does not have (its environment is `installs/<id>/environments/<gen>/venv`).

On one machine a second krew lives in `~/.hermes-krew` while `~/.hermes` is still in use. Its
kanban-loop read the OTHER install's board (read-only, nothing changed). A buyer who sets `HERMES_HOME` must get a
krew that reads and writes that home only.

## The rule: `crew/lib/krew_hermes.py`

| function | answers |
|---|---|
| `hermes_home()` | the Hermes ROOT, first match wins: `KREW_HERMES_ROOT` / `KREW_HERMES` (krew's own overrides); `HERMES_REAL_HOME` or `KREW_REAL_HOME` + `/.hermes` (the test knobs krew already had); `$HERMES_HOME` (a profile's own `<root>/profiles/<name>` reads `<root>`, as Hermes's `get_default_hermes_root` does); else `~/.hermes` (a profile's own HOME, `<root>/profiles/<name>/home`, reads `<root>`). `passwd=True` reads the passwd home in that last step, for the files built that way on purpose (a cron or worker shell repoints HOME). `env=` reads another environment. |
| `kanban_dir()` | `$HERMES_KANBAN_HOME/kanban` (Hermes's own override), else `<root>/kanban` |
| `hermes_tree()` | `$HERMES_TREE`, else `<root>/hermes-agent` |
| `runtime_python()` | asks the launcher (`hermes --print-runtime-command`, read only), else the python its `exec` line names. Otherwise a `RuntimeError` naming what was tried, never a guessed path. |
| `runtime_argv(script, args)` | runs a script the way the launcher runs its entry point: the checkout on `sys.path`, `hermes_bootstrap`, then the script as `__main__` |

- **Copies:** the file is copied byte for byte beside every folder that needs it (a plugin is installed alone): crew/lib, janitor, ms/lib, window/lib, rails, panel, scripts, seven plugins and two skills' scripts.
- **Shell half:** `crew/lib/krew_hermes.sh` has the same order. Shell scripts source it and use `$KREW_HERMES_HOME`, which is exported so their python heredocs read the same root.

## What changed

- **Python (~110 files):** every built root path asks the rule. An explicit `HERMES_HOME` read in a file keeps its place; only its `~/.hermes` default became the rule.
- **Config defaults:** `or "~/.hermes/<x>"` defaults (janitor, panel tiles, wall-tab probe, places watch, docs index) build from the rule.
- **Recognising the default profile:** board.py, team-crissa, tier0_gate `_is_crew` and krew-review-route `builder_of` no longer compare a folder name to `.hermes`. A profile is the folder under `<root>/profiles/`, whatever the root is called.
  - Before, `HERMES_HOME=~/.hermes-krew` read as a crew profile named `.hermes-krew`.
  - On a `~/.hermes` root the answer is unchanged.
- **Twelve shell scripts** (steward-sweep, sync-packs, pack-drift-check, kanban-lean, team-eval-run, start-comfyui, start-engine, …) source the shell half.
- **Installs:**
  - krew-crew-install's `unit_text` writes `%h/.hermes` in the unit templates as the install's own Hermes home when it is elsewhere, as it already did for `%h/.krew`.
  - krew_install_rails' `seed_text` does the same for `~/.hermes` in the first live tier0 / rails / janitor settings.
- **Runtime python:**
  - krew-crew-run: a `# krew: hermes-python` script runs under `KREW_HERMES_PYTHON`, else `runtime_argv`. With neither it exits 2 and says why. It used to fall back silently to an interpreter without Hermes's packages.
  - Other crew scripts: `KREW_PYTHON` / `KREW_HERMES_PYTHON`, else this interpreter (the old default was a path that does not exist on a fresh install).
  - krew-crew-install's YAML re-exec uses the same lookup.

## The allow-list (tests/test_hermes_home.py, each with its why)

- `scripts/krew_install_homes.py` `REAL_HERMES = "~/.hermes"`: the account's own default home, which the install guard compares a `--hermes-home` against.
- `<checkout>/.hermes/bin/hermes` (krew_install_homes, krew-config-restore): the Hermes checkout's own launcher folder, not the root.
- `scripts/krew_install_rails.py`: the `~/.hermes` token that `seed_text` replaces.
- `crew/lib/render_window_gate.py` `HERMES_DIR`: its tested passwd-vs-sandbox anchor, reached only when no named root is set.
- `crew/scripts/kanban-env-clean`: the sandbox it BUILDS.

## Proof

`tests/test_hermes_home.py`, 16 tests:
- the copies are identical;
- no shipped Python string literal or shell line builds the home (the allow-list aside), and every allow entry is still needed;
- the rule's order, a profile's process and a profile's HOME;
- the shell half equals the Python rule over 10 environments;
- `runtime_python` asks the launcher first, then the exec line, and never guesses;
- krew-crew-run runs a hermes-python script on the runtime a stand-in launcher names (`hermes_bootstrap` ran, argv kept), exits 2 with no runtime, and an explicit `KREW_HERMES_PYTHON` still wins;
- **kanban-loop under `HERMES_HOME=<tmp>/.hermes-krew` reads `<tmp>/.hermes-krew/kanban/boards`** (the finding);
- units and settings written for a non-default home name it.

**Not done here:**
- T0's deny patterns (`config/tier0.example.yaml`: `\.hermes/profiles/...`, `\.hermes/auth\.json`) still match a folder named `.hermes` only. A root under another name is guarded by the path rules that `seed_text` now rewrites, not by those regexes. That is T0 policy and is left for its owner.
- The `config/*.example.*` prose and the docs keep `~/.hermes` as the reference install's path.

Kill/rollback: revert the branch. No switch: with `HERMES_HOME` unset and `HOME` the login home, every path is what it was.
