# M&S: the machine map, the nightly report, and (opt-in) upgrades

M&S is the maintenance lane the reference install runs every night, lifted whole. Run it from this checkout:

    python3 ms/scripts/krew-ms.py              # discover, then the report run (report only by default)
    python3 ms/scripts/krew-ms.py --discover-only
    python3 ms/scripts/krew-ms.py --where      # where the settings, ledger, reports and map live
    python3 ms/scripts/krew-ms.py --no-discover --no-kanban   # the trail alone, as the reference install runs it

Everything it writes lives under `$KREW_HOME/ms` (default `~/.krew/ms`): `discovery/map.json`, `map.md` and
`changes.jsonl`; `reports/ms-trail-<day>.json|md` and `ms-trail-LATEST.*`; `decisions.json` (the ledger);
`manifests/`; `krew-ms.log`.

## Settings: `~/.krew/ms.yaml`

Optional. With no `ms.yaml`, M&S starts from empty lists (no envs, no services, no health checks, no canary, no
kanban) and fills the env list from discovery. `krew-install.py` copies `config/ms.example.yaml` to
`~/.krew/ms.example.yaml` (never to `ms.yaml`). Its top keys are the live ones (`upgrades`, `discovery`). Below
them, commented out, is the reference install's whole settings file, regenerated 2026-10-10 from its current
profile: every env with its smoke test, services and apply mode, retired envs, the canary, the health lanes, kanban
assignees, paths. Placeholders: `${MS_ENVS_ROOT}` (your env root; the env var, else `${KREW_HOME}/envs`) and
`${KREW_HOME}` expand when M&S reads the file; `{data}`, `{crissa_repo}` and `{deliverables}` are yours to fill in;
`{krew_repo}` expands to this checkout. The Wall's own services (ear, aligner, enroll, presence, camera) are read
through the Crissa player on :8100, never on their own ports. Copy what fits your machine.
`MS_SETTINGS=/path/ms.yaml` points at another file; `MS_PROFILE_FILE` names a profile layered under it.

Two companion files, both optional:
- `config/ms-text.example.yaml`: the reference install's wording for every message M&S shows a person (cards, notes,
  the canary line). Copy to `$KREW_HOME/ms-text.yaml` and set `text_file:` to use it; without it `ms_text`'s neutral
  built-in wording is used.
- `config/ms-env-roots.example.json`: the roots report's rows (what each env exists to run). `envs` ships empty;
  `_reference_install.envs` holds the reference rows verbatim (placeholders in, audit prose left out). Copy a row
  into `envs` when that env exists on your machine.

## Upgrades are OFF by default

`upgrades: false` means report only: no package installs, no vLLM canary, no rollbacks, no unit restarts.
Every run says so on screen and in the log. `--apply-green`, `--apply-pending`, `--force-heavy`,
`--service-check` and `--rebuild` (except `--step status|plan`) refuse with exit 2 and the same line. Set
`upgrades: true` in `ms.yaml`, or `MS_UPGRADES=1` for one run, to allow them.

## Discovery (read-only; v2 preview)

The reference install does not run discovery: it runs the trail on `ms.yaml`'s lists (`--no-discover`). Discovery
is kept as built for v2 and is not extended here.

`ms_discover.py` follows the same pattern as the reference install's unit registry. It finds:
- systemd units whose name or command line points at this user's home, `$KREW_HOME` or `$HERMES_HOME` (or
  `discovery.units.prefixes` / `exec_markers`);
- listening TCP ports owned by this user, probed with one GET each (`/v1/models` first, so it also finds the
  models an endpoint serves);
- Python envs (under `discovery.env_roots`, the `hermes` command's interpreter, unit and endpoint interpreters);
- NVIDIA GPUs (`nvidia-smi`), model caches (Hugging Face, `discovery.model_dirs`, `ollama list`);
- Hermes profiles (which crew profiles exist and which do not yet), plugins and skills.

The first run is the baseline. Each run after that reports NEW and REMOVED items, which stay on the map for
`note_days` days. It also reports NOT SCANNED (a check it could not run, and why: no systemctl, ports owned
by other users, no nvidia-smi) and DROPPED (every item it skipped, and why). The trail adds its own DROPPED
list to the report (`dropped` in the JSON): backup env folders, retired envs, envs without a Python. Discovered
envs not named in `ms.yaml` join the report as `apply: report`.

GPUs: only NVIDIA is checked. Without `nvidia-smi` the map says "no NVIDIA GPU found". AMD (`rocm-smi`) and
Apple (`system_profiler`/`powermetrics`) are a later item.

## The ledger: `decisions.json`

    {"schema": "ms_decisions/v1", "updated_at": "...", "updated_by": "...",
     "decisions": [{"env": "...", "package": "...", "decision": "hold|pin|never|wontfix|deferred|reviewed|...",
                    "current": "...", "target": "...", "reason": "...", "source": "...",
                    "actionable": false, "until": "", "ts": "...", "unlock": {...}, "extra": {...}}]}

The first run creates it empty. Rows are written only through
`krew-ms.py --record-decision --env E --package P --decision D --reason "..."` (also `--record-finding`,
`--annotate`), under a file lock, so a held package stays held and the reason travels with it.

## Python

The trail needs `packaging`. If the Python you start it with does not have it, `krew-ms.py` re-runs itself
with the Hermes env's Python (the `hermes` command's interpreter), and logs that. PyYAML is used when present;
otherwise `ruamel.yaml` (Hermes ships it), and then JSON.

