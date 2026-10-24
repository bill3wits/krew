# krew skills (feature freeze, 2026-10-22)

Every skill in `skills/`, grouped by the package that ships it (`config/krew-packages.json` and
`config/krew-packages.d/*.json`; `tests/test_krew_packages.py` fails on a skill outside a package). 51 skills.
`scripts/krew-install.py` places them in the Hermes skills folder; `krew-kitting` keeps each profile's index to its
own allowlist plus krew's skills, and `crew/config/profile-kit.json` names which profile carries which. The one-line
descriptions are each skill's own frontmatter, first sentence.

Skill and profile names keep their source spelling (`zoey-*` is the front door, Crissa). In the Crissa 1.0 kit the
krew archive is renamed on the way out (`zoey` and `zoeyos` become `crissa`), so a kit reader sees `crissa-*`.

## Core (any crew) (`skills`)

| skill | what it is for |
|---|---|
| `adversarial-gate` | Single-stream or dual hot/cold adversarial review before shipping design or code. |
| `krew-reframe` | Step up a level before the third try. Write the frame that lets the planner cut a different plan, for any crew member. |
| `ms-maintenance` | Keep this machine's Python environments current and safe with M&S - read its nightly report, record decisions, register an env. |

## Maps: one lookup, then the skill to load (`skills-maps`)

| skill | what it is for |
|---|---|
| `bash-map` | One lookup, then the skill to load and act on the host. |
| `lila-map` | One lookup, then the skill to load and design or research. |
| `vera-map` | One lookup, then the review skill and the board verbs. |

## The board loop (`skills-crew-loop`)

| skill | what it is for |
|---|---|
| `kanban-terminal-watchdog` | Watchdog wrapper enforcing kanban terminal-signal protocol (complete/block) on any worker process; catches rc=0 no-signal exits. |
| `steward-sweep` | Hourly board sweep — archive, stop churn, escalate to Crissa not the user. |

## The crew: orchestration, research, reports, upkeep (`skills-crew-crew`)

| skill | what it is for |
|---|---|
| `ada-map` | One lookup — the skill to load, then act with your board tools. |
| `ada-ops-efficiency` | Sunday ops-efficiency narrative. KPI-tied action items only. |
| `ada-orchestrate` | How Ada runs the board — crystal, fan-out, T0, Vera, diagnostics. |
| `krew-deliverable` | Land work in the deliverables folder (`{{deliverables_dir}}`) as markdown plus an HTML sibling the user can open. |
| `krew-judge` | The Judge protocol — read one diagnosed card, answer with ONE `VERB:` line on the source card, complete your own. |
| `ops-toks` | Measure ops/crew tok/s from Control Panel :8086 (Engine journal line) and record it for Crissa's report. |
| `scout-architect-stack` | Map today's local-AI world onto krew stack pieces; try/watch/ignore. |
| `scout-architect-world` | Daily local-AI world note — GitHub, models, Next New Thing, HN, r/LocalLLaMA. |
| `scout-map` | One lookup — notes and search. Never a kanban assignee. |
| `scout-one-off` | One-off look-up from Crissa (team_scout). |
| `scout-team-research` | Midnight TV Time revival — per-role improvement research, real sources only. |
| `scout-team-sources` | Monthly involved scan for new trusted research sources; self-healing list. |
| `steward-logs` | Daily log anomaly judgment, M&S snapshot, archive already done by script. |
| `steward-map` | One lookup — M&S / security / architect skill, then act. |
| `steward-spike` | Throwaway local-AI bench under spike rails. |
| `team-eval` | Score tonight's research picks for YOUR job (0–3 each, one line why) so the krew can rank them; items that help every role go to the top. |
| `zoey-big-push` | Big push — Crissa stays the voice, a consultant does the work, Vera reviews. |
| `zoey-spotcheck` | Spot-check the krew — sample what they shipped, read the judge's verdicts and the improvement round, keep your own account of who has earned trust, and tell the user what you found. |

## Renders and loops (`skills-crew-render`)

| skill | what it is for |
|---|---|
| `krew-bring-to-life` | The render-and-judge loop for a wall loop (submit, wait for the collector, judge with vision) and the mouth-tile intake a take needs. |
| `krew-loops` | Deliver a serviceable hub loop or flourish from one anchor still: the written words per loop class, the one picture, the gates every frame, automatic retakes inside a fixed price. Details below. |
| `krew-render` | Local Comfy stills and short video in Render occupancy. |
| `krew-vlm` | Look at a still or clip with vision. Lila, Bash, and Vera only — the renderers and reviewers. |
| `render-avatars` | Bring a Krew mate to life on the wall from one reference photo — bust shot, scene, hub plates, core loops, slides, flourishes, the talking harvest, intake, and how mouth tiles are chosen (the mouth classes). |
| `render-lane` | Working a render card. Render open and Comfy on :8188 means draw with image_generate and iterate. |
| `wan-local` | Local Wan-style stills and clip-from-still on krew-comfy, not DashScope. |
| `zoey-render-cycle` | ONLY while the krew is already in Render. |

## The Control Panel (`krew-panel-skill`)

| skill | what it is for |
|---|---|
| `krew-panel` | Read the machine from a card — unit lamps, journals, unit scripts, log lanes, security, health. |

## Glyph art and the live lap (`skills-crew-glyph`)

