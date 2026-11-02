#!/usr/bin/env python3
"""kanban-loop — the two loops that let a card work itself out.

the User 2026-09-12: "they should have an algorithm to work themselves out of
nearly everything if it can go back through the Decomposer loop … before he
fails final he writes up a new frame to rethink the issue from higher-level
framing." This is that net, mechanical, beside the crew — it never replaces
the worker's own `frame:` comment, it consumes it.

Tick (krew-kanban-loop.timer, every 20 min; off switch ~/.hermes/kanban-loop.off
or KANBAN_LOOP=0). Reads the current board's sqlite read-only, writes only
through `hermes kanban` so every move is an audited event.

  GREEN   a card filed `gate: green` (team-improve / architect-try) that Crissa
          answered on the Decide queue. Her first word decides:
            try   -> cut `try: <title>` as TRIAGE (auto-decompose takes it),
                     archive the gate card
            park  -> archive the gate card (her words stay on it)
            later -> block `dependency` until the date, then back on the queue
          A gate card that is unblocked with no answer goes back on the queue.

  REFRAME a card the dispatcher has failed >= REFRAME_AT times the same way
          (kanban diagnostics: repeated_failures), a sticky ``gave_up``
          breaker park (verb wall trips at 1; patch 70 will not retry — that
          already *is* the repeat), or one a worker blocked after writing
          `frame:` itself. Once per card:
            - frame  : the worker's `frame:` comment if there is one, else the
                       reframer model (crew :8005), else a mechanical frame
                       that still asks the six questions — the decomposer can
                       think from that
            - cut    : `reframe: <title>` as TRIAGE with the frame as body,
                       generation +1; the old card is blocked `dependency`
                       "superseded by …" so it stops burning
            - retreat: at generation REFRAME_MAX_GEN the loop stops cutting
                       and blocks the card needs_input with both frames as
                       the question — Crissa rules (try again / park / hands)

  PARK    (2026-09-17) a Park ruling is sticky on EVERY card, not only gates.
          Crissa's or the User's latest ruling `park` (a comment, `UNBLOCK: park`,
          or a run summary "Per Crissa … (UNBLOCK: Park)") -> `schedule` the card
          with the ruling as the reason, comment once, schedule any children
          kanban-loop cut after the ruling, and never reframe, decompose,
          re-queue or cut from it until a different ruling (try | later <date>).
          Gate cards keep their archive. Kill: KANBAN_LOOP_PARK_STICKY=0.

  GEN     The reframe generation and source live where the triage specifier
          does not rewrite: a kanban-loop comment `reframe-gen: N source: <id>`
          on the cut and the idempotency key `reframe:<source>:g<N>`; the body
          is the fallback. A decomposed child inherits its root's generation.

Retreat spots: the off switch; REFRAME_AT=99 (no reframes); MAX_CUTS_PER_TICK;
ARCHITECT_GREEN_GATE=0 on the filer (no gate cards at all). Nothing here
deletes; archive is the only terminal verb and it keeps the thread.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "janitor"))  # krew: gate_hold / gate_strikes live with the janitor

HERMES = Path.home() / ".local" / "bin" / "hermes"
CURRENT = KH.kanban_dir() / "current"
BOARDS = KH.kanban_dir() / "boards"
OFF_SWITCH = KH.hermes_home() / "kanban-loop.off"
LOG = KH.hermes_home() / "logs" / "kanban-loop.log"

REFRAME_AT = int(os.environ.get("REFRAME_AT", "3"))          # failures before a frame
REFRAME_MAX_GEN = int(os.environ.get("REFRAME_MAX_GEN", "2"))  # reframe of a reframe, then Crissa
MAX_CUTS_PER_TICK = int(os.environ.get("MAX_CUTS_PER_TICK", "3"))
ORCHESTRATOR = os.environ.get("KANBAN_LOOP_ORCHESTRATOR", "ada")
LLM_URL = os.environ.get("KANBAN_LOOP_LLM_URL", "http://127.0.0.1:8005/v1/chat/completions")
LLM_MODEL = os.environ.get("KANBAN_LOOP_LLM_MODEL", "crew")
LLM_TIMEOUT = float(os.environ.get("KANBAN_LOOP_LLM_TIMEOUT", "180"))
# Phase 1 of docs/KANBAN-SELF-HEALING-20260918.md (the User 2026-09-18 "go ahead and land this").
# Every rule below has its kill; the old behaviour is one env away.
THINK_MAX_TOKENS = int(os.environ.get("KANBAN_LOOP_THINK_MAX_TOKENS", "3000"))   # room for reasoning + 600 of content
THINK_EFFORT = os.environ.get("KANBAN_LOOP_THINK_EFFORT", "medium")
NOFRAME_RETREAT_AT = int(os.environ.get("KANBAN_LOOP_NOFRAME_RETREAT_AT", "3"))  # no-frame ticks before Crissa


def _on(env: str, default: str = "1") -> bool:
    return os.environ.get(env, default).strip().lower() not in ("0", "false", "no", "off")


def hold_source_on() -> bool:            # item 1: link + schedule the source (kill KANBAN_LOOP_HOLD_SOURCE=0)
    return _on("KANBAN_LOOP_HOLD_SOURCE")


def mechanical_cuts_on() -> bool:        # item 2: old rule — a mechanical frame mints (KANBAN_LOOP_MECHANICAL_CUTS=1)
    return _on("KANBAN_LOOP_MECHANICAL_CUTS", "0")


def frame_any_author_on() -> bool:       # item 3: old rule — any `frame:` counts (KANBAN_LOOP_FRAME_ANY_AUTHOR=1)
    return _on("KANBAN_LOOP_FRAME_ANY_AUTHOR", "0")


def skip_capability_on() -> bool:        # item 4: never reframe a NEEDS-HUMAN card (kill KANBAN_LOOP_SKIP_CAPABILITY=0)
    return _on("KANBAN_LOOP_SKIP_CAPABILITY")


def ruling_parks_on() -> bool:           # item 4: `hands` / a future `later` park the card for the loop (kill KANBAN_LOOP_RULING_PARKS=0)
    return _on("KANBAN_LOOP_RULING_PARKS")


def cut_once_on() -> bool:               # item 4: old rule — a cut is forever (KANBAN_LOOP_CUT_ONCE=1)
    return _on("KANBAN_LOOP_CUT_ONCE", "0")


def map_pin_on() -> bool:                # skills once per thread: every loop create pins `<assignee>-map` (kill KANBAN_LOOP_MAP_PIN=0)
    return _on("KANBAN_LOOP_MAP_PIN")


def frame_hold_on() -> bool:             # item 5: a todo frame behind open children is held, never run (kill KANBAN_LOOP_FRAME_HOLD=0)
    return _on("KANBAN_LOOP_FRAME_HOLD")


def triage_guard_on() -> bool:           # item 7: a card already on the Decide queue gets a `decide:` comment, never a re-block (kill KANBAN_LOOP_TRIAGE_GUARD=0)
    return _on("KANBAN_LOOP_TRIAGE_GUARD")


def decompose_looped_on() -> bool:       # item 7: old rule — decompose a block_loop_detected triage card (KANBAN_LOOP_DECOMPOSE_LOOPED=1)
    return _on("KANBAN_LOOP_DECOMPOSE_LOOPED", "0")


def ask_promotes_on() -> bool:
    return _on("KANBAN_LOOP_ASK_PROMOTES")

GATE_MARK = "gate: green"
# Whole line only — substring match also hit `gate: greened` on try: children
# and the next tick blocked them as "Unblocked without a ruling" (2026-09-15).
GATE_LINE_RE = re.compile(r"^gate: green\s*$", re.M)
GEN_RE = re.compile(r"^reframe-gen:\s*(\d+)\s*$", re.M)
SOURCE_RE = re.compile(r"^source:\s*(t_[0-9a-f]{8})\s*$", re.M)
# The marker the specifier cannot rewrite: a kanban-loop comment on the cut.
GEN_MARK_RE = re.compile(r"^reframe-gen:\s*(\d+)(?:\s+source:\s*(t_[0-9a-f]{8}))?", re.I)
# reframe:<source>:g<N> (2026-09-17) and the older reframe-<source>-<N>.
IDEM_RE = re.compile(r"^reframe(?::|-)(t_[0-9a-f]{8})(?::g|-)(\d+)$")
CUT_RE = re.compile(r"^reframe-cut:\s*(t_[0-9a-f]{8})", re.I)
# Card ids named inside a ruler's park reason ("park — waiting on t_abcd1234"). Used to ask
# her again when the thing she cited has finished. Not for acting on: only for re-asking.
CITED_ID_RE = re.compile(r"\bt_[0-9a-f]{8}\b")
PREMISE_MARK = "park-premise:"
CREATED_RE = re.compile(r"\b(t_[0-9a-f]{8})\b")
LATER_RE = re.compile(r"^\s*later\b[\s:,-]*(\d{4}-\d{2}-\d{2})?", re.I)

FRAME_QUESTIONS = (
    "1. What was being attempted, in one sentence?",
    "2. Which failure class repeated (same error, same step)?",
    "3. What assumption did every attempt share?",
    "4. Two or three framings one level up that drop that assumption.",
    "5. The smallest test that tells those framings apart.",
    "6. Is any part of this hands or a body in the room? If not: software.",
)

REFRAME_SKILL = Path(os.environ.get(
    "REFRAME_SKILL",
    # krew: the checkout's skills/, else the copy krew-install put in the Hermes skills folder
    str(next((p for p in (Path(__file__).resolve().parents[2] / "skills" / "krew-reframe" / "SKILL.md",
                          KH.hermes_home() / "skills" / "krew-reframe" / "SKILL.md") if p.is_file()),
             Path(__file__).resolve().parents[2] / "skills" / "krew-reframe" / "SKILL.md")),
))


def reframer_system(skill_path: Path = REFRAME_SKILL) -> str:
    """One method, three callers: the worker reads the skill, the loop's
    model gets the same Method section, Crissa rules by the same numbers.
    Falls back to the six questions when the skill file is missing."""
    base = (
        "You are the reframer on a small engineering crew. A kanban card failed "
        "the same way several times. Do not solve it. Write the frame one level "
        "up, plain text, no markdown headings, under 220 words, answering exactly "
        "these six points in order:\n" + "\n".join(FRAME_QUESTIONS)
    )
    try:
        text = skill_path.read_text(encoding="utf-8")
    except OSError:
        return base
    m = re.search(r"^## Method\s*$(.*?)^## ", text, re.S | re.M)
    if not m:
        return base
    return base + "\n\nThe house's method, which you follow exactly:\n" + m.group(1).strip()


REFRAMER_SYSTEM = reframer_system()


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} kanban-loop: {msg}"
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# --- board reads (sqlite, read-only) --------------------------------------

def board_db() -> Path | None:
    slug = "daily"
    if CURRENT.is_file():
        slug = CURRENT.read_text(encoding="utf-8").strip() or slug
    p = BOARDS / slug / "kanban.db"
    return p if p.is_file() else None


@dataclass
class Card:
    id: str
    title: str
    body: str
    status: str
    assignee: str
    block_kind: str
    consecutive_failures: int
    created_by: str
    comments: list[tuple[str, str]] = field(default_factory=list)   # (author, body) oldest first
    last_error: str = ""
    last_event_kind: str = ""
    breaker_parked: bool = False  # last of (gave_up, unblocked) is gave_up
    idempotency_key: str = ""
    created_at: int = 0
    comment_at: list[int] = field(default_factory=list)            # parallel to comments
    summaries: list[tuple[int, str]] = field(default_factory=list)  # (ts, run summary / block reason)
    decompose_of: str = ""   # root id from the `created` event of a decomposed child


OPEN_CARD_STATUSES = ("ready", "blocked", "triage", "todo", "running", "in_progress", "scheduled")


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def read_cards(db: Path, statuses: tuple[str, ...] | None = OPEN_CARD_STATUSES) -> list[Card]:
    """Open cards (default) or every card (statuses=None, the lineage index)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tables = _tables(con)
    cols = _cols(con, "tasks")
    def col(name: str, default: str = "''") -> str:
        return name if name in cols else f"{default} AS {name}"
    where, params = "", ()
    if statuses is not None:
        where = f"WHERE status IN ({','.join('?' for _ in statuses)}) "
        params = tuple(statuses)
    rows = con.execute(
        f"SELECT id, title, body, status, {col('assignee')}, {col('block_kind')}, "
        f"{col('consecutive_failures', '0')}, {col('created_by')}, {col('last_failure_error')}, "
        f"{col('idempotency_key')}, {col('created_at', '0')} "
        f"FROM tasks {where}ORDER BY id", params
    ).fetchall()
    ccols = _cols(con, "task_comments") if "task_comments" in tables else set()
    ecols = _cols(con, "task_events") if "task_events" in tables else set()
    rcols = _cols(con, "task_runs") if "task_runs" in tables else set()
    out: list[Card] = []
    for r in rows:
        c = Card(
            id=r["id"], title=r["title"] or "", body=r["body"] or "", status=r["status"] or "",
            assignee=r["assignee"] or "", block_kind=str(r["block_kind"] or ""),
            consecutive_failures=int(r["consecutive_failures"] or 0),
            created_by=str(r["created_by"] or ""), last_error=str(r["last_failure_error"] or ""),
            idempotency_key=str(r["idempotency_key"] or ""), created_at=int(r["created_at"] or 0),
        )
        if ccols:
            at = "created_at" if "created_at" in ccols else "0"
            crow = con.execute(
                f"SELECT author, body, {at} AS at FROM task_comments WHERE task_id = ? ORDER BY id", (c.id,)
            ).fetchall()
            c.comments = [(str(x["author"] or ""), str(x["body"] or "")) for x in crow]
            c.comment_at = [int(x["at"] or 0) for x in crow]
        if ecols:
            ev = con.execute(
                "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1", (c.id,)
            ).fetchone()
            c.last_event_kind = str(ev["kind"]) if ev else ""
            # Same predicate as hermes `_breaker_parked`: a gave_up that has not
            # been released by unblock_task. Kind-none is the usual stamp (the
            # breaker UPDATE never writes block_kind) but is not the class —
            # untyped worker kanban_block() is also NULL.
            br = con.execute(
                "SELECT kind FROM task_events WHERE task_id = ? "
                "AND kind IN ('gave_up', 'unblocked') ORDER BY id DESC LIMIT 1",
                (c.id,),
            ).fetchone()
            c.breaker_parked = bool(br) and str(br["kind"]) == "gave_up"
            if "payload" in ecols:
                at = "created_at" if "created_at" in ecols else "0"
                for e in con.execute(
                    f"SELECT kind, payload, {at} AS at FROM task_events WHERE task_id = ? "
                    "AND kind IN ('created', 'blocked', 'block_loop_detected') ORDER BY id", (c.id,)
                ).fetchall():
                    try:
                        pl = json.loads(e["payload"] or "{}") or {}
                    except (json.JSONDecodeError, TypeError):
                        pl = {}
                    if not isinstance(pl, dict):
                        continue
                    if e["kind"] == "created":
                        c.decompose_of = str(pl.get("from_decompose_of") or "")
                    elif pl.get("reason"):
                        c.summaries.append((int(e["at"] or 0), str(pl["reason"])))
        if {"summary", "task_id"} <= rcols:
            at = "COALESCE(ended_at, started_at)" if {"ended_at", "started_at"} <= rcols else "0"
            for x in con.execute(
                f"SELECT summary, {at} AS at FROM task_runs WHERE task_id = ? AND summary IS NOT NULL ORDER BY id",
                (c.id,),
            ).fetchall():
                c.summaries.append((int(x["at"] or 0), str(x["summary"] or "")))
        out.append(c)
    con.close()
    return out


