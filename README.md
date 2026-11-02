# krew

A crew for [Hermes Agent](https://github.com/NousResearch/hermes-agent): the plugins, skills, SOUL templates and
Hermes patches that turn one agent into a working team (Crissa, the front door you talk to; Ada, who owns the
board; Bash and Steward, the coders; Vera, the reviewer; Lila, the designer; Scout and Judge), plus the plugin that lets that crew drive a
Crissa Wall.

Crissa is the only repository you need to put the avatar on a different model endpoint. Add krew when you also
want the agent crew. Everything machine- or person-specific lives in your config, never in this code.

## What is in it

| Piece | What it gives an agent |
|-------|------------------------|
| `plugins/crissa-wall` | `wall_state`, `wall_keys`, `wall_cue`, `wall_home`, `wall_comms`, `wall_speak`, `wall_ask`, `wall_stop` (toolset `crissa`): drive the Crissa avatar on this machine; and Crissa's kill-search switch for the agent's web tools. |
| `plugins/krew-board` | `team_board` (the board in one tool: list, show, create, comment, unblock, complete), `team_time`, `team_config_audit` (toolset `team`); a `kanban_show` result that says each thing once. |
| `plugins/krew-docs` | `krew_docs` (toolset `docs`): look up the Hermes docs and your own notes first; read a passage whole; suggest a reference for your review. Plus the index builder. |
| `plugins/krew-hands` | `hands` (toolset `hands`): the front door's one action tool, a child Hermes run that does the job and reports back; `skill_read` (just `skill_view`). |
| `plugins/krew-kanban-lean` | `kanban_lean`: a dispatched worker's ten kanban verbs with shorter schema prose (needs patch 50). |
| `plugins/krew-render` | `render_order` (toolset `image_gen`): file a ComfyUI render by recipe id; render cards (licence, inputs, the look step); `render_runner.py` works the queue. |
| `plugins/krew-comfy` | ComfyUI as Hermes `image_generate` / `video_generate`, refusing unless the GPU is already in Render; beside a Crissa Wall it asks the Wall's own ComfyUI port. |
| `plugins/krew-vlm` | `look_compare`: a render judged against its target still in one vision call. |
| `plugins/krew-panel` | the Control Panel as tools (`panel_*`: unit lamps, journals, log lanes, security, health) plus its safe buttons. |
| `plugins/krew-tts` | Crissa's voice tiers as a streaming Hermes TTS provider named `crissa`: turbo 8186, audio8 8187, pocket 8188, kokoro 8189, the first that answers. Local only. |
| `plugins/krew-oracle` | the front door's presence and face tools, over the Wall's player; the camera verbs need `oracle.presence_url`. |
| `plugins/team-crissa` | the front door's team glue: her voice, the Wall's card, presence peek, ear health, list, show_me. |
| `plugins/krew-kitting`, `plugins/krew-skill-preload` | each profile's skill allowlist; skill bodies once per voice session. |
| `plugins/krew-complete-probe` | a spike probe, enabled on no profile. |
| `ms/` | M&S: `krew-ms.py` maps this machine (units, endpoints, envs, GPUs, models, Hermes profiles) and writes the nightly report and decisions ledger; upgrades ship off. |
| `skills/` | 51 skills, from `adversarial-gate` and `krew-reframe` to `krew-loops` (serviceable hub loops from one anchor still); every one, by package, in [docs/SKILLS.md](docs/SKILLS.md). |
| `crew/` | what runs between turns: the board loop and its gates, the hooks, the steward sweep, the keeper, the Daily, the render lane, their unit templates ([crew/README.md](crew/README.md)). |
| `souls/` | SOUL templates for the crew profiles: `crissa` (front door), `lila` (designer), `ada` (orchestrator), `bash` and `steward` (coders; Steward also runs M&S), `vera` (reviewer), `scout` (researcher), `judge` (one-verb rulings), and `TRUNK.md` (how everyone talks and works). |
| `patches/` | 17 patches to Hermes Agent at the pinned commit (`config/hermes-pin.json`), applied by `scripts/krew-patch.py`. |
| `crew/lib/krew_services.py` | the one services list (`~/.krew/services.json`) every patrol, the window and the panel read; `krew-install init` fills it from discovery and your answers. |

`config/krew-packages.json` lists every file this repository ships; `tests/test_krew_packages.py` fails on any file
or import outside it.

## The agents

Eight crew profiles and one console, each with a SOUL template in `souls/` (`souls/README.md` has the full roles):

| profile | role |
|---|---|
| `crissa` (Crissa) | the front door: the voice you talk to; rules the Decide queue, decides software calls, files projects for Ada |
| `ada` | orchestrator: owns the board, decomposes, assigns, routes; never implements |
| `bash`, `steward` | the coders; Steward is also the default assignee and runs M&S and host security |
| `vera` | reviewer: every finished build gets a `review:` card when the board gate's review step is on |
| `lila` | designer and researcher: the browser, specs, render minting |
| `scout` | research notes, overnight or on request; never assigned implementation |
| `judge` | one call, one verb: a single `VERB:` line on a diagnosed card |
| `main` | the Bridge: your own console (Hermes's default profile), not crew |

The board is Hermes's kanban. krew adds the loop and its gates (`crew/`): `kanban-loop.py` every 20 minutes (a
model pass: rule greens, reframe a repeated failure, retreat a card to Crissa), `kanban-board-gate.py` every 2 minutes
(no model: routes blocked cards, files reviews, liveness, orphans, breakers), the hourly steward sweep, and the
nightly improvement round (research, team eval by every role, a capped filer). Every card generator starts off on a
fresh install (the table below). Each board call signs as the profile that makes it, never a fixed name.

## What is optional

Required: Hermes Agent at the pin with krew's patches, an OpenAI-compatible endpoint, the profiles and their SOULs.
Everything else is opt-in:

| piece | default | turn it on |
|---|---|---|
| every timer (board loop, hold sweep, keeper, steward sweep, patrols, window, M&S nightly) | not started | `--enable <names>` or `--enable all` (the steward sweep, keeper and patrols only by name) |
| card generators | off or capped | one at a time (the table below) |
| memory (Hindsight + its pgvector store, on this machine) | init installs it, ships empty | `--no-memory` leaves it out; `--memory` alone adds it later |
| web search (SearXNG on loopback) | init installs it; Hermes's keyless cloud search off | `--no-search` leaves it out; `--search` alone adds it later |
| the two local brains (`engines/`) | not started; needs a large NVIDIA GPU | `krew-engine@ops`, `krew-engine@crew` by name, or the `claude` / `xai` presets instead |
| docs meaning search | on with the memory: the shared e5 model on the CPU | keyword search only without `--memory` (or name a venv in `docs.embedder_venv`) |
| the Wall (Crissa) | not needed | install Crissa; `crissa-wall`, `team-crissa`, `krew-tts`, `krew-oracle` then answer instead of "Crissa not installed" |
| renders (`krew-comfy`, `krew-render`, the render skills) | inert without your ComfyUI | a ComfyUI folder in `krew.yaml`; LTX and flux2 are opt-in recipes under their own licences |
| M&S upgrades | report only | `upgrades: true` in `~/.krew/ms.yaml` |
| the restart window | off | `enabled: true` in `~/.krew/restart-window.json` |
| the Janitor's restarts, the card breaker, the tool watch, the wall-tab probe | report only | `KREW_JANITOR_RESTART=1`, `KREW_CARD_BREAKER=act`, `KREW_TOOL_WATCH_CARDS=1`, `KREW_WALL_TAB_PROBE_CARDS=1` |

## Names

krew speaks Crissa's names: the front door's profile is `crissa`, the Wall plugin is `crissa-wall`, the player is `CRISSA_PLAYER` and the Wall's home is `~/.crissa` / `CRISSA_HOME`; krew's own units are `krew-crissa-*`. This repository and the krew inside the Crissa kit are the same files.

## Install

You need Python 3.14 (what the pinned Hermes requires), git, and an OpenAI-compatible model endpoint (vLLM,
SGLang, llama.cpp server, LM Studio, Ollama, or a hosted API). Linux and macOS.

### The one guided step

    git clone https://github.com/bill3wits/krew ~/krew
    cd ~/krew
    python3 scripts/krew-install.py init

`init` does the steps below in one pass and says what it did. **Hermes first, then krew on top**: it clones Hermes
Agent and checks out the pin, runs Hermes's own installer when no `hermes` command serves `~/.hermes` yet (it makes
the venv and the command), then applies krew's patches and reads them back from the tree; after the profiles it lays
krew's settings back over whatever Hermes changed (`crew/scripts/krew-config-restore.py`, the same routine that runs
after a `hermes update`) and puts the security minimums for the pin into Hermes's environment. It ends `init done`
only when every step passed, else `init NOT done` with the list (no `hermes` command = the cron jobs fail loudly).
Then the eight crew profiles with their config.yaml (the toolsets each carries, the model endpoint and model
name you give; the key's value goes to `~/.hermes/.env` and each profile's own `.env`, where a profile reads it
under the one gateway, and nowhere else; `--switch-brains` changes the brains of an install that has them), the SOULs, krew's plugins, skills, rails
and units, then **the services list**: it discovers this machine's units and listening ports, pre-fills
`~/.krew/services.json`, and asks you to name and approve every row (`--yes` approves them as found, as observed
rows: nothing is restarted from a row you did not set to `watch`). Loops are opt-in: `--enable kanban-loop,hold-sweep`
names the timers that start (`--enable all` for every timer a straight clone runs; the steward sweep, the keeper
and the patrols stay off unless named). Non-interactive: `--brains --endpoint --model --key-env --api-key --yes`;
`--dry-run` writes nothing. The crew's long memory (Hindsight) and web search (SearXNG) are installed on this machine
unless `--no-memory` / `--no-search` (docs/LOCAL-SERVICES.md); without memory init says it is off and how to add it. Other `--hermes-home`/`--krew-home` than your own are a scratch install: everything it writes stays under
those homes (units under `<krew home>/systemd-user`, the Wall only with `--crissa-home`), and it never runs another
install's `hermes`.

