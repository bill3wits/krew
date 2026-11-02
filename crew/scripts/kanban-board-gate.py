#!/usr/bin/env python3
"""Board-side kanban gates — verb wall + render hold. No Hermes tree hook.

History: written by Grok 2026-09-15 (REVIEW-20260915-CREW-EFFICIENCY-CLAUDE.md) and run live
from this path ever since, but never committed. Adopted into the kanban lane 2026-09-17
(Claude): the verb wall respects a release newer than the bad run, calls name their profile.
`_is_render_card` is imported by the render window — keep its signature.

Hold-sweep (2 min) runs this. Kill switches:
  KREW_RENDER_DISPATCH_GATE=0  skip the render-hold / missing-plate gate
  KREW_VERB_WALL=0             skip parking rc=0 no-verb cards
  KREW_ASSIGNEE_GATE=0         skip unknown-profile (assignee=worker) reclaim
  KREW_OWNERLESS_GATE=0        skip handing ownerless blocks back to the orchestrator
  KREW_OWNERLESS_BATCH=0       one worker per card (unblock each) instead of one batch card
  KREW_REVIEW_GATE=0           skip cutting Vera's review of a finished implement card
  KREW_BALANCE_GATE=0          skip levelling ready cards between the two host shells
  KREW_ADA_CAPS_SYNC=0         skip recording when Ada's caps on disk differ from what she runs
  KREW_SALAD_GATE=0            skip the salad detector (was the 5-min tripwire timer)

Salad (folded in 2026-09-18, the User "land"): the tripwire's detector, park mark and release run
here on the 2-min tick, through the tripwire module's own tested functions. With prefix
caching and MTP off on crew there is no cache to reset, so a burned card is marked and
released at once (up to its MAX_RELEASES, then it is the reframe loop's). The standalone
script keeps `--force` for a hand reset. A `crissa` assignee on a ready/todo card is swept
to Ada by the assignee gate (was kanban-crissa-sweep, 1 min).

Balance (the User 2026-09-18 "the whole point is to double throughput"): bash and steward are the
same host shell on two engines (bash on crew, steward on ops under TurboKrew). When one has
ready cards waiting and the other has a free per-profile slot, the oldest ready cards the
peer's kit can do move over (never a render / vision / web card to steward, never a cron / M&S
/ steward-skill card to bash; a card moves once). Ready only — a todo card behind a parent
runs on nobody. Standard occupancy only (Render sleeps steward).

Caps: Ada's dispatcher reads kanban.max_in_progress ONCE at gateway start. The gate records
what she runs with (~/.hermes/ada-dispatch-live.json) and, when the file says something else
(TurboKrew 6/3, back to 3/2, a Render 1/1 leftover), logs `caps: … restart owed` and writes
~/.hermes/ada-caps-pending — it does not restart her (the restart is a human's or the window's).

Review (the User 2026-09-18 "vera reviews"): a finished implement card (title verb Implement / Fix /
Wire / Land / Apply / … or `M&S land`, done by bash / steward / lila, not closed as superseded)
gets ONE `review: <title>` card to Vera, linked under it, with the completion summary and
what to check; the done card is marked `gate: review → <id>`. hermes' native review lane
(`request_review --reviewer vera`, status `review`) only opens from a RUNNING worker, so a
card already done needs this child. Vera's verdict: PASS completes the review; FAIL files one
fix card to the implementer (she does not implement) and completes the review with FAIL.

Ownerless (2026-09-18, the User: "the contra case to logic worked where it had an owner"):
a card blocked with no kind that nobody will revisit — a worker's GO/PASS/REJECT verdict
written as prose, Steward's "SUPERSEDED" in words, a reframe that was archived (not done),
a ruler's `later <date>` whose date has passed. Every mechanical path skips exactly this
class (kanban-loop reframes typed/framed/breaker parks; hermes' stuck-in-blocked rule is
read-only). After KREW_OWNERLESS_AFTER_MIN of no owner activity the card is handed back
to the orchestrator (60 min of no owner activity). Batched (the User 2026-09-18 "3 subs with 3 subs, one pass"): the cards stay
blocked, marked `batched in <id>`, and ONE `route: N ownerless blocks` card goes to Ada — she
reads them all, fans the artifact checks out to children, and writes ONE `VERB:` line per
card (kanban_comment on that card): `complete <result> | archive | park <why> | reassign
<profile> | crissa <question> | hands <what>`. Her worker cannot land the verb itself: hermes
scopes a dispatcher-spawned worker to its own card (`worker is scoped to task …; refusing
to mutate`, upstream #19534; the lean kit has no reassign at all), so the three batches of
2026-09-18 each ended in a verdict comment and a card still blocked (t_e76055c2 12:00,
t_781724c7 12:02). `gate_routed` lands the newest VERB line after the last ownerless mark
from the CLI (no scope) and marks the card `gate: routed → …`; a newer line lands again.
Kill KREW_ROUTED_VERBS=0. One open batch at a time. Unbatched mode (KREW_OWNERLESS_BATCH=0): comment,
reassign, unblock, one worker per card.
Activity is the block event, the last run and comments by an OWNER (ruler / assignee);
Steward's hourly "Sweep t_x: unchanged" and status beats are chatter and never keep a
card owned. A second untyped block after that lands in hermes `triage` on its own
(BLOCK_RECURRENCE_LIMIT=2: None == None is the same kind); only a re-block that reads as
ownerless again is retreated to Crissa, reason first, and only when the unblock lands ready.

A newly minted render card can still spawn once before this tick (Ada 120 s).
That is accepted: it kills the 6× respawn class, not the first look.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import krew_cut as _kc  # noqa: E402  (coach, don't cap: a cut says so and where the rest is; `cut` is a reframe id below)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "janitor"))  # krew: gate_hold / gate_strikes live with the janitor

HERMES = Path(os.environ.get("HERMES_HOME") or KH.hermes_home())
HERMES_BIN = os.environ.get("KREW_HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
PROFILE = os.environ.get("KREW_BOARD_GATE_PROFILE", "ada")
HOLD = Path(os.environ.get("KREW_HERMES") or KH.hermes_home()) / "krew-render-hold"
LOG = Path(os.environ.get("KREW_BOARD_GATE_LOG", str(HERMES / "logs" / "kanban-board-gate.log")))

RENDER_GATE_ON = os.environ.get("KREW_RENDER_DISPATCH_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
VERB_WALL_ON = os.environ.get("KREW_VERB_WALL", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
ASSIGNEE_GATE_ON = os.environ.get("KREW_ASSIGNEE_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
OWNERLESS_ON = os.environ.get("KREW_OWNERLESS_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# 60 (was 180, the User 2026-09-18 "trim that 3 hours"): under the batch the card stays blocked,
# so batching early costs one read, not a respawn.
OWNERLESS_AFTER_MIN = int(os.environ.get("KREW_OWNERLESS_AFTER_MIN", "60"))
BATCH_ON = os.environ.get("KREW_OWNERLESS_BATCH", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
OWNERLESS_MAX = int(os.environ.get("KREW_OWNERLESS_MAX", "8" if BATCH_ON else "3"))
REVIEW_ON = os.environ.get("KREW_REVIEW_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
REVIEW_MAX = int(os.environ.get("KREW_REVIEW_MAX", "3"))
REVIEW_LOOKBACK_H = float(os.environ.get("KREW_REVIEW_LOOKBACK_H", "6"))
REVIEWER = os.environ.get("KREW_REVIEWER", "vera")
BALANCE_ON = os.environ.get("KREW_BALANCE_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
BALANCE_MAX = int(os.environ.get("KREW_BALANCE_MAX", "3"))
BALANCE_AGE_MIN = int(os.environ.get("KREW_BALANCE_AGE_MIN", "5"))
CAPS_SYNC_ON = os.environ.get("KREW_ADA_CAPS_SYNC", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
GPU_MODE = Path(os.environ.get("KREW_HERMES") or KH.hermes_home()) / "krew-gpu-mode"
ADA_GATEWAY = os.environ.get("KREW_DISPATCHER_UNIT", "hermes-gateway.service")
CREW_ENGINE_UNIT = os.environ.get("KREW_CREW_ENGINE_UNIT", "crew.service")
CAPS_LIVE = HERMES / "ada-dispatch-live.json"
CAPS_PENDING = HERMES / "ada-caps-pending"
SALAD_ON = os.environ.get("KREW_SALAD_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# A salad relapse has a reader again (Phase 1 of docs/KANBAN-SELF-HEALING-20260918.md, moved
# forward for the prefix-cache A/B night of 09-18): the in-gate detector never resets and the
# standalone tripwire is a no-op, so salad>0 files ONE card a day to the restart window's queue
# (Crissa rules; the restart is the window's or a hand's, never this gate's). Kill KREW_SALAD_RELAPSE_CARD=0.
SALAD_RELAPSE_ON = os.environ.get("KREW_SALAD_RELAPSE_CARD", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
RELAPSE_MARK = KH.hermes_home() / "crew-restart-owed"


def _on(env: str, default: str = "1") -> bool:
    return os.environ.get(env, default).strip().lower() not in ("0", "false", "no", "off")


# Phase 1 session 2 of docs/KANBAN-SELF-HEALING-20260918.md (the User 2026-09-18 "go ahead and land this").
WHOLE_THREAD_ON = _on("KREW_GATE_WHOLE_THREAD")          # 6a: marks are found anywhere in the thread
BATCH_RELEASE_ON = _on("KREW_OWNERLESS_BATCH_RELEASE")    # 6b: a stuck batch card is not an open slot
BATCH_MAX_H = float(os.environ.get("KREW_OWNERLESS_BATCH_MAX_H", "6"))
STRAGGLERS_ON = _on("KREW_SALAD_STRAGGLERS")              # 6c: stragglers by age, not by a reset that never comes
STRAGGLER_MIN = int(os.environ.get("SALAD_STRAGGLER_MIN", "10"))
TRIAGE_GUARD_ON = _on("KREW_GATE_TRIAGE_GUARD")           # 7: a second untyped block parks instead of routing to triage
DUP_GATE_ON = _on("KREW_DUP_GATE")                        # one idea, one card: the younger open twin is archived
DUP_MAX = int(os.environ.get("KREW_DUP_MAX", "3"))
DUP_MARK = "gate: dup of"
SKILLS_INHERIT_ON = _on("KREW_SKILLS_INHERIT")            # skills once per thread: a child takes its root's pins
SKILLS_INHERIT_MAX = int(os.environ.get("KREW_SKILLS_INHERIT_MAX", "5"))
SKILLS_FILTER_ON = _on("KREW_SKILLS_FILTER")              # strip foreign pins when a card changes hands
SKILLS_MARK = "gate: skills inherited"
# Phase 4 of docs/KANBAN-SELF-HEALING-20260918.md, landed 2026-09-22. `cause:` is a GRAMMAR:
# lib/kanban_kpis counts the three prefixes and lib/kanban_liveness branches on `cause: verb`.
# Both were shipped; nothing ever wrote one, so all three KPIs read 0 while 76 % of failed
# runs were harness faults nobody classified. Kill KREW_CAUSE_CLASSIFIER=0.
# Law 1 of docs/KANBAN-SELF-HEALING-20260918.md is "every open card has exactly one reader AND
# A DEADLINE AFTER WHICH IT ESCALATES ONE RUNG". The first clause shipped and works (`none=[]`
# on every tick proves it). The second was never built: LIVENESS_ACT lands WAKES, and an
# OVERDUE row falls straight through the `wake_due` filter, so it is re-reported and nothing
# else — 139 identical lines on 2026-09-22, 427 on 09-21, always the same cards. That is the
# whole answer to the User's "why would anything fail 60 or 70 times?".
# Report-only until KREW_LIVENESS_ESCALATE=1, the way every gate in this house arms.
OVERDUE_MARK = "gate: overdue"
LIVENESS_ESCALATE = _on("KREW_LIVENESS_ESCALATE", "0")
STRIKES_PATH = HERMES / "kanban-gate-strikes.json"
OVERDUE_MAX = int(os.environ.get("KREW_OVERDUE_MAX", "6") or 6)
CAUSE_MARK = "cause:"
CAUSE_ON = _on("KREW_CAUSE_CLASSIFIER", "1")
CAUSE_MAX = int(os.environ.get("KREW_CAUSE_MAX", "12") or 12)
CAUSE_WINDOW_S = float(os.environ.get("KREW_CAUSE_WINDOW_S", "7200") or 7200)
# The per-card breaker (the User 09-28, kanban repair Stage 1), inside gate_cause: a card whose
# last KREW_CARD_BREAKER_RUNS runs inside KREW_CARD_BREAKER_WINDOW_H ended the SAME way
# (lib/kanban_cause.trips) is held ONCE for Crissa instead of another run. `report` (default) only
# logs and writes ~/.hermes/kanban-breaker.json; `act` blocks needs_input; `off`/0 does nothing.
# Replay 09-24..28: 28 cards would have tripped, 178 runs after the trip point (t_7ae5fc3a: 91).
# Arm only after a full day of report lines matches hand judgement (the User's rule).
BREAKER_MARK = "breaker:"
BREAKER_RUNS = int(os.environ.get("KREW_CARD_BREAKER_RUNS", "3") or 3)
BREAKER_WINDOW_H = float(os.environ.get("KREW_CARD_BREAKER_WINDOW_H", "6") or 6)
BREAKER_REPORT_NAME = "kanban-breaker.json"   # under HERMES, beside kanban-orphans.json
LAST_BREAKER = {"n": 0, "mode": "report"}


def breaker_mode() -> str:
    v = os.environ.get("KREW_CARD_BREAKER", "report").strip().lower()
    if v in ("0", "off", "false", "no"):
        return "off"
    return "act" if v in ("1", "on", "act", "true", "yes") else "report"
SKILLS_ADD = Path(__file__).resolve().parent / "kanban-skills-add.py"
TRIAGE_GUARD_MARK = "gate: parked, not blocked"
# Phase 2: the reader registry (lib/kanban_liveness). Report-only until KREW_LIVENESS_ACT=1.
LIVENESS_ON = _on("KREW_LIVENESS_GATE")
LIVENESS_ACT = _on("KREW_LIVENESS_ACT", "0")
LIVENESS_PATH = KH.hermes_home() / "kanban-liveness.json"
DEFAULT_WAKE_DAYS = int(os.environ.get("KREW_LIVENESS_DEFAULT_WAKE_D", "7"))
WAKE_MARK = "gate: wake"

# Dispatch targets Ada will actually spawn. `worker` is the Hermes test
# profile (fake pid 992001). `crissa` is the live gateway — swept to ada here too.
# `judge` (2026-09-18, <repo-worktree> landing 3): one-call verdict seat, created only by the loop
# and the gate, never routed by the decomposer. In the roster so its cards are not bounced to Ada.
ROSTER = {"ada", "bash", "lila", "vera", "steward", "judge"}
SCRATCH_TITLES = {"dbg", "dbg2", "iso-test"}
ASSIGNEE_MARK = "gate: unknown assignee"

# A mint, not a pack-drift path list. Do not match skill names as substrings
# of hermes-packs/skills/krew-render/SKILL.md (t_ab9e662c, 2026-09-15).
SKILL_RE = re.compile(r"(?:^|[\s,])(?:krew-render|render-avatars|pose-flourish|render-lane)(?:[\s,]|$)", re.I)
SUBMIT_RE = re.compile(r"(?:python3\s+\S*|scripts/)render-submit\.py", re.I)
GPU_RE = re.compile(r"krew_gpu_mode\s*\(\s*mode\s*=\s*['\"]render", re.I)
RECIPE_RE = re.compile(r"--recipe\s+(?:wan22-i2v|ltx23-flf2v|ltx25-flf2v|ltx23-nvfp4)", re.I)
PLATE_RE = re.compile(
    r"(?:~|/)[^\s\"'`]*/[^\s\"'`]+(?:hub|plate)[^\s\"'`]+\.(?:png|jpg|jpeg)",
    re.I,
)
HOLD_MARK = "gate: render hold"
VERB_MARK = "gate: verb wall"
ROUTED_ON = os.environ.get("KREW_ROUTED_VERBS", "1").strip().lower() not in (
    "0", "false", "no", "off")
ROUTED_MAX = int(os.environ.get("KREW_ROUTED_MAX", "8"))
GATE_AUTHOR = "kanban-board-gate"
OWNERLESS_MARK = "gate: ownerless"
RETREAT_MARK = "gate: ownerless retreat"
BATCH_TITLE = "route: {n} ownerless block{s}"
BATCH_TITLE_LIKE = "route: % ownerless block%"
BATCHED_RE = re.compile(r"batched in (t_[0-9a-f]{8})")
ROUTED_MARK = "gate: routed"
# Ada's verb on a routed card: one line of its own, last one wins. `reassign` takes a profile,
# the rest a free text (result / why / question / what).
VERB_RE = re.compile(
    r"^\s*(?:\*\*)?VERB:(?:\*\*)?\s*(complete|archive|park|reassign|crissa|hands)\b[\s:—–-]*(.*?)\s*$",
    re.I | re.M)
ROUTE_PROFILES = ("bash", "steward", "lila", "vera", "ada", "crissa")
REVIEW_MARK = "gate: review"
REVIEW_TITLE_RE = re.compile(
    r"^\s*(?:M&S land\b|(?:Implement|Fix|Wire|Refactor|Land|Apply|Add|Build|Harden|Patch|Update|Install|"
    r"Migrate|Enable|Deploy|Rewrite|Write|Create|Repair)\b)", re.I)
REVIEW_SKIP_TITLE_RE = re.compile(r"^\s*(?:review:|reframe:|route:|try:|acc-|dbg|smoke|steward sweep|verify|SMOKE)", re.I)
REVIEW_SKIP_RESULT_RE = re.compile(r"^\s*(?:superseded|rejected|routed|reframe resolved|nothing to|no deliverable|done via host shell)", re.I)
REVIEW_IMPLEMENTERS = {"bash", "steward", "lila"}
BALANCE_MARK = "gate: balance"
PEERS = (("bash", "steward"), ("steward", "bash"))
# kit a card needs that only ONE shell carries (title + body + skills)
BASH_ONLY_RE = re.compile(r"render|vision|vlm|viseme|pose-|flourish|mouth|wan-local|image_generate|video_generate|"
                          r"\bweb\b|browser|comfy", re.I)
STEWARD_ONLY_RE = re.compile(r"\bcron|steward-(?:sweep|logs|spike|map)|\bM&S\b|ms-trail|manifest|steward sweep", re.I)
RENDER_WINDOW_MARK = "render window"          # scripts/render-window.py MARK / HOLD_REASON
RULERS = set(tuple(os.environ.get("KREW_RULERS", "crissa,user,human,dashboard").split(",")))
SALAD_PARK_MARK, SALAD_RELEASE_MARK = "salad-park:", "salad-release:"
CUT_RE = re.compile(r"^\s*reframe-cut:\s*(t_[0-9a-f]{8})", re.I)
# Crissa's own words, or the BLOCKED:/UNBLOCK: line hermes wrote from them.
LATER_RE = re.compile(r"^\s*(?:(?:unblock|blocked)\b[\s:,.;!-]*)?later\b[\s:,-]*(\d{4}-\d{2}-\d{2})", re.I)
LATER_PARK_MARK = "gate: later park"
LATER_PARK_ON = os.environ.get("KREW_LATER_PARK_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off")

# LATER (2026-09-25, t_8c615508): a ruler's `later <date>` that is not a date is parked, not
# left parked. `hermes kanban schedule` parks a card; nothing unparks a scheduled card — the
# dispatcher only dispatches ready/todo — so an unparseable date parks a card until the end of
# the board's life (observed on t_801d9350: parked 2026-09-24 21:38, 0 unblocked events by
# 2026-09-25). If the date cannot be read, nothing is scheduled and the card is handed to the
# orchestrator with the offending line quoted — one read undoes the park. Kill
# KREW_LATER_PARK_GATE=0.


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line)


BOARD_PIN_ON = os.environ.get("KREW_BOARD_PIN", "1").strip() != "0"


def gate_board_pin(*, dry: bool = False) -> str:
    """`kanban/current` names the HOUSE board, and only krew-board-roll moves it (2026-09-27).

    A crew probe (t_4602219a) ran `hermes kanban boards create … --switch` ten times from
    16:58, so `current` named a one-card scratch board and every reader that follows it —
    this gate, kanban-loop, the tripwire, KPIs, the render window — worked an empty board
    while 113 cards sat on the real one (kanban-loop: 108 open at 16:51, 6 at 17:11). The
    roll's own record `kanban/krew-today` is the pin: a `current` naming anything else is put
    back (atomic replace) and logged. Returns the slug it moved off, else "". A pin naming no
    board is never followed. Kill KREW_BOARD_PIN=0."""
    kdir = HERMES / "kanban"
    try:
        pin = (kdir / "krew-today").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not pin or "/" in pin or not (kdir / "boards" / pin / "kanban.db").is_file():
        return ""
    try:
        cur = (kdir / "current").read_text(encoding="utf-8").strip()
    except OSError:
        cur = ""
    if cur == pin:
        return ""
    moved = cur or "(none)"
    if not dry:
        tmp = kdir / f".current.pin-{os.getpid()}"
        try:
            tmp.write_text(pin + "\n", encoding="utf-8")
            os.replace(tmp, kdir / "current")
        except OSError as e:
            log(f"board_pin: could not restore {pin} over {moved}: {e}"[:200])
            return ""
    log(f"board_pin: current named {moved}, not the krew board — {'would restore' if dry else 'restored'} {pin}")
    return moved


def board_db() -> Path | None:
    try:
        slug = (HERMES / "kanban" / "current").read_text().strip()
    except OSError:
        return None
    p = HERMES / "kanban" / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def kanban(*args: str, dry: bool = False) -> tuple[int, str]:
    if dry:
        return 0, "dry-run " + " ".join(args)
    try:
        # House rule: every kanban call names its profile (the bare CLI is `default` = Bridge),
        # and a timer's PATH has no ~/.local/bin (pose-freelance: 102 x "No such file: 'hermes'").
        r = subprocess.run(
            [HERMES_BIN, "-p", PROFILE, "kanban", *args],
            capture_output=True, text=True, timeout=60,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def _is_render_card(title: str, body: str, skills: str) -> bool:
    # A review is never a mint (2026-09-18 10:17: t_b22b50b4 `review: …` sat in the render
    # window because its body quotes the render job it reviews). render-window imports this.
    if re.match(r"\s*review:", title or "", re.I):
        return False
    if SKILL_RE.search(skills or ""):
        return True
    blob = " ".join((title or "", body or ""))
    if GPU_RE.search(blob) or RECIPE_RE.search(blob):
        return True
    # An invocation of the submitter is a mint — "python3 scripts/render-submit.py",
    # "python3 -m scripts.render-submit", "bash scripts/render-submit.py" — but a bare
    # mention of its name in prose is not (t_6698facd, 2026-09-22: a code-fix named
    # render-collect.py was held 4.5h on the name alone). The interpreter must sit
    # DIRECTLY before the path (one optional flag like -m allowed): "we shell
    # scripts/render-submit.py out of the way" is prose, not a command. The list
    # holds only unambiguous interpreter words — shell/exec/system are dropped
    # because they are common English prose words (adversarial gate, 2026-09-22).
    if re.search(r"(?i)\b(?:python3|bash|sh|subprocess|popen)\s+[`'\"]?(?:-\S+\s+){0,1}\S*render-submit", blob):
        return True
    if re.search(r"\b(image_generate|video_generate)\b", blob):
        return True
    if re.match(r"\s*(smoke|resubmit|mint)\b", title or "", re.I):
        return True
    # render-window title mint signals, moved in from render-window.py's
    # EXTRA_RENDER_RE (2026-09-22, t_122fe536) so the gate is the single
    # classifier. Title-only, on purpose: a prose "ComfyUI" in the BODY of a
    # code-fix card (t_6698facd) is not a mint — that body-match held the fix
    # 4.5h. A card TITLED "…via ComfyUI" or "Render freelance chain: …" is.
    # The comfy title name also needs a MINT verb in the title — a bare env-name
    # mention is not a mint (t_af53c50d, 2026-09-23: forensics TRIPLE titled
    # "…in comfyui env" was parked in the render window; t_b5edfa11, 2026-09-17
    # retired the body match for the same reason).
    if re.search(r"(?i)\bcomfy(?:ui)?\b", title or "") and re.search(
        r"(?i)\b(mint|render|still|video|recipe|submit|pose)\b", title or ""
    ):
        return True
    if re.search(r"(?i)^render\s+freelance\s+chain\b|\bhub\s+smoke\b", title or ""):
        return True
    return False


def _missing_plate(body: str) -> str | None:
    """Absolute-looking plate/hub path in the body that is not a file.

    Conservative: name must contain hub or plate. A card that *will write*
    display/.../hub-x.png is not a miss.
    """
    for raw in PLATE_RE.findall(body or ""):
        p = Path(raw).expanduser()
        if p.is_absolute() and not p.is_file():
            return str(p)
    return None


def _latest_runs(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    out = {}
    for row in conn.execute(
        "SELECT r.task_id, r.outcome, r.metadata, r.error, r.id, r.ended_at "
        "FROM task_runs r "
        "JOIN (SELECT task_id, MAX(id) AS mid FROM task_runs "
        "      WHERE ended_at IS NOT NULL GROUP BY task_id) x "
        "ON r.id = x.mid"
    ):
        out[row["task_id"]] = row
    return out


def _released_since(conn: sqlite3.Connection, tid: str, row: sqlite3.Row) -> bool:
    """A deliberate release is newer than the bad run: leave the card alone.

    2026-09-17: the wall parked any ready card whose LATEST RUN was a no-verb exit, so an
    `unblock` by Crissa, Steward or the User was undone within one 2-min tick with no worker
    spawned (t_df1c04ca: unblocked 11:06:10, re-parked 11:07:07) — nobody could give a
    salad-parked card a second look. The kernel (patch 70) keeps the park sticky by
    itself; this wall must not also veto the one verb that releases it."""
    try:
        ev = conn.execute(
            "SELECT MAX(created_at) FROM task_events WHERE task_id = ? AND kind = 'unblocked'", (tid,)
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    released = ev[0] if ev else None
    ended = row["ended_at"] if "ended_at" in row.keys() else None
    return released is not None and (ended is None or float(released) >= float(ended))


def _is_protocol_violation(row: sqlite3.Row | None) -> bool:
    if row is None:
        return False
    if "protocol violation" in str(row["error"] or "").lower():
        return True
    raw = row["metadata"]
    if not raw:
        return False
    try:
        md = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False
    return bool(isinstance(md, dict) and md.get("protocol_violation"))


def _comments_have(conn: sqlite3.Connection, tid: str, mark: str) -> bool:
    for (body,) in conn.execute(
        "SELECT body FROM task_comments WHERE task_id = ? ORDER BY id DESC",
        (tid,),
    ):
        if mark in str(body or ""):
            return True
    return False


def _marked(conn: sqlite3.Connection, tid: str, mark: str, since: int = 0) -> bool:
    """6a: a gate mark anywhere in the thread (at/after `since` when given). The 12-comment
    window of `_comments_have` scrolled marks out on chatty cards (11 threads > 12 on 09-18)
    and the gate re-fired. Kill KREW_GATE_WHOLE_THREAD=0 (old window)."""
    if not WHOLE_THREAD_ON:
        return _comments_have(conn, tid, mark)
    try:
        rows = conn.execute(
            "SELECT body, created_at FROM task_comments WHERE task_id = ? ORDER BY id", (tid,)).fetchall()
    except sqlite3.OperationalError:
        rows = [(b, 0) for (b,) in conn.execute("SELECT body FROM task_comments WHERE task_id = ? ORDER BY id", (tid,))]
    return any(mark in str(b or "") and int(t or 0) >= since for b, t in rows)


def _would_route_to_triage(conn: sqlite3.Connection, tid: str) -> bool:
    """7: hermes block_task routes a card to TRIAGE when the same cause re-blocks it at
    block_recurrences >= 1 (BLOCK_RECURRENCE_LIMIT 2; an untyped block after an untyped block
    is "the same cause"). 13 such events in 48 h on 09-18, all from this gate's own untyped
    blocks; run_decompose then decomposed them. Kill KREW_GATE_TRIAGE_GUARD=0."""
    if not TRIAGE_GUARD_ON:
        return False
    try:
        r = conn.execute("SELECT block_kind, block_recurrences FROM tasks WHERE id = ?", (tid,)).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(r) and r[0] in (None, "") and int(r[1] or 0) >= 1


def _block_untyped(conn: sqlite3.Connection, tid: str, reason: str, mark: str) -> None:
    """The gate's untyped block, guarded: park (schedule) when a block would route to triage."""
    if _would_route_to_triage(conn, tid):
        log(f"triage guard {tid}: a block would route it to triage — parked instead")
        kanban("comment", tid, f"{TRIAGE_GUARD_MARK}: a second untyped block would have routed this card to "
               f"triage (hermes block_loop_detected); {mark} — release: unblock", "--author", "kanban-board-gate")
        kanban("schedule", tid, reason)
        return
    kanban("block", tid, reason)