def index_of(cards: list[Card]) -> dict[str, Card]:
    return {c.id: c for c in cards}


# --- CLI writes ------------------------------------------------------------

PROFILE = os.environ.get("KANBAN_LOOP_PROFILE", "ada")


class Cli:
    """Every write is `hermes -p ada kanban …`; tests hand in a fake.

    A bare `hermes` runs as the ACTIVE profile (Crissa), whose kanban model
    pins were ops :8000 — `decompose` from here failed APIConnectionError
    while ops was parked (measured 07:52). The board is Ada's."""

    def __init__(self, hermes: Path = HERMES, dry: bool = False, profile: str = PROFILE):
        self.hermes, self.dry, self.profile = hermes, dry, profile

    def __call__(self, *args: str) -> tuple[int, str]:
        argv = [str(self.hermes), "-p", self.profile, "kanban", *args]
        if self.dry:
            log("dry: " + " ".join(a if len(a) < 60 else a[:57] + "…" for a in argv[4:]))
            return 0, "Created t_00000000"
        r = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=60)
        return r.returncode, (r.stdout or "") + (r.stderr or "")


def created_id(out: str) -> str:
    m = CREATED_RE.search(out or "")
    return m.group(1) if m else ""


def create_pinned(cli: "Cli", *args: str, assignee: str) -> tuple[int, str]:
    """`kanban create …` with `--skill <assignee>-map` (the User 2026-09-18: skills once per thread,
    the map preloaded so the worker never spends a round finding it). `--skill` validates
    against the CALLING profile's registry (patch 10), so an unknown map is retried bare."""
    if not map_pin_on():
        return cli("create", *args)
    rc, out = cli("create", *args, "--skill", f"{assignee}-map")
    if rc != 0 and "skill" in (out or "").lower():
        log(f"map pin {assignee}-map refused ({out.strip()[:100]}); creating without it")
        return cli("create", *args)
    return rc, out


def block_args(tid: str, kind: str, reason: str) -> tuple[str, ...]:
    """`hermes kanban block task_id [reason ...] --kind K`: the reason is a
    trailing nargs=* positional, so argparse has already closed it (empty) when
    it meets --kind and a reason AFTER the flag is "unrecognized arguments".
    Measured 2026-09-17: every typed block this loop and the architect filer
    ever issued died that way — no gate was blocked at birth, no retreat
    reached Crissa, no superseded source stopped burning. Reason first."""
    return ("block", tid, reason, "--kind", kind)


# --- GREEN -----------------------------------------------------------------

def _after_unblock(text: str) -> str:
    """Drop a leading UNBLOCK: so 'UNBLOCK: try — …' is a try (2026-09-15)."""
    t = (text or "").strip()
    m = re.match(r"^unblock\b[\s:,.;!-]*", t, re.I)
    return t[m.end():].strip() if m else t


def done_verb_on() -> bool:
    """PARK HAD NO CLOSE VERB (2026-09-21). `archive` retires a card the ruler is
    finished with; nothing said "this is FINISHED — close it as done". So a park
    whose work landed had one exit, by hand: `unblock` then `complete`, two verbs
    with a dispatcher window between them, and hermes refuses `complete` on
    `scheduled` outright (kanban_db.complete_task: running|ready|blocked|review).
    Measured on t_84fa8600 — four children done, Crissa's own ruling "superseded",
    7 runs parked, and it was holding 4 live cards as the board's top blocking
    park. `done` cannot be `archive`: an archived card is not `done`, and
    `run_complete_frames` counts only `done`, so archiving a decompose child
    freezes its frame for good (t_0e36e644 did exactly that to t_2593eae2).
    Kill KANBAN_LOOP_DONE_VERB=0 to put those words back to meaning nothing."""
    return os.environ.get("KANBAN_LOOP_DONE_VERB", "1").strip().lower() not in ("0", "false", "no", "off")


def archive_verb_on() -> bool:
    """ARCHIVE MEANS ARCHIVE (2026-09-21). Until today `archive` and `drop` were
    aliases of `park`, so the one word that means "retire this card" pinned it in
    `scheduled` instead — and nothing else ever retires a park (day-close reads
    `done` only). Ten cards on the 09-16 board were parked by their own archive
    ruling, one of them after the steward wrote `VERB: archive` on it in as many
    words. Kill KANBAN_LOOP_ARCHIVE_VERB=0 to put the alias back."""
    return os.environ.get("KANBAN_LOOP_ARCHIVE_VERB", "1").strip().lower() not in ("0", "false", "no", "off")


def verdict_of(text: str) -> tuple[str, str]:
    """('try'|'park'|'archive'|'later'|'hands'|'', text) from a ruling's first word."""
    t = (text or "").strip()
    rest = _after_unblock(t)
    w = re.split(r"[\s:,.;!-]+", rest.lower(), 1)[0] if rest else ""
    if w in ("try", "yes", "green", "go"):
        return "try", t
    if w in ("archive", "drop"):
        return ("archive" if archive_verb_on() else "park"), t
    # ONE spelling on purpose (trimmed from five, 2026-09-21 pm, the User: "does it
    # simply bloat complexity by n combinations"). Five synonyms multiplied the
    # false-positive surface on a ruler's prose for no added meaning, and `done` is
    # the word her watch prompt names. `complete` is not it either: a ruler opens
    # sentences with it ("complete the smoke first"), and only the FIRST word is read.
    if w == "done" and done_verb_on():
        return "done", t
    if w in ("park", "no"):
        return "park", t
    if LATER_RE.match(rest) or LATER_RE.match(t):
        return "later", rest if LATER_RE.match(rest) else t
    if w == "hands":
        return "hands", t
    return "", t


def gate_answer(card: Card) -> tuple[str, str]:
    """('try'|'park'|'later'|'', text) from Crissa's last comment on a gate card."""
    for author, body in reversed(card.comments):
        if author.lower() != "crissa":
            continue
        verdict, t = verdict_of(body)
        # A gate asks "green this try?"; `hands` and `done` are not answers to it,
        # so the gate re-asks exactly as it did before either word existed.
        return ("" if verdict in ("hands", "done") else verdict), t
    return "", ""


# --- PARK (sticky on every card) --------------------------------------------

LOOP_AUTHOR = "kanban-loop"
PARK_MARK = "park-sticky:"
SCHEDULABLE = ("todo", "ready", "running", "blocked")
# A worker's summary that carries the ruling: "Per Crissa 2026-09-16 23:03 (UNBLOCK: Park): …"
SUMMARY_RULING_RE = re.compile(
    r"^\s*per\s+(crissa|the\s+user|human)\b[^\n]{0,60}?[(:]\s*(?:unblock:\s*)?(try|park|later|hands)\b", re.I)


def park_sticky_on() -> bool:
    return os.environ.get("KANBAN_LOOP_PARK_STICKY", "1").strip().lower() not in ("0", "false", "no", "off")


def rulers() -> set[str]:
    raw = os.environ.get("KANBAN_LOOP_RULERS", os.environ.get("KREW_RULERS", "crissa,user,human,dashboard"))
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def hand_unblock_on() -> bool:
    """A HAND'S UNBLOCK IS A RULING (2026-09-21 pm). `run_park` re-reads the latest
    ruling, and an unblock carrying no verdict left `park` as the latest — so the
    loop re-parked the card 20 min later, enforcing a ruler's older WORD against her
    own newer ACT. Crissa unblocks more than anyone (109 of the last 200 audit lines)
    and it was the one act the loop would undo. Scoped to a hand: a ruler's line, or
    the bare CLI (`default` = a terminal; every gate script names its profile — the
    board gate and render-window run as `ada`, 84 of those 200). Kill
    KANBAN_LOOP_HAND_UNBLOCK=0."""
    return os.environ.get("KANBAN_LOOP_HAND_UNBLOCK", "1").strip().lower() not in ("0", "false", "no", "off")


def rulings(card: Card) -> list[tuple[int, int, str, str, str]]:
    """Every ruling on the card, oldest first: (ts, seq, verdict, who, text).

    A ruler's comment (Crissa / the User), an ``UNBLOCK: <verdict>`` by anyone
    but this loop (that is the unblock verb's own audit line — how a human
    releases a park), or a run summary that quotes the ruling."""
    who_rules = rulers()
    out = []
    for i, (author, body) in enumerate(card.comments):
        a = (author or "").lower()
        if a == LOOP_AUTHOR:
            continue
        t = (body or "").strip()
        if a not in who_rules and not re.match(r"^unblock\b", t, re.I):
            continue
        verdict, _ = verdict_of(t)
        if not verdict and hand_unblock_on() and re.match(r"^unblock\b", t, re.I) \
                and (a in who_rules or a == "default"):
            verdict = "try"      # a hand took it out of the park; do not put it back
        if verdict:
            ts = card.comment_at[i] if i < len(card.comment_at) else 0
            out.append((ts, i, verdict, author, t))
    for j, (ts, text) in enumerate(card.summaries):
        m = SUMMARY_RULING_RE.match(text or "")
        if m:
            out.append((ts, 10_000 + j, m.group(2).lower(), m.group(1), (text or "").strip()))
    out.sort(key=lambda r: (r[0], r[1]))
    return out


def park_ruling(card: Card) -> tuple[int, str, str] | None:
    """(ruling ts, who, text) when the latest ruling is park, else None.

    ts is the first park of the trailing streak — the moment the card was
    parked — so children cut after it are the loop's mistake to undo."""
    if not park_sticky_on():
        return None
    rs = rulings(card)
    if not rs or rs[-1][2] != "park":
        return None
    first = rs[-1]
    for r in reversed(rs):
        if r[2] != "park":
            break
        first = r
    return first[0], first[3], first[4]


def archive_ruling(card: Card) -> tuple[int, str, str] | None:
    """(ts, who, text) when a ruler's newest word is `archive`/`drop`, else None.

    Kept apart from `park_ruling` on purpose: a park is a card the krew still
    owes an answer on, an archive is a card the ruler has finished with.
    """
    if not archive_verb_on():
        return None
    rs = rulings(card)
    if not rs or rs[-1][2] != "archive":
        return None
    return rs[-1][0], rs[-1][3], rs[-1][4]


def done_ruling(card: Card) -> tuple[int, str, str] | None:
    """(ts, who, text) when a ruler's newest word is `done`, else None.

    Apart from `park_ruling` and `archive_ruling` for the same reason they are
    apart from each other: a park is a card the krew still owes an answer on,
    an archive is one the ruler has finished with, a `done` is one whose WORK
    landed — the only one of the three that an open frame may read as finished.
    """
    if not done_verb_on():
        return None
    rs = rulings(card)
    if not rs or rs[-1][2] != "done":
        return None
    return rs[-1][0], rs[-1][3], rs[-1][4]


HOLD_MARK = "hold:"