### Card generators on a fresh install

A crew that files its own cards can bury a board. On a fresh install every machine that files cards is off or capped,
and stays that way through `--enable all`; turn each on by itself, a day apart, and watch the day's new cards by
creator before the next:

| generator | fresh install | turn it on |
|---|---|---|
| Hermes's auto-decomposer (splits each new card in triage into children) | off: `kanban.auto_decompose: false` in the default profile's `config.yaml` (Hermes's own default is on) | `true` there; Hermes reads it every dispatcher tick |
| kanban-loop's reframe cuts (a repeated failure becomes a `reframe:` card) | off: `MAX_CUTS_PER_TICK=0` in `krew-kanban-loop.service` | `systemctl --user edit krew-kanban-loop.service`, `[Service]` `Environment=MAX_CUTS_PER_TICK=1` |
| kanban-loop's own decompose pass (cards left in triage over 5 min) | off: `MAX_DECOMPOSE_PER_TICK=0` in the same unit | `Environment=MAX_DECOMPOSE_PER_TICK=1` the same way |
| the board gate's `review:` card for each finished build | off: `KREW_REVIEW_GATE=0` in `krew-hold-sweep.service` | `Environment=KREW_REVIEW_GATE=1` in that unit |
| the board gate's `route:` card for ownerless blocks | off: `KREW_OWNERLESS_GATE=0` in the same unit | `Environment=KREW_OWNERLESS_GATE=1` |
| the improvement filer (the night's agreed ideas) | capped: at most `IMPROVE_FILE_CAP` (2) cards a night, only with a quorum of 3 voters, and only when `krew-window-tick.timer` runs | off: `KREW_ARCHITECT_CARDS=0` or the file `~/.hermes/architect-cards.off` |
| Ada's hourly board beat (one status card an hour) | created paused | `hermes -p ada cron resume kanban-heartbeat` |
| the steward sweep (a card per finding) | opt-in: `steward-sweep.timer` is never enabled unless named | `--enable steward-sweep` |
| the card breaker (parks a card whose last 3 runs in 6 h ended the same way) | `KREW_CARD_BREAKER=report`: it logs, parks nothing | `act`, once a week of its reports shows no false alarm |

**The brains are a preset** (`crew/config/brain-presets.json`, `--brains`): Crissa (the front door) runs on the `ops`
brain, every other profile on `crew`.

| preset | ops (Crissa) | crew (everyone else) | key |
|---|---|---|---|
| `engines` (default) | `http://127.0.0.1:8000/v1`, model `ops` | `http://127.0.0.1:8005/v1`, model `crew` | none (this box's own vLLM engines, `engines/`) |
| `claude` (no GPU) | `https://api.anthropic.com/v1`, model `claude-fable-5-1` | the same | yours: `--api-key` or `ANTHROPIC_API_KEY`, kept only in `~/.hermes/.env` |
| `xai` (no GPU) | `https://api.x.ai/v1`, model `grok-4.7` | `https://api.x.ai/v1`, model `grok-4.7` | yours: `--api-key` or `XAI_API_KEY`, kept only in `~/.hermes/.env` |

`claude` is Anthropic's OpenAI-compatible endpoint (checked 2026-10-10): `claude-fable-5-1` is the most capable model
its docs list; `--model claude-opus-5-5` (the docs' starting point), `claude-sonnet-5-5` (faster) or
`claude-haiku-5-5` (fastest, cheapest) pick another. Anthropic describes that layer as meant for testing and
comparison (no prompt caching, `strict` ignored, temperature below 1 refused on Claude 4.7 and later). `xai`:
`grok-4.7` is the model xAI's docs recommend (checked 2026-10-10). `--endpoint URL --model NAME` lays one custom
endpoint over both brains. krew is a Hermes Agent plugin: it runs on its own as an operational crew, or plugs into
Crissa. The steps by hand:

### 1. Hermes Agent at the pin

krew's patches were proved on one Hermes commit and are applied to that commit only:

    git clone https://github.com/NousResearch/hermes-agent ~/.hermes/hermes-agent
    git -C ~/.hermes/hermes-agent checkout 35272ce28b29bae90e9ecceff9def17ae791c012

Then install Hermes from that checkout the way its own README says (it creates its venv and the `hermes` command).
If you already run Hermes, use a checkout you can spare, or check the pin out in yours; `krew-patch.py` refuses any
other commit and says so.

### 2. krew

    git clone https://github.com/bill3wits/krew ~/krew
    cd ~/krew
    python3 scripts/krew-patch.py check        # would all 17 apply? changes nothing
    python3 scripts/krew-patch.py apply        # all of them, or none (a failure rolls back)
    python3 scripts/krew-install.py            # plugins + skills into ~/.hermes, ~/.krew/krew.yaml from the example

`krew-install.py` prints one `hermes plugins enable <name>` line per plugin (`--enable` runs them). It never
overwrites a plugin, skill, SOUL or settings file you already have (`--force` replaces plugins and skills).
`--souls crissa,ada,bash,steward,vera,lila,scout,judge` seeds those profiles' `SOUL.md` from the templates where they have none.

One plugin alone, straight from GitHub, also works:

    hermes plugins install bill3wits/krew/plugins/crissa-wall --enable

### 3. Point it at your endpoint

Merge `config/config.example.yaml` into each profile's `config.yaml`:

```yaml
model:
  provider: custom
  base_url: http://127.0.0.1:8005/v1     # any OpenAI-compatible endpoint (Crissa: http://127.0.0.1:8000/v1)
  default: crew                          # the model the endpoint serves (Crissa: ops)
  key_env: KREW_MODEL_API_KEY            # drop for a local server with no auth
```

Put the key's value in `~/.hermes/.env` (`config/env.example` lists the names; it holds no values). Add krew's
toolsets to each profile's `platform_toolsets` (the example shows which profile takes which; keep the crew names, see `souls/README.md`).

### 3b. The brains themselves (the reference box)

The endpoint can be the reference box's own: two vLLM engines, `ops` (:8000) and `crew` (:8005), serving Qwen3.8-27B
(Unsloth NVFP4) on one 96 GB GPU. `engines/README.md` has the hardware, the environment lock, the settings
(`config/engines.example.env` → `~/.krew/engines.env`), the unit (`krew-engine@ops`, `krew-engine@crew`) and the map of
every service on that box to the pack that provides it. A single smaller GPU runs one engine; under 24 GB, a hosted
endpoint.

### 4. Docs index

`krew_docs` searches an index you build once (and again when the docs change):

    python3 scripts/krew-docs-index.py build
    python3 scripts/krew-docs-index.py status

By default it indexes the Hermes docs in your checkout and your Hermes skills; add your own folders under
`docs.sources` in `~/.krew/krew.yaml`. Keyword search (SQLite FTS5) always works. Meaning search comes with the memory: the index embeds with the one
model krew and the Crissa kit share (`intfloat/multilingual-e5-small`, fetched once by `--memory`, run on the CPU
from the Hindsight venv); the Crissa kit's knowledge index reads this index as it is.

### 5. Memory and search (on this machine)

    python3 scripts/krew-install.py --memory --search      # init does both unless --no-memory / --no-search

Everything runs in user space on loopback, nothing needs sudo or docker, and every download is checked against a pin
in `crew/local-services.json`: the Hindsight plugin (vendored in `crew/hindsight/plugin/hindsight`, never fetched),
`hindsight-api` 0.10.2 on CPU, the embedding model (fetched once, offline after), the vector store (PostgreSQL +
pgvector, from pg0) and SearXNG (fetched at its pinned commit). At run time the only outbound calls are your brain
endpoint and SearXNG's search engines. Units: `krew-memory-db`, `krew-hindsight`, `krew-searxng`. Memory ships empty.
Details: `docs/LOCAL-SERVICES.md`.

### 6. Check

    python3 scripts/krew-patch.py status
    python3 scripts/krew-patch.py test --python /path/to/hermes-venv/bin/python   # each patch's own tests
    python3 -m pytest tests                                                     # krew's own tests (needs pytest, PyYAML)

Before you push from a clone, turn on the pre-push hook once (`git config core.hooksPath .githooks`): it runs krew's
scrub on every pushed commit and refuses a private word, path, key or address. CI runs the tests on every push.

`krew-patch.py test` needs the python of the venv Hermes runs in (`which hermes` leads to it), with Hermes's dev
tools in it: in the Hermes checkout, `uv sync --group dev` adds pytest.

## The Wall (Crissa)

krew and the Wall meet at two seams and no others: the Wall talks to a Hermes profile through that profile's
OpenAI-compatible API server (the Wall's agent endpoint), and krew talks to the Wall through the player's routes
(`/v1/speak`, `/v1/announce`, `/v1/stop` on `CRISSA_PLAYER`, default `http://127.0.0.1:8100`). Crissa's crons and
skills stay in krew and run through the board.

**init does this for you** (with Crissa installed): it turns the API server on (a port free on this machine), gives
Crissa's profile its own key, writes where her player really is (`CRISSA_PLAYER` in `$HERMES_HOME/.env`, read from her
launcher's table, so a `--port-offset` install is found) and offers the endpoint. By hand, or to see what it wrote:

**Turn on the API server.** Hermes runs one gateway per host. In the default profile's `$HERMES_HOME/.env`:

    API_SERVER_ENABLED=true
    API_SERVER_KEY=<a random string of 16+ characters>    # e.g. python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    # API_SERVER_PORT=8642     API_SERVER_HOST=127.0.0.1  (the defaults)

For a named profile (Crissa's), also put `gateway: {multiplex_profiles: true}` in `$HERMES_HOME/config.yaml`, and
give that profile its own key in `$HERMES_HOME/profiles/<name>/.env`:

    API_SERVER_KEY=<another random string of 16+ characters>

A named profile never borrows the owner's key: without its own, Hermes answers 401 at `/p/<name>/v1`. Then
restart the gateway (yours to do).

**Offer it to the Wall.** With Crissa installed (`~/.crissa`, or `CRISSA_HOME`), `krew-install.py` writes
`<crissa home>/endpoints.d/hermes-<profile>.json` (`--marry-profile <name>`; default `crissa` when that profile
exists, else `main`; `--no-marry` skips it). The offer names where the key lives (`key_ref`: the profile's `.env`
and `API_SERVER_KEY`); the key is never copied and never printed. It asks for no system prompt: the profile's SOUL
is her personality. The Wall keeps the endpoint it has until you open its Connect page (or Init's Connect step) and
click **Use** under Detected; the test call must pass.

**Her voice from krew.** `team_speak` speaks through the player's `/v1/speak` (`queue` waits for what she is
saying); `team_announce` and the board hooks hand lines to `/v1/announce`, which queues them behind her answers and
holds them while a conversation is open or someone is talking; the keeper's announcements go the same way. With no
player answering, each says "Crissa not installed".

## The services list

`~/.krew/services.json` (schema `krew-services/v1`, shape and every key in `crew/lib/krew_services.py`,
the reference install's rows in `config/services.example.json`) is the one list of this machine's services: name,
unit, scope, port, health, **role** (`watch` = the keeper heals it; `report-only` = watched and carded, never
restarted by a patrol; `observe` = not ours to touch; `never-restart` = ours, the owner's call; `retired`), GPU
yes/no, tier, and the Wall's rows marked `crissa` (parked while its player is down). The keeper's lanes, the
Janitor's watch and probe lists, the Daily's health units, Steward's log collect, the restart window's tiers and
groups, the boot check's checks and the Control Panel's tiles all read it, so a service cannot be on one list and
missing from another. No list = no lanes, and every reader says so (`krew-install init` writes it).

## The Control Panel

Crissa owns the one Control Panel; krew writes an add-on manifest for its bottom section
(`panel/krew-panel-tiles.py`, `krew-install` does it when it finds the panel). The manifest carries the host
gateway, the dashboard, crew/kanban, the Janitor, one tile per approved services row of krew's own, and a labeled
**krew services** section: the keeper, hold sweep, steward sweep, restart window, window tick, M&S nightly, dream
night, Hindsight, docs index, boot check and render window, each with **Run now** and **Off** (the off files the
loops honour: `~/.krew/<script>.off`). What the panel must do with `sections`, `run` and `off`, and what it shows
when krew is not installed, is the contract in [docs/PANEL-CONTRACT.md](docs/PANEL-CONTRACT.md); a v1 panel
ignores those keys and draws the tiles plain.

## Optional patrols (off by default)

`crew/scripts/krew-tool-watch.py` logs the front door's tool calls, failures and retries and files one card when
her failure rate jumps; `crew/scripts/krew-wall-tab-probe.py` asks the Wall's player whether its page still paints
and files one card per dead episode. Both are report-only until `KREW_TOOL_WATCH_CARDS=1` /
`KREW_WALL_TAB_PROBE_CARDS=1`, and their timers are opt-in: `krew-crew-install.py --enable tool-watch,wall-tab-probe`.

## Settings

One file, `~/.krew/krew.yaml` (`KREW_HOME` moves the folder, `KREW_CONFIG` the file). `config/krew.example.yaml`
lists every key with its default: the board's author and default assignee, docs sources and passage length,
which profile `hands` runs as, your ComfyUI folders and URL. Every piece also has an environment switch to turn
it off without uninstalling (listed at the end of the example).

`panel.url` (env `KREW_PANEL_URL`) must point at the Crissa panel server's port (`crissa-panel-server.py`,
default 8097), e.g. `http://127.0.0.1:8097`, not the avatar player's port: the player answers 404 on the routes
the `panel_*` tools call (`/v1/logs`, `/v1/security/summary`).

## M&S (the machine map and nightly report)

`python3 ms/scripts/krew-ms.py` maps this machine (units, endpoints and the models they serve, Python envs,
GPUs, model caches, Hermes profiles) and writes the nightly report and the decisions ledger under
`~/.krew/ms`. It is report only until you set `upgrades: true` in `~/.krew/ms.yaml`, and it says so each run.
With no `ms.yaml` it starts empty. `~/.krew/ms.example.yaml` shows every setting, including the reference
install's own (commented out). Discovery (the machine map) is a v2 preview: read-only, and the reference install
runs without it (`--no-discover`, lists from `ms.yaml`). Only NVIDIA GPUs are checked so far; AMD and Apple are a
later item. Details, the ledger format and what is not lifted yet: [ms/README.md](ms/README.md).

## The off-hours window (ticker, restart window, boot check)

Lifted as the reference install runs them, every machine value in `~/.krew` settings:
- `window/scripts/krew-window-tick.py` starts the night's jobs from `~/.krew/window-schedule.json` inside
  `off_hours` (set in `~/.krew/krew.yaml`; unset = idle, and it says why). krew ships one job: the M&S nightly.
- `window/scripts/krew-restart-window.py` files one card a window for the front door to rule on and restarts
  what is owed, in tier order, checking each. It is OFF until `enabled: true` in `~/.krew/restart-window.json`,
  and every unit list is empty until you fill it (`config/restart-window.example.json` has the shape and the
  reference install's values). After-checks: `restart-window-after.json`. Reboot intent: `krew-reboot-stamp.py`.
- `window/scripts/fleet-bootcheck` confirms the services came back after a deliberate reboot. It is a system unit
  (`units/system/fleet-bootcheck.service`) the installer never writes; config `/etc/fleet-bootcheck.json`.

`krew-install.py --units` writes the user units and enables none.

## Nothing is cut silently

Where a tool shortens what it returns, it says so and offers the rest: a board list says how many more cards
there are and the `offset=` that shows them; a shortened card field or docs passage names the exact call that
returns it whole; `hands` returns the child's whole reply. `krew-kanban-lean` shortens tool *descriptions* to
their first sentence by design (marked with an ellipsis); list `kanban` instead of `kanban_lean` for the full ones.

## Updating Hermes

`hermes update` moves your checkout off the pin. Run `python3 scripts/krew-patch.py revert` first, update, and use
the krew release made for the new pin; krew-patch refuses a commit it was not proved on.

## Removed or retired

Kept so nothing comes back by accident. Each row: the piece, the day, why, and what replaced it.

| area | piece | when | why | replaced by |
|---|---|---|---|---|
| gateways | one Hermes gateway unit per profile | 2026-10-08 | Hermes serves every profile from one host gateway (the User's ruling) | the one host gateway, profiles at `/p/<name>/v1` with their own keys |
| renders | flux2 as the default still | 2026-10-08 | non-commercial weights | the first commercial-OK still (qwen-image-2512, then sdxl); flux2 and LTX stay opt-in recipes |
| paths | the docker `/deliverables` path | 2026-10-08 | not a path on a user's machine | `{{deliverables_dir}}` |
| ear | naming the reference box's ear engine | 2026-10-08 | the engine is Crissa's choice, not krew's | the ear read through the Wall's player |
| voice in | the reference install's transcription script | 2026-10-09 | its STT engine was retired there | `krew-stt-cli.py` (Crissa's ASR) |
| names | the old character name in user-visible lines | 2026-10-09 / 10-10 | the product is Crissa | Crissa in every visible line; identifiers keep the source spelling (above) |
| brains | a placeholder endpoint | 2026-10-10 | no install should start on a value nobody chose | the `engines`, `claude` and `xai` presets |
| renders | ComfyUI's stock port 8188 beside a Wall | 2026-10-10 | on a Crissa machine 8188 is the Pocket voice | the Wall's own ComfyUI port |
| board | `team_board` signing as a fixed `crissa` | 2026-10-11 | a card's author must be the profile that wrote it | signs as the calling profile |
| board | Hermes's auto-decomposer on by default | 2026-10-12 | the September card flood | off on a fresh install, with every other generator |
| hours | quiet hours falling back to the off-hours or 22:00-08:00 | 2026-10-15 | a volume button covers it | quiet hours default to never; one off-hours window, asked in Crissa's Setup (`krew.yaml` still wins) |
| skills | `composer-coach`, `demo-rounds` | 2026-10-09 | studio-only | none in krew (more in [docs/SKILLS.md](docs/SKILLS.md)) |

## Licence

MIT (`LICENSE`). Hermes Agent is MIT and is not included here; the patches modify it (`THIRD-PARTY-NOTICES.md`).
