# Crissa's kit: what the front door runs, and where each piece came from

The owner's order for the parity build (2026-10-09): a clean install of the packs must be indistinguishable from
the reference box, private data and personal paths aside, by extraction rather than rewrite. This file is the
front door's half of that: every piece the reference install's `crissa` profile runs, where krew ships it, and what
was changed on the way. `crew/config/profile-kit.json` is the one list the installers read; `tests/test_crissa_kit.py`
holds it to this page.

## Toolsets

Her config (`config/config.example.yaml`, the `#crissa` lines init uncomments): `[hands, skill_read, crissa]` on
the CLI surface, with `agent.disabled_toolsets: [terminal, code_execution, desktop_ui, project]`, a BLOCK list, so
cron jobs and delegated children lose a shell and a code runner too. The reference profile lists the full set
(clarify, file, memory, session_search, web, search, vision, video, team, browser, computer_use, skills) and
subtracts the same four; krew keeps the subtraction and lets the hands child carry the full kit
(`crew/config/crissa-hands.json`, `crew/scripts/crissa-hands.py`).

## Hooks (`profile-kit.json` → `hooks`, written by `krew-crew-install.py --profiles`)

| hook | matcher | on crissa | from |
|---|---|---|---|
| `review-route` | `^kanban_request_review$` | yes | the reference's review-route script, lifted earlier |
| `tier0` | `terminal\|execute_code\|write_file\|patch` + `browser` for crissa | yes, **`fail_closed: true`** | `rails/tier0_gate.py` + `config/tier0.example.yaml` (the crissa-kit rules: no shell, a write BLOCK list); the same spec `rails/krew_hooks.py` writes, so the two installers agree |
| `crissa-tool-callout` | the reference's matcher | yes | `crew/scripts/crissa-tool-callout.py` (below) |
| `tool-count` | every call | yes | lifted earlier |

A hook with `path` runs that file under `$KREW_HOME` directly: the gate is never behind a runner or an `.off`
switch. `fail_closed` is carried into the spec; `krew-pin-check` fails a T0 hook without it.

## The Decide watch (`profile-kit.json` → `crons`, `krew-crew-install.py --crons`)

`needs-crissa-watch`: every 10 minutes, `--monitor-script crissa-needs-watch.py`, the prompt pinned from the script's
`CRON_PROMPT` by its own `install_cron_prompt` (the reference's rule: the job and the script never drift). The
monitor reads presence through the Wall's player (`/v1/rail/presence/v1/presence`), the board read-only, Crissa's
list under `$KREW_HOME/team/list`, and writes its heartbeat to `$KREW_HOME/crissa-decide-watch.beat`. The owner's
id and spoken name come from `owner:` in `krew.yaml` or `KREW_OWNER_ID` / `KREW_OWNER_NAME`; the reference read
them from its identity store, which does not ship.

## The spoken tool callout

`crew/scripts/crissa-tool-callout.py`: one short English line when a profile starts a tool, deduped (8 s burst, 12 s
same phrase or tool, 20 s memory), queued behind whatever is on the air. The reference spoke through its own
speech script and gate files; krew speaks through the Wall's player `/v1/announce` (its lane waits for quiet) and
keeps the stamps, the queue and the roll under `$KREW_HOME`. `$KREW_HOME/voice.json` is the voice master
(`voice`, `tool_callout`); `$KREW_HOME/announce-mute` silences it.

## Skills (17, `profile-kit.json` → `skills`)

krew-deliverable, krew-panel, krew-reframe, crissa-auth, crissa-big-push, crissa-cua, crissa-list, crissa-look, crissa-map,
crissa-marketing, crissa-morning, crissa-needs, crissa-render-cycle, crissa-report, crissa-spotcheck, crissa-surface-art,
crissa-her-wall: the reference profile's skills folder, the three reference-named ones renamed (deliverable, panel, reframe). Two of the reference's
nineteen stay behind: `composer-coach` (the Composer is studio-only) and `demo-rounds` (the studio's demo
tape). `krew-install.py` places every skill in `$HERMES_HOME/skills`; `krew-crew-install.py --profiles` copies
hers into her profile.

## Plugins (`profile-kit.json` → `plugins`)

| reference | krew | notes |
|---|---|---|
| team-crissa | team-crissa | same 27 tools |
| the reference's hands plugin | krew-hands | hands + skill_read |
| the reference's kitting plugin | krew-kitting | |
| the reference's panel plugin | krew-panel | the `panel_*` tools |
| the reference's skill-preload plugin | krew-skill-preload | |
| the reference's Oracle tools plugin | **krew-oracle** | new in this build: presence through the player; the camera verbs need `oracle.presence_url` |
| the reference's streaming-voice plugin | **krew-tts** | new in this build: the Crissa voice tiers turbo → audio8 → pocket → kokoro as the `crissa` streaming provider |
| hindsight | hindsight (catalog) | `crew/hindsight/plugin-pin.json` pins repo + sha; `config.example.json` carries `auto_recall: false` and the recall tags |
| the reference's comfy / vlm / docs / render-card plugins | krew-comfy / krew-vlm / krew-docs / krew-render | krew-vlm gained the frames look (one frame a second through a clip's middle) |

## Her hands' ceiling

`crew/config/crissa-cua-manifest.yaml` → `$HERMES_HOME/profiles/crissa/cua-manifest.yaml`, named in her config's
`computer_use.capability_manifest` (init writes both). Version 3; her own screen's Chromium and desktop only.

## Voice in, voice out

`tts.streaming.provider: crissa` (krew-tts). `stt.providers.crissa.command: krew-stt-cli.py {input_path}`: the
Crissa ASR (`POST /stt`) for a voice note sent to her profile. The reference named a transcription script that no
longer existed on disk (its STT engine was retired), so this one is written for krew, not extracted.

## SOUL

`souls/crissa/SOUL.md` is the reference SOUL line for line (88 lines), with the owner as `{{owner}}`, the deliverables
folder as `{{deliverables_dir}}`, the glass and presence named by the Wall's player instead of ports, and
`krew_docs` for the docs tool.

## Names in the Crissa 1.0 kit (2026-10-22)

The profile stays `crissa` here, and her skills `crissa-*`. The krew archive inside the Crissa 1.0 kit is renamed by
Crissa's export (`crissa` and `crissa` become `crissa`), so the kit's copy of this page and of her files says
`crissa`. Nothing she says or shows uses the old name in either.

## Removed or retired

| piece | when | why | replaced by |
|---|---|---|---|
| `composer-coach` skill | 2026-10-09 | the Composer is studio-only | none |
| `demo-rounds` skill | 2026-10-09 | the studio's own demo tape | none (the live lap is Bash's `xspaces-live`) |
| the reference's transcription script | 2026-10-09 | its STT engine was retired on the reference box | `crew/scripts/krew-stt-cli.py` (Crissa's ASR) |
| her own gateway unit | 2026-10-08 | one Hermes host gateway serves every profile | `/p/crissa/v1` on the host gateway, her own key |
| a fixed `crissa` signature on board calls | 2026-10-11 | every profile signs as itself | `team_board` signs as the caller |