def loop_held(card: Card) -> bool:
    """A source (or a frame) this loop parked with `schedule` — not a ruler's park. The newest
    kanban-loop `hold:` comment is newer than any ruling; the hold's reader is run_complete_frames."""
    if card.status != "scheduled":
        return False
    hold_ts = None
    for i, (a, b) in enumerate(card.comments):
        if (a or "").lower() == LOOP_AUTHOR and (b or "").strip().lower().startswith(HOLD_MARK):
            hold_ts = (card.comment_at[i] if i < len(card.comment_at) else 0, i)
    if hold_ts is None:
        return False
    rs = rulings(card)
    if not rs:
        return True
    return hold_ts >= (rs[-1][0], rs[-1][1])


def hold_ruling(card: Card, today: date | None = None) -> bool:
    """A ruler's newest word is `hands`, or `later <date>` with the date still ahead: the card
    is theirs, not the loop's (t_ad140ba1's `later` lasted 38 s on 09-18; t_a44ce2b8 was
    reframed off the User's hands). Kill KANBAN_LOOP_RULING_PARKS=0."""
    if not ruling_parks_on():
        return False
    rs = rulings(card)
    if not rs:
        return False
    _ts, _i, verdict, _who, text = rs[-1]
    if verdict == "hands":
        return True
    if verdict == "later":
        d = later_date(text)
        return bool(d and d > (today or date.today()))
    return False


def is_parked(card: Card) -> bool:
    if card.status == "scheduled" and not loop_held(card):
        return True
    return (park_ruling(card) is not None or archive_ruling(card) is not None
            or hold_ruling(card))