def gate_render(conn: sqlite3.Connection, *, dry: bool = False) -> int:
    n = 0
    hold_on = HOLD.is_file()
    rows = list(conn.execute(
        "SELECT id, title, body, status, skills FROM tasks "
        "WHERE status IN ('ready', 'todo', 'blocked', 'scheduled')"
    ))
    for t in rows:
        tid, title, body, status, skills = t
        is_render = _is_render_card(title or "", body or "", skills or "")
        if hold_on and is_render and status in ("ready", "todo"):
            reason = "render hold — ~/.hermes/krew-render-hold is on; do not spawn. Lift: delete the file."
            log(f"block {tid} render-hold ({status})")
            if not dry:
                kanban("comment", tid, HOLD_MARK + " " + reason, "--author", "kanban-board-gate")
                _block_untyped(conn, tid, reason, HOLD_MARK)
            n += 1
            continue
        # release only OUR hold: the mark must be at/after this block (an old hold mark must not
        # release a later, different block); a hold the triage guard parked is `scheduled`
        if status in ("blocked", "scheduled") and _marked(conn, tid, HOLD_MARK, since=_block_ts(conn, tid) - 300) and (
            (not hold_on) or (not is_render)
        ) and (status == "blocked" or _marked(conn, tid, TRIAGE_GUARD_MARK)):
            why = "krew-render-hold gone" if not hold_on else "not a mint (pack-drift / prose)"
            log(f"unblock {tid} {why}")
            if not dry:
                kanban("unblock", tid, "--reason", why)
            n += 1
            continue
        if status in ("ready", "todo") and is_render:
            miss = _missing_plate(body or "")
            if miss:
                reason = f"missing plate {miss} — will not spawn until the file exists"
                log(f"block {tid} missing-plate {miss}")
                if not dry:
                    kanban("comment", tid, "gate: missing plate " + miss, "--author", "kanban-board-gate")
                    _block_untyped(conn, tid, reason, "gate: missing plate")
                n += 1
    return n


def _is_scratch(title: str, body: str) -> bool:
    t = (title or "").strip().lower()
    b = (body or "").strip()
    if t in SCRATCH_TITLES:
        return True
    return (not b) and len(t) <= 12