## Units and cadence

As the reference install runs them (rows in `engines/README.md`; the installer writes units and enables none):
- `krew-ms-nightly.service`: the trail, started by the off-hours ticker at window +5 min (`ms-nightly` in
  `window-schedule.json`), or `krew-ms-nightly.timer` (22:30) on a machine with no `off_hours`. It runs with
  `MS_ROOTS=report` (the roots report, Stage A: report only).
- `krew-ms-survey.timer`: the survey every 2 h (read-only drift report between nights; no `Persistent=`, as there).
- `krew-pyjwt-trail-confirm.timer` + `.service`: the reference install's dated one-shot, `ms-security-confirm.py PyJWT`
  (did the nightly security scan drop a package after an install). Copy the pair for another package or date.
  Opt-in: init writes it, `--enable pyjwt-trail-confirm` turns it on, `--enable all` never does (a fresh install
  would fire it at once, for nothing).
- `krew-hands-step-cap-remeasure.timer` + `.service`: its dated one-shot re-measure of Crissa's hands step cap
  (`crew/scripts/hands-step-cap.py`, the hands lift). Opt-in the same way (`--enable hands-step-cap-remeasure`).

## The rebuild (Stage D)

`ms-trail-run.py --rebuild ENV --to VER --step plan|build|check|serve|renders|compare|stop|flip|rollback`
(`ms/lib/env_rebuild.py`, `MS_REBUILD=1`, and `upgrades: true` for every step but status / plan). State lives under
`$KREW_HOME/ms/rebuilds/`; the env root is `MS_ENVS_ROOT`. Its machine pieces are settings: the GPU mode file
(`MS_GPU_MODE_FILE`, default `$KREW_HOME/gpu-mode`), the script whose `stop_comfy()` the `stop` step calls
(`MS_MODE_BIN`, default `$KREW_HOME/bin/gpu-mode`; absent = the step refuses), the train hold
(`$KREW_HOME/train-hold`). The compare renders post through the crew lift's `crew/lib/render_wip.py`, so they
carry the same client id the render stage watches.

## What is not lifted yet

- `ms_baseline`: measurement hooks (off by default); imported when present, skipped when absent.
- The reference install's patched torchaudio file (`config/env-patches/vllm-cu132/...`): a modified BSD source and a
  one-env workaround of that machine. Its roots row still declares it; the file is not shipped.
- `code_canary` / `code-canary.py`: called by the Janitor and the reference install's daily digest, not by the trail;
  it goes with the Janitor lift, not M&S.
- `self_claims`: the crew's daily self-review; ships with the daily lift (`crew/lib/krew_daily.py`).
- `ms-system-picture`: a helper of the reference install's legacy model-serving promote script; stays there.
- The reference install's second nightly (its test battery, `nightly-tests.sh`): it runs that machine's own test suite
  and room captures; krew's nightly is the trail alone.
- `capability-snapshot.json`, `last-update.json`: read by the Hermes settings and tree checks (v2, below).

v2, stubbed: discovery-driven env lists. The patch-queue check runs the crew lift's `crew/scripts/hermes-tree.py`
(`patch_queue.script` names another; absent = "not checked"); the restart window runs the same tool before
restarting Hermes units.

The restart window lives in `window/` (see the main README). The files in `ms/lib` (except `ms_discover.py`) and
`ms/scripts/ms-trail-run.py` are generated from the reference install by its `scripts/ms-export.py` (comments and
docstrings blanked, house names renamed), then carry the krew seams listed above. Fix them there, not here.

## Tests

`tests/test_ms*.py`, `tests/test_env_roots.py`, `tests/test_env_rebuild.py`: the reference install's M&S tests,
lifted. `tests/ms_defaults_only.py` renders the commented reference block of `config/ms.example.yaml` into a temp
profile (a fake env root, a fake data disk) so they run against the same values the reference install runs with.
Every test that would reach a live board runs with `--no-kanban` or a stubbed hermes; the notifier is stubbed.