def _stamp(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"


def cut_children(card: Card, index: dict[str, Card], since: int) -> list[Card]:
    """Cards kanban-loop cut from ``card`` at or after ``since`` (decompose
    children and reframe cuts), transitively."""
    out: list[Card] = []
    seen = {card.id}
    frontier = [card]
    while frontier:
        parent = frontier.pop()
        cut_ids = {m.group(1) for a, b in parent.comments
                   if (a or "").lower() == LOOP_AUTHOR and (m := CUT_RE.match((b or "").strip()))}
        for k in index.values():
            if k.id in seen:
                continue
            by_loop = k.created_by == LOOP_AUTHOR
            mine = (k.decompose_of == parent.id and by_loop) or k.id in cut_ids or (
                by_loop and source_of(k) == parent.id and own_generation(k) is not None)
            if mine and (not since or not k.created_at or k.created_at >= since):
                seen.add(k.id)
                out.append(k)
                frontier.append(k)
    return out


ARCHIVE_MARK = "archive-ruled:"


def run_archive_rulings(cards: list[Card], cli: Cli) -> int:
    """A ruler said `archive`/`drop`: retire the card instead of parking it.

    The one exit `scheduled` never had. Nothing is purged — hermes' `archive`
    keeps the card and its thread, and day-close never calls `archive --rm` —
    so a wrong call here is readable back, unlike a park that simply never ends.
    A gate card keeps its own path in run_green (archive there too).
    """
    if not archive_verb_on():
        return 0
    n = 0
    for c in cards:
        if c.status in ("done", "archived") or is_gate(c):
            continue
        ar = archive_ruling(c)
        if ar is None:
            continue
        ts, who, text = ar
        if any((a or "").lower() == LOOP_AUTHOR and (b or "").startswith(ARCHIVE_MARK)
               for a, b in c.comments):
            continue
        ruling = cut(re.sub(r"\s+", " ", text), 220, "the ruling comment has it whole")
        cli("comment", c.id,
            f'{ARCHIVE_MARK} {who} ruled archive {_stamp(ts)}: "{ruling}" — retired by kanban-loop. '
            "Nothing is purged: the card and its thread stay readable, and a board search still finds it.",
            "--author", LOOP_AUTHOR)
        rc, out = cli("archive", c.id)
        if rc == 0:
            c.status = "archived"
            n += 1
        log(f"archive {c.id}: {'ok' if rc == 0 else 'refused: ' + out[:120]}")
    return n


DONE_MARK = "done-ruled:"          # written ONLY after a successful close
DONE_REFUSED_MARK = "done-refused:"  # said once; never blocks the retry


def run_done_rulings(cards: list[Card], cli: Cli, *, index: dict[str, Card] | None = None) -> int:
    """A ruler said `done`: close the card as done, from whatever status it sits in.

    The close verb `scheduled` never had. hermes completes `running|ready|blocked|
    review` only, so a park's own close took two hand verbs with a dispatcher
    window between them — and a ruler who typed `done` on a parked card (Crissa did,
    twice) was writing to nobody. `close_card` does the whole move in one pass.

    Open children are reported, never a veto: the ruler is the authority on whether
    the work landed, and a frame above this card reads `done` either way. What this
    must never do is reach for `archive` when it cannot complete — an archived card
    is not `done` and freezes any frame that counts it.
    """
    if not done_verb_on():
        return 0
    index = index or index_of(cards)
    n = 0
    for c in cards:
        if c.status in ("done", "archived") or is_gate(c):
            continue
        dr = done_ruling(c)
        if dr is None:
            continue
        ts, who, text = dr
        ruling = cut(re.sub(r"\s+", " ", text), 220, "the ruling comment has it whole")
        open_kids = [k.id for k in index.values()
                     if k.decompose_of == c.id and k.status not in ("done", "archived")]
        why = (f'{who} ruled done {_stamp(ts)}: "{ruling}" — closed by kanban-loop'
               + (f' (open children left: {" ".join(open_kids[:6])})' if open_kids else ""))
        already_refused = any((a or "").lower() == LOOP_AUTHOR and (b or "").startswith(DONE_REFUSED_MARK)
                              for a, b in c.comments)
        rc, out = close_card(cli, c, why[:400], registry=index,
                             phantom_once=DONE_REFUSED_MARK if already_refused else None, triage_ok=True)
        if rc == 0:
            # THE MARK GOES ON AFTER THE CLOSE, NEVER BEFORE (2026-09-21 17:15).
            # It was written first and used as the idempotency key, so when the close
            # was refused the mark stayed and every later tick skipped the card —
            # "attempted" read as "done". t_57a06e7c: the unblock landed it in `todo`
            # behind a parent that closed three seconds later, complete refused, and
            # the retry that would have worked never came. Same shape as the 09-20
            # `ask-refused` lesson: a refused act must not leave the marker that means
            # it happened. A mark can now only mean the card IS closed, so the status
            # check at the top of the loop is the whole idempotency.
            cli("comment", c.id, f"{DONE_MARK} {why}", "--author", LOOP_AUTHOR)
            c.status = "done"
            n += 1
        elif not already_refused:
            # Said once, not every 20 minutes — and it never blocks the retry.
            cli("comment", c.id,
                f"{DONE_REFUSED_MARK} {who} ruled done {_stamp(ts)} but the card would not close "
                f"from `{c.status}`: {out.strip()}. The loop keeps trying; nothing is lost.",
                "--author", LOOP_AUTHOR)
        log(f"done {c.id}: {'ok' if rc == 0 else 'refused: ' + out[:120]}")
    return n


def run_park(cards: list[Card], cli: Cli, *, index: dict[str, Card] | None = None) -> int:
    """Park is sticky: schedule a parked card and what the loop cut after the ruling."""
    if not park_sticky_on():
        return 0
    index = index or index_of(cards)
    n = 0
    for c in cards:
        if c.status in ("done", "archived") or is_gate(c):
            continue   # a gate's park is its archive (run_green)
        pr = park_ruling(c)
        if pr is None:
            continue
        ts, who, text = pr
        marked = any((a or "").lower() == LOOP_AUTHOR and (b or "").startswith(PARK_MARK)
                     and (i >= len(c.comment_at) or c.comment_at[i] >= ts)
                     for i, (a, b) in enumerate(c.comments))
        ruling = cut(re.sub(r"\s+", " ", text), 220, "the ruling comment has it whole")
        reason = (f"park ruled by {who} {_stamp(ts)}: \"{ruling}\" — kanban-loop keeps it here "
                  "(no reframe, decompose or re-queue) until a different ruling: try | later <date>")
        moved = False
        if c.status in SCHEDULABLE:
            rc, out = cli("schedule", c.id, reason)
            moved = rc == 0
            if moved:
                c.status = "scheduled"
            log(f"park {c.id}: schedule {'ok' if moved else 'refused: ' + out[:120]}")
            n += int(moved)
        if marked:
            continue
        kids_done, kids_left = [], []
        for k in cut_children(c, index, ts):
            if k.status in SCHEDULABLE:
                rc, _out = cli("schedule", k.id,
                               f"parent {c.id} parked by {who} {_stamp(ts)}; kanban-loop cut this after the "
                               "ruling — it waits with its parent")
                (kids_done if rc == 0 else kids_left).append(k.id)
                if rc == 0:
                    k.status = "scheduled"
            elif k.status in ("triage", "review"):
                kids_left.append(k.id)
        note = f"{PARK_MARK} {who} ruled park {_stamp(ts)}; this card is not reframed, decomposed or re-queued"
        if c.status == "scheduled":
            note += "; scheduled"
        elif c.status == "triage":
            note += "; left in triage (hermes has no triage→scheduled verb) and never decomposed by kanban-loop"
        if kids_done:
            note += f". Children cut after the ruling, now scheduled: {', '.join(kids_done)}"
        if kids_left:
            note += f". Children left for a human (status would not schedule): {', '.join(kids_left)}"
        note += ". Release: unblock with a different ruling (try | later <date>)."
        cli("comment", c.id, note, "--author", LOOP_AUTHOR)
        n += len(kids_done)
    return n


def later_date(text: str) -> date | None:
    m = LATER_RE.match(_after_unblock(text or "")) or LATER_RE.match(text or "")
    if not m or not m.group(1):
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def is_gate(card: Card) -> bool:
    return bool(GATE_LINE_RE.search(card.body or ""))


# A try carries the KPI it should move and that KPI's value at Crissa's ruling (2026-09-19:
# 0 of the 09-18 tries named one, so nothing could be measured 7 days on).
# scripts/improvement-outcomes.py reads the `watch:` line. KANBAN_LOOP_TRY_BASELINE=0 = no stamp.
TRY_BASELINE = os.environ.get("KANBAN_LOOP_TRY_BASELINE", "1").strip().lower() not in ("0", "false", "no", "off")
KPIS_JSON = Path(os.environ.get("KREW_IMPROVE_DIR", str(Path.home() / ".krew" / "memory" / "improvements"))) / "kpis.json"
_KPI_NAMED = re.compile(r"\bkpi\s*[:=]\s*([a-z][a-z0-9_]*)", re.I)


def watch_line(title: str, body: str, *, kpis_path: Path | None = None, now: float | None = None) -> str:
    """`watch: kpi=<id> baseline=<v> ...` for a try card, from the KPI its gate names."""
    m = _KPI_NAMED.search(title or "") or _KPI_NAMED.search(body or "")
    kid = m.group(1).lower() if m else ""
    stamp = datetime.fromtimestamp(now or time.time()).isoformat(timespec="minutes")
    if not kid or kid in ("none", "unnamed"):
        return f"watch: kpi=none — no KPI named; this try cannot be measured (ruled {stamp})"
    try:
        k = (json.loads((kpis_path or KPIS_JSON).read_text()).get("kpis") or {}).get(kid) or {}
    except (OSError, ValueError, AttributeError):
        k = {}
    if not k or k.get("current") is None:
        return f"watch: kpi={kid} baseline=unknown — not in kpis.json or no sample yet (ruled {stamp})"
    return (f"watch: kpi={kid} baseline={k.get('current')} {k.get('unit') or ''} "
            f"direction={k.get('direction') or '?'} at the ruling {stamp}").replace("  ", " ")


def run_green(cards: list[Card], cli: Cli, *, today: date | None = None) -> int:
    today = today or date.today()
    n = 0
    for c in cards:
        if not is_gate(c) or c.status == "scheduled":
            continue
        verdict, text = gate_answer(c)
        if c.status == "blocked" and c.block_kind == "needs_input":
            if not verdict:
                continue  # on the queue; Crissa has not answered
            # She already ruled (UNBLOCK: try/park). Consume; do not re-ask.
        if c.status == "blocked" and c.block_kind == "dependency":
            d = later_date(text)
            if d and d <= today:
                cli("unblock", c.id, "--reason", f"later {d} reached — back on the Decide queue")
                cli(*block_args(c.id, "needs_input",
                                "Later date reached. Green this try? try | park | later <date>."))
                n += 1
            continue
        if c.status == "blocked" and not verdict:
            continue  # already blocked (transient / None); do not re-ask every tick
        if c.status in ("done", "archived"):
            continue
        if verdict == "try":
            body = (c.body.replace(GATE_MARK, "gate: greened") + f"\ncrissa: {text}\nsource: {c.id}\n"
                    + (watch_line(c.title, c.body) + "\n" if TRY_BASELINE else "")
                    + "Cut by kanban-loop from Crissa's try. Triage: decompose; Lila researches, bash implements.")
            rc, out = create_pinned(cli, f"try: {c.title[:150]}", "--triage", "--assignee", ORCHESTRATOR,
                                    "--body", cut(body, 100000, "the source card has the rest"), "--created-by", "kanban-loop",
                                    "--idempotency-key", f"green-try-{c.id}", assignee=ORCHESTRATOR)
            tid = created_id(out)
            cli("comment", c.id, f"green: try -> {tid or 'cut failed'}; archiving this gate card", "--author", "kanban-loop")
            if tid:
                cli("archive", c.id)
            n += 1
        elif verdict in ("park", "archive"):
            cli("comment", c.id, f"green: {verdict} by Crissa; archiving", "--author", "kanban-loop")
            cli("archive", c.id)
            n += 1
        elif verdict == "later":
            d = later_date(text)
            cli(*block_args(c.id, "dependency",
                            f"later {d or 'unset'}: kanban-loop puts it back on the Decide queue then"))
            n += 1
        else:
            # unblocked with no ruling: the question was never settled
            cli(*block_args(c.id, "needs_input",
                            "Unblocked without a ruling. Green this try? try | park | later <date>."))
            n += 1
    return n


RULERS = tuple(os.environ.get("KREW_RULERS", "crissa,user,human,dashboard").split(","))


def later_ruling(card: Card) -> date | None:
    """The date in a ruler's newest `later YYYY-MM-DD` on this card (her own words)."""
    for author, body in reversed(card.comments):
        if (author or "").strip().lower() not in RULERS:
            continue
        d = later_date(body or "")
        if d:
            return d
    return None


def later_parks_on() -> bool:
    """A DATED PARK IN `scheduled` HAD NO ALARM (2026-09-21). CLAUDE.md says a
    `later <date>` is "the alarm on any card", but `run_later` only ever looked at
    `blocked` + `dependency`; a ruling that landed in `scheduled` instead (the routed
    gate parks that way) kept its date as prose nothing reads. t_efb37f06 carries
    "until 2026-09-22" and would have sat there for good. Kill KANBAN_LOOP_LATER_PARKS=0."""
    return os.environ.get("KANBAN_LOOP_LATER_PARKS", "1").strip().lower() not in ("0", "false", "no", "off")


def parked_later_date(card: Card) -> date | None:
    """The date when a parked card's NEWEST ruling is `later <date>`, else None.

    Deliberately not `later_ruling`: that walks back to the first ruler comment
    carrying a date, so a card parked today over a `later` from last week would
    read as due. Only the trailing ruling counts.
    """
    rs = rulings(card)
    if not rs or rs[-1][2] != "later":
        return None
    return later_date(rs[-1][4])


def run_later(cards: list[Card], cli: Cli, *, today: date | None = None) -> int:
    """A `later <date>` on ANY card is a park with an alarm (the User 2026-09-18).

    team-crissa `answer` used to comment + unblock every `later`, so t_f2156ac4 ("later
    2026-09-18") was claimed and re-blocked untyped within a minute and nothing ever read
    the date. The plugin now holds it and re-blocks `dependency` with the date; this pass
    is the alarm: date reached -> back on the Decide queue as needs_input. Gate cards keep
    their own path in run_green (same move). Kill KANBAN_LOOP_LATER=0."""
    if os.environ.get("KANBAN_LOOP_LATER", "1").strip().lower() in ("0", "false", "no", "off"):
        return 0
    today = today or date.today()
    n = 0
    for c in cards:
        if is_gate(c):
            continue
        if c.status == "blocked" and c.block_kind == "dependency":
            d = later_ruling(c)
        elif c.status == "scheduled" and later_parks_on():
            d = parked_later_date(c)       # a dated park: `unblock` is its only exit
        else:
            continue
        if not d or d > today:
            continue
        cli("unblock", c.id, "--reason", f"later {d} reached — back on the Decide queue")
        rc, out = cli(*block_args(c.id, "needs_input",
                                  f"Later date {d} reached. Rule again: try | park | later <date> | hands."))
        if rc != 0:
            log(f"later {c.id}: re-block refused ({out[:120]})")
        n += 1
    return n


def prereqs(db: Path, card_id: str) -> list[str]:
    """What this card is WAITING ON. `task_links.parent_id` is the PREREQUISITE and
    `child_id` the DEPENDENT — the columns read the opposite way round to the words, and
    reading them the intuitive way inverts every blocking conclusion (09-21)."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if "task_links" not in _tables(con):
            return []
        return [r[0] for r in con.execute(
            "SELECT parent_id FROM task_links WHERE child_id = ?", (card_id,)).fetchall()]
    finally:
        con.close()


def run_dependency_clear(cards: list[Card], cli: Cli, db: Path, *, index: dict | None = None) -> int:
    """A `dependency` park whose prerequisites are ALL finished releases itself.

    `scheduled` is sticky: `recompute_ready` promotes a `todo` or `blocked` card the moment
    its parents go terminal, but it skips parks, and `unblock_task` is a park's only exit.
    So a card parked "waiting on X" stayed parked after X finished, with nobody watching —
    and because the park carries no `needs_input`, it never reached Crissa either. It is not a
    question for a ruler: the thing it was waiting for is simply over.

    Measured 2026-09-22 07:3x, with the board idle at 0 running and `0 of 4 todo cards
    dispatchable`: t_1ca673a5 waiting on t_dd0c68e6 (done) and t_e2b379c0 waiting on
    t_542ebba9 (done) + t_cc9bf076 (archived). Those two parks alone held five live cards.

    Guards: a card with NO recorded prerequisite is left alone (nothing to check — the wait
    may be prose only); a ruler's own park or `hands` outranks this and is skipped; and the
    release is `unblock`, which re-gates on parents itself, so a prerequisite this pass
    somehow misread lands the card back in `todo` rather than loose.
    Kill KANBAN_LOOP_DEPENDENCY_CLEAR=0.
    """
    if os.environ.get("KANBAN_LOOP_DEPENDENCY_CLEAR", "1").strip().lower() in ("0", "false", "no", "off"):
        return 0
    index = index if index is not None else {c.id: c for c in cards}
    n = 0
    for c in cards:
        if c.status != "scheduled" or c.block_kind != "dependency" or is_gate(c):
            continue
        if park_ruling(c) is not None or hold_ruling(c):
            continue                      # a ruler's word outranks the graph
        need = prereqs(db, c.id)
        if not need:
            continue                      # prose-only wait: not ours to judge
        open_ = [i for i in need
                 if i in index and index[i].status not in ("done", "archived", "cancelled")]
        if open_:
            continue
        done = ", ".join(need[:4])
        log(f"dependency clear {c.id}: every prerequisite finished ({done})")
        rc, out = cli("unblock", c.id, "--reason",
                      f"dependency clear: every prerequisite is finished ({done}). "
                      f"`scheduled` is sticky and recompute_ready skips parks, so nothing "
                      f"else would have released this.")
        if rc != 0:
            log(f"dependency clear {c.id}: refused ({out[:120]})")
            continue
        n += 1
    return n


def run_park_premise_expired(cards: list[Card], cli: Cli, *, index: dict | None = None) -> int:
    """A ruler parked a card FOR A REASON, and the reason has finished. Ask her again.

    Her word outranks the graph, so `run_dependency_clear` correctly skips a card she parked
    herself — but a park is a judgement about the world at a moment, and the world moves.
    Measured 2026-09-22: t_1ca673a5 carried Crissa's *"park — threshold acceptance t_4ca8a9bc
    is still in flight so the guard has nothing to point at yet"*, and t_4ca8a9bc is now
    `done`. Correct when she said it; stale now; and because a park carries no `needs_input`
    it would never have come back to her. It held three live cards.

    This does NOT release the card — releasing on a ruler's park is exactly what "a ruler's
    park outranks all of it" forbids. It puts the decision back where it belongs, ONCE per
    set of cited cards (the marker), with the evidence attached. The same lesson as "a card
    that cites a directory carries that listing's TIMESTAMP", in card form.
    Kill KANBAN_LOOP_PARK_PREMISE=0.
    """
    if os.environ.get("KANBAN_LOOP_PARK_PREMISE", "1").strip().lower() in ("0", "false", "no", "off"):
        return 0
    index = index if index is not None else {c.id: c for c in cards}
    n = 0
    for c in cards:
        if c.status != "scheduled" or is_gate(c):
            continue
        r = park_ruling(c)
        if r is None and premise_routed_on() and not rulings(c):
            r = routed_park(c)
        if r is None:
            continue
        who, text = r[1], r[-1]
        cited = [i for i in dict.fromkeys(CITED_ID_RE.findall(text or "")) if i != c.id]
        if not cited:
            continue
        # Every cited card must be finished AND at least one must be known to us, or a typo
        # in her note would read as "all done" and wake her for nothing.
        known = [i for i in cited if i in index or card_status_terminal(i, index)]
        if not known or any(i in index and index[i].status not in ("done", "archived", "cancelled")
                            for i in cited):
            continue
        mark = f"{PREMISE_MARK} {' '.join(cited[:4])}"
        if any(b.startswith(mark) for _a, b in c.comments):
            continue                       # asked once already for this exact premise
        log(f"park premise expired {c.id}: {who} cited {', '.join(cited[:3])} — now finished")
        cli("comment", c.id, mark, "--author", LOOP_AUTHOR)
        ask_crissa(cli, c,
                 f"You parked this: \"{(text or '').strip()}\". "
                 f"What you cited has since finished ({', '.join(cited[:4])}), so the reason "
                 f"for the park is over. Rule again: try | park | later <date> | done | archive.",
                 release_reason="park premise expired — the card it waited on has finished")
        n += 1
    return n


def premise_routed_on() -> bool:
    """2026-09-24: a park landed THROUGH THE GATE (Ada's `VERB: park …` → `gate: routed → park …`)
    is read for its premise like a ruler's. t_506667f7 was parked by Ada on 09-19 19:28 "awaiting
    t_9b4416ae prodgate apply"; t_9b4416ae was archived at 20:38 the same evening, and the park sat
    five days — `park_ruling` reads RULERS only, so the premise exit never looked, Crissa's Decide
    list shows a park only with needs_input, and liveness gives it a 7-day rope. It is NOT a ruler's
    park for stickiness (a ruler's later word still wins: the fallback runs only when no ruler has
    spoken on the card at all). Kill: KANBAN_LOOP_PREMISE_ROUTED=0."""
    return os.environ.get("KANBAN_LOOP_PREMISE_ROUTED", "1").strip().lower() not in ("0", "false", "no", "off")


ROUTED_PARK_RE = re.compile(r"^gate:\s*routed\s*(?:→|->)\s*park\b[\s:—-]*", re.I)
MACHINE_PARK_RE = re.compile(r"^(?:VERB:\s*park|SCHEDULED:)\s*[—:-]?\s*", re.I)


def routed_park(card: Card) -> tuple[int, str, str] | None:
    """(ts, who, text) of the newest park the GATE landed on this card, else None.

    Reads the gate's own `gate: routed → park <text>` line first (it is the landing), else the
    machine's `VERB: park <text>` / `SCHEDULED: <text>` line (the ask). `who` is the author of the
    machine line when there is one (ada), else the gate. Never a ruler's line — those are
    `park_ruling`'s."""
    text = ""; who = ""; ts = 0; found = False
    for i, (author, body) in enumerate(card.comments):
        a = (author or "").lower(); b = (body or "").strip()
        m = ROUTED_PARK_RE.match(b) if a == "kanban-board-gate" else None
        if m:
            text, who, ts, found = b[m.end():].strip(), who or a, (card.comment_at[i] if i < len(card.comment_at) else 0), True
            continue
        m = MACHINE_PARK_RE.match(b) if a not in RULERS and a != LOOP_AUTHOR and a != "kanban-board-gate" else None
        if m:
            text, who, ts, found = b[m.end():].strip(), a, (card.comment_at[i] if i < len(card.comment_at) else 0), True
    return (ts, who, text) if found else None


CYCLE_MARK = "park-cycle:"


def cycle_mode() -> str:
    """`report` (default: log + tick count, no board write) | `act` (mark once, ask Crissa).
    KANBAN_LOOP_CYCLE=off kills it. Arm a new gate report-only, always (User 2026-09-22)."""
    v = os.environ.get("KANBAN_LOOP_CYCLE", "report").strip().lower()
    if v in ("0", "false", "no", "off"):
        return "off"
    return "act" if v in ("1", "act", "enforce", "true", "yes") else "report"


def run_park_cycle(cards: list[Card], cli: Cli, db: Path, *, index: dict | None = None) -> int:
    """A todo card whose prerequisite is a PARK that names it (or that it names) is a cycle:
    the park waits on the card, the card waits on the park, and neither reader fires — a todo
    with an open prerequisite is "waiting on its parent" (not the ruler's, by design), and the
    park has no question and no date (7-day rope). 2026-09-24: t_ee5aa2e2 "deploy …, then unblock
    t_506667f7" was filed by the steward sweep with t_506667f7 as its PREREQUISITE (task_links
    parent_id), and t_506667f7 had been parked five days waiting for exactly that deploy.

    The question goes on the PARK (a scheduled card's exit is unblock → needs_input; asking on
    the todo is refused while its parent is open, and that refusal is right). Once per pair.
    Report-only until KANBAN_LOOP_CYCLE=act. Kill: KANBAN_LOOP_CYCLE=off."""
    mode = cycle_mode()
    if mode == "off":
        return 0
    index = index if index is not None else {c.id: c for c in cards}
    n = 0
    for t in cards:
        if t.status != "todo" or is_gate(t):
            continue
        own = f"{t.title}\n{t.body}"
        for pid in prereqs(db, t.id):
            p = index.get(pid)
            if p is None or p.status != "scheduled" or is_gate(p):
                continue
            r = park_ruling(p) or routed_park(p)
            park_text = (r[-1] if r else "") or ""
            names_me = t.id in CITED_ID_RE.findall(park_text)
            i_name_it = pid in CITED_ID_RE.findall(own)
            if not (names_me or i_name_it):
                continue
            mark = f"{CYCLE_MARK} {t.id}"
            if any(b.startswith(mark) for _a, b in p.comments):
                continue
            why = ("its park names the card" if names_me else "the card says it will release its park")
            log(f"park cycle {p.id} ⇄ {t.id}: {t.id} waits on the park, {why} [{mode}]")
            n += 1
            if mode != "act":
                continue
            cli("comment", p.id, f"{mark} — {t.id} waits on this park ({why}); neither can move", "--author", LOOP_AUTHOR)
            ask_crissa(cli, p,
                     f"A cycle: {t.id} \"{cut(t.title, 120, t.id + ' has the whole title')}\" has THIS card as its prerequisite, and {why}. "
                     f"Neither can move on its own. Rule: try | done | archive (archive {t.id} first, or it "
                     f"promotes when this one closes) | park.",
                     release_reason=f"park cycle with {t.id}")
    return n


def card_status_terminal(card_id: str, index: dict) -> bool:
    """A card absent from the OPEN index is closed — that is what `open cards` means here."""
    return card_id not in index


# --- REFRAME ---------------------------------------------------------------

def worker_frame(card: Card) -> str:
    """The worker's own `frame:` — by the card's assignee or a ruler. 120 of 130 `frame:` comments
    on the standing board were this loop's, 3 were steward's sweep; author-blind reading made the
    loop read back its own restatement of an error. Kill KANBAN_LOOP_FRAME_ANY_AUTHOR=1."""
    who_rules = rulers()
    assignee = (card.assignee or "").strip().lower()
    for author, body in reversed(card.comments):
        b = (body or "").strip()
        if not b.lower().startswith("frame:"):
            continue
        a = (author or "").strip().lower()
        if frame_any_author_on() or (a and (a == assignee or a in who_rules)):
            return b
    return ""


def ruler_frame(card: Card) -> str:
    """A RULER's own `frame:` on this card — her answer, not a pending question.

    2026-09-21: her ruling verbs are try | park | later | archive | hands, so a
    MIS-FRAMED card had no exit but `try` (run the wrong question again) or `park`
    (bury it). t_e2d5f2a3 took four passes that drifted try/try/try/park/park before
    the premise itself was found false — the word she needed was "wrong question".
    She already has it: `worker_frame` reads a ruler's `frame:` and `run_reframe`
    cuts from it. Only `reframe_candidates`' needs_input skip stood in the way, and
    that skip means "still with Crissa" — which a frame FROM Crissa disproves.
    Kill KANBAN_LOOP_RULER_REFRAME=0.
    """
    who_rules = rulers()
    for author, body in reversed(card.comments):
        b = (body or "").strip()
        if b.lower().startswith("frame:") and (author or "").strip().lower() in who_rules:
            return b
    return ""


def ruler_reframe_on() -> bool:
    return os.environ.get("KANBAN_LOOP_RULER_REFRAME", "1").strip().lower() not in ("0", "false", "no", "off")


def _newest_index(card: Card, pred) -> int:
    """Index of the newest comment matching pred(author, body), else -1."""
    for i in range(len(card.comments) - 1, -1, -1):
        a, b = card.comments[i]
        if pred((a or "").lower(), (b or "").strip()):
            return i
    return -1


def newest_cut_index(card: Card) -> int:
    return _newest_index(card, lambda a, b: a == LOOP_AUTHOR and b.lower().startswith("reframe-cut:"))


def newest_try_index(card: Card) -> int:
    who = rulers()
    return _newest_index(card, lambda a, b: a in who and verdict_of(b)[0] == "try")


def already_cut(card: Card) -> bool:
    """A loop `reframe-cut:` newer than the newest ruler `try`. A `try` after a cut (or after a
    retreat's `reframe-cut: none`) reopens the card. Kill KANBAN_LOOP_CUT_ONCE=1 (once ever)."""
    cut = newest_cut_index(card)
    if cut < 0:
        return False
    if cut_once_on():
        return True
    return newest_try_index(card) < cut


def try_after_retreat(card: Card) -> bool:
    """A ruler's `try` newer than the loop's retreat mark allows one more generation."""
    retreat = _newest_index(card, lambda a, b: a == LOOP_AUTHOR and b.lower().startswith("reframe-cut: none"))
    return retreat >= 0 and newest_try_index(card) > retreat


def _own_marker(card: Card) -> tuple[int | None, str]:
    """(generation, source) from where the specifier cannot reach first:
    this loop's `reframe-gen: N source: <id>` comment, then the idempotency
    key, then the body (rewritten on ~170 of 299 loop cards, 2026-09-17)."""
    for a, b in reversed(card.comments):
        if (a or "").lower() != LOOP_AUTHOR:
            continue
        m = GEN_MARK_RE.match((b or "").strip())
        if m:
            return int(m.group(1)), (m.group(2) or "")
    m = IDEM_RE.match(card.idempotency_key or "")
    if m:
        return int(m.group(2)), m.group(1)
    g = GEN_RE.search(card.body or "")
    s = SOURCE_RE.search(card.body or "")
    return (int(g.group(1)) if g else None), (s.group(1) if s else "")


def own_generation(card: Card) -> int | None:
    return _own_marker(card)[0]


def _lineage(card: Card, index: dict[str, Card] | None):
    """The card, then its decompose root, then that root's root (bounded)."""
    cur, seen = card, set()
    while cur is not None and cur.id not in seen and len(seen) < 8:
        seen.add(cur.id)
        yield cur
        cur = (index or {}).get(cur.decompose_of) if cur.decompose_of else None


def generation(card: Card, index: dict[str, Card] | None = None) -> int:
    """A decomposed child of a gen-N frame is gen N: its failure is the
    frame's failure (21 of 66 reframes on 09-16 were cut from such children,
    each at gen 1, so the retreat never came)."""
    for c in _lineage(card, index):
        g = own_generation(c)
        if g is not None:
            return g
    return 0


def source_of(card: Card, index: dict[str, Card] | None = None) -> str:
    for c in _lineage(card, index):
        g, s = _own_marker(c)
        if g is not None or s:
            return s
    return ""


SALAD_PARK_MARK, SALAD_RELEASE_MARK = "salad-park:", "salad-release:"
SALAD_MAX_RELEASES = int(os.environ.get("SALAD_MAX_RELEASES", "3"))


def salad_owned(card: Card) -> bool:
    """hermes records a token-salad run as a protocol violation, and a frame written from that
    text is a frame about the protocol (~40 such cards on 2026-09-17). kanban-salad-tripwire marks
    the card `salad-park:` and releases it after an engine reset; until its releases are spent the
    card is the tripwire's, not a repeat. Kill KANBAN_LOOP_SALAD_SKIP=0."""
    if os.environ.get("KANBAN_LOOP_SALAD_SKIP", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    if (KH.hermes_home() / "kanban-salad-tripwire.off").is_file():
        return False
    bodies = [(b or "") for _a, b in card.comments]
    parks = sum(SALAD_PARK_MARK in b for b in bodies)
    return parks > 0 and sum(SALAD_RELEASE_MARK in b for b in bodies) < SALAD_MAX_RELEASES


def reframe_candidates(cards: list[Card], diagnostics: list[dict]) -> list[Card]:
    """Cards that failed REFRAME_AT+ times the same way, a sticky gave_up
    breaker park, or that a worker framed and blocked. Never a card the
    loop already cut.

    Verb wall parks at 1 (protocol_violation) and patch 70 keeps that
    park until unblock_task, so consecutive_failures never reaches
    REFRAME_AT=3. A gave_up that has not been unblocked *is* the repeat
    the loop was built for (2026-09-16). Untyped worker blocks
    (block_kind NULL, last event ``blocked``) are not this class.
    """
    repeated: dict[str, int] = {}
    for d in diagnostics or []:
        for x in d.get("diagnostics") or []:
            if x.get("kind") != "repeated_failures":
                continue
            data = x.get("data") or {}
            n = int(data.get("consecutive_failures") or 0)
            repeated[str(d.get("task_id"))] = max(n, repeated.get(str(d.get("task_id")), 0))
    out = []
    for c in cards:
        if already_cut(c) or c.status in ("done", "archived", "scheduled"):
            continue
        if is_parked(c):
            continue  # a Park ruling is sticky: never reframed
        if c.block_kind == "needs_input" and not (ruler_reframe_on() and ruler_frame(c)):
            continue  # already with Crissa — unless SHE framed it: that frame IS the answer
        if c.block_kind == "capability" and skip_capability_on():
            continue
        if salad_owned(c):
            continue  # an engine fault, not a repeat: the salad tripwire resets crew and releases it
        n = max(c.consecutive_failures, repeated.get(c.id, 0))
        if c.breaker_parked and c.status == "blocked":
            n = max(n, REFRAME_AT)
        framed = bool(worker_frame(c))
        if n >= REFRAME_AT or (framed and c.status == "blocked"):
            out.append(c)
    return out


def mechanical_frame(card: Card, why: str = "reframer returned no frame") -> str:
    errs = [card.last_error] if card.last_error else []
    lines = [
        f"frame: (mechanical — {why}) card {card.id} failed "
        f"{card.consecutive_failures}x: {card.title}",
        "Last error: " + (cut(" ".join(errs), 300, f"kanban_show {card.id} has the full error") if errs else "none recorded"),
        "Recent thread: " + " | ".join(cut(b, 120, "kanban_show has it whole").replace("\n", " ") for _a, b in card.comments[-3:]),
        "Answer these before doing anything the last runs did:",
        *FRAME_QUESTIONS,
    ]
    return "\n".join(lines)


THINK_URL = os.environ.get("KANBAN_LOOP_THINK_URL", "http://127.0.0.1:8000/v1/chat/completions")
THINK_MODEL = os.environ.get("KANBAN_LOOP_THINK_MODEL", "ops")


def krew_numbers() -> str:
    """The residents on the card, so the frame can sum them. The dry run of
    2026-09-12 showed the crew model abstracting ("a shared pool") where the
    arithmetic was the frame: 40.7 + 37.4 + 1.8 GiB resident, 24 needed,
    97.9 on the card — no take was ever going to land at those pins."""
    try:
        used_total = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8).stdout.strip().splitlines()[0]
        used, total = [int(float(x)) for x in used_total.split(",")]
        apps = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=process_name,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8).stdout.strip().splitlines()
        res = "; ".join(
            f"{a.split(',')[0].strip().split('/')[-1][:28]} {int(float(a.split(',')[1]))} MiB"
            for a in apps if "," in a
        )
        mode = ""
        try:
            mode = (KH.hermes_home() / "krew-gpu-mode").read_text().strip()
        except OSError:
            pass
        return (f"GPU now: used {used} of {total} MiB, free {total - used} MiB; residents: {res or 'none'}; "
                f"krew-gpu-mode={mode or '?'}. If the failure is about memory, add the residents up "
                "against the total before you write point 3.")
    except Exception:  # noqa: BLE001
        return ""


def think_engine_up(url: str = THINK_URL) -> bool:
    try:
        base = url.split("/v1/")[0] + "/v1/models"
        with urllib.request.urlopen(base, timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def model_frame(card: Card, *, url: str | None = None, model: str | None = None,
                timeout: float = LLM_TIMEOUT, numbers: str | None = None) -> str:
    # The thinking engine writes a better frame; it is parked in Render, so
    # the crew is the fallback, thinking off.
    thinking = url is None and think_engine_up()
    url = url or (THINK_URL if thinking else LLM_URL)
    model = model or (THINK_MODEL if thinking else LLM_MODEL)
    thread = "\n".join(f"- {a}: {cut(b, 1200, f'kanban_show {card.id} has it whole')}" for a, b in card.comments[-4:])
    numbers = krew_numbers() if numbers is None else numbers
    user = (
        f"Card {card.id}: {card.title}\n\nBody:\n{cut(card.body, 6000, f"kanban_show {card.id} has the whole body")}\n\n"
        f"Consecutive failures: {card.consecutive_failures}\nLast error: {cut(card.last_error, 1200, f"kanban_show {card.id} has the full error")}\n"
        f"Recent thread:\n{thread}\n" + (f"\n{numbers}\n" if numbers else "")
    )
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": REFRAMER_SYSTEM}, {"role": "user", "content": user}],
        # 09-18: 900 tokens at reasoning_effort high were spent inside <think>; the content came
        # back empty and the card got a mechanical frame stamped "no reframer model reachable"
        # while both engines were up (12 of 18 cuts that day). Size the call to return content.
        "max_tokens": THINK_MAX_TOKENS if thinking else 600,
        "temperature": 0.4,
    }
    if thinking:
        payload["reasoning_effort"] = THINK_EFFORT
    else:
        payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer sk-local-noauth"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    choice = (d.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    usage = d.get("usage") or {}
    log(f"reframer {model}: finish={choice.get('finish_reason')} content={len(text)}ch reasoning={len(reasoning)}ch "
        f"completion_tokens={usage.get('completion_tokens')} {time.time() - t0:.1f}s for {card.id}")
    if choice.get("finish_reason") == "length" and len(text) < 40:
        log(f"reframer {model}: reasoning ate the budget for {card.id} (max_tokens {payload['max_tokens']})")
    return ("frame: " + text) if text and not text.lower().startswith("frame:") else text


def crew_frame(card: Card) -> str:
    """The no-thinking fallback on crew :8005, tried once after the thinking engine returned nothing."""
    return model_frame(card, url=LLM_URL, model=LLM_MODEL)


def make_frame(card: Card, *, framer=model_frame, fallback=None) -> tuple[str, str, str]:
    """(frame text, who wrote it, why): worker > model > crew fallback > mechanical.

    The mechanical branch is loud and labelled with the real reason; run_reframe decides
    whether it may mint (it may not, unless a hand said --mechanical or
    KANBAN_LOOP_MECHANICAL_CUTS=1)."""
    wf = worker_frame(card)
    if wf:
        return wf, "worker", ""
    if fallback is None and framer is model_frame:
        fallback = crew_frame
    why = ""
    for name, fn in (("model", framer), ("crew", fallback)):
        if fn is None:
            continue
        try:
            mf = fn(card) or ""
        except Exception as e:  # noqa: BLE001
            why = f"{name} unreachable: {type(e).__name__}: {str(e)[:80]}"
            log(f"reframer {name} unavailable for {card.id}: {type(e).__name__}: {e}")
            continue
        if mf.lower().startswith("frame: (mechanical"):
            return mf, "mechanical", "hand-mechanical"
        if len(mf) > 40:
            return mf, "model", ""
        why = f"{name} returned {len(mf)} chars"
        log(f"reframer {name} returned no frame for {card.id} ({len(mf)} chars)")
    return mechanical_frame(card, why or "reframer returned no frame"), "mechanical", why or "no frame"


def noframe_attempts(card: Card) -> int:
    """This loop's `reframe-noframe:` marks since the newest cut / ruler try."""
    floor = max(newest_cut_index(card), newest_try_index(card))
    return sum(1 for i, (a, b) in enumerate(card.comments)
               if i > floor and (a or "").lower() == LOOP_AUTHOR and (b or "").strip().lower().startswith("reframe-noframe:"))


def ask_crissa(cli: Cli, c: Card, question: str, *, release_reason: str) -> tuple[int, str]:
    """Put a question on the Decide queue. A card already `needs_input` is already hers: a second
    needs_input block on the same cause routes the card to TRIAGE (hermes BLOCK_RECURRENCE_LIMIT
    2) and the decomposer eats the question — so it gets a `decide:` comment instead. Otherwise
    release (hermes blocks only running/ready) and re-block typed in the same breath."""
    if triage_guard_on() and c.status == "blocked" and c.block_kind == "needs_input":
        return cli("comment", c.id, "decide: " + question, "--author", LOOP_AUTHOR)
    # A loop-held source is `scheduled`, and hermes blocks only running/ready: 2026-09-19
    # t_d3b9a1db and t_8b610fa4 were "asked" twice with the block refused (cannot block
    # a scheduled card) and sat held forever. `unblock` is the scheduled card's only exit.
    if c.status in ("blocked", "scheduled"):
        cli("unblock", c.id, "--reason", release_reason)
    elif c.status == "todo" and ask_promotes_on():
        # 2026-09-20: the `scheduled` fix above lands the card in TODO (unblock re-gates on
        # parents), and hermes blocks only running/ready — so the ask was refused on every
        # tick and, by the rule below, left no marker: t_4c1fdafa spun 29 ticks over 10 h
        # with nobody told. `promote` is todo's exit (close_card's path); hermes refuses it
        # while a parent is open, and THAT refusal is the honest answer — a card waiting on
        # its parent is not waiting on Crissa. Kill: KANBAN_LOOP_ASK_PROMOTES=0.
        rc, out = cli("promote", c.id, release_reason)
        if rc != 0:
            return rc, out
    return cli(*block_args(c.id, "needs_input", question))


def ask_refused_before(card: Card, cause: str) -> bool:
    """Has this loop already recorded a refused ask for `cause` on this card?"""
    tag = f"ask-refused: {cause}".lower()
    return any((a or "").lower() == LOOP_AUTHOR and (b or "").strip().lower().startswith(tag)
               for a, b in card.comments)


def note_ask_refused(cli: Cli, card: Card, cause: str, out: str) -> bool:
    """Record ONE refused ask per cause, then stop asking while the cause holds.

    2026-09-20: "a refused ask was not an ask" (2026-09-19) is right about the `asked once`
    marker — but on its own it means retry forever, silently. The refusal gets its own marker
    so the loop says it once and stops; the card is still un-asked, so the question is put for
    real the moment the card can take it (a parent finishes → todo leaves → the block lands).
    """
    if ask_refused_before(card, cause):
        return False
    log(f"ask refused for {card.id} ({card.status}) on {cause}: {out[:120]} — recorded, not retried")
    cli("comment", card.id,
        f"ask-refused: {cause} — {card.status} cannot take a needs_input block ({out.strip()}). "
        "The question waits for the card to be askable; kanban-loop stops retrying.",
        "--author", LOOP_AUTHOR)
    return True


def _retreat(cli: Cli, c: Card, gen: int, frame: str, index: dict[str, Card], *, why: str = "") -> None:
    """Two frames did not land (or no frame could be written): Crissa rules."""
    src = source_of(c, index)
    q = (f"Reframe generation {gen} failed again ({c.consecutive_failures}x). "
         f"Source card {src or 'unknown'}. Rule: try again with a new frame, "
         f"park, or hands? First word: try | park | hands. Frame: {cut(frame, 600, 'the source card has the whole frame')}")
    if why:
        q = f"No model frame in {NOFRAME_RETREAT_AT} ticks ({why}). Source card {src or 'unknown'}. Rule: try (a mechanical cut) | park | hands. Frame: {cut(frame, 600, 'the source card has the whole frame')}"
    rc, out = ask_crissa(cli, c, q, release_reason=f"retreat: reframe generation {gen} to Crissa")
    if rc != 0:
        log(f"retreat block refused for {c.id} ({c.status}): {out[:120]}")
        cli("comment", c.id, "retreat: " + q, "--author", "kanban-loop")
    mark = "(no frame)" if why else f"(gen {gen} >= max {REFRAME_MAX_GEN})"
    cli("comment", c.id, f"reframe-cut: none {mark}; to Crissa", "--author", "kanban-loop")


def hold_source(cli: Cli, c: Card, tid: str) -> bool:
    """Item 1: link the cut over its source and park the source with `schedule` (hermes' own
    verb: outside recompute_ready, outside ownerless, only unblock exits). A `dependency` block
    lands in todo and is promoted next tick (33 of 33 on 09-18); a link alone is promoted the
    instant the cut completes (complete_task → recompute_ready). The hold's wake is
    `until:<cut>` (the Phase 2 grammar); its reader is run_complete_frames."""
    rc, out = cli("link", tid, c.id)
    if rc != 0:
        log(f"link {tid} → {c.id} refused: {out[:120]}")
    if c.status not in SCHEDULABLE:
        return False
    rc, out = cli("schedule", c.id, f"hold: until:{tid} — superseded by reframe {tid}; kanban-loop holds it "
                                    f"until the reframe is done, then closes it as superseded")
    if rc != 0:
        log(f"hold {c.id} refused: {out[:120]}")
        return False
    cli("comment", c.id, f"{HOLD_MARK} reframe {tid} until:{tid}", "--author", LOOP_AUTHOR)
    c.status = "scheduled"
    return True


def run_reframe(cards: list[Card], diagnostics: list[dict], cli: Cli, *, framer=model_frame,
                max_cuts: int = MAX_CUTS_PER_TICK, index: dict[str, Card] | None = None,
                mint_mechanical: bool | None = None) -> int:
    index = index or index_of(cards)
    mint_mechanical = mechanical_cuts_on() if mint_mechanical is None else mint_mechanical
    n = 0
    for c in reframe_candidates(cards, diagnostics):
        if n >= max_cuts:
            log("cut budget reached this tick; the rest wait")
            break
        gen = generation(c, index)
        frame, who, why = make_frame(c, framer=framer)
        if who == "mechanical" and not mint_mechanical:
            # Item 2: a mechanical frame is the failure re-posted with six questions; it never
            # mints a card. Three no-frame ticks → Crissa with the mechanical frame as the question.
            k = noframe_attempts(c) + 1
            if k >= NOFRAME_RETREAT_AT:
                log(f"no frame for {c.id} in {k} ticks ({why}); retreating to Crissa")
                _retreat(cli, c, gen, frame, index, why=why)
                n += 1
            else:
                log(f"NO FRAME for {c.id} ({why}); not cut ({k} of {NOFRAME_RETREAT_AT})")
                cli("comment", c.id, f"reframe-noframe: {why} ({k} of {NOFRAME_RETREAT_AT}); not cut", "--author", LOOP_AUTHOR)
            continue
        if who != "worker":
            cli("comment", c.id, frame, "--author", "kanban-loop")
        if gen + 1 > REFRAME_MAX_GEN and not try_after_retreat(c):
            _retreat(cli, c, gen, frame, index)
            n += 1
            continue
        body = (f"{frame}\n\nreframe-gen: {gen + 1}\nsource: {c.id}\n"
                f"Origin: {cut(c.title, 160, c.id + ' has the whole title')}\nDo not repeat the last runs' steps; the frame above is the spec. "
                "If the frame names a physical step, block needs_input and say which.")
        rc, out = create_pinned(cli, f"reframe: {c.title[:140]}", "--triage", "--assignee", ORCHESTRATOR,
                                "--body", cut(body, 100000, "the source card has the rest"), "--created-by", "kanban-loop",
                                "--idempotency-key", f"reframe:{c.id}:g{gen + 1}", assignee=ORCHESTRATOR)
        tid = created_id(out)
        if not tid:
            log(f"reframe cut failed for {c.id}: {out[:160]}")
            continue
        # The marker the specifier cannot rewrite (it rewrote ~170 of 299 bodies).
        cli("comment", tid, f"reframe-gen: {gen + 1} source: {c.id}", "--author", "kanban-loop")
        cli("comment", c.id, f"reframe-cut: {tid} (gen {gen + 1}, frame by {who})", "--author", "kanban-loop")
        if hold_source_on():
            hold_source(cli, c, tid)
        elif c.status != "blocked":
            cli(*block_args(c.id, "dependency", f"superseded by reframe {tid}; stops here so it stops burning"))
        n += 1
    return n


# --- DECOMPOSE (the gateway's auto-decompose has been silent since 09-08) ----

DECOMPOSE_AFTER_MIN = int(os.environ.get("DECOMPOSE_AFTER_MIN", "5"))
MAX_DECOMPOSE_PER_TICK = int(os.environ.get("MAX_DECOMPOSE_PER_TICK", "3"))


LOOP_DETECTED_MARK = "loop-detected:"


def loop_detected_triage(db: Path) -> list[str]:
    """Triage cards hermes routed there by `block_loop_detected` (a second same-cause block):
    the question that was being asked is now decomposer input. 13 in 48 h on 09-18, all from the
    gate's untyped re-blocks. Not decomposed; commented once for a ruling."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if "task_events" not in _tables(con):
            return []
        rows = con.execute(
            "SELECT t.id FROM tasks t WHERE t.status = 'triage' AND (SELECT e.kind FROM task_events e "
            "WHERE e.task_id = t.id ORDER BY e.id DESC LIMIT 1) = 'block_loop_detected'").fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def triage_older_than(db: Path, minutes: int, *, now: float | None = None) -> list[str]:
    """Triage cards the gateway had its chance at. Ada's in-process
    auto-decompose logged nothing after 2026-09-08 11:01 while the CLI path
    decomposed the same cards in seconds (measured 09-12 07:30: t_33cab38f,
    t_332f2611, t_8605e495). Until that is found, the loop is the decomposer's
    clock; `hermes kanban decompose` is the same code, audited by author."""
    now = time.time() if now is None else now
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id FROM tasks WHERE status = 'triage' AND created_at <= ? ORDER BY created_at",
        (int(now - minutes * 60),),
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


# Work IN FLIGHT — the same set hermes patch 70 counts (`_OPEN_STATUSES` in
# gateway/kanban_watchers.py; a test pins the two together). 2026-09-17: on one standing
# board with sticky parks, counting blocked + scheduled walks the board into the ceiling
# for good (136 open vs 150 the day it was born) — a park costs no worker.
OPEN_STATUSES = ("todo", "ready", "running", "review", "triage")
ADA_LIVE = KH.hermes_home() / "profiles" / "ada" / "config.yaml"
ADA_PACK = Path(__file__).resolve().parent.parent / "hermes-packs" / "distributions" / "ada" / "config.yaml"


def decompose_wip_ceiling(path: Path | None = None) -> int | None:
    """Same number the gateway honours. Unset / 0 → no ceiling.

    Env KANBAN_DECOMPOSE_WIP_CEILING wins (tests). Else Ada live, else pack.
    """
    raw = os.environ.get("KANBAN_DECOMPOSE_WIP_CEILING")
    if raw is not None and raw.strip() != "":
        try:
            n = int(raw)
        except ValueError:
            return None
        return n if n > 0 else None
    for p in ((path,) if path else (ADA_LIVE, ADA_PACK)):
        if p is None or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^  decompose_wip_ceiling:\s*(\d+)", text, re.M)
        if m:
            n = int(m.group(1))
            return n if n > 0 else None
    return None


def count_open_cards(db: Path) -> int:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    marks = ",".join("?" for _ in OPEN_STATUSES)
    n = con.execute(
        f"SELECT COUNT(*) FROM tasks WHERE status IN ({marks})", OPEN_STATUSES
    ).fetchone()[0]
    con.close()
    return int(n)


def parked_ids(cards: list[Card]) -> set[str]:
    return {c.id for c in cards if is_parked(c)}


# --- the exit from `crashed` (the User 09-28, kanban repair Stage 2) ----------------------------
# `crashed` is no stock hermes status and no verb leaves it (09-27's test dispatcher wrote it on 59
# real cards); the User's 05:13 parks on 21 of them landed as comments and moved nothing. Order:
# a ruler's latest word wins (park/later -> scheduled, archive -> archived, a try newer than the
# crash -> todo); a worker pid still alive leaves the card alone; a harness crash is retried ONCE
# (todo, failures reset); everything else goes to Crissa (blocked needs_input). The status flip is
# scripts/kanban-crashed-exit.py (write_txn + one event). Kill KREW_CRASHED_EXIT=0.
CRASHED_EXIT = Path(__file__).resolve().parent / "kanban-crashed-exit.py"
CRASHED_EXIT_MAX = int(os.environ.get("KREW_CRASHED_EXIT_MAX", "25") or 25)
CRASHED_MARK = "crashed-exit:"
_ALIVE_RE = re.compile(r"\bpid (\d+) still alive", re.I)


def crashed_exit_on() -> bool:
    return os.environ.get("KREW_CRASHED_EXIT", "1").strip().lower() not in ("0", "off", "false", "no")


def _pid_alive(pid: int) -> bool:
    return pid > 1 and Path(f"/proc/{pid}").exists()


def crashed_decision(card: Card, *, last_error: str, last_end: int, retried: bool,
                     pid_alive=_pid_alive) -> tuple[str, str]:
    """(to, why) for a card in `crashed`: to is scheduled | archive | todo | blocked | '' (leave)."""
    ats = list(card.comment_at) + [0] * (len(card.comments) - len(card.comment_at))
    for (author, body), at in reversed(list(zip(card.comments, ats))):
        if (author or "").strip().lower() not in RULERS:
            continue
        v, text = verdict_of(body)
        if not v:
            continue
        if v in ("park", "later"):
            return "scheduled", _after_unblock(text) if v == "park" else text
        if v == "archive":
            return "archive", text
        if v == "try" and at and last_end and at > last_end:
            return "todo", f"{author}'s try after the crash: {cut(text, 200, 'the comment has it whole')}"
        if v in ("hands", "done"):
            return "blocked", (f"crashed after `{v}` was ruled; the card cannot move from `crashed` by "
                               "itself. First word: try (run it again) | park | archive | hands.")
        break                                  # a try the crash already answered: fall through
    m = _ALIVE_RE.search(last_error or "")
    if m and pid_alive(int(m.group(1))):
        return "", f"its worker pid {m.group(1)} is still alive"
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import kanban_cause
    c = kanban_cause.classify("crashed", last_error or "")
    if c is not None and c.cause == kanban_cause.HARNESS and not retried:
        return "todo", f"crashed on the harness ({c.why}); retried once, failures reset"
    what = f"{c.cause} — {c.why}" if c is not None else "no reason recorded"
    return "blocked", (f"crashed ({what}){' after its one retry' if retried else ''}. "
                       "First word: try (run it again) | park | archive | hands.")


def run_retry_watch(db: Path, cards: list[Card], cli: Cli, *, dry: bool = False) -> int:
    """A card retried out of `crashed` that fails AGAIN goes to Crissa, never another retry (the User
    09-28). Its max_retries 1 makes hermes block it untyped on that failure (a `gave_up` after the
    `crashed_exit`); `block --kind needs_input` classifies that block in place for her. Once (mark)."""
    if not crashed_exit_on():
        return 0
    blocked = {c.id: c for c in cards if c.status == "blocked" and not (c.block_kind or "")}
    if not blocked:
        return 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return 0
    n = 0
    try:
        for tid, c in blocked.items():
            ex = con.execute("SELECT max(id) FROM task_events WHERE task_id = ? AND kind = 'crashed_exit'",
                             (tid,)).fetchone()[0]
            if not ex:
                continue
            gave = con.execute("SELECT max(id) FROM task_events WHERE task_id = ? AND kind = 'gave_up' AND id > ?",
                               (tid, ex)).fetchone()[0]
            if not gave or any(b.startswith(f"{CRASHED_MARK} crissa") for _a, b in c.comments):
                continue
            why = (f"failed again after its one retry out of `crashed` ({c.last_error or 'no reason'}). "
                   "First word: try (run it again) | park | archive | hands.")
            log(f"crashed-exit {tid}: failed after its retry -> crissa")
            n += 1
            if dry:
                continue
            rc, out = cli("block", tid, why, "--kind", "needs_input")
            if rc != 0:
                log(f"crashed-exit {tid}: classify refused ({out.strip()[-120:]})")
                continue
            cli("comment", tid, f"{CRASHED_MARK} crissa — {why}", "--author", LOOP_AUTHOR)
    finally:
        con.close()
    return n


def run_crashed_exit(db: Path, cards: list[Card], cli: Cli, *, dry: bool = False, runner=None,
                     pid_alive=_pid_alive, max_n: int = CRASHED_EXIT_MAX) -> int:
    if not crashed_exit_on():
        return 0
    crashed = [c for c in cards if c.status == "crashed"]
    if not crashed:
        return 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        log(f"crashed-exit: cannot read the board ({exc})")
        return 0
    run = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False))
    n = 0
    try:
        for c in crashed:
            if n >= max_n:
                log("crashed-exit: cap reached this tick; the rest wait")
                break
            last = con.execute("SELECT coalesce(error,''), coalesce(summary,''), coalesce(ended_at,0) "
                               "FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1", (c.id,)).fetchone()
            err = " ".join(x for x in (last or ("", "", 0))[:2] if x) or c.last_error
            retried = con.execute("SELECT 1 FROM task_events WHERE task_id = ? AND kind = 'crashed_exit' "
                                  "LIMIT 1", (c.id,)).fetchone() is not None
            to, why = crashed_decision(c, last_error=err, last_end=int((last or (0, 0, 0))[2] or 0),
                                       retried=retried, pid_alive=pid_alive)
            if not to:
                log(f"crashed-exit {c.id}: left — {why}")
                continue
            log(f"crashed-exit {c.id}: -> {to} — {why[:120]}")
            n += 1
            if dry:
                continue
            if to == "archive":
                rc, out = cli("archive", c.id)
            else:
                r = run([str(CRASHED_EXIT), c.id, "--to", to, "--why", why])
                rc, out = getattr(r, "returncode", 1), (getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or "")
            if rc != 0:
                log(f"crashed-exit {c.id}: refused rc={rc} {out.strip()[-160:]}")
                continue
            cli("comment", c.id, f"{CRASHED_MARK} {to} — {why}", "--author", LOOP_AUTHOR)
    finally:
        con.close()
    return n


def run_decompose(db: Path, cli: Cli, *, max_n: int = MAX_DECOMPOSE_PER_TICK,
                  after_min: int = DECOMPOSE_AFTER_MIN, now: float | None = None,
                  skip: set[str] | None = None) -> int:
    ceiling = decompose_wip_ceiling()
    if ceiling:
        open_n = count_open_cards(db)
        if open_n >= ceiling:
            log(f"holding decompose: {open_n} open >= ceiling {ceiling}")
            return 0
    if skip is None:
        # t_6cfdca80 (09-16 23:04): Crissa's Park, block_loop_detected routed the
        # card to triage, and this pass cut four children from it.
        skip = parked_ids(read_cards(db)) if park_sticky_on() else set()
    n = 0
    looped = set() if decompose_looped_on() else set(loop_detected_triage(db))
    if looped:
        marked = {c.id for c in read_cards(db, statuses=("triage",))
                  if any((a or "").lower() == LOOP_AUTHOR and (b or "").startswith(LOOP_DETECTED_MARK) for a, b in c.comments)}
        for tid in sorted(looped - marked - set(skip or ())):     # a parked triage card already carries the park note
            cli("comment", tid, f"{LOOP_DETECTED_MARK} hermes routed this card to triage after a second same-cause block "
                                "(block_loop_detected); kanban-loop does not decompose it — rule: try | park | hands",
                "--author", LOOP_AUTHOR)
            log(f"decompose {tid}: skipped, block_loop_detected — not decomposed")
    due = [t for t in triage_older_than(db, after_min, now=now) if t not in skip and t not in looped]
    for tid in due[:max_n]:
        rc, out = cli("decompose", tid, "--json", "--author", "kanban-loop")
        try:
            d = json.loads(out[out.find("{"):]) if "{" in out else {}
        except json.JSONDecodeError:
            d = {}
        if rc == 0 and d.get("ok"):
            log(f"decompose {tid}: {d.get('reason')}")
            n += 1
        else:
            log(f"decompose {tid} refused: {(d.get('reason') or out)[:160]}")
    return n


# --- tick ------------------------------------------------------------------

def is_frame(card: Card) -> bool:
    return own_generation(card) is not None or (card.title or "").lower().startswith("reframe:")


def frame_children(db: Path, frame_id: str, index: dict[str, Card]) -> list[Card]:
    """What the frame waits on. hermes decompose links (child -> root), i.e.
    the child is the link's PARENT; the old query read `parent_id = frame`
    and found nothing on 75 of 76 frame links, so no frame ever completed."""
    ids = {c.id for c in index.values() if c.decompose_of == frame_id}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if "task_links" in _tables(con):
            ids |= {r[0] for r in con.execute(
                "SELECT parent_id FROM task_links WHERE child_id = ?", (frame_id,)).fetchall()}
    finally:
        con.close()
    # Item 1 links a cut OVER its source (link <cut> <source>): that row is the hold, not a child.
    # Without this a gen-2 cut above a gen-1 made the gen-1 look like a "project".
    ids = {i for i in ids if not (i in index and own_generation(index[i]) is not None
                                  and source_of(index[i], index) == frame_id)}
    return [index[i] for i in sorted(ids) if i in index]


def _log_phantom_ref(card_id: str, invalid_ids: list[str], note: str,
                     once: str | None = None) -> None:
    """Structured error for a close attempt that referenced IDs missing from the
    active task registry (t_d808c2bf). The close is already refused; this is the
    record. `event=phantom_ref_close` is the machine-readable hook so a grep or a
    future alarm keys on the event and pulls invalid_ids from the same line.

    `once` (a card's comment mark, e.g. `done-refused:`): the mark means the
    refusal was already said, so the structured line is suppressed — once a
    ruler has ruled `done`, the refusal re-fires every tick, and the same
    `event=` line every 20 minutes is noise that masks a genuinely new one.
    The mark is written AFTER the close is refused (never before), so the
    first refusal is always the one that logs."""
    if once is not None:
        return
    ids = " ".join(sorted(set(invalid_ids))) or "n/a"
    log(f"event=phantom_ref_close task={card_id} invalid_ids=[{ids}] {note}")


def triage_closes_on() -> bool:
    """KREW_LOOP_TRIAGE_CLOSES=1 (OFF by default): a ruler's done closes a TRIAGE card (the User 2026-10-04)."""
    return os.environ.get("KREW_LOOP_TRIAGE_CLOSES", "").strip().lower() in ("1", "on", "true", "yes")


TRIAGE_CLOSE = Path(__file__).resolve().with_name("kanban-triage-close.py")


def close_triage_card(card_id: str, why: str) -> tuple[int, str]:
    """hermes cannot complete or block a triage card, and its CLI's only triage exit is an LLM `specify`:
    kanban-triage-close.py does specify (no field changed) + complete in one process, so no dispatcher
    tick can claim the card between them (t_619b4198 / t_b28650c7, 2026-10-04)."""
    try:
        r = subprocess.run([sys.executable, str(TRIAGE_CLOSE), card_id, why], capture_output=True,
                           text=True, timeout=120)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()[-400:]
    except Exception as e:  # noqa: BLE001
        return 1, f"triage close failed: {type(e).__name__}: {str(e)[:160]}"


def close_card(cli: Cli, card: Card, why: str,
               registry: dict[str, Card] | None = None,
               phantom_once: str | None = None, triage_ok: bool = False) -> tuple[int, str]:
    """hermes completes only running/ready/blocked/review; a frame waiting on
    its children (and a source a dependency block parked) sits in todo, so
    promote it first — its parents are done, that is why we are here.

    t_d808c2bf (2026-09-25): when a registry is given, the ID of the card the
    close is for must exist in the active task registry before any state
    change. A missing ID refuses the whole close and logs a structured error
    with the invalid IDs — once, unless `phantom_once` says the refusal was
    already said on this card's thread. No registry — or a registry that
    contains the card — is the pre-existing flow, unchanged."""
    if registry is not None:
        phantom = [card.id] if card.id not in registry else []
        if phantom:
            _log_phantom_ref(card.id, phantom,
                             f"close refused (status={card.status}): provided IDs missing from the "
                             "active task registry; no state change attempted",
                             once=phantom_once)
            return 1, (f"phantom-ref: close refused, IDs not in active task registry: "
                       f"{' '.join(sorted(set(phantom)))}")
    if card.status == "todo":
        rc, out = cli("promote", card.id, why)
        if rc != 0:
            return rc, out
    elif card.status == "scheduled":
        # Item 1's release: the hold's cut is done. unblock lands ready (or todo behind other
        # parents — promote is then a no-op refusal we ignore). One CLI call vs a 120 s
        # dispatcher: the race window is one command.
        # 2026-09-21: this branch was `and loop_held(card)`, so a RULER's park had no close
        # path at all — the caller that needs it (run_done_rulings) is new, and the two
        # callers that predate it (run_complete_frames, run_green) skip parked cards before
        # they ever get here, so widening it moves nothing that was moving.
        rc, out = cli("unblock", card.id, "--reason", why)
        if rc != 0:
            return rc, out
        cli("promote", card.id, why)
    elif card.status == "triage" and triage_ok and triage_closes_on():   # only a ruler's done (run_done_rulings)
        return close_triage_card(card.id, why)
    elif card.status not in ("running", "ready", "blocked", "review"):
        return 1, f"status {card.status} does not complete"
    return cli("complete", card.id, "--result", why)


FRAME_RETIRED_MARK = "frame-retired:"


def frame_retire_on() -> bool:
    """A FRAME WHOSE CHILDREN ARE ALL RESOLVED, ONE OF THEM RETIRED, HAD NO EXIT
    (2026-09-21 pm). Only `done` counts as delivered here — deliberately, and the
    reason is the source-closer below: it completes the SOURCE as "superseded by
    reframe <id>, which is done", i.e. the frame's `done` is the evidence that the
    card's deliverable exists. Let an ARCHIVED child produce a `done` frame and that
    chain closes a card whose work never happened (t_697564ed, 09-18: source closed
    as superseded while Vera found no clip). hermes can afford the looser rule
    (`recompute_ready` treats archived as terminal) because its gate only decides
    when the NEXT card may start; this one decides whether work was delivered.

    So the frame is not completed — it is RETIRED, which claims nothing. The source
    then meets the `reframe-archived:` branch below on the next pass (every cut now
    archived) and goes to Crissa with the question, which is the honest reader.
    Kill KANBAN_LOOP_FRAME_RETIRE=0."""
    return os.environ.get("KANBAN_LOOP_FRAME_RETIRE", "1").strip().lower() not in ("0", "false", "no", "off")


def run_complete_frames(db: Path, cli: Cli, *, index: dict[str, Card] | None = None) -> int:
    """A reframe/frame whose children are all done is not work — complete it;
    a source whose reframe is done is superseded — complete it too.

    Fable 2026-09-16: Ada ran the 07:26 frame as a wrap-up after both children
    finished, tripped the verb wall, and parked a finished card. 2026-09-17:
    frames are found by the loop's marker (the specifier rewrites titles and
    bodies), and an ``archived`` child is not done — only ``done`` is.
    """
    index = index if index is not None else index_of(read_cards(db, statuses=None))
    n = 0
    for f in sorted(index.values(), key=lambda c: c.id):
        if f.status in ("done", "archived", "triage") or not is_frame(f) or is_parked(f):
            continue
        if f.status == "scheduled" and not loop_held(f):
            continue
        kids = frame_children(db, f.id, index)
        if not kids:
            continue
        if any(k.status != "done" for k in kids):
            # All resolved, at least one RETIRED: `done` will never come, so this frame
            # would sit open for good (t_7d374fc5 — two done, one archived, nothing left
            # to wait for). Retire it instead; the source is asked, never closed.
            arch = [k.id for k in kids if k.status == "archived"]
            if (arch and all(k.status in ("done", "archived") for k in kids) and frame_retire_on()
                    and not any((a or "").lower() == LOOP_AUTHOR and (b or "").startswith(FRAME_RETIRED_MARK)
                                for a, b in f.comments)):
                cli("comment", f.id,
                    f"{FRAME_RETIRED_MARK} every child is resolved and {' '.join(arch[:4])} "
                    "was archived, not done — this frame can never complete (only `done` counts, "
                    "and a frame's `done` is what closes its source as delivered). Retiring it, "
                    "which claims nothing; the source goes to Crissa with the question.",
                    "--author", LOOP_AUTHOR)
                rc, out = cli("archive", f.id)
                if rc == 0:
                    f.status = "archived"
                    n += 1
                log(f"retire frame {f.id}: {'ok' if rc == 0 else 'refused: ' + out[:120]} (archived child {arch[0]})")
                continue
            # Item 5: a frame in todo behind open children has no reader and, once they finish,
            # is promoted and RUN as work (52 on the standing board). Hold it with `schedule` so it
            # leaves WIP and never reaches a worker; this closer releases it when the children are
            # done. Kill KANBAN_LOOP_FRAME_HOLD=0.
            if f.status == "todo" and frame_hold_on():
                waiting = [k.id for k in kids if k.status != "done"]
                rc, out = cli("schedule", f.id, f"hold: frame waits on children {' '.join(waiting[:6])} — kanban-loop "
                                                "holds it; a frame is not work, it closes when they are done")
                if rc == 0:
                    cli("comment", f.id, f"{HOLD_MARK} frame waits on children {' '.join(waiting[:6])}", "--author", LOOP_AUTHOR)
                    f.status = "scheduled"
                    log(f"hold frame {f.id}: waits on {len(waiting)} child(ren)")
                    n += 1
                else:
                    log(f"hold frame {f.id} refused: {out[:120]}")
            continue
        rc, out = close_card(cli, f, "children done — frame is not work", registry=index)
        if rc == 0:
            log(f"complete frame {f.id}: {len(kids)} children done")
            f.status = "done"
            n += 1
        else:
            log(f"complete frame {f.id} refused: {out[:160]}")
    # The superseded source (t_9c768401 stayed blocked after t_62e61662 was done).
    if os.environ.get("KANBAN_LOOP_CLOSE_SOURCES", "1").strip().lower() in ("0", "false", "no", "off"):
        return n
    for s in sorted(index.values(), key=lambda c: c.id):
        if s.status in ("done", "archived", "triage") or is_parked(s):
            continue
        if s.status == "scheduled" and not loop_held(s):
            continue
        cuts = [m.group(1) for a, b in s.comments
                if (a or "").lower() == LOOP_AUTHOR and (m := CUT_RE.match((b or "").strip()))]
        done = [t for t in cuts if t in index and index[t].status == "done"]
        if cuts and not done and loop_held(s) and hold_source_on():
            # Item 1's other release: every cut is archived (not done) — the hold would be
            # forever. Hand the source to Crissa instead of holding it.
            archived = [t for t in cuts if t in index and index[t].status == "archived"]
            if archived and len(archived) == len([t for t in cuts if t in index]):
                if any((a or "").lower() == LOOP_AUTHOR and (b or "").startswith("reframe-archived:") for a, b in s.comments):
                    continue
                cli("unblock", s.id, "--reason", f"reframe {archived[-1]} archived, not done; asking Crissa")
                rc, out = cli(*block_args(s.id, "needs_input",
                                          f"Reframe {archived[-1]} was archived, not done, so it never closed this card. "
                                          f"Rule: try (run it again) | park | drop."))
                if rc != 0:
                    log(f"source {s.id}: archived-cut ask refused: {out[:120]}")
                cli("comment", s.id, f"reframe-archived: {archived[-1]} — released to Crissa", "--author", LOOP_AUTHOR)
                log(f"source {s.id}: reframe {archived[-1]} archived — released to Crissa")
                n += 1
            continue
        if not cuts or not done:
            continue
        # 2026-09-18: a reframe the decomposer turned into a PROJECT (children of
        # its own) is not the source's work done again — t_697564ed (render a
        # clip) was "superseded by reframe t_52fb1371, which is done" while that
        # reframe's three children rewrote the worker protocol and Vera found no
        # clip. A plain reframe (one task, done) still closes its source; a
        # decomposed one asks Crissa whether the source's deliverable exists.
        # Kill: KANBAN_LOOP_PROJECT_REFRAME_ASKS=0 (old rule: any done reframe closes).
        plain = [t for t in done if not frame_children(db, t, index)]
        if not plain and os.environ.get("KANBAN_LOOP_PROJECT_REFRAME_ASKS", "1").strip().lower() not in ("0", "false", "no", "off"):
            if any((a or "").lower() == LOOP_AUTHOR and (b or "").startswith("reframe-project:") for a, b in s.comments):
                continue                                   # asked once; her ruling is the sink
            cause = f"reframe-project {done[-1]}"
            if ask_promotes_on() and s.status == "todo" and ask_refused_before(s, cause):
                continue          # said once; the card is waiting on a parent, not on Crissa
            kids = frame_children(db, done[-1], index)
            q = (f"Reframe {done[-1]} closed as a project ({len(kids)} children: "
                 f"{', '.join(k.id for k in kids[:4])}) — that is the reframe's work, not proof this card's "
                 f"deliverable exists. Is it? First word: done (close this card) | try (run it again) | park.")
            rc, out = ask_crissa(cli, s, q, release_reason=f"reframe {done[-1]} closed as a project; asking Crissa")
            if rc != 0:
                # No `reframe-project:` marker: the marker is "asked once", and a refused ask
                # was not an ask (the 2026-09-19 strand) — the question is still owed the
                # moment the card can take it. But the REFUSAL is recorded, so the loop says
                # it once instead of every tick for ten hours (2026-09-20, t_4c1fdafa).
                if ask_promotes_on():
                    if note_ask_refused(cli, s, cause, out):
                        n += 1
                    continue
                log(f"source {s.id}: project-reframe ask refused ({s.status}): {out[:120]}")
                continue
            cli("comment", s.id, f"reframe-project: {done[-1]} — asked Crissa, not closed", "--author", "kanban-loop")
            log(f"source {s.id}: reframe {done[-1]} is a project — asked Crissa, not closed")
            n += 1
            continue
        closer = (plain or done)[-1]
        why = f"superseded by reframe {closer}, which is done"
        rc, out = close_card(cli, s, why, registry=index)
        if rc == 0:
            log(f"source {s.id}: {why}")
            s.status = "done"
            n += 1
        else:
            log(f"source {s.id} refused: {out[:160]}")
    return n


def diagnostics_json(cli: Cli) -> list[dict]:
    rc, out = cli("diagnostics", "--json")
    if rc != 0:
        return []
    try:
        d = json.loads(out[out.find("["):]) if "[" in out else []
        return d if isinstance(d, list) else []
    except json.JSONDecodeError:
        return []


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "-h" in argv or "--help" in argv:
        # 2026-09-17: `--help` fell through to a LIVE tick (26 completions, 3 cuts).
        print(__doc__)
        print("usage: kanban-loop.py [--dry-run] [--reframe-only [--mechanical]] [--card <id>]")
        return 0
    known = {"--dry-run", "--reframe-only", "--mechanical", "--card"}
    unknown = [a for i, a in enumerate(argv)
               if a.startswith("-") and a not in known and not (i and argv[i - 1] == "--card")]
    if unknown:
        print(f"kanban-loop: unknown option(s) {' '.join(unknown)}; nothing done", file=sys.stderr)
        return 2
    dry = "--dry-run" in argv
    try:                                          # tests failed after a hermes update: report-only 24 h
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
        import gate_hold
        held = gate_hold.report_only("kanban-loop")
    except Exception:                             # the hold must never break the loop
        held = ""
    if held:
        log(f"report-only: {held} (lib/gate_hold; kill KREW_GATE_UPDATE_HOLD=0)")
        dry = True
    reframe_only = "--reframe-only" in argv
    mechanical = "--mechanical" in argv
    card_id = None
    stripped = [a for a in argv if a not in ("--dry-run", "--reframe-only", "--mechanical")]
    if "--card" in stripped:
        i = stripped.index("--card")
        if i + 1 < len(stripped):
            card_id = stripped[i + 1]
    if OFF_SWITCH.is_file() or os.environ.get("KANBAN_LOOP", "1") in ("0", "false", "no"):
        log("off switch set; nothing done")
        return 0
    db = board_db()
    if db is None:
        log("no board db")
        return 0
    everything = read_cards(db, statuses=None)
    index = index_of(everything)
    cards = [c for c in everything if c.status in OPEN_CARD_STATUSES]
    if card_id:
        cards = [c for c in cards if c.id == card_id]
    cli = Cli(dry=dry)
    if reframe_only:
        framer = (lambda c: mechanical_frame(c, "hand: --mechanical")) if mechanical else model_frame
        r = run_reframe(cards, diagnostics_json(cli), cli, framer=framer, index=index,
                        mint_mechanical=mechanical or None)
        log(f"reframe-only: {len(cards)} cards, reframes {r} mechanical={mechanical}")
        return 0
    ar = run_archive_rulings(cards, cli)
    dn = run_done_rulings(cards, cli, index=index)
    p = run_park(cards, cli, index=index)
    fdone = run_complete_frames(db, cli, index=index)
    g = run_green(cards, cli)
    lt = run_later(cards, cli)
    ce = run_crashed_exit(db, everything, cli, dry=dry) + run_retry_watch(db, cards, cli, dry=dry)
    # Before the reframe pass: a park whose wait is simply over is not a card to rewrite.
    dc = run_dependency_clear(cards, cli, db, index=index)
    pe = run_park_premise_expired(cards, cli, index=index)
    cy = run_park_cycle(cards, cli, db, index=index)
    r = run_reframe(cards, diagnostics_json(cli), cli, index=index)
    d = run_decompose(db, cli, skip=parked_ids(cards) if park_sticky_on() else set())
    log(f"tick: {len(cards)} open cards, parked {p}, archived {ar}, closed {dn}, frames {fdone}, "
        f"green moves {g}, reframes {r}, decomposed {d}, later {lt}, dep-clear {dc}, premise {pe}, cycle {cy}, "
        f"crashed-exit {ce}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