| skill | what it is for |
|---|---|
| `glyph-density` | Procedural glyph art (+ * # @ on black) for the room screen. |
| `lila-glyph-catalog` | Lila: curate the crew's glyph font categories and original ASCII seeds for Crissa's Wall. |
| `picture-to-glyph` | Any picture → wall ASCII. team_wall_photo for a face (crops to it, so it is recognised); img2glyph.py for a piece in the crew's glyph style. |
| `text-to-ascii` | Text → glyph ASCII for the Wall's glass. Lightweight (LLM mono + pyfiglet). |
| `xspaces-live` | Run the live room lap. The user's own recorded questions play on the speakers, Crissa answers, and the announcer says Pass or Fail from the ledger. |

## Crissa's own (the front door) (`skills-crew-team`)

| skill | what it is for |
|---|---|
| `zoey-auth` | Match the user-auth-only stance from krew-mode.json. |
| `zoey-cua` | Drive web pages in your own browser and native windows on your own screen like an operator — look, act, verify. |
| `zoey-list` | Crissa's list on the Control Panel. Ask the user yes/no, or keep a task with steps. |
| `zoey-look` | Look at the room and at clips. team_look aims the camera. |
| `zoey-map` | One lookup — words → the skill to load and the act. |
| `zoey-marketing` | Marketing Crissa with the owner, live: copy, reports, the owner's summary, renders, X posts, Google Ads. |
| `zoey-morning` | Morning report on the glass while she speaks: M&S, architecture, Ada efficiency, yesterday's completions, curated improvement ideas. |
| `zoey-needs` | Crissa decides software calls; NEEDS HUMAN is physical; the user (the king) only if she can ask one simple question. |
| `zoey-report` | Write a finding as a beautiful HTML report on the glass — show_me report. |
| `zoey-surface-art` | Publish roll ASCII and coffee on VoiceOnly $ZOEYOS_PLAYER/v1/screen. |
| `zoey-wall` | Put pages and notes on Crissa's Wall. |

## krew-loops: a serviceable loop from one anchor (added 2026-10-20, gates 2026-10-21)

`skills/krew-loops/SKILL.md`, in the `skills-crew-render` package since d7f0cf7. The goal it serves: the crew
delivers hub loops and flourishes the mouth painter can speak on all day, with no human until the owner's eye.

- **One input.** One anchor still per hub, QC'd before any render (one frontal face, lips closed, face 0.30-0.40 of
  the frame height, no hand near the mouth), cut to the output's exact aspect.
- **Written words per class.** The words a provider is sent come from a spec per loop class (listen, work, work-up,
  multitask, scroll, and the flourishes: nod-settle, hair-tuck, laugh-held, earring-touch, smile-blink,
  hair-clump-up/-down). Never hand-written, never a buyer prompt; the loader refuses words that freeze her or carry
  no life.
- **Routes.** Local LTX 2.5 first/last-frame on ComfyUI, Wan hosted first/last-frame, Grok Imagine; every route
  starts and ends on the anchor. A hosted call needs the spend on the card and the owner's go.
- **Gates, every frame, by machine.** Head ceiling against the anchor's pose (interim pitch 8 / yaw 12 / roll 10),
  a floor of life (real head travel, a blink, motion), the ends against the anchor and the loop's own seam, a still
  closed mouth, identity against the portrait, a locked camera, the asked beats, and for a talking flourish a single
  mouth-cover window of at most 1.0 s.
- **The 2026-10-21 bake added two gates.** The seam is also cut against the hub loop's own first frame (the anchor
  PNG can sit a few pixels and grey levels off the loop it joins), and "mouth covered" means anything over the lips,
  a strand of hair included, not hands only.
- **Retakes.** A failed gate retakes the same words and picture inside the spec's allowance; past it the clip is held
  with its numbers, never shipped. A passing take carries its score, compliance and occlusion sidecars and goes
  through the normal intake; the numbers go on the card, never "looks good".

Where the runnable half lives (checked against the Crissa source at the freeze): the loop verb is Crissa's optional
render module (`zoeyos-render.py loop`, off until `ZOEYOS_RENDER_LOOPS=1`); the class specs it loads cover
`listen-loop`, `scroll-loop`, `transition-up` and the seven flourishes; the per-frame gates are Crissa's render-loop
QC. The skill also names `work-loop`, `work-loop-up` and `work-loop-multitask` specs and a `modules/render/specs/`
folder, which the source does not carry yet. krew ships the skill; without Crissa's render module it is a written
method, not a runnable check.

## Removed or retired

| piece | when | why | what replaced it |
|---|---|---|---|
| `composer-coach` (front door skill) | 2026-10-09 | the Composer is studio-only and never ships | none in krew |
| `demo-rounds` (front door skill) | 2026-10-09 | the studio's own demo tape | none (the live lap is Bash's `xspaces-live`) |
| Three reference-named skills | 2026-10-09 | names of the machine krew came from | `krew-deliverable`, `krew-panel`, `krew-reframe` |
| `/deliverables` (a docker path) in skills | 2026-10-08 | not a path on any user's machine | `{{deliverables_dir}}` |
| flux2 as the render skills' default | 2026-10-08 | non-commercial weights | the first commercial-OK still (qwen-image-2512, then sdxl); flux2 and LTX stay opt-in recipes |
| Loop words written by hand per take | 2026-10-20 | a 2026-10-15 set froze the head ("head steady") | `krew-loops`' per-class specs |
