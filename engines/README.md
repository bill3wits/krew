# The reference box: the brains, and what else runs on it

Crissa and krew were built on one machine. A person who builds that machine and installs both packs should get
the same experience, so this folder carries the part neither pack could ship before: **the two brains**, exactly as
the reference box runs them, plus the map of everything else on that box and which pack provides it.

## Hardware (the reference box, read on 2026-10-08)

| Part | Reference box |
|---|---|
| GPU | NVIDIA RTX PRO 6000 Blackwell Workstation Edition, 96 GB, driver 595.91 |
| CPU | AMD Ryzen 9 9950X, 16 cores / 32 threads |
| RAM | 96 GB |
| Disks | two 2 TB NVMe: the system, and a data disk for models and environments |
| CUDA | 13.2 toolkit (`/usr/local/cuda-13.2`; `KREW_CUDA_HOME` names another) |
| OS | Ubuntu, GNOME on Wayland, PipeWire; systemd user units run everything |

A smaller GPU runs ONE engine (make `crew` the only one and point every profile at it); a GPU under 24 GB runs the
packs with a hosted endpoint instead.

## The two brains

Both engines serve the same checkpoint, **Qwen3.8-27B in Unsloth's NVFP4 quantisation** (Apache-2.0, about 18 GB of
weights), with vLLM, on the one GPU:

| Served name | Port | Who asks it | Scheduling | MTP | KV pin |
|---|---|---|---|---|---|
| `ops` | 8000 | the talking profile (Crissa), the decomposer, long research | priority | on, 3 draft tokens | 14.0 GiB |
| `crew` | 8005 | the board's workers (every other profile) | default | off | 14.5 GiB |

Hermes only ever asks for the served names `ops` and `crew`. When a checkpoint moves, change the model path in
`engines.env`; never the served name, the port, or Hermes's `model:`.

1. **The vLLM environment.** `engines/vllm-0271-cu132.lock.txt` is the reference box's pinned environment (vLLM 0.27.1,
   torch 2.13 + CUDA 13.2, FlashInfer 0.6.16). Build it beside the packs, never inside Hermes's own venv:

       python3.12 -m venv ~/.krew/envs/vllm
       ~/.krew/envs/vllm/bin/pip install -r ~/krew/engines/vllm-0271-cu132.lock.txt

   (`KREW_VLLM_ENV` names another venv. The reference box's `ops` has run vLLM 0.30.0 from a second environment since
   2026-10-07, a canary; `OPS_VLLM_PREFIX=0.30` accepts it. `crew` stays on 0.27.1.)
2. **The weights.** `huggingface-cli download unsloth/Qwen3.8-27B-NVFP4 --local-dir "$HF_HOME/hub-local/Qwen3.8-27B-NVFP4-unsloth"`
3. **The settings.** `cp ~/krew/config/engines.example.env ~/.krew/engines.env` and read it: the values are the
   reference box's. The KV pins are bytes and are never 0 (a 0 lets vLLM take a VRAM fraction and the second engine
   cannot load). `engines/chat-templates/` holds the chat template passed to vLLM (Apache-2.0, see SOURCE.txt).
4. **The units.** `python3 scripts/krew-install.py --units` renders `krew-engine@.service`; then

       systemctl --user daemon-reload
       systemctl --user enable --now krew-engine@ops krew-engine@crew

   Each engine is wrapped by `engines/vllm_alive.py`, so an engine that dies inside vLLM exits non-zero and
   `Restart=on-failure` brings it back (vLLM's API server once swallowed a dead engine and exited 0).
5. **Point Hermes at them.** `scripts/krew-install.py init --endpoint http://127.0.0.1:8005/v1 --model crew`; the
   talking profile's config names `http://127.0.0.1:8000/v1` and `ops` (see the README's Wall section).

Parking: `beast` or `render` in the GPU mode flag parks `ops`; a unit named in `/tmp/krew-train-hold` (`ops.service` or
`krew-engine@<name>.service`) is parked. The flag is the one the mode switch writes, `crew/scripts/krew-mode`
(`~/.hermes/krew-gpu-mode`, krew.yaml `gpu.gpu_mode_file`; `$KREW_HOME/gpu-mode` is still read when that file is absent).
The keeper, the janitor and the hold sweep write those files; `start-engine.sh` only reads them.