def gate_assignee(conn: sqlite3.Connection, *, dry: bool = False) -> int:
    """Ada dispatches whatever string is in assignee. `worker` is not a house
    profile — she will spawn `hermes -p worker` until the card leaves ready.
    Steward can comment; the dispatcher does not read comments. This is the
    hands: reassign to ada, or archive empty test stubs."""
    n = 0
    for t in conn.execute(
        "SELECT id, title, body, status, assignee FROM tasks "
        "WHERE status IN ('ready', 'todo')"
    ):
        tid, title, body, status, assignee = t
        who = (assignee or "").strip().lower()
        if not who or who in ROSTER:
            continue
        if _marked(conn, tid, ASSIGNEE_MARK):
            continue
        if who == "crissa":
            # was kanban-crissa-sweep (1 min): crissa is gateway :8652, not a worker; a User decision is
            # a needs_input block, not an assignment (Ada SOUL). If this fires, doctrine slipped.
            log(f"reassign {tid} crissa -> ada ({status}) doctrine slipped {title[:50]!r}")
            if not dry:
                kanban("comment", tid, f"{ASSIGNEE_MARK} `crissa` is the front door (a gateway), not a kanban worker. A User "
                       "decision is a needs_input block, not an assignment (Ada SOUL). Reassigned to ada.",
                       "--author", GATE_AUTHOR)
                kanban("reassign", tid, "ada")
            n += 1
            continue
        if _is_scratch(title or "", body or ""):
            log(f"archive {tid} scratch assignee={who!r} {title!r}")
            if not dry:
                kanban(
                    "comment", tid,
                    f"{ASSIGNEE_MARK} `{who}` is not a krew profile "
                    f"(ada/bash/lila/vera/steward). Empty scratch — archiving.",
                    "--author", "kanban-board-gate",
                )
                kanban("archive", tid)
            n += 1
            continue
        log(f"reassign {tid} {who} -> ada ({status}) {title[:50]!r}")
        if not dry:
            kanban(
                "comment", tid,
                f"{ASSIGNEE_MARK} `{who}` is not a krew profile. Reassigned to ada.",
                "--author", "kanban-board-gate",
            )
            kanban("reassign", tid, "ada")
        n += 1
    return n


def gate_verb_wall(conn: sqlite3.Connection, *, dry: bool = False) -> int:
    n = 0
    latest = _latest_runs(conn)
    for t in conn.execute(
        "SELECT id, title, status FROM tasks WHERE status IN ('ready', 'todo')"
    ):
        tid, title, status = t
        row = latest.get(tid)
        if not _is_protocol_violation(row):
            continue
        if _marked(conn, tid, VERB_MARK, since=int(row["ended_at"] or 0) if "ended_at" in row.keys() else 0):
            continue
        if _released_since(conn, tid, row):
            continue
        reason = (
            "protocol violation: worker exited rc=0 without kanban_complete/"
            "kanban_block. First clean silent exit parks the card (verb wall)."
        )
        log(f"block {tid} verb-wall ({status}) {title[:60]!r}")
        if not dry:
            kanban("comment", tid, VERB_MARK + " " + reason, "--author", "kanban-board-gate")
            _block_untyped(conn, tid, reason, VERB_MARK)
        n += 1
    return n