## What else runs on the reference box, and which pack provides it

Read on the live box 2026-10-08. "studio-only" = part of the authoring studio that no pack ships, by design.

| On the box | What it is | Crissa pack | krew |
|---|---|---|---|
| `ops`, `crew` | the two brains above | | **this folder** |
| the Wall (conductor :8100, ASCII wall, Control Panel :8099/panel) | her picture, the screen, the panel | `scripts/zoeyos.py` children `player`, `wall`, `panel` | panel's krew section |
| the voice (Chatterbox Turbo :7789) | her spoken voice on a GPU box | voice tier `turbo` (`modules/voice`) | |
| the announcer (Audio8 TTS :8024) | the board's announcer | child `audio8`, tier `audio8` | `team_announce` → `/v1/announce` |
| the ear (Audio8 ASR :8096, rail-stt :8190, ear-bridge :8194) | speech in, the turn | children `asr`, `stt`, `ear` | |
| presence + Secure (rail-presence :8191), the camera (rail-camera :8192), the face check | who is in the room | children `presence`, `oracle` (camera, face, door) | |
| Init coach (enroll-panel :8196) | first-boot enrolment | Init (`lib/zoeyos_init.py`) | |
| aligner (rail-align :8195) | word timing for the mouth | child `aligner` (optional) | |
| face-shell :8088 | expression surface | inside the player | |
| hermes-gateway :8642, hermes-dashboard :9119 | the one host gateway, the board UI | | `krew-install.py init` |
| the eight profiles, SOULs, skills, plugins, rails (T0) | the crew | | `krew-install.py init` |
| kanban-loop, board gate, hold sweep, keeper, janitor, restart window, window ticker, M&S nightly | the patrols | | units + `--enable` (opt-in) |
| Comfy :8188 (Render mode) | local stills and video | mint client only (link out) | `krew-render` plugin, `start-comfyui.sh` |
| Hindsight :8888 | memory | | README step 5 (optional) |
| Searxng :8080 | the crew's search | | services list row |
| cua-driver, Crissa's private screen | hands on a desktop | | `krew-hands` |
| zoey-tool-watch, decide-sheet watch, wall-tab probe, beam-mic probe | report-only watchers | | `krew-tool-watch`, `krew-wall-tab-probe` |
| Composer, Clip Doctor, Lila's bench, pose map, Takes (:8199, :8212, :8213, :8198, :8101) | the authoring studio and the second face | studio-only (store link out) | |
| the marketing site, the tunnel, the LAN proxy | publishing | studio-only | |
| ws-shadow, a11y-flip-watch, renderer-mem probe, places watch | the studio's own instruments | studio-only | |

What a buyer does not get, by design: the second face (Lila) and the full Crissa library (the packs ship the public
loop sets), the Composer, and the studio's own research instruments.

## Every unit file krew ships, and what it stands for

Read on the reference box 2026-10-09 (the parity build). Every `.service`, `.timer` and `.path` under `crew/units/`,
`units/` and `systemd/user/` is one row; `tests/test_zoey_kit.py` holds this table to the files. "Crissa child" would
name a row the Wall's launcher starts instead; there is none: the units krew ships are all krew's own, and the
Crissa services the reference box ran beside them (the player, the ear, presence, the voice tiers, the oracle) are
the pack's children (`scripts/zoeyos.py`), reached through the player, listed in `services.json`, never a krew unit.

| Unit file | Description | Stood for on the reference box | Runs as |
|---|---|---|---|
| `crew/units/krew-announce-drain.service` | krew PA announce drain (one line at a time, through the Wall's player) | ref-announce-drain (the PA drain; spoke through the reference box's own announcer) | krew |
| `crew/units/krew-architect-collect.service` | krew architect collect bundle (no LLM) | ref-architect-collect | krew |
| `crew/units/krew-architect-collect.timer` | 06:50 architect collect | ref-architect-collect | krew |
| `crew/units/krew-architect-logs.service` | krew Steward logs + archive + M&S snapshot | steward-architect-logs | krew |
| `crew/units/krew-architect-stack.service` | krew Scout stack-vs-SOTA note | steward-architect-stack | krew |
| `crew/units/krew-architect-world.service` | krew Scout local-AI world note | steward-architect-world | krew |
| `crew/units/krew-beam-mic-probe.service` | krew beam-mic probe: the beam mic's source present + beam angle fresh, one card per 5-min fault, report only | beam-mic-probe | krew, opt-in, report-only |
| `crew/units/krew-beam-mic-probe.timer` | krew beam-mic probe every minute | beam-mic-probe | krew, opt-in, report-only |
| `crew/units/krew-board-roll.service` | krew-board-roll — make today's dated Daily the fleet's current board, carry open cards | ref-board-roll (no timer; ExecCondition = the drained-board gate) | krew, opt-in |
| `crew/units/krew-daily.service` | krew Daily (the one report, built as the off-hours close) | ref-daily (the one report) | krew |
| `crew/units/krew-docs-index-night.service` | krew nightly docs-index rebuild (incremental, CPU-capped scope) | ref-index nightly rebuild | krew |
| `crew/units/krew-dream-night.service` | krew dream night (day roll, 3-day roll, Hindsight retain/consolidate/reflect) | hermes dream night | krew |
| `crew/units/krew-dream-reflect.service` | krew dream reflect (re-run the night's dream after the closer) | dream reflect | krew |
| `crew/units/krew-hindsight.service` | krew Hindsight memory API (Postgres store + local embeddings, loopback) | hindsight (the memory API; system unit on the reference box) | krew, opt-in |
| `crew/units/krew-hold-sweep.service` | krew board gate + GPU hold sweep | ref-hold-sweep (the board gate + GPU hold sweep) | krew |
| `crew/units/krew-hold-sweep.timer` | krew board gate every 2 min | ref-hold-sweep (the board gate + GPU hold sweep) | krew |
| `crew/units/krew-improve-file.service` | krew improve-file: records the idea ledger, files only the team's agreed tier under a quorum, cap 2 | improve-file | krew |
| `crew/units/krew-improvement-lessons.service` | krew improvement lessons: the cooldown list and last verdicts for tonight's Scout and voters | improvement-lessons | krew |
| `crew/units/krew-improvement-outcomes.service` | krew improvement outcomes: did each agreed, landed improvement move the KPI it named? | improvement-outcomes | krew |
| `crew/units/krew-kanban-chatter.service` | krew kanban chatter (board events to the log roll) | kanban-chatter | krew |
| `crew/units/krew-kanban-chatter.timer` | krew kanban chatter every 60 s | kanban-chatter | krew |
| `crew/units/krew-kanban-day-close.service` | krew kanban day-close (archive closed done cards, gc the board) | kanban-day-close | krew |
| `crew/units/krew-kanban-loop.service` | krew kanban-loop (green a try into triage; reframe a repeated failure; retreat to Crissa) | kanban-loop | krew |
| `crew/units/krew-kanban-loop.timer` | krew kanban-loop tick every 20 min | kanban-loop | krew |
| `crew/units/krew-keeper.service` | krew keeper (ear reopen + lane watchdog + config assert) | ref-keeper (the lane watchdog) | krew |
| `crew/units/krew-kit-reapply.path` | watch the hermes tree for `hermes update` (off switch: ~/.hermes/krew-kit-reapply.off) | ref-kit-reapply (.path + .service) | krew |
| `crew/units/krew-kit-reapply.service` | krew kit reapply (after the hermes tree moves, put the kit back) | ref-kit-reapply (.path + .service) | krew |
| `crew/units/krew-morning-brief.service` | krew Crissa morning brief of yesterday + inbox INDEX | zoey-morning-brief | krew |
| `crew/units/krew-ops-efficiency.service` | krew Ada weekly ops-efficiency narrative | ada-ops-efficiency | krew |
| `crew/units/krew-ops-efficiency.timer` | Sunday 09:00 Ada ops-efficiency report | ada-ops-efficiency | krew |
| `crew/units/krew-pack-drift-check.service` | krew check that the committed packs match what is running | pack-drift-check | krew |
| `crew/units/krew-places-watch.service` | krew places watch (places map + docs index `places` source, on folder change) | places-watch | krew |
| `crew/units/krew-render-collect.service` | krew render collect — land finished ComfyUI takes, score ends, unblock the card | render-collect | krew |
| `crew/units/krew-render-collect.timer` | krew render collect every 2 minutes | render-collect | krew |
| `crew/units/krew-render-window.service` | krew render window — hold render cards by day, batch them into the off-hours for Ada to judge | render-window | krew |
| `crew/units/krew-render-window.timer` | krew render window tick every 2 min (off switch: ~/.hermes/render-window.off) | render-window | krew |
| `crew/units/krew-t0-unit-control-review.service` | List the T0 window rule's report-only hits for the owner's enforce decision - read only | t0-unit-control-review (a dated one shot) | krew, read-only |
| `crew/units/krew-t0-unit-control-review.timer` | Weekly T0 window report-only review (one shot on the reference install) | t0-unit-control-review (a dated one shot) | krew, read-only |
| `crew/units/krew-t0-zoey-kit-review.service` | List the T0 zoey-kit rule's report-only hits for the owner's enforce decision - read only | t0-zoey-kit-review (a dated one shot) | krew, read-only |
| `crew/units/krew-t0-zoey-kit-review.timer` | Weekly T0 zoey-kit report-only review (one shot on the reference install) | t0-zoey-kit-review (a dated one shot) | krew, read-only |
| `crew/units/krew-team-eval.service` | krew team eval: each role scores tonight's research picks; the ranker aggregates | team-eval | krew |
| `crew/units/krew-team-research-catchup.service` | krew team research catch-up | team-research-catchup | krew |
| `crew/units/krew-team-research-catchup.timer` | 02:25 team research catch-up | team-research-catchup | krew |
| `crew/units/krew-team-research.service` | krew Scout team research (head of the improvement chain) | team-research | krew |
| `crew/units/krew-team-sources.service` | krew monthly scan of the team research sources | team-sources | krew |
| `crew/units/krew-team-sources.timer` | Monthly team source scan (1st 03:00) | team-sources | krew |
| `crew/units/krew-tmp-worktree-reap.service` | krew tmp-worktree-reap (report-only by default) | tmp-worktree-reap | krew, report-only |
| `crew/units/krew-tmp-worktree-reap.timer` | krew tmp-worktree-reap every 30 min | tmp-worktree-reap | krew, report-only |
| `crew/units/krew-tool-watch.service` | krew tool-watch: the front door's tool-call watch: her calls, failures and retries; one card when her failure rate jumps (report-only until KREW_TOOL_WATCH_CARDS=1) | zoey-tool-watch | krew, report-only |
| `crew/units/krew-tool-watch.timer` | krew tool-watch every 5min (off switch: $KREW_HOME/krew-tool-watch.off) | zoey-tool-watch | krew, report-only |
| `crew/units/krew-wall-tab-probe.service` | krew wall-tab-probe: the Wall's page probe through its player: report only, never reloads (one card per dead episode with KREW_WALL_TAB_PROBE_CARDS=1) | wall-tab-probe (through the Wall's player) | krew, report-only |
| `crew/units/krew-wall-tab-probe.timer` | krew wall-tab-probe every 2min (off switch: $KREW_HOME/krew-wall-tab-probe.off) | wall-tab-probe (through the Wall's player) | krew, report-only |
| `crew/units/steward-sweep.service` | krew steward sweep (stewardship card with stuck-card diagnostics) | steward-sweep | krew, opt-in |
| `crew/units/steward-sweep.timer` | krew steward sweep, hourly (catches up after downtime) | steward-sweep | krew, opt-in |
| `units/krew-engine@.service` | krew engine %i (vLLM, served name %i) | ops.service / crew.service (the two brains) | krew |
| `units/krew-ms-nightly.service` | M&S nightly: map + report + decisions ledger (report only until upgrades: true in {krew_home}/ms.yaml) | ms-trail nightly | krew, opt-in (the ticker runs it) |
| `units/krew-ms-nightly.timer` | M&S nightly at 22:30 (only when off_hours is not used) | ms-trail nightly | krew, opt-in (the ticker runs it) |
| `units/krew-ms-survey.service` | M&S survey: re-read the envs, report drift between nightly runs, refresh manifests (read-only) | ms survey | krew |
| `units/krew-ms-survey.timer` | M&S survey every 2h | ms survey | krew |
| `units/krew-hands-step-cap-remeasure.service` | Re-measure Crissa's hands step cap ("step caps re-measure in 3 days") - report only | hands-step-cap-remeasure | krew, report-only |
| `units/krew-hands-step-cap-remeasure.timer` | One shot, 2026-10-03 09:30: re-measure Crissa's hands step cap after three days on the fixed tool schemas | hands-step-cap-remeasure | krew, one shot, opt-in |
| `units/krew-pyjwt-trail-confirm.service` | Confirm the nightly trail's security scan no longer lists PyJWT after the live-env install - report only (exit 0 gone, 1 still listed, 2 no fresh scan) | pyjwt-trail-confirm | krew, report-only |
| `units/krew-pyjwt-trail-confirm.timer` | One shot, 2026-10-01 06:00: did the nightly trail's security scan drop PyJWT after the live-env install | pyjwt-trail-confirm | krew, one shot, opt-in |
| `units/krew-restart-window-morning-report.service` | krew restart-window morning report: what last night's window restarted, held or left owed (read-only) | restart-window-morning-report (a dated one shot) | krew, read-only |
| `units/krew-restart-window-morning-report.timer` | krew restart-window morning report at 07:43 (the reference install's one-shot time; Persistent: runs at the next start if missed) | restart-window-morning-report (a dated one shot) | krew, read-only |
| `units/krew-restart-window.service` | krew restart window: one card in off_hours, the front door's ruling carried out and checked (OFF until enabled in {krew_home}/restart-window.json) | ref-restart-window | krew, OFF until enabled |
| `units/krew-restart-window.timer` | krew restart window tick every 5 min (off switches: enabled: false in {krew_home}/restart-window.json, {krew_home}/restart-window.off) | ref-restart-window | krew, OFF until enabled |
| `units/krew-units-refresh.service` | krew-units-refresh: the night's full unit-registry discovery (report only) | unit-registry discovery | krew, report-only |
| `units/krew-window-tick.service` | krew-window-tick: start the jobs in {krew_home}/window-schedule.json when off_hours (krew.yaml) open | ref-window-tick | krew |
| `units/krew-window-tick.timer` | krew-window-tick every 5 min: starts the night's jobs inside off_hours (off switch: {krew_home}/krew-window-tick.off) | ref-window-tick | krew |
| `systemd/user/krew-janitor-watch.service` | krew Janitor's watcher: tick fresh, timer active, last run ok; one card per episode (off switch: ~/.hermes/janitor-watch.off) | janitor-watch | krew |
| `systemd/user/krew-janitor-watch.timer` | krew Janitor's watcher every 10 min (off switch: ~/.hermes/janitor-watch.off) | janitor-watch | krew |
| `systemd/user/krew-janitor-weekly.service` | krew Janitor weekly report (uptime, probe uptime, suspect config, log growth) | ref-janitor weekly report | krew |
| `systemd/user/krew-janitor-weekly.timer` | krew Janitor weekly report, Monday 08:30 | ref-janitor weekly report | krew |
| `systemd/user/krew-janitor.service` | krew Janitor: one tick (unit health, crash loops, probes, metrics; restarts only when armed) | ref-janitor | krew |
| `systemd/user/krew-janitor.timer` | krew Janitor every 5 minutes | ref-janitor | krew |
| `units/system/fleet-bootcheck.service` | fleet-bootcheck: verify services returned after a deliberate reboot | fleet-bootcheck (system) | krew, opt-in |