def orchestrator() -> str:
    """kanban.orchestrator_profile from the dispatcher's config — no yaml import here."""
    try:
        text = (HERMES / "profiles" / PROFILE / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return "ada"
    m = re.search(r"^\s*orchestrator_profile:\s*([A-Za-z0-9_-]+)", text, re.M)
    return m.group(1).strip().lower() if m else "ada"


def _thread(conn: sqlite3.Connection, tid: str) -> list[tuple[str, str, int]]:
    """(author, body, created_at) oldest first — the WHOLE thread, never a LIMIT window."""
    try:
        rows = conn.execute(
            "SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id", (tid,)
        ).fetchall()
    except sqlite3.OperationalError:
        rows = [(None, b, 0) for (b,) in conn.execute(
            "SELECT body FROM task_comments WHERE task_id = ? ORDER BY id", (tid,))]
    return [(str(a or "").strip().lower(), str(b or ""), int(t or 0)) for a, b, t in rows]


def _last_pin_added_ts(conn: sqlite3.Connection, tid: str) -> int | None:
    """Wall-clock seconds of the newest `skills_added` task_event for the card —
    kanban-skills-add.py is the sanctioned pin writer (add, inherit, reframe cut,
    worker exit) and appends one event per write, so the event stream is the
    re-arm signal. None when the table is absent or has no such event."""
    try:
        r = conn.execute(
            "SELECT MAX(created_at) FROM task_events WHERE task_id = ? AND kind = 'skills_added'",
            (tid,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return int(r[0]) if r and r[0] else None


def _block_ts(conn: sqlite3.Connection, tid: str) -> int:
    try:
        r = conn.execute(
            "SELECT MAX(created_at) FROM task_events WHERE task_id = ? AND kind IN ('blocked', 'gave_up')",
            (tid,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(r[0] or 0) if r else 0


def _last_run_end(conn: sqlite3.Connection, tid: str) -> int:
    try:
        r = conn.execute("SELECT MAX(ended_at) FROM task_runs WHERE task_id = ?", (tid,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(r[0] or 0) if r else 0


def _later_passed(body: str, today: str) -> str | None:
    m = LATER_RE.match(body or "")
    return m.group(1) if m and m.group(1) <= today else None


def ownerless_why(conn: sqlite3.Connection, tid: str, assignee: str, block_kind: str | None,
                  thread: list[tuple[str, str, int]], *, now: float, today: str) -> str:
    """'' when someone still owns this blocked card, else why nobody does."""
    if block_kind:
        return ""
    blocked_at = _block_ts(conn, tid)
    who = (assignee or "").strip().lower()
    bodies = [b for _a, b, _t in thread]
    # the salad tripwire's until its releases are spent (it re-releases every tick now)
    if sum(SALAD_PARK_MARK in b for b in bodies) > sum(SALAD_RELEASE_MARK in b for b in bodies):
        return ""
    # this script's own render hold lifts itself
    if any(HOLD_MARK in b and t >= blocked_at - 300 for _a, b, t in thread):
        return ""
    # render-window's typed hold (comment then block within a second)
    if any(RENDER_WINDOW_MARK in b.lower() and "released" not in b.lower() and t >= blocked_at - 300
           for _a, b, t in thread):
        return ""
    # activity: block, last run, comments by an owner (ruler / the assignee) — never sweep chatter.
    # Checked before ANY reason: 06:44 the first live tick retreated t_a6e67fad 8 s after its
    # crash because the archived-reframe reason returned first.
    owner_ts = max([t for a, _b, t in thread if a in RULERS or (who and a == who)] or [0])
    active = max(blocked_at, _last_run_end(conn, tid), owner_ts)
    if now - active < OWNERLESS_AFTER_MIN * 60:
        return ""
    # a ruler's word newer than the block owns it — unless it was `later <date>` and the date came
    rulings = [(b, t) for a, b, t in thread if a in RULERS]
    if rulings and rulings[-1][1] >= blocked_at:
        d = _later_passed(rulings[-1][0], today)
        if not d:
            return ""
        return f"later {d} has passed"
    # a reframe owns its source while it lives; an archived reframe is not done (kanban-loop rule)
    cut = None
    for b in reversed(bodies):
        m = CUT_RE.match(b)
        if m:
            cut = m.group(1)
            break
    if cut:
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (cut,)).fetchone()
        st = str(row[0]) if row else "missing"
        if st != "archived" and row:
            return ""
        return f"reframe {cut} is {st}, so no reframe will close it"
    verdict = ""
    for a, b, t in reversed(thread):
        if t >= blocked_at - 300 and a not in ("steward", "worker", GATE_AUTHOR, "kanban-loop"):
            verdict = _kc.cut(b.strip().splitlines()[0], 160, "read the card") if b.strip() else ""
            break
    return "no kind, no reframe, no ruling" + (f' — last word: "{verdict}"' if verdict else "")


def _open_parents(conn: sqlite3.Connection, tid: str) -> list[str]:
    """Parents not done/archived: hermes lands an unblock in `todo` while any is open, and
    `block` refuses a todo card — so a retreat is only possible when this is empty."""
    try:
        return [str(r[0]) for r in conn.execute(
            "SELECT p.id FROM task_links l JOIN tasks p ON p.id = l.parent_id "
            "WHERE l.child_id = ? AND p.status NOT IN ('done', 'archived')", (tid,))]
    except sqlite3.OperationalError:
        return []


def _open_batch(conn: sqlite3.Connection) -> str:
    try:
        r = conn.execute(
            "SELECT id FROM tasks WHERE title LIKE ? AND status NOT IN ('done', 'archived') "
            "ORDER BY created_at DESC LIMIT 1", (BATCH_TITLE_LIKE,)).fetchone()
    except sqlite3.OperationalError:
        return ""
    return str(r[0]) if r else ""


BATCH_RELEASE_MARK = "gate: ownerless batch released"


def _batch_released(conn: sqlite3.Connection, bid: str) -> bool:
    """6b: this gate archived the batch card as stuck; its cards are re-batched, never strike 2."""
    return bool(bid) and _marked(conn, bid, BATCH_RELEASE_MARK)


def _release_stuck_batch(conn: sqlite3.Connection, bid: str, now: float, *, dry: bool = False) -> bool:
    """6b: a `route:` card that is blocked / scheduled / triage or older than BATCH_MAX_H is not an
    open slot — one such card jammed the ownerless gate for good (a batch that trips the verb wall
    is `blocked`, `_open_batch` reads it as open, nothing is batched again). Comment, archive, and
    let its cards come back as fresh. Kill KREW_OWNERLESS_BATCH_RELEASE=0."""
    if not BATCH_RELEASE_ON:
        return False
    try:
        r = conn.execute("SELECT status, created_at FROM tasks WHERE id = ?", (bid,)).fetchone()
    except sqlite3.OperationalError:
        return False
    if not r:
        return False
    status, created = str(r[0] or ""), int(r[1] or 0)
    hours = (now - created) / 3600 if created else 0
    stuck = status in ("blocked", "scheduled", "triage") or hours > BATCH_MAX_H
    if not stuck:
        return False
    log(f"ownerless: batch {bid} released — {status}, {hours:.1f} h old")
    if dry:
        return True
    kanban("comment", bid, f"{BATCH_RELEASE_MARK} — {status}, {hours:.1f} h old; the gate archives it and "
           "re-batches its cards on the next tick.", "--author", GATE_AUTHOR)
    if status in ("blocked", "scheduled"):
        kanban("unblock", bid, "--reason", "releasing a stuck ownerless batch")
    kanban("archive", bid)
    return True


def _batch_closed_at(conn: sqlite3.Connection, bid: str) -> int | None:
    """completed_at of a done/archived batch card; None while it is open or unknown."""
    try:
        r = conn.execute("SELECT status, completed_at FROM tasks WHERE id = ?", (bid,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not r or str(r[0]) not in ("done", "archived"):
        return None
    return int(r[1] or 0)


def _last_word(thread: list[tuple[str, str, int]], blocked_at: int) -> str:
    for a, b, t in reversed(thread):
        if t >= blocked_at - 300 and a not in ("steward", "worker", GATE_AUTHOR, "kanban-loop"):
            return _kc.cut(b.strip().splitlines()[0], 160, "read the card") if b.strip() else ""
    return ""


def batch_body(orch: str, cards: list[tuple[str, str, str, int, str, str]]) -> str:
    """cards: (id, assignee, title, hours, why, last_word)."""
    head = (
        f"{orch}: ROUTE these blocked cards; do not redo their work. They stay blocked until your verb.\n"
        "One pass, not one worker per card: read each card's last comment first (it is the verdict),\n"
        "delegate the artifact checks to children (delegate_task, two at a time, each may take two) —\n"
        "a child has NO kanban tools and no board dir: give it the card text in context, ask it only to\n"
        "stat/read/run and report —\n"
        "then ONE verb per card, written ON THAT CARD with kanban_comment as a line of its own.\n"
        "Your worker is scoped to THIS card: kanban_complete / kanban_block on another id is refused\n"
        "and there is no reassign tool (seen 2026-09-18 12:02) — never try, never report the wall.\n"
        "The board gate lands the line within 2 min (`gate: routed`):\n"
        "  VERB: complete <artifact or verdict>     the work exists; works on the blocked card as is\n"
        "  VERB: archive                            superseded / nothing left to do\n"
        "  VERB: park <why>                         schedule\n"
        "  VERB: reassign <bash|steward|lila|vera>  real work in the wrong hands (unblocks to ready)\n"
        "  VERB: crissa <question>                    Crissa rules (typed needs_input)\n"
        "  VERB: hands <what>                       the user's hands (typed capability)\n"
        "One VERB line per card, the last one wins. Finish with kanban_complete on THIS card:\n"
        "\"routed N — complete a, archive b, reassign c, crissa d, park e\".\n\nCards:\n"
    )
    lines = [f"- {tid} | {who} | blocked {h} h | {why} | \"{last}\" | {title}"
             for tid, who, title, h, why, last in cards]
    return head + "\n".join(lines)


def _later_card_date(conn: sqlite3.Connection, tid: str) -> tuple[str, str, int] | None:
    """The newest `later <date>` line in the thread — (date, line, created_at) — or None.

    A parked (`scheduled`) card has no owner: the dispatcher only dispatches ready/todo, and
    nothing in the krew unparks a scheduled card. So the date on the LATER line is the only
    deadline that exists, and when it has passed the card is parked on a date that has already
    gone — the exact class gate_ownerless exists for, only wearing a status the gate's
    blocked-only query never sees."""
    best: tuple[str, str, int] | None = None
    for a, b, t in _thread(conn, tid):
        if a not in RULERS:
            continue
        for line in b.splitlines():
            m = LATER_RE.match(line)
            if not m or (best and t <= best[2]):
                continue
            best = (m.group(1), line.strip()[:100], t)
    return best


def gate_ownerless(conn: sqlite3.Connection, *, dry: bool = False, now: float | None = None,
                   today: str | None = None) -> int:
    """Hand ownerless untyped blocks back to the orchestrator — as one batch card (default) or,
    with KREW_OWNERLESS_BATCH=0, comment + reassign + unblock one card at a time. At most
    OWNERLESS_MAX per tick. See the module docstring."""
    now = time.time() if now is None else now
    today = today or time.strftime("%Y-%m-%d", time.localtime(now))
    orch = orchestrator()
    n = 0
    try:
        rows = list(conn.execute(
            "SELECT id, title, assignee, block_kind FROM tasks "
            "WHERE status = 'blocked' AND block_kind IS NULL ORDER BY created_at"
        ))
    except sqlite3.OperationalError as exc:
        log(f"ownerless: cannot read the board ({exc})")
        return 0
    fresh: list[tuple[str, str, str, int, str, str]] = []      # batch mode: cards to hand over
    for tid, title, assignee, block_kind in rows:
        if n >= OWNERLESS_MAX:
            break
        thread = _thread(conn, tid)
        why = ownerless_why(conn, tid, assignee or "", block_kind, thread, now=now, today=today)
        if not why:
            continue
        marks = [(b, t) for a, b, t in thread if a == GATE_AUTHOR and b.startswith(OWNERLESS_MARK)]
        blocked_at = _block_ts(conn, tid)
        hours = int((now - blocked_at) / 3600) if blocked_at else 0
        who = (assignee or "").strip().lower() or "nobody"
        if BATCH_ON and not any(BATCHED_RE.search(b) or b.startswith(RETREAT_MARK) for b, _t in marks):
            # never batched (a per-card strike 1 that came back untyped was a crashed worker,
            # not a ruling — the User 2026-09-18): into the batch
            fresh.append((tid, who, str(title or ""), hours, why, _last_word(thread, blocked_at)))
            n += 1
            continue
        if marks and BATCH_ON:
            m = BATCHED_RE.search(marks[-1][0])
            if m and _batch_released(conn, m.group(1)):
                # 6b: its batch was archived as stuck — this card was never routed; batch it again
                fresh.append((tid, who, str(title or ""), hours, why, _last_word(thread, blocked_at)))
                n += 1
                continue
            if m:
                closed = _batch_closed_at(conn, m.group(1))
                if closed is None:
                    continue                            # the batch is Ada's until she completes it
                if now - closed < OWNERLESS_AFTER_MIN * 60:
                    continue                            # she just ruled the batch; give her verb time
                # the batch closed and this card is still an untyped block: fall through to strike 2
                marks = [(b, max(t, closed)) for b, t in marks]
        if not marks:
            log(f"ownerless {tid} strike 1 {who}->{orch} blocked {hours}h: {why} {str(title)[:50]!r}")
            if not dry:
                kanban(
                    "comment", tid,
                    f"{OWNERLESS_MARK} — blocked {hours} h by {who}: {why}. "
                    f"{orch}: ROUTE this card, do not redo it. One call, then stop: "
                    "complete (the work exists — cite the artifact) | archive (superseded) | "
                    "reassign <bash|steward|lila|vera> | block --kind needs_input <question> (Crissa) | "
                    "block --kind capability (the user's hands) | schedule (park). "
                    "This note is the last comment on the thread; read it before the body.",
                    "--author", GATE_AUTHOR,
                )
                if who != orch:
                    kanban("reassign", tid, orch)
                kanban("unblock", tid, "--reason", f"{OWNERLESS_MARK} → {orch} to route")
            n += 1
            continue
        if any(b.startswith(RETREAT_MARK) and ("with Crissa" in b or "with Crissa" in b) for b, _t in marks):   # a mark from before the rename (2026-10-10) still counts
            continue                                    # the typed block landed: hers now
        if blocked_at <= marks[-1][1] and not (BATCH_ON and BATCHED_RE.search(marks[-1][0])):
            # the same block our strike-1 unblock did not lift — say so once per tick, do not loop
            log(f"ownerless {tid}: strike 1 did not land (still the block of {hours}h ago)")
            continue
        parents = _open_parents(conn, tid)
        if parents:
            # an unblock would land `todo` and `block` refuses it: wait for the parents, log only
            log(f"ownerless {tid}: retreat waits on open parent(s) {' '.join(parents[:3])}")
            continue
        q = (f"Routed once by the board gate and blocked again without a kind: {why}. "
             "First word: try (run it again) | park | reassign <profile> | hands.")
        log(f"ownerless {tid} strike 2 -> crissa: {why} {str(title)[:50]!r}")
        if not dry:
            kanban("unblock", tid, "--reason", f"{RETREAT_MARK}: releasing to re-block typed")
            # hermes blocks only running/ready; the reason is a trailing positional — reason FIRST
            rc, out = kanban("block", tid, q, "--kind", "needs_input")
            if rc == 0:
                kanban("comment", tid, f"{RETREAT_MARK} — with Crissa: {why}", "--author", GATE_AUTHOR)
            else:
                log(f"ownerless {tid}: retreat block refused ({out[:100]})")
                kanban("comment", tid, f"{RETREAT_MARK}: typed block refused ({out.strip()}); "
                       "the card is where the unblock left it — retried after the wait.", "--author", GATE_AUTHOR)
        n += 1
    if fresh:
        open_batch = _open_batch(conn)
        if open_batch and _release_stuck_batch(conn, open_batch, now, dry=dry):
            return 0                                    # archived this tick; its cards come back next tick
        if open_batch:
            log(f"ownerless: {len(fresh)} waiting; batch {open_batch} still open")
            return 0
        title = BATCH_TITLE.format(n=len(fresh), s="" if len(fresh) == 1 else "s")
        log(f"ownerless batch {title!r} -> {orch}: " + " ".join(c[0] for c in fresh))
        if dry:
            return len(fresh)
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(now))
        rc, out = kanban("create", title, "--assignee", orch, "--body", batch_body(orch, fresh),
                         "--created-by", GATE_AUTHOR, "--idempotency-key", f"ownerless-batch-{stamp}")
        m = re.search(r"\bt_[0-9a-f]{8}\b", out or "")
        if rc != 0 or not m:
            log(f"ownerless: batch create failed ({(out or '')[:120]})")
            return 0
        bid = m.group(0)
        for tid, _who, _title, _h, why, _last in fresh:
            kanban("comment", tid, f"{OWNERLESS_MARK} — batched in {bid}: {why}. Stays blocked until "
                   f"{orch} routes it from that card.", "--author", GATE_AUTHOR)
    # LATER (2026-09-25, t_8c615508): parked (`scheduled`) cards are handled by
    # gate_later_parks in main() — a separate gate, its own kill switch.
    return n


def gate_later_parks(conn: sqlite3.Connection, *, dry: bool = False, now: float | None = None,
                     today: str | None = None) -> int:
    """A parked card with no readable deadline has no reader — park it back to the orchestrator.

    `hermes kanban schedule` parks; nothing unparks, and hermes dispatches only ready/todo
    (kanban_db.py: `WHERE status IN ('ready','todo')`). A `later <date>` from a ruler is the
    card's only deadline; when the date has passed the card is parked on a date that has
    already gone (t_801d9350: parked 2026-09-24 21:38 with no unblocked event by 2026-09-25).
    Same class the ownerless gate exists for, wearing a status its blocked-only query never
    sees. The card is NOT unblocked to ready (that would respawn the worker immediately); it
    is re-parked (schedule) on the orchestrator with the offending LATER line quoted — one
    read undoes it. A `later <date>` that is not a date parks a card forever by construction;
    those are not scheduled at all and go straight to the orchestrator. Returns the count.
    """
    now = time.time() if now is None else now
    today = today or time.strftime("%Y-%m-%d", time.localtime(now))
    orch = orchestrator()
    n = 0
    try:
        rows = list(conn.execute(
            "SELECT id, title, assignee FROM tasks WHERE status = 'scheduled' ORDER BY created_at"))
    except sqlite3.OperationalError as exc:
        log(f"later-park: cannot read the board ({exc})")
        return 0
    for tid, title, assignee in rows:
        thread = _thread(conn, tid)
        best = _later_card_date(conn, tid)
        # a gate mark (any author: the unblock reason carries it) newer than nothing: already ours
        if any(a == GATE_AUTHOR and b.startswith(LATER_PARK_MARK) for a, b, _t in thread):
            continue
        bad: str | None = None
        why: str | None = None
        reschedule: str | None = None
        if best:
            d, _line, _t = best
            if d <= today:
                why = f"later {d} has passed (parked)"
                reschedule = "later date passed — route this card (unpark: unblock, or re-schedule on a future date)"
            # a future date is the card's own deadline: leave it parked, do nothing
        else:
            bad = _bad_later_line(thread)
            if bad:
                why = "later line is not a date"
                reschedule = None            # nothing is scheduled; hand straight to the orchestrator
        if not why:
            continue
        parked_at = _parked_ts(conn, tid)
        hours = int((now - parked_at) / 3600) if parked_at else 0
        who = (assignee or "").strip().lower() or "nobody"
        if reschedule is not None:
            log(f"later-park {tid}: {why}; parked {hours}h by {who} -> re-park {orch}")
            if not dry:
                kanban("comment", tid, f"{LATER_PARK_MARK} — {why}. {orch}: ROUTE this card, do not redo it. "
                       f"UNBLOCK (unpark) and do it | re-schedule on a future date | archive (superseded).",
                       "--author", GATE_AUTHOR)
                rc, out = kanban("schedule", tid, reschedule)
                if rc != 0:
                    log(f"later-park {tid}: schedule refused ({out[:100]})")
        else:
            log(f"later-park {tid}: {why}; parked {hours}h by {who} -> {orch} (no date to schedule)")
            if not dry:
                kanban("comment", tid, f"{LATER_PARK_MARK} — a `later` line on this card is not a date "
                       f"(\"{bad}\"); it cannot be scheduled. {orch}: give it a date (re-schedule) or "
                       f"UNBLOCK and do it.", "--author", GATE_AUTHOR)
                if who != orch:
                    kanban("reassign", tid, orch)
                kanban("unblock", tid, "--reason", f"{LATER_PARK_MARK} → {orch} to give it a date")
        n += 1
    return n


def _parked_ts(conn: sqlite3.Connection, tid: str) -> int:
    try:
        r = conn.execute(
            "SELECT MAX(created_at) FROM task_events WHERE task_id = ? AND kind = 'scheduled'",
            (tid,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(r[0] or 0) if r else 0


def _bad_later_line(thread: list[tuple[str, str, int]]) -> str | None:
    """A ruler's `later <...>` line that is not a date — newest first."""
    for a, b, _t in reversed(thread):
        if a not in RULERS:
            continue
        for line in b.splitlines():
            if re.match(r"^\s*(?:(?:unblock|blocked)\b[\s:,.;!-]*)?later\b", line, re.I):
                if not LATER_RE.match(line):
                    return line.strip()[:100]
    return None


def _routed_verb(thread: list[tuple[str, str, int]], orch: str) -> tuple[str, str] | None:
    """The orchestrator's newest `VERB:` line after the gate's last ownerless mark, unless a
    `gate: routed` mark (any author: the unblock reason carries it too) is newer than it."""
    start = 0
    for i, (a, b, _t) in enumerate(thread):
        if a == GATE_AUTHOR and b.startswith(OWNERLESS_MARK):
            start = i
    verb: tuple[str, str] | None = None
    for a, b, _t in thread[start:]:
        if b.startswith(ROUTED_MARK):
            verb = None
            continue
        if a != orch:
            continue
        hits = VERB_RE.findall(b)
        if hits:
            v, arg = hits[-1]
            verb = (v.lower(), " ".join(arg.split()))
    return verb


def gate_routed(conn: sqlite3.Connection, *, dry: bool = False) -> int:
    """Land Ada's `VERB:` line on a routed card — her worker cannot (hermes scopes it to its own
    card; the lean kit has no reassign). At most ROUTED_MAX per tick, each verb marked once."""
    orch = orchestrator()
    n = 0
    try:
        rows = list(conn.execute(
            "SELECT id, title FROM tasks WHERE status = 'blocked' ORDER BY created_at"))
    except sqlite3.OperationalError as exc:
        log(f"routed: cannot read the board ({exc})")
        return 0
    for tid, title in rows:
        if n >= ROUTED_MAX:
            break
        thread = _thread(conn, tid)
        if not any(a == GATE_AUTHOR and b.startswith(OWNERLESS_MARK) for a, b, _t in thread):
            continue
        found = _routed_verb(thread, orch)
        if not found:
            continue
        verb, arg = found
        log(f"routed {tid}: {orch} said {verb} {arg[:60]!r} {str(title)[:50]!r}")
        n += 1
        if dry:
            continue
        head = f"{ROUTED_MARK} → {verb} {arg}".strip()
        outs: list[tuple[int, str]] = []
        if verb == "complete":
            outs.append(kanban("complete", tid, "--result", arg or f"routed by {orch}"))
        elif verb == "archive":
            outs.append(kanban("archive", tid))
        elif verb == "park":
            outs.append(kanban("schedule", tid, arg or f"parked by {orch}"))
        elif verb == "reassign":
            dst = (arg.split() or [""])[0].lower().strip("`'\"")
            if dst not in ROUTE_PROFILES:
                outs.append((1, f"no such profile {dst!r}"))
            else:
                outs.append(kanban("reassign", tid, dst))
                outs.append(kanban("unblock", tid, "--reason", f"{head} ({orch}'s verb)"))
        else:                                             # crissa | hands → a typed block
            kind = "needs_input" if verb == "crissa" else "capability"
            q = arg or (f"{orch} routed this to Crissa" if verb == "crissa" else f"{orch} routed this to the user")
            outs.append(kanban("unblock", tid, "--reason", f"{ROUTED_MARK}: releasing to block typed"))
            # hermes blocks only running/ready; the reason is a trailing positional — reason FIRST
            outs.append(kanban("block", tid, q, "--kind", kind))
        bad = [o for rc, o in outs if rc != 0]
        if bad:
            log(f"routed {tid}: {verb} refused ({bad[0][:100]})")
            kanban("comment", tid, f"{head}: refused ({bad[0][:120]}) — the card is where the last "
                   f"call left it; {orch}: a new VERB line is tried again.", "--author", GATE_AUTHOR)
        else:
            kanban("comment", tid, f"{head}: landed.", "--author", GATE_AUTHOR)
    return n


def _completion_summary(conn: sqlite3.Connection, tid: str) -> str:
    try:
        r = conn.execute(
            "SELECT summary FROM task_runs WHERE task_id = ? AND outcome = 'completed' ORDER BY id DESC LIMIT 1",
            (tid,)).fetchone()
    except sqlite3.OperationalError:
        return ""
    return str(r[0] or "") if r else ""


def _has_review_child(conn: sqlite3.Connection, tid: str) -> bool:
    try:
        r = conn.execute(
            "SELECT 1 FROM task_links l JOIN tasks c ON c.id = l.child_id "
            "WHERE l.parent_id = ? AND c.assignee = ? AND c.status != 'archived' LIMIT 1", (tid, REVIEWER)).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(r)


def review_body(tid: str, who: str, title: str, result: str, summary: str) -> str:
    return (
        f"Review of {tid} ({who}): {title}\n\n"
        "Verify, do not redo (Vera SOUL: builders build; you judge). One pass — delegate_task the\n"
        "independent checks (two at a time), then verify anything you write here yourself.\n"
        "1. The crystal: the artifact / pin / host action the card promised exists and does what the\n"
        "   card says (stat the path, read the ledger row, run the smoke it names).\n"
        "2. Files / packs / units changed: walk ~/.hermes/prodgate-checklist.md on what landed; a\n"
        "   change that skipped prodgate is a FAIL that names the route.\n"
        "3. M&S land: DEPS-PINNED / decisions.json row present, unlock= written, keep has its KPI.\n"
        "Verdict, first word of your completion: PASS | FAIL. FAIL: file ONE fix card to the\n"
        f"implementer ({who}) citing exactly what is missing, then complete this review with FAIL and\n"
        "that card's id. Never a bare unblock, never a block without a kind, never the fix yourself.\n\n"
        f"Card result: {result or '(none)'}\n\nWorker summary: {summary or '(none)'}\n"
    )


# The house reviews on hermes's own lane (the User 2026-09-27, "Upstream lane"): a worker calls
# kanban_request_review, the SAME card moves to `review` and a reviewer who is not the builder
# (scripts/krew-review-route.py) approves it or sends it back with kanban_request_changes. This
# gate's post-done `review:` card is now the BACKSTOP for a build that closed without asking:
# a card that went through the lane is never reviewed twice. Kill: KREW_REVIEW_LANE_STANDS=0.
REVIEW_LANE_STANDS = os.environ.get("KREW_REVIEW_LANE_STANDS", "1").strip().lower() not in ("0", "false", "no", "off")


def _reviewed_on_the_lane(conn: sqlite3.Connection, tid: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM task_events WHERE task_id = ? AND kind = 'review_requested' LIMIT 1",
                            (tid,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def gate_review(conn: sqlite3.Connection, *, dry: bool = False, now: float | None = None) -> int:
    """A finished implement card gets one review card to Vera. See the module docstring."""
    now = time.time() if now is None else now
    since = int(now - REVIEW_LOOKBACK_H * 3600)
    n = 0
    try:
        rows = list(conn.execute(
            "SELECT id, title, assignee, result, completed_at FROM tasks "
            "WHERE status = 'done' AND completed_at IS NOT NULL AND completed_at >= ? ORDER BY completed_at",
            (since,)))
    except sqlite3.OperationalError as exc:
        log(f"review: cannot read the board ({exc})")
        return 0
    for tid, title, assignee, result, _done_at in rows:
        if n >= REVIEW_MAX:
            break
        who = (assignee or "").strip().lower()
        title = str(title or "")
        if who not in REVIEW_IMPLEMENTERS or who == REVIEWER:
            continue
        if not REVIEW_TITLE_RE.match(title) or REVIEW_SKIP_TITLE_RE.match(title):
            continue
        summary = _completion_summary(conn, tid)
        if REVIEW_SKIP_RESULT_RE.match(str(result or "")) or REVIEW_SKIP_RESULT_RE.match(summary):
            continue                                    # closed as superseded / nothing built
        if _has_review_child(conn, tid):
            continue
        if REVIEW_LANE_STANDS and _reviewed_on_the_lane(conn, tid):
            continue                                    # hermes's own review lane already judged it
        if any(b.startswith(REVIEW_MARK) for a, b, _t in _thread(conn, tid) if a == GATE_AUTHOR):
            continue
        log(f"review {tid} ({who}) -> {REVIEWER}: {title[:60]!r}")
        n += 1
        if dry:
            continue
        rc, out = kanban("create", f"review: {title[:140]}", "--assignee", REVIEWER, "--parent", tid,
                         "--body", review_body(tid, who, title, str(result or ""), summary),
                         "--created-by", GATE_AUTHOR, "--idempotency-key", f"review:{tid}")
        m = re.search(r"\bt_[0-9a-f]{8}\b", out or "")
        if rc != 0 or not m:
            log(f"review {tid}: create failed ({(out or '')[:120]})")
            continue
        kanban("comment", tid, f"{REVIEW_MARK} → {m.group(0)} ({REVIEWER})", "--author", GATE_AUTHOR)
    return n


def _occupancy() -> str:
    try:
        return GPU_MODE.read_text(encoding="utf-8").strip().lower() or "normal"
    except OSError:
        return "normal"


def ada_caps_on_disk() -> tuple[int, int]:
    """kanban.max_in_progress / _per_profile from Ada's YAML (3/2 when unreadable)."""
    topo = _topology()      # one host gateway: the dispatcher reads the DEFAULT profile's block
    cfg = topo.dispatcher_config() if topo else HERMES / "profiles" / PROFILE / "config.yaml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return 3, 2
    t = re.search(r"^\s*max_in_progress:\s*(\d+)", text, re.M)
    per = re.search(r"^\s*max_in_progress_per_profile:\s*(\d+)", text, re.M)
    return (int(t.group(1)) if t else 3), (int(per.group(1)) if per else 2)


def _ready_age_s(conn: sqlite3.Connection, tid: str, now: float) -> float:
    try:
        r = conn.execute(
            "SELECT MAX(created_at) FROM task_events WHERE task_id = ? "
            "AND kind IN ('promoted', 'unblocked', 'created', 'assigned', 'ready')", (tid,)).fetchone()
    except sqlite3.OperationalError:
        return float("inf")
    return now - float(r[0]) if r and r[0] else float("inf")


def _needs_only(blob: str, dst: str) -> bool:
    if dst == "steward":
        return bool(BASH_ONLY_RE.search(blob))
    if dst == "bash":
        return bool(STEWARD_ONLY_RE.search(blob))
    return True


def gate_balance(conn: sqlite3.Connection, *, dry: bool = False, now: float | None = None) -> int:
    """Level ready cards between the two host shells. See the module docstring."""
    now = time.time() if now is None else now
    if _occupancy() != "normal":
        return 0
    _total, per = ada_caps_on_disk()
    n = 0
    for src, dst in PEERS:
        if n >= BALANCE_MAX:
            break
        try:
            load = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE assignee = ? AND status IN ('ready', 'running')", (dst,)
            ).fetchone()[0]
            ready = list(conn.execute(
                "SELECT id, title, body, COALESCE(skills, '') FROM tasks WHERE assignee = ? AND status = 'ready' "
                "ORDER BY created_at", (src,)))
        except sqlite3.OperationalError as exc:
            log(f"balance: cannot read the board ({exc})")
            return n
        free = per - int(load)
        if free <= 0 or len(ready) < 2:
            continue
        moved = 0
        for tid, title, body, skills in ready:
            if moved >= free or n >= BALANCE_MAX or len(ready) - moved <= 1:
                break
            blob = " ".join((str(title or ""), str(body or ""), str(skills or "")))
            if dst == "steward" and _is_render_card(str(title or ""), str(body or ""), str(skills or "")):
                continue
            if _needs_only(blob, dst):
                continue
            if _ready_age_s(conn, tid, now) < BALANCE_AGE_MIN * 60:
                continue
            if any(b.startswith(BALANCE_MARK) for a, b, _t in _thread(conn, tid) if a == GATE_AUTHOR):
                continue                                # moved once; never ping-pong
            why = f"{src} {len(ready)} ready, {dst} {load} in flight of {per}"
            log(f"balance {tid} {src} -> {dst} ({why}) {str(title)[:50]!r}")
            if not dry:
                kanban("comment", tid, f"{BALANCE_MARK} {src} → {dst}: {why}. Same host shell, the other engine.",
                       "--author", GATE_AUTHOR)
                kanban("reassign", tid, dst)
            moved += 1
            n += 1
    return n


def _own_lib(name: str):
    """crew/lib/<name>.py by its path: never a same-named module from another tree on sys.path."""
    import importlib.util
    lib = Path(__file__).resolve().parent.parent / "lib"
    path = lib / f"{name}.py"
    if not path.is_file():
        raise ImportError(f"no {path}")
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))      # what it imports comes from the same lib
    spec = importlib.util.spec_from_file_location(f"krew_own_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_TOPOLOGY_SAID: list = []


def _topology():
    """lib/hermes_topology when ONE host gateway serves every profile (KREW_GATEWAY_TOPOLOGY=host),
    else None: under the default `units` nothing in this file changes. Never raises."""
    try:
        hermes_topology = _own_lib("hermes_topology")
        return hermes_topology if hermes_topology.is_host() else None
    except Exception as e:  # noqa: BLE001
        if os.environ.get("KREW_GATEWAY_TOPOLOGY", "").strip().lower() == "host" and not _TOPOLOGY_SAID:
            _TOPOLOGY_SAID.append(1)
            print(f"kanban-board-gate: KREW_GATEWAY_TOPOLOGY=host but lib/hermes_topology did not load "
                  f"({type(e).__name__}: {e}); gateways are taken one unit per profile", file=sys.stderr)
        return None



def _gateway_start() -> str:
    topo = _topology()
    unit = topo.dispatcher_unit() if topo else ADA_GATEWAY      # the dispatcher reads its caps at ITS start
    try:
        r = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveEnterTimestamp", "--value"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def sync_ada_caps(conn: sqlite3.Connection, *, dry: bool = False) -> int:
    """Record what Ada runs with; say when the file differs and the restart is owed (1) — no restart."""
    start = _gateway_start()
    if not start:
        return 0                                        # gateway down or unknown: not ours
    disk = list(ada_caps_on_disk())
    try:
        live = json.loads(CAPS_LIVE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        live = {}
    if live.get("gateway_start") != start:
        # she started since we last looked: what is on disk now is what she loaded
        if not dry:
            try:
                CAPS_LIVE.write_text(json.dumps({"gateway_start": start, "caps": disk}), encoding="utf-8")
                CAPS_PENDING.unlink(missing_ok=True)
            except OSError:
                pass
        return 0
    if list(live.get("caps") or []) == disk:
        if not dry:
            try:
                CAPS_PENDING.unlink(missing_ok=True)
            except OSError:
                pass
        return 0
    try:
        running = conn.execute("SELECT COUNT(*) FROM tasks WHERE status = 'running'").fetchone()[0]
    except sqlite3.OperationalError:
        running = -1
    log(f"caps: {disk[0]}/{disk[1]} on disk, gateway runs {live.get('caps')} — restart owed "
        f"({running} running{'; a gap now' if running == 0 else ''})")
    if not dry:
        try:
            CAPS_PENDING.write_text(f"{disk[0]}/{disk[1]} running={running} {time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
                                    encoding="utf-8")
        except OSError:
            pass
    return 1


_TRIPWIRE = None


def _tripwire():
    """scripts/kanban-salad-tripwire.py, loaded once by path (hyphenated name)."""
    global _TRIPWIRE
    if _TRIPWIRE is None:
        import importlib.util
        path = Path(__file__).resolve().parent / "kanban-salad-tripwire.py"
        spec = importlib.util.spec_from_file_location("kanban_salad_tripwire", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TRIPWIRE = mod
    return _TRIPWIRE


def salad_relapse_card(slug: str, hits: list[str], clear: list[str], *, dry: bool = False,
                       now: float | None = None) -> int:
    """One `Restart window: crew engine relapse` card per board per day, on Crissa's queue
    (needs_input), plus the `~/.hermes/crew-restart-owed` marker the Daily and a hand can read.
    The gate never restarts crew: the remedy is the restart window's or the User's."""
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    key = f"salad-relapse:{slug}:{day}"
    title = f"Restart window: crew engine relapse — {len(hits)} salad session(s) {day}"
    body = (f"The salad detector marked {len(hits)} worker session(s) as token salad on the crew engine "
            f"({', '.join(hits[:6])}{'; clearly garbage: ' + ', '.join(clear[:4]) if clear else ''}).\n"
            "Crew runs with the prefix-cache A/B live (ON + MTP OFF since 09-18 16:59); a relapse means the "
            "engine's state is bad again and a crew restart is owed. This gate never restarts anything: "
            "rule it for the restart window (go = restart crew in the window; wait; later HH:MM; park; hands = "
            "the user flips the drop-in back and restarts). Measure first: scripts/crew-prefix-ab-measure.py --summary.\n"
            f"gate: salad-relapse {day}")
    if dry:
        log(f"salad relapse: would file {title!r}")
        return 1
    rc, out = kanban("create", title, "--assignee", "ada", "--body", _kc.cut(body, 100000, "the cards it names have the rest"),
                     "--created-by", GATE_AUTHOR, "--idempotency-key", key)
    m = re.search(r"\bt_[0-9a-f]{8}\b", out or "")
    if rc != 0 or not m:
        log(f"salad relapse: create failed ({(out or '')[:120]})")
        return 0
    tid = m.group(0)
    try:
        RELAPSE_MARK.write_text(f"crew salad relapse {day}: {len(hits)} session(s); card {tid}\n", encoding="utf-8")
    except OSError:
        pass
    if "already" in (out or "").lower() or "exists" in (out or "").lower():
        return 0                                    # the day's card is already on her queue
    rc2, out2 = kanban("block", tid, "Crew engine relapse (salad) under the prefix-cache test: go | wait | later HH:MM | park | hands?",
                       "--kind", "needs_input")
    if rc2 != 0:
        log(f"salad relapse {tid}: block refused ({(out2 or '')[:100]})")
    log(f"salad relapse: filed {tid} for {len(hits)} session(s)")
    return 1


_DUP_NOISE = re.compile(r"[^a-z0-9]+")


def dup_key(title: str) -> str:
    """One idea, one card. Case and punctuation folded, ids dropped, prefixes KEPT: `reframe: X`
    is X's frame, not X's twin."""
    t = re.sub(r"\bt_[0-9a-f]{8}\b", "", (title or "").lower())
    return _DUP_NOISE.sub(" ", t).strip()


def gate_dups(conn: sqlite3.Connection, *, dry: bool = False) -> int:
    """Six open cards for one idea on 09-18 (the terminal-state wrapper on six profiles): the
    filer's idempotency never reached decomposer children. Among OPEN cards sharing a title
    key the oldest stays; each younger twin is archived with `gate: dup of <id>` — unless the two
    are linked (a child and its root are not twins) or one names the other. Never a running
    card, at most DUP_MAX per tick. Kill KREW_DUP_GATE=0."""
    if not DUP_GATE_ON:
        return 0
    try:
        rows = conn.execute(
            "SELECT id, title, status, created_at, body FROM tasks "
            "WHERE status IN ('todo', 'ready', 'blocked', 'triage', 'scheduled') ORDER BY created_at, id").fetchall()
    except sqlite3.OperationalError:
        return 0
    groups: dict[str, list[tuple]] = {}
    for r in rows:
        k = dup_key(r[1])
        if len(k) >= 12:
            groups.setdefault(k, []).append(r)
    try:
        links = {(a, b) for a, b in conn.execute("SELECT parent_id, child_id FROM task_links")}
    except sqlite3.OperationalError:
        links = set()
    n = 0
    for k, cards in groups.items():
        if len(cards) < 2:
            continue
        keep = cards[0]
        for twin in cards[1:]:
            if n >= DUP_MAX:
                return n
            tid, title, status, _c, body = twin
            if (keep[0], tid) in links or (tid, keep[0]) in links:
                continue
            if keep[0] in (body or "") or tid in (keep[4] or ""):
                continue
            if _marked(conn, tid, DUP_MARK):
                continue
            log(f"dup {tid} ({status}) of {keep[0]}: {title[:50]!r}")
            n += 1
            if dry:
                continue
            kanban("comment", tid, f"{DUP_MARK} {keep[0]} — same title, both open; the older card is the one "
                   "idea's card. Archived by the board gate; reopen with a different title if it is not.",
                   "--author", GATE_AUTHOR)
            if status in ("blocked", "scheduled"):
                kanban("unblock", tid, "--reason", f"dup of {keep[0]}; archiving")
            rc, out = kanban("archive", tid)
            if rc != 0:
                log(f"dup {tid}: archive refused ({(out or '')[:100]})")
    return n


def _skills_of(raw) -> list[str]:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return [str(x) for x in v if x] if isinstance(v, list) else []


def gate_skills_inherit(conn: sqlite3.Connection, *, dry: bool = False, runner=None) -> int:
    """Skills once per thread (the User 2026-09-18): an open card with no pins takes its lineage's —
    the root it was decomposed from (`task_links` row parent_id=card → child_id=root, and the
    `created` event's from_decompose_of), the source a reframe cut sits over (same row shape),
    plus its own `<assignee>-map`. Written through scripts/kanban-skills-add.py (validates
    against the assignee's registry). Once per card, SKILLS_INHERIT_MAX per tick.
    Kill KREW_SKILLS_INHERIT=0."""
    if not SKILLS_INHERIT_ON:
        return 0
    try:
        rows = conn.execute(
            "SELECT id, assignee, skills FROM tasks WHERE status IN ('todo', 'ready', 'triage', 'blocked') "
            "ORDER BY created_at").fetchall()
    except sqlite3.OperationalError:
        return 0
    n = 0
    for tid, assignee, raw in rows:
        if n >= SKILLS_INHERIT_MAX:
            break
        if _skills_of(raw) or _marked(conn, tid, SKILLS_MARK):
            continue
        lineage: list[str] = []
        try:
            lineage += [str(r[0]) for r in conn.execute("SELECT child_id FROM task_links WHERE parent_id = ?", (tid,))]
        except sqlite3.OperationalError:
            pass
        try:
            for (payload,) in conn.execute(
                    "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'created' ORDER BY id DESC LIMIT 1", (tid,)):
                try:
                    root = (json.loads(payload or "{}") or {}).get("from_decompose_of")
                except (TypeError, ValueError):
                    root = None
                if root:
                    lineage.append(str(root))
        except sqlite3.OperationalError:
            pass
        got: list[str] = []
        src = ""
        for lid in dict.fromkeys(lineage):
            try:
                r = conn.execute("SELECT skills FROM tasks WHERE id = ?", (lid,)).fetchone()
            except sqlite3.OperationalError:
                r = None
            for sk in _skills_of(r[0] if r else None):
                if sk not in got:
                    got.append(sk)
                    src = src or lid
        who = (assignee or "").strip().lower()
        if who in ROSTER and f"{who}-map" not in got and got:
            got.append(f"{who}-map")
        if not got:
            continue
        log(f"skills inherit {tid} ← {src or 'map'}: {' '.join(got)}")
        n += 1
        if dry:
            continue
        run = runner or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=60, check=False))
        r = run([str(SKILLS_ADD), tid, *got, "--why", f"inherited from {src or 'lineage'} by the board gate"])
        rc = getattr(r, "returncode", 1)
        if rc != 0:
            log(f"skills inherit {tid}: kanban-skills-add rc={rc} {(getattr(r, 'stdout', '') or '')[:80]}")
            # A TERMINAL refusal must still mark, or the card is re-selected every 2 minutes for
            # good: rc 2 = unknown card, rc 3 = the ASSIGNEE's registry can load none of these
            # names — neither improves by waiting. Measured 2026-09-22: t_df57cb5c (judge) took
            # `ada-map judge-map` off its ada parent and burned 59 ticks on rc=3, because a
            # lineage pin is its parent's index, not the child's, and judge has no map at all
            # (its kit is deliberately kanban_lean + file + skills, one call, one verb). rc 4
            # (no board) and a timeout ARE transient and keep retrying.
            if rc in (2, 3):
                kanban("comment", tid, f"{SKILLS_MARK}: none of {', '.join(got)} is in "
                       f"{assignee or 'the assignee'}'s registry — nothing to inherit (rc={rc})",
                       "--author", GATE_AUTHOR)
            continue
        kanban("comment", tid, f"{SKILLS_MARK} from {src or 'lineage'}: {', '.join(got)} (skills once per thread)",
               "--author", GATE_AUTHOR)
    return n


def _unit_active(unit: str) -> bool:
    topo = _topology()
    prof = topo.profile_of_unit(unit) if topo else None
    if prof:
        # one host gateway: a profile is up while the host is active AND serves it (not parked)
        return bool(topo.profile_up(prof, timeout=10))
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def _unit_started(unit: str) -> float:
    try:
        r = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveEnterTimestampMonotonic", "--value"],
                           capture_output=True, text=True, timeout=10)
        mono = float(r.stdout.strip() or 0) / 1e6
        return time.time() - (time.monotonic() - mono) if mono else 0.0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


SKILLS_FILTER_MAX = int(os.environ.get("KREW_SKILLS_FILTER_MAX", "5"))
SKILLS_FILTER_FAILS_MAX = int(os.environ.get("KREW_SKILLS_FILTER_FAILS_MAX", "3"))
SKILLS_FILTER_GIVEUP_MARK = "gate: skills filter gave up"
SKILLS_FILTER_MARK = "gate: skills stripped after reassignment"


def _profile_skills(profile: str) -> set[str] | None:
    """The skill names in this profile's registry: <profile>/skills/**/<name> with a SKILL.md.
    None when the profile home or its skills dir is missing (nothing to compare against)."""
    base = HERMES / "profiles" / profile / "skills"
    if not base.is_dir():
        return None
    try:
        return {p.parent.name for p in base.rglob("SKILL.md") if p.is_file()}
    except OSError:
        return None


def _skills_filter_fn():
    """lib/skills_filter.filter_skills, or None with ONE clear log line (the User 09-30).
    The import used to sit bare inside the card loop: a missing or broken module raised
    out of gate_skills_filter and ended the tick before gate_cause / liveness / orphans.
    A failed import now skips only this step - no pins are stripped this tick."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        from skills_filter import filter_skills   # noqa: PLC0415 (same shape as the gates above)
        return filter_skills
    except Exception as e:                            # ImportError, or a broken module
        log(f"skills filter: ERROR lib/skills_filter.py would not import "
            f"({type(e).__name__}: {e}) - no pins stripped this tick; the rest of the gate runs"[:300])
        return None


def gate_skills_filter(conn: sqlite3.Connection, *, dry: bool = False,
                       runner=None) -> int:
    """Strip foreign pins the moment a card changes hands (2026-09-26, t_197285fb).

    `hermes kanban reassign` does not touch `tasks.skills`, so a card reassigned from
    profile A to profile B keeps A's pins (gate_balance's bash ↔ steward moves, gate_routed's
    VERB reassigns, gate_assignee's doctrine slips). A pin the new assignee's registry
    cannot resolve is a guaranteed rc=1: hermes raises `Unknown skill(s)` at preload and
    the worker dies at `Initializing agent...` (t_47c4bebd `bash-map` on a steward card
    x22). For every OPEN card we inspect the skills list, drop the names absent from the
    target profile's skill directory, and comment one `Dropped <skill>: not present in
    <profile> profile` line per drop. Removal goes through kanban-skills-add.py --remove
    (write_txn + a `skills_removed` event, not a raw sqlite write); the comment is the log
    the acceptance asks for. Once per card via the mark — reassigns never ping-pong — and
    a failed call leaves the mark unwritten, so the next tick retries.
    Kill KREW_SKILLS_FILTER=0; SKILLS_FILTER_MAX caps strips/would-drops
    performed per tick (skipped cards — no drops, missing registry, already
    marked — do not count against it)."""
    if not SKILLS_FILTER_ON:
        return 0
    try:
        rows = list(conn.execute(
            "SELECT id, assignee, skills FROM tasks "
            "WHERE status IN ('todo', 'ready', 'triage', 'running', 'blocked', 'scheduled') "
            "ORDER BY created_at"))
    except sqlite3.OperationalError:
        return 0
    n = 0
    for tid, assignee, raw in rows:
        if n >= SKILLS_FILTER_MAX:
            break
        who = (assignee or "").strip().lower()
        if who not in ROSTER:
            continue
        pins = _skills_of(raw)
        if not pins:
            continue
        # Skip a card the gate already worked: a strip comment landed — the
        # card is done until something re-pins it (inherit, a reframe cut, a
        # human create --skill). A mark keyed on the card alone is the
        # once-per-card idiom; reassigns must never ping-pong, so a re-added
        # pin is honored only when it carries a `skills_added` event with a
        # wall-clock timestamp NEWER than the newest strip mark — the pin
        # writer (kanban-skills-add.py) appends one event per write, and a
        # re-pin stamped with an older (skewed or back-dated) timestamp is
        # noise the gate must not chase.
        # Both compare sides live in the writer's domain: task_comments and
        # task_events rows carry created_at = int(time.time()) (epoch, stamped
        # by the writer — kanban_db_comments INSERT, hermes event writes). The
        # mark's embedded ts is a strftime LOCAL string (the gate's idiom), and
        # decoding it with mktime is a RE-ENCODE in the host's CURRENT tz: a
        # UTC host matches the epoch, but an EDT host reads the mark 4h in the
        # past — a pin stamped after the mark silently compares as OLDER, the
        # marked card re-arms on every pin, and the give-up cap never stops
        # firing. The row behind the mark IS the same write: its created_at is
        # the epoch the ts encodes. One domain, no re-encode.
        threads = _thread(conn, tid)
        strip_marks = [(t, b) for _a, b, t in threads if b.startswith(SKILLS_FILTER_MARK)]
        if strip_marks:
            tsd = [b.rsplit("ts=", 1)[1][:19] for _t, b in strip_marks if "ts=" in b]
            if tsd:
                newest_strip_epoch = max(int(t or 0) for t, _b in strip_marks)
                latest_pin = _last_pin_added_ts(conn, tid)
                # A pin stamped NEWER than the newest strip mark re-arms; an
                # older (skewed or back-dated) stamp is noise the gate must not
                # chase.
                if not (latest_pin is not None and latest_pin > newest_strip_epoch):
                    continue
            # mark without a parseable timestamp — trust it
        # A give-up mark (SKILLS_FILTER_FAILS_MAX consecutive failed removals) is
        # the loop breaker: a poison card is the human's problem until they clear
        # it. But the mark is a point in time, not a verdict forever — a pin
        # landed AFTER the give-up (a newer skills_added event) re-arms the gate,
        # and the failure count is consecutive-since-re-arm, not cumulative: the
        # count window opens at the re-arm, so a recovered card cannot carry its
        # old failures into the next window.
        giveup_marks = [(t, b) for _a, b, t in threads if b.startswith(SKILLS_FILTER_GIVEUP_MARK)]
        if giveup_marks:
            latest_pin_g = _last_pin_added_ts(conn, tid)
            re_arms = (latest_pin_g is not None
                       and latest_pin_g > max(int(t or 0) for t, _b in giveup_marks))
            if not re_arms:
                continue  # capped or unparseable — a human owns this card from here
        reg = _profile_skills(who)
        if reg is None:
            # missing skills dir is a legitimate skip (fail-open, never strip to
            # zero); a READ failure is the same value but must be visible — an
            # unreadable registry otherwise masks the card's foreign pins every
            # tick with no signal
            base = HERMES / "profiles" / who / "skills"
            if base.is_dir():
                log(f"skills filter {tid}: {who}'s skill dir exists but could not be read — skipped (no strip)")
            continue
        filter_skills = _skills_filter_fn()
        if filter_skills is None:
            return n      # logged once; this step stands aside, the rest of the tick runs
        dropped = filter_skills(pins, reg)
        if dropped:
            log(f"skills filter {tid}: Dropped foreign skills: {dropped} (not in {who}'s registry)")
        if not dropped:
            continue
        if dry:
            log(f"skills filter {tid}: would drop {' '.join(dropped)} (not in {who}'s registry)")
            n += 1
            continue
        run = runner or (lambda args: subprocess.run(args, capture_output=True, text=True,
                                                     timeout=60, check=False))
        r = run([str(SKILLS_ADD), tid, *dropped, "--remove",
                 "--why", f"reassigned to {who}: pin absent from the {who} profile's skill directory"])
        rc = getattr(r, "returncode", 1)
        # A failed removal (any nonzero rc — kanban-skills-add.py returns 2 for an
        # unknown card but we cannot tell that from a locked DB here) burns one
        # failure count, tracked in task_events as `gate:skills_filter_fail`.
        # The count is CONSECUTIVE-since-re-arm, not cumulative: every re-arm
        # (a newer skills_added event than the newest strip/give-up mark) carries
        # a timestamp, and a failure older than that re-arm belongs to the
        # previous window — a recovered card must not carry its old failures
        # into the next one. Until the cap: the card re-arms next tick (the pin
        # stays unstripped, the removal is retried).
        if rc != 0:
            rearm_ts = _last_pin_added_ts(conn, tid) or 0
            window_fails = sum(1 for (etype, ecreated,) in conn.execute(
                "SELECT kind, created_at FROM task_events "
                "WHERE task_id = ? AND kind = 'gate:skills_filter_fail'", (tid,))
                if int(ecreated or 0) >= rearm_ts)
            log(f"skills filter {tid}: kanban-skills-add rc={rc} "
                f"{(getattr(r, 'stdout', '') or '')[:80]} — failure {window_fails + 1}/{SKILLS_FILTER_FAILS_MAX}")
            conn.execute(
                "INSERT INTO task_events (task_id, kind, payload) VALUES (?, ?, ?)",
                (tid, "gate:skills_filter_fail",
                 json.dumps({"rc": rc, "dropped": dropped,
                             "ts": time.strftime('%Y-%m-%dT%H:%M:%S')},
                            sort_keys=True)))
            window_fails += 1
            if window_fails >= SKILLS_FILTER_FAILS_MAX and not giveup_marks:
                # one give-up mark per window — the marks are the visible log a
                # human reads/clears; the event count alone cannot carry it
                kanban("comment", tid,
                       f"{SKILLS_FILTER_GIVEUP_MARK} ts={time.strftime('%Y-%m-%dT%H:%M:%S')}: "
                       f"remove failed rc={rc} ({', '.join(dropped)} still unstripped) — "
                       f"stops after {SKILLS_FILTER_FAILS_MAX} consecutive failures",
                       "--author", GATE_AUTHOR)
            continue
        n += 1
        lines = [f"{SKILLS_FILTER_MARK} ts={time.strftime('%Y-%m-%dT%H:%M:%S')}: "
                 f"dropped {len(dropped)} pin(s) the {who} profile "
                 "cannot load (a pin the registry cannot resolve is a guaranteed rc=1 at preload):"]
        lines += [f"- Dropped {s}: not present in {who} profile" for s in dropped]
        row = conn.execute("SELECT skills FROM tasks WHERE id = ?", (tid,)).fetchone()
        after = _skills_of(row[0]) if row else []
        if not row:
            log(f"skills filter {tid}: card gone after strip — comment skipped (nothing to verify)")
        else:
            lines.append(f"Skills now on the card: {', '.join(after) if after else '(none)'}")
            crec, cout = kanban("comment", tid, "\n".join(lines), "--author", GATE_AUTHOR)
            if crec != 0:
                # Pins are gone but the card has NO strip mark — the loss is now
                # visible on the gate's side (a silent partial-failure strip is the
                # state the gate exists to log). The card stays unmarked, so the
                # next tick re-arms on the card's pins (now empty → no-op).
                log(f"skills filter {tid}: strip landed but the log comment failed "
                    f"rc={crec}: {cout.strip()[:120]}")
    return n


def _engine_up(url: str = os.environ.get("KREW_CREW_ENGINE_MODELS_URL", "http://127.0.0.1:8005/v1/models")) -> bool:
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def liveness_ctx(conn: sqlite3.Connection, *, tw=None) -> dict:
    """What the readers' clocks depend on, measured once per tick."""
    total, _per = ada_caps_on_disk()
    try:
        running = conn.execute("SELECT count(*) FROM tasks WHERE status = 'running'").fetchone()[0]
    except sqlite3.Error:
        running = 0
    salad_recent = False
    try:
        tw = tw or _tripwire()
        st = tw._read_state()
        salad_recent = bool(st.get("hits")) and (time.time() - float(st.get("last_seen") or 0)) < 15 * 60
    except Exception:  # noqa: BLE001
        pass
    off = False
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "window" / "lib"))
        import krew_hours
        import datetime as _dt
        win = (krew_hours.off_hours(None) or {}).get("window") or ""
        off = bool(win) and krew_hours.contains(_dt.datetime.now(), win)
    except Exception:  # noqa: BLE001
        pass
    mode = ""
    try:
        mode = (HERMES / "krew-gpu-mode").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return {"board": "", "max_in_progress": total, "running": int(running or 0), "engine_up": _engine_up(),
            "engine_started": _unit_started(CREW_ENGINE_UNIT), "ada_up": _unit_active(ADA_GATEWAY),
            "crissa_up": _unit_active(CRISSA_UNIT), "salad_recent": salad_recent,
            "off_hours": off, "render_window_open": off, "serving_stop_open": off and mode == "normal",
            "gpu_mode": mode, "default_wake_days": DEFAULT_WAKE_DAYS, "act": LIVENESS_ACT}


def gate_cause(conn: sqlite3.Connection, *, dry: bool = False, now: float | None = None) -> int:
    """Write ONE `cause: harness|verb|real` line per failed run (Principle 2, the missing writer).

    "a failure is classified before it is acted on" — the readers were built and shipped
    (three KPIs, the liveness gate's `cause: verb` branch) and nothing ever wrote the input.

    Measured over the three days to 2026-09-22: 496 failed runs, **76.2 % harness** — 204 of
    them a mis-calibrated pre-spawn tool cap refusing the spawn before the worker existed.
    Those cards did nothing wrong, and 25 of them burned their retry budget to the breaker.
    Classifying the first one is the difference between a board that releases them and a hand
    that does it at 03:00.

    Bounded on purpose: only runs that ended inside CAUSE_WINDOW_S, at most CAUSE_MAX per
    tick, never an archived card, one comment per run (the run id is the idempotency key).
    """
    if not CAUSE_ON:
        return 0
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import kanban_cause as kanban_cause          # noqa: PLC0414 (same shape as the gates above)
    now = time.time() if now is None else now
    # `summary` is not on every board's task_runs (older boards, and the gate's own test
    # fixture). Ask for what is there rather than losing the whole classification to one
    # missing column — the error text alone classifies 100 % of the live rows.
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(task_runs)")}
    except sqlite3.OperationalError:
        cols = set()
    if not {"id", "task_id", "outcome", "ended_at"} <= cols:
        return 0
    summary = "r.summary" if "summary" in cols else "''"
    error = "r.error" if "error" in cols else "''"
    try:
        rows = list(conn.execute(
            f"SELECT r.id, r.task_id, r.outcome, {error}, {summary} FROM task_runs r "
            "JOIN tasks t ON t.id = r.task_id "
            "WHERE r.ended_at IS NOT NULL AND r.ended_at >= ? AND r.outcome IN (%s) "
            "AND t.status != 'archived' ORDER BY r.ended_at DESC"
            % ",".join("?" * len(kanban_cause.FAILED_OUTCOMES)),
            (now - CAUSE_WINDOW_S, *kanban_cause.FAILED_OUTCOMES)))
    except sqlite3.OperationalError as exc:
        log(f"cause: cannot read the runs ({exc})")
        return 0
    # One line per (card, cause) — NOT per run. A card that is retried four times against the
    # same harness fault says one thing, not four; four identical comments are the "one idea
    # filed many times" noise this house already has a law about. A LATER failure with a
    # different cause is a different thing and does get its own line.
    seen: dict[tuple[str, str, str], tuple[int, int]] = {}   # (tid,cause,why) -> (run, count)
    for run_id, tid, outcome, error, summary in rows:
        c = kanban_cause.classify(outcome, error, summary)
        if c is None:
            continue                       # no evidence: never invent `real` out of silence
        key = (tid, c.cause, c.why)
        run, count = seen.get(key, (run_id, 0))
        seen[key] = (max(run, run_id), count + 1)            # rows are newest-first
    n = 0
    for (tid, cause, why), (run_id, count) in seen.items():
        if n >= CAUSE_MAX:
            break
        body = kanban_cause.comment(kanban_cause.Cause(cause, why), run_id, runs=count)
        if _marked(conn, tid, f"{CAUSE_MARK} {cause} — {why}"):
            continue
        log(f"cause {tid}: {cause} — {why} ({count} run(s), newest {run_id})")
        n += 1
        if dry:
            continue
        kanban("comment", tid, body, "--author", GATE_AUTHOR)
    _cause_breaker(conn, kanban_cause, dry=dry, now=now)
    return n


def _cause_breaker(conn: sqlite3.Connection, kanban_cause, *, dry: bool, now: float) -> int:
    """gate_cause's second half: the per-card breaker. Returns the number of trips seen."""
    mode = breaker_mode()
    LAST_BREAKER.update(n=0, mode=mode)
    if mode == "off":
        return 0
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(task_runs)")}
    except sqlite3.OperationalError:
        cols = set()
    error = "coalesce(r.error,'')" if "error" in cols else "''"
    summary = "coalesce(r.summary,'')" if "summary" in cols else "''"
    try:
        rows = list(conn.execute(
            f"SELECT r.id, r.task_id, r.outcome, {error}, {summary}, r.ended_at "
            "FROM task_runs r JOIN tasks t ON t.id = r.task_id "
            "WHERE r.ended_at IS NOT NULL AND r.ended_at >= ? AND t.status NOT IN ('archived','done')",
            (now - BREAKER_WINDOW_H * 3600,)))
    except sqlite3.OperationalError as exc:
        log(f"breaker: cannot read the runs ({exc})")
        return 0
    found = kanban_cause.trips(rows, now=now, window_s=BREAKER_WINDOW_H * 3600, threshold=BREAKER_RUNS)
    LAST_BREAKER["n"] = len(found)
    acted = []
    for tr in found:
        held = _marked(conn, tr.task_id, BREAKER_MARK)
        log(f"breaker {tr.task_id}: {tr.runs} runs, {tr.key}"
            + (" (held before)" if held else "" if mode == "act" else " (report-only)"))
        if mode != "act" or held or dry:
            continue
        row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tr.task_id,)).fetchone()
        status = str(row[0] if row else "")
        line = kanban_cause.breaker_line(tr, BREAKER_WINDOW_H)
        if status == "blocked":
            kanban("unblock", tr.task_id, "--reason", f"{BREAKER_MARK} releasing to re-block for Crissa")
        if status in ("blocked", "ready", "running"):
            # hermes blocks only running/ready; the reason is a trailing positional — reason FIRST
            rc, out = kanban("block", tr.task_id, line, "--kind", "needs_input")
            if rc != 0:
                log(f"breaker {tr.task_id}: block refused ({out[:100]})")
        kanban("comment", tr.task_id, line, "--author", GATE_AUTHOR)
        acted.append(tr.task_id)
    if not dry:
        try:
            report = HERMES / BREAKER_REPORT_NAME
            report.parent.mkdir(parents=True, exist_ok=True)
            tmp = report.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "ts": int(now), "mode": mode, "runs": BREAKER_RUNS, "window_h": BREAKER_WINDOW_H,
                "trips": [tr._asdict() for tr in found], "acted": acted}, indent=1) + "\n")
            tmp.replace(report)
        except OSError as exc:
            log(f"breaker: report not written ({exc})")
    return len(found)


def gate_overdue(conn: sqlite3.Connection, reg: dict, *, dry: bool = False,
                 now: float | None = None) -> dict:
    """Law 1's second clause: a card past its reader's deadline escalates ONE rung, once.

    Strike 1-2 is silent. At the third identical verdict the card gets ONE `gate: overdue`
    comment naming its reader, how long it has been overdue and whose rung it is next — and
    then it goes QUIET, dropping out of the `overdue=[...]` log line until the verdict
    changes. That is the difference between a signal and 139 copies of one.

    It deliberately does NOT re-route a card that another gate already owns: an `ownerless`
    row has `gate_ownerless`'s own two-strike ladder, and a `parents:*` row is waiting on a
    prerequisite, which is not a fault of the card. Those are marked and quieted, not acted
    on. Routing beyond the comment is the next arming step, not this one.

    Returns {"quiet": [ids already escalated], "new": n}.
    """
    out = {"quiet": [], "new": 0}
    if not reg.get("overdue"):
        return out
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import gate_strikes as gs
    now = time.time() if now is None else now
    st = gs.load(STRIKES_PATH)
    rows = {r["id"]: r for r in reg.get("rows") or []}
    for tid in reg["overdue"]:
        r = rows.get(tid) or {}
        reader = str(r.get("reader") or "?")
        verdict = f"overdue:{reader}"
        n = gs.strike(st, "liveness", tid, verdict, now=now)
        if n < gs.ESCALATE_AT:
            continue
        if n > gs.ESCALATE_AT or _marked(conn, tid, f"{OVERDUE_MARK} ({reader})"):
            out["quiet"].append(tid)          # already escalated: stay quiet, do not repeat
            continue
        if out["new"] >= OVERDUE_MAX:
            continue
        mins = int(r.get("overdue_min") or 0)
        body = (f"{OVERDUE_MARK} ({reader}): past its reader's deadline by {mins} min, "
                f"{gs.ESCALATE_AT} ticks running. {r.get('why') or ''}".strip())
        log(f"{'would escalate' if not LIVENESS_ESCALATE else 'escalate'} {tid}: "
            f"overdue {mins} min, reader={reader}")
        out["new"] += 1
        out["quiet"].append(tid)
        if dry or not LIVENESS_ESCALATE:
            continue
        kanban("comment", tid, body, "--author", GATE_AUTHOR)
    gs.prune(st, "liveness", reg["overdue"], now=now)
    if not dry and not gs.save(st, STRIKES_PATH):
        # Loud on purpose: a strike counter that cannot persist reads back 0 every tick,
        # never reaches three, and the escalation silently never happens.
        log(f"liveness: CANNOT WRITE the strike counter {STRIKES_PATH} — "
            f"overdue cards will be re-reported forever until this is fixed")
    return out


def gate_liveness(conn: sqlite3.Connection, *, dry: bool = False, ctx: dict | None = None,
                  now: float | None = None, path: Path | None = None) -> tuple[int, int, int]:
    """Phase 2: compute the reader registry, write it for the Daily, and — only with
    KREW_LIVENESS_ACT=1 — land the wakes: a wake that came true is an `unblock`; a bare park's
    default wake asks Crissa (never respawns). Returns (overdue, none, due)."""
    if not LIVENESS_ON:
        return 0, 0, 0
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import kanban_liveness as kv
    now = time.time() if now is None else now
    ctx = ctx if ctx is not None else liveness_ctx(conn)
    reg = kv.compute(conn, ctx, now)
    if reg.get("error"):
        log(f"liveness: {reg['error']}")
        return 0, 0, 0
    path = path or LIVENESS_PATH
    if not dry:
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(reg, indent=1, default=str), encoding="utf-8")
            tmp.replace(path)
        except OSError as e:
            log(f"liveness: cannot write {path}: {e}")
    overdue = gate_overdue(conn, reg, dry=dry, now=now)
    loud = [i for i in reg["overdue"] if i not in overdue["quiet"]]
    if reg["none"] or loud:
        log(f"liveness: none={reg['none'][:6]} overdue={loud[:8]}"
            + (f" (+{len(overdue['quiet'])} escalated, quiet)" if overdue["quiet"] else ""))
    if LIVENESS_ACT and not dry:
        for r in reg["rows"]:
            if not r.get("wake_due"):
                continue
            wake = r.get("wake") or {}
            if wake.get("default"):
                if _marked(conn, r["id"], WAKE_MARK + " asked"):
                    continue
                kanban("unblock", r["id"], "--reason", f"{WAKE_MARK}: parked {DEFAULT_WAKE_DAYS} d without a wake; asking Crissa")
                kanban("block", r["id"], f"Parked {DEFAULT_WAKE_DAYS} d without a wake. Rule: drop | keep {DEFAULT_WAKE_DAYS} more | try.",
                       "--kind", "needs_input")
                kanban("comment", r["id"], f"{WAKE_MARK} asked: bare park, {DEFAULT_WAKE_DAYS} d — on the Decide queue", "--author", GATE_AUTHOR)
                continue
            if wake.get("kind") == "until" and r["status"] == "scheduled" and any(
                    a == "kanban-loop" and b.lower().startswith("hold:") for a, b, _t in _thread(conn, r["id"])):
                continue                                # the loop's hold: its closer releases and closes it
            kanban("unblock", r["id"], "--reason", f"{WAKE_MARK} {wake.get('kind')}: the wake condition holds")
            kanban("comment", r["id"], f"{WAKE_MARK} {wake.get('kind')} landed — released by the board gate", "--author", GATE_AUTHOR)
    return len(reg["overdue"]), len(reg["none"]), len(reg["due"])


def gate_salad(conn: sqlite3.Connection, *, dry: bool = False, tw=None) -> int:
    """Detect salad runs in the board's worker logs, mark the burned cards, release them.
    The tripwire's functions, on this tick, without a reset (see the module docstring)."""
    tw = tw or _tripwire()
    if tw.OFF.is_file():
        return 0
    try:
        slug = tw.current_board()
    except Exception as exc:
        log(f"salad: no board ({exc})")
        return 0
    st = tw._read_state()
    since = float(st.get("last_seen") or st.get("last_reset") or 0)
    mutes: list[str] = []
    clear: list[str] = []
    hits = tw.salad_sessions(tw.board_dir(slug) / "logs", tw.WINDOW_MIN * 60, mutes, clear, since=since)
    n = 0
    if hits:
        log(f"salad: {len(hits)} session(s) {hits}" + (f" — clearly garbage {clear}" if clear else ""))
        tw.mark_salad_parks(slug, hits, dry=dry)
        n += tw.release_burned(slug, hits, dry=dry,
                               why="Prefix caching and MTP are off on crew (2026-09-18); nothing to reset — released at once.")
        if not dry:
            st.update({"last_seen": time.time(), "hits": hits, "board": slug})
            tw._write_state(st)
        if SALAD_RELAPSE_ON:
            n += salad_relapse_card(slug, hits, clear, dry=dry)
    db = tw.board_dir(slug) / "kanban.db"
    if STRAGGLERS_ON:
        # 6c: no engine reset happens any more, so `last_reset` never moves and stragglers() could
        # never fire; a straggler is a salad park older than STRAGGLER_MIN that is still releasable.
        late = tw.stragglers(db, time.time() - STRAGGLER_MIN * 60)
    else:
        late = tw.stragglers(db, float(st.get("last_reset") or 0))
    if late:
        go, left = tw.releasable(db, late) if hasattr(tw, "releasable") else (late, {})
        seen = dict(st.get("left") or {})
        changed = False
        for tid, why_left in left.items():
            if seen.get(tid) != why_left:
                log(f"release: leave {tid} — {why_left}")     # once per (card, why), not every 2 min
                seen[tid] = why_left
                changed = True
        for tid in list(seen):
            if tid not in left:
                seen.pop(tid, None)
                changed = True
        if changed and not dry:
            st["left"] = seen
            tw._write_state(st)
        if go:
            n += tw.release_burned(slug, go, dry=dry,
                                   why="Parked before the last engine reset and never released.")
    return n


ORPHANS_STATE = HERMES / "kanban-orphans-termed.json"
ORPHANS_REPORT = HERMES / "kanban-orphans.json"


def _gateway_mainpids() -> list[int]:
    out = []
    topo = _topology()
    units = [topo.HOST_UNIT] if topo else [f"hermes-{u}-gateway" for u in ("ada", "crissa", "lila", "steward")]
    for unit in units:
        try:
            r = subprocess.run(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"],
                               capture_output=True, text=True, timeout=10)
            if r.stdout.strip().isdigit():
                out.append(int(r.stdout.strip()))
        except (OSError, subprocess.SubprocessError):
            pass
    return out


def gate_orphans(conn: sqlite3.Connection, *, dry: bool = False, procs=None, now: float | None = None,
                 mainpids=None, signaller=None) -> int:
    """A card's leftover worker processes (lib/kanban_orphans; adversarially reviewed 2026-09-19).
    REPORT-ONLY until the User sets KREW_ORPHAN_REAP=1; 0 turns it off. Returns the leftover count."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import kanban_orphans as ko
    m = ko.mode()
    if m == "off":
        return 0
    try:
        board = (HERMES / "kanban" / "current").read_text(encoding="utf-8").strip()
    except OSError:
        board = ""
    found = ko.find(conn, ko.read_procs() if procs is None else procs, board=board, now=now,
                    gateway_mainpids=_gateway_mainpids() if mainpids is None else mainpids)
    left = [f for f in found if f.leftover]
    for f in found:
        if f.leftover:
            log(f"orphan {f.card} pid={f.pid} (+{len(f.descendants)} desc) {f.why}"
                f"{'' if m == 'act' else ' -> would TERM (report-only)'}")
    if not dry:
        try:
            ORPHANS_REPORT.write_text(json.dumps({"at": time.time(), "mode": m, "rows": [
                {"card": f.card, "pid": f.pid, "run": f.run_id, "leftover": f.leftover, "why": f.why}
                for f in found]}, indent=1), encoding="utf-8")
        except OSError:
            pass
    if m != "act" or dry or not left:
        return len(left)
    ok, why = ko.may_act()
    if not ok:
        log(f"orphans: not acting — {why}")
        return len(left)
    rw = sqlite3.connect(f"file:{board_db()}?mode=ro", uri=True)
    try:
        def recheck(card: str):
            r = rw.execute("SELECT status FROM tasks WHERE id = ?", (card,)).fetchone()
            return r[0] if r else None
        kw = {} if signaller is None else {"signaller": signaller}
        for line in ko.act(left, ORPHANS_STATE, recheck, **kw):
            log(line)
    finally:
        rw.close()
    for f in left:
        kanban("comment", f.card, f"gate: orphan pid {f.pid} stopped — {f.why}", "--author", GATE_AUTHOR)
    return len(left)


# --- CRISSA DOWN (2026-09-21) --------------------------------------------------
# Crissa is the single point of contact by design, and every self-clearing path ends
# at her: the Decide watch is a cron INSIDE her gateway, the reframe rule asks her
# when a reframe is archived or turns into a project, and the steward sweep reaches
# a verdict it has no standing to execute. So the one fault that stalls the board —
# a Render hold parks ops, her brain — is the same fault that disables everything
# built to unstall it. On 2026-09-20 that cost the board 12 `needs_input` cards.
#
# This is a FALLBACK, not a bypass: only while her gateway is actually down, only on
# a card nobody has touched for a day, and only the ONE verb that is reversible —
# `archive` keeps the card and its whole thread readable and `archive --rm` is never
# called, so a wrong call here is a search away from being undone, unlike a park that
# simply never ends. Widen at your own risk with KREW_CRISSA_DOWN_VERBS.
CRISSA_UNIT = os.environ.get("KREW_FRONT_DOOR_UNIT", "hermes-gateway.service")
CRISSA_DOWN_ON = _on("KREW_CRISSA_DOWN_FALLBACK")
CRISSA_DOWN_MARK = "gate: crissa down"
CRISSA_DOWN_H = float(os.environ.get("KREW_CRISSA_DOWN_H", "24"))
CRISSA_DOWN_MAX = int(os.environ.get("KREW_CRISSA_DOWN_MAX", "10"))
CRISSA_DOWN_VERBS = {v.strip().lower() for v in
                   os.environ.get("KREW_CRISSA_DOWN_VERBS", "archive").split(",") if v.strip()}


def _sweeper_verb(thread: list[tuple[str, str, int]],
                  sweepers: set[str]) -> tuple[str, str, int] | None:
    """A sweeper's newest `VERB:` line that no gate mark has executed since."""
    verb: tuple[str, str, int] | None = None
    for a, b, t in thread:
        if b.startswith(ROUTED_MARK) or b.startswith(CRISSA_DOWN_MARK):
            verb = None
            continue
        if a not in sweepers:
            continue
        hits = VERB_RE.findall(b)
        if hits:
            v, arg = hits[-1]
            verb = (v.lower(), " ".join(arg.split()), t)
    return verb


def gate_crissa_down(conn: sqlite3.Connection, *, dry: bool = False,
                   now: float | None = None) -> int:
    """Execute a sweeper's verdict on a stale card while Crissa cannot be asked."""
    if not CRISSA_DOWN_ON or _unit_active(CRISSA_UNIT):
        return 0
    now = time.time() if now is None else now
    sweepers = {"steward", orchestrator()}
    n = 0
    try:
        rows = list(conn.execute(
            "SELECT id, title FROM tasks WHERE status IN ('blocked', 'scheduled') ORDER BY created_at"))
    except sqlite3.OperationalError as exc:
        log(f"crissa-down: cannot read the board ({exc})")
        return 0
    for tid, title in rows:
        if n >= CRISSA_DOWN_MAX:
            break
        thread = _thread(conn, tid)
        if any(a == GATE_AUTHOR and b.startswith(OWNERLESS_MARK) for a, b, _t in thread):
            continue                       # gate_routed owns the ownerless batch
        found = _sweeper_verb(thread, sweepers)
        if not found:
            continue
        verb, arg, _at = found
        if verb not in CRISSA_DOWN_VERBS:
            continue
        last = max([t for _a, _b, t in thread] or [0])
        if (now - last) < CRISSA_DOWN_H * 3600:
            continue                       # give her time to come back and rule it herself
        log(f"crissa-down {tid}: a sweeper said {verb} {arg[:50]!r}, untouched "
            f"{(now - last) / 3600:.0f} h, her gateway is down {str(title)[:40]!r}")
        n += 1
        if dry:
            continue
        note = (f"{CRISSA_DOWN_MARK} — Crissa's gateway is down, so the Decide queue cannot be ruled. "
                f"A sweep already reached a verdict on this card ({verb}) and nothing has touched it "
                f"for {(now - last) / 3600:.0f} hours, so the gate is carrying that verdict out. "
                "Nothing is deleted: the card and its thread stay readable and a board search still "
                "finds it. If this was wrong, unarchive it and rule it again.")
        kanban("comment", tid, note, "--author", GATE_AUTHOR, dry=dry)
        rc, out = kanban("archive", tid, dry=dry)
        if rc != 0:
            log(f"crissa-down {tid}: archive refused ({out.strip()[:120]})")
            n -= 1
    return n


def _update_hold(gate: str) -> str:
    """A gate whose tests failed after a hermes update runs report-only for 24 h (lib/gate_hold)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    try:
        import gate_hold
        return gate_hold.report_only(gate)
    except Exception as exc:                     # the hold must never break the tick
        log(f"gate_hold: {type(exc).__name__}: {exc}"[:160])
        return ""


# A HERMES CRON JOB THAT FAILS THREE TIMES IN A ROW GETS ONE CARD (the User 2026-09-30). Crissa's Decide watch
# failed at every 10-minute tick for two days (211 times) and only her jobs.json knew. hermes counts the
# streak itself (`failure_streak`); lib/cron_streak reads it for every profile. One triage card to Ada per
# streak: the state file remembers the streak it filed for and forgets it when the job runs clean again.
# No board read is needed, so it runs even when the board db is away. Kill: KREW_CRON_STREAK_CARD=0.
CRON_STREAK_ON = os.environ.get("KREW_CRON_STREAK_CARD", "1").strip().lower() not in ("0", "false", "no", "off")
CRON_STREAK_STATE = HERMES / "cron-streak-cards.json"


def gate_cron_streak(*, dry: bool = False, state_path: Path | None = None, home: Path | None = None) -> int:
    """Cards filed this tick (dry: cards that would be filed). A library that will not import is logged, once a tick."""
    try:
        lib = str(Path(__file__).resolve().parent.parent / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import cron_streak   # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log(f"cron streak: ERROR lib/cron_streak.py would not import ({type(e).__name__}: {e})"[:200])
        return 0
    path = state_path or CRON_STREAK_STATE
    rows = cron_streak.failing(home or HERMES)
    state = cron_streak.settle(rows, cron_streak.load_state(path))
    filed = 0
    for row in cron_streak.due(rows, state):
        title, body = cron_streak.card_for(row)
        key = f"cron-streak:{row['profile']}:{row['id']}:{time.strftime('%Y%m%d')}"
        rc, out = kanban("create", title, "--assignee", "ada", "--triage", "--body", _kc.cut(body, 100000, "the cards it names have the rest"),
                         "--created-by", GATE_AUTHOR, "--idempotency-key", key, dry=dry)
        if rc != 0:
            log(f"cron streak: card for {row['profile']}:{row['name']} did not file ({out.strip()[:120]})")
            continue
        m = re.search(r"\bt_[0-9a-f]{6,}\b", out)
        log(f"gate: cron streak -> {m.group(0) if m else 'card'} ({row['profile']}:{row['name']} failed {row['streak']}x)")
        filed += 1
        if not dry:
            cron_streak.mark(state, row, m.group(0) if m else "")
    if not dry:
        try:
            cron_streak.save_state(path, state)
        except OSError as e:
            log(f"cron streak: state not saved ({e})"[:160])
    return filed


# THE HOUSE'S MINIMUM VERSIONS SURVIVE A HERMES UPDATE DONE ANY WAY (the User 2026-09-30). hermes rebuilds its
# environment from upstream's lock at every update (PyJWT back to 2.13.0); `hermes-tree update` re-pins, and
# this is the net for an update that did not go through it. The live environment is asked only when a
# facts.json under ~/.hermes/installs has moved since the last look (a new environment generation), so a quiet
# tick costs one stat. Below a pin: install it, log it, owe the gateway a restart. Kill: KREW_ENV_PINS_GATE=0
# (or KREW_HERMES_ENV_PINS=0 for every caller).
ENV_PINS_ON = os.environ.get("KREW_ENV_PINS_GATE", "1").strip().lower() not in ("0", "false", "no", "off")
ENV_PINS_STATE = HERMES / "hermes-env-pins.json"


def gate_env_pins(*, dry: bool = False, state_path: Path | None = None, pins_lib=None) -> int:
    """Dists re-pinned this tick (dry: dists that are below). -1 = could not tell."""
    try:
        if pins_lib is None:
            lib = str(Path(__file__).resolve().parent.parent / "lib")
            if lib not in sys.path:
                sys.path.insert(0, lib)
            import hermes_env_pins as pins_lib   # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log(f"env pins: ERROR lib/hermes_env_pins.py would not import ({type(e).__name__}: {e})"[:200])
        return -1
    path = state_path or ENV_PINS_STATE
    stamp = pins_lib.facts_stamp()
    try:
        seen = float(json.loads(path.read_text()).get("facts_stamp") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        seen = -1.0
    if stamp == seen:
        return 0
    st = pins_lib.status() if dry else pins_lib.apply(by=GATE_AUTHOR)
    if st.get("error"):
        log(f"env pins: unknown ({st['error']})"[:200])
        return -1                                   # not recorded: the next tick asks again
    for a in st.get("applied") or []:
        log(f"gate: env pin {a['dist']} {a['was']} -> {a.get('now')} (rc {a['rc']}; gateway restart owed)")
    n = len(st.get("below") or []) if dry else sum(1 for a in st.get("applied") or [] if a["rc"] == 0)
    if not dry and not st.get("below"):             # still below: not recorded, the next tick tries again
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"facts_stamp": stamp, "checked": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                       "versions": st.get("versions")}, indent=1))
            os.replace(tmp, path)
        except OSError as e:
            log(f"env pins: state not saved ({e})"[:160])
    return n


# A CREW RELAPSE RULED GO IS OWED TO THE RESTART WINDOW, NEVER A WORKER CARD (the User 2026-09-30). The relapse card
# asks Crissa go | wait | later | park, but only a restart-owed row reaches the window's executor, and nothing wrote one:
# on 09-30 Ada turned her 10:33 `go` into a bash card and the worker restarted crew under running workers at 10:49 (and
# got past the approvals refusal with a script). Here her go becomes the row, dated at the ruling, so a crew start after
# it has already paid it; the window files its card, she rules there, and the executor restarts crew after its
# workers finish. Kill: KREW_SALAD_GO_OWES=0.
SALAD_GO_ON = os.environ.get("KREW_SALAD_GO_OWES", "1").strip().lower() not in ("0", "false", "no", "off")
SALAD_GO_MARK = "gate: crew restart owed to the restart window"
SALAD_GO_DAYS = 7


def _crew_started() -> float | None:
    try:
        r = subprocess.run(["systemctl", "--user", "show", "-P", "ActiveEnterTimestamp", "--timestamp=unix",
                            CREW_ENGINE_UNIT], capture_output=True, text=True, timeout=10)
        v = (r.stdout or "").strip().lstrip("@")
        return float(v) if v and v != "0" else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def gate_salad_go(conn: sqlite3.Connection, *, dry: bool = False, now: float | None = None, rw=None,
                  started=None, owed_path: Path | None = None) -> int:
    """Rows owed this tick. A go that crew has already restarted past is left alone (no row, no comment)."""
    now = time.time() if now is None else now
    try:
        if rw is None:
            lib = str(Path(__file__).resolve().parent.parent / "lib")
            if lib not in sys.path:
                sys.path.insert(0, lib)
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "window" / "lib"))
            import restart_window as rw   # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log(f"salad go: ERROR lib/restart_window.py would not import ({type(e).__name__}: {e})"[:200])
        return 0
    rows = conn.execute(
        "SELECT id, created_at FROM tasks WHERE title LIKE 'Restart window: crew engine relapse%' "
        "AND status != 'archived' AND created_at >= ?", (int(now - SALAD_GO_DAYS * 86400),)).fetchall()
    owed = 0
    for tid, _created in rows:
        if _marked(conn, tid, SALAD_GO_MARK):
            continue
        comments = [{"author": a, "body": b, "created_at": c} for a, b, c in conn.execute(
            "SELECT author, body, created_at FROM task_comments WHERE task_id = ? ORDER BY id", (tid,))]
        ruled = rw.latest_ruling(comments)
        if not ruled or ruled["verdict"] != "go":
            continue
        start = (started or _crew_started)()
        if start is not None and start >= float(ruled["ts"]):
            continue                                # crew already restarted after the go
        why = f"crew salad relapse ruled go by {ruled['who']} on {tid}"
        log(f"gate: salad go -> {CREW_ENGINE_UNIT} owed ({why})")
        if not dry:
            rw.owe_restart(CREW_ENGINE_UNIT, why, GATE_AUTHOR, since=float(ruled["ts"]),
                           **({"path": owed_path} if owed_path else {}))
            kanban("comment", tid, f"{SALAD_GO_MARK}: {why}. The window's executor restarts crew after its "
                   "workers finish; no worker card restarts it.", "--author", GATE_AUTHOR)
        owed += 1
    return owed


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    held = _update_hold("kanban-board-gate")
    if held:
        log(f"report-only: {held} (lib/gate_hold; kill KREW_GATE_UPDATE_HOLD=0)")
        dry = True
    pin = gate_board_pin(dry=dry) if BOARD_PIN_ON else ""
    try:
        cs = gate_cron_streak(dry=dry) if CRON_STREAK_ON else 0
    except Exception as e:                           # a jobs file must never break the tick
        log(f"cron streak: {type(e).__name__}: {e}"[:160])
        cs = -1
    try:
        ep = gate_env_pins(dry=dry) if ENV_PINS_ON else 0
    except Exception as e:                           # never break the tick
        log(f"env pins: {type(e).__name__}: {e}"[:160])
        ep = -1
    db = board_db()
    if db is None:
        log("no board db")
        return 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        a = gate_assignee(conn, dry=dry) if ASSIGNEE_GATE_ON else 0
        v = gate_verb_wall(conn, dry=dry) if VERB_WALL_ON else 0
        r = gate_render(conn, dry=dry) if RENDER_GATE_ON else 0
        rv = gate_routed(conn, dry=dry) if ROUTED_ON else 0
        o = gate_ownerless(conn, dry=dry) if OWNERLESS_ON else 0
        lp = gate_later_parks(conn, dry=dry) if LATER_PARK_ON else 0
        w = gate_review(conn, dry=dry) if REVIEW_ON else 0
        b = gate_balance(conn, dry=dry) if BALANCE_ON else 0
        c = sync_ada_caps(conn, dry=dry) if CAPS_SYNC_ON else 0
        sl = gate_salad(conn, dry=dry) if SALAD_ON else 0
        try:
            sg = gate_salad_go(conn, dry=dry) if SALAD_GO_ON else 0
        except Exception as e:                       # never break the tick
            log(f"salad go: {type(e).__name__}: {e}"[:160])
            sg = -1
        zd = gate_crissa_down(conn, dry=dry)
        dp = gate_dups(conn, dry=dry)
        sk = gate_skills_inherit(conn, dry=dry)
        sf = gate_skills_filter(conn, dry=dry)
        cz = gate_cause(conn, dry=dry)
        lo, ln, ld = gate_liveness(conn, dry=dry)
        try:
            orp = gate_orphans(conn, dry=dry)
        except Exception as e:                       # a /proc read must never break the tick
            log(f"orphans: {type(e).__name__}: {e}"[:160])
            orp = -1
    finally:
        conn.close()
    log(f"tick assignee={a} verb_wall={v} render_gate={r} hold={'on' if HOLD.is_file() else 'off'} dry={dry} "
        f"ownerless={o} later_park={lp} routed={rv} review={w} balance={b} caps={'owed' if c else 'ok'} salad={sl} salad_go={sg} "
        f"crissa_down={zd} dups={dp} skills={sk} filter={sf} cause={cz} cron_streak={cs} env_pins={ep} "
        f"breaker={LAST_BREAKER['n']}{'' if LAST_BREAKER['mode'] == 'act' else ' (' + LAST_BREAKER['mode'] + ')'} "
        f"{'board_pin=' + pin + ' ' if pin else ''}liveness={lo}/{ln}/{ld}{'' if LIVENESS_ACT else ' (report-only)'} "
        f"orphans={orp}{'' if os.environ.get('KREW_ORPHAN_REAP', 'report').strip().lower() in ('1', 'on', 'true', 'yes', 'act') else ' (report-only)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
