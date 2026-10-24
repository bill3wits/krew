"""kanban_cause — classify a failed run before anything acts on it.

Principle 2 of `docs/KANBAN-SELF-HEALING-20260918.md` (Fable, 2026-09-18):

  "a failure is classified before it is acted on ... Harness faults (budget, salad, reboot,
   rate limit) are parked with a wake and retried once; verb faults (did the work, forgot the
   verb) go to the orchestrator for the verb; only a *real* repeat is framed."

The readers of that classification were written and shipped. `lib/kanban_kpis.py` counts
`cause: harness|verb|real` comments into three KPIs; `lib/kanban_liveness.py` branches on
`cause: verb`. **Nothing ever wrote one.** So all three KPIs read 0 while a third of runs
ended in nothing (`kanban_runs_nothing_share` 0.323 against a 0.25 target), and the
retry/route/escalate decision had no input at all. This is the missing writer.

It is a script, not an LLM, because Law 2 says so and because the evidence is already on the
row: `task_runs.outcome` and `task_runs.error` say what happened in plain words.

WHY IT MATTERS, measured 2026-09-22. A pre-spawn tool-count cap mis-calibrated to 45 aborted
**194 runs in three days** — every one of them `spawn aborted: <profile> kit has N tools`,
i.e. the worker never started and the card did nothing wrong. 25 cards burned their retry
budget against it and 17 reached the circuit breaker. A `cause: harness` on the first one is
the difference between a board that releases them and a hand that does it at 03:00.

Classes, in the order they are tested (first match wins):

  harness  the run never got a fair attempt: the spawn was refused, the process was killed,
           the engine was in salad, or the turn budget ran out. Retry once with a wake; never
           frame it, because there is nothing in the CARD to reframe.
  verb     the worker did the work and exited 0 without a terminal kanban call. The fix is the
           verb, not the work — this is the orchestrator's, not a rewrite.
  real     everything else: the work itself failed. Only a REPEAT of this earns a reframe.

Pure: `classify()` takes strings and returns a verdict. No db, no clock, no side effects.
"""
from __future__ import annotations

import re
from typing import NamedTuple

# Outcomes worth classifying at all. `blocked` and `reclaimed` are not failures.
FAILED_OUTCOMES = ("crashed", "timed_out", "gave_up", "spawn_failed")

HARNESS = "harness"
VERB = "verb"
REAL = "real"


class Cause(NamedTuple):
    cause: str
    why: str          # one short clause, quoted onto the card so the class is arguable


# --- harness: the run never got a fair attempt ------------------------------------------------
# Each pattern carries the reason it is harness, because a class with no argument is a label.
_HARNESS: tuple[tuple[re.Pattern[str], str], ...] = (
    # The spawn was refused before the worker existed. 194 runs in 3 days (2026-09-22).
    (re.compile(r"spawn aborted", re.I), "the spawn was refused before the worker started"),
    (re.compile(r"pre-spawn cap", re.I), "the spawn was refused by the pre-spawn tool cap"),
    # An unknown skill pin aborts at preload, before any tool call. Non-retryable as filed,
    # but it is still the harness refusing, not the card failing (kanban-skills-add --remove).
    (re.compile(r"unknown skill", re.I), "a skill pin the assignee's registry cannot load"),
    (re.compile(r"could not be measured", re.I), "the kit could not be measured, so the gate failed closed"),
    # Turn budget. Fable lists `budget` as harness: the card was never finished OR failed.
    (re.compile(r"iteration budget exhausted", re.I), "the turn budget ran out mid-task"),
    (re.compile(r"\b\d+/\d+\b.*budget|budget.*\b\d+/\d+\b", re.I), "the turn budget ran out mid-task"),
    # The process died under us.
    (re.compile(r"not alive", re.I), "the worker process was gone when the dispatcher looked"),
    (re.compile(r"\boom\b|out of memory|memoryerror|killed process|signal 9|sigkill", re.I),
     "the worker was killed for memory"),
    (re.compile(r"\bsigterm\b|terminated by signal", re.I), "the worker was signalled, not exited"),
    # Engine and plumbing.
    (re.compile(r"salad|prefix cache|token salad", re.I), "the engine was serving salad"),
    (re.compile(r"apiconnectionerror|connection refused|connection reset|read timed out"
                r"|502 bad gateway|503 service unavailable|rate limit", re.I),
     "the brain was unreachable or rate-limited"),
    (re.compile(r"gateway (?:restart|stopped|shut ?down)|no such file or directory.*venv", re.I),
     "the gateway went away under the run"),
)

# --- verb: the work happened, the terminal call did not ---------------------------------------
_VERB: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"without calling kanban_complete|no terminal kanban call"
                r"|exited cleanly \(rc=0\)", re.I),
     "the worker exited 0 without a terminal kanban call"),
    (re.compile(r"protocol[_ ]violation", re.I), "the run ended with no terminal verb"),
)


# "pid 1818532 exited with code 1" and friends: a failure with a number and no story.
_BARE_EXIT = re.compile(r"^\s*pid \d+ exited with code \d+\s*$|^\s*exit(?:ed)? (?:code )?\d+\s*$", re.I)


def classify(outcome: str | None, error: str | None = "", summary: str | None = "") -> Cause | None:
    """The class of ONE failed run, or None when there is nothing to classify.

    `None` means: not a failure, or a failure with no evidence at all. An unevidenced run is
    deliberately NOT called `real` — `real` is the class that earns a reframe, and inventing
    one from silence is how a card gets rewritten for a fault it never had.
    """
    oc = (outcome or "").strip().lower()
    if oc not in FAILED_OUTCOMES:
        return None
    text = " ".join(t for t in (error or "", summary or "") if t).strip()
    if not text:
        # `spawn_failed` is self-evidencing: the spawn is the thing that failed.
        if oc == "spawn_failed":
            return Cause(HARNESS, "the spawn failed before the worker started")
        return None
    for pat, why in _HARNESS:
        if pat.search(text):
            return Cause(HARNESS, why)
    for pat, why in _VERB:
        if pat.search(text):
            return Cause(VERB, why)
    # A bare non-zero exit is still `real` — a REPEAT of it earns a reframe, which is the
    # whole point of the class — but the wording must not claim more than the row says.
    # 78 of 496 failures over three days are this shape ("pid NNNN exited with code 1") and
    # a comment asserting "the work itself failed" on no evidence is how a card gets
    # rewritten for a fault it never had (09-21: 338 reframe cuts, 142 mechanical).
    if _BARE_EXIT.search(text):
        return Cause(REAL, "a non-zero exit with no reason recorded on the run")
    return Cause(REAL, "the work itself failed")


# --- the per-card breaker (the User 09-28, kanban repair Stage 1) -------------------------------
# A card that ends the SAME way run after run is not failing, it is being eaten: t_7ae5fc3a ran
# 94 times in 3 h on 09-26, 93 of them `blocked` by the terminal guarantee with the same reason,
# re-lifted each time. None of those runs was a FAILED_OUTCOME, so `classify` never saw them.
BREAKER_OUTCOMES = FAILED_OUTCOMES + ("blocked", "spawn_never_verified", "reclaimed")
# Blocks that are a gate holding the card ON PURPOSE (a wait with its own clock), or that are
# already a question with a verb menu (it is with a ruler): repeating is their job, not a loop.
# Measured 09-23..28: the render-window slot wait (2 cards), the VRAM precheck (1), the salad
# relapse question (1).
_DESIGNED_WAIT = re.compile(r"render window|insufficient gpu memory|vram precheck"
                            r"|\b(?:go|try|park|drop|keep) \| ", re.I)
_KEY_NOISE = re.compile(r"(?:run log:|/home/|/tmp/|~/).*$|\b(?:pid|rc|run|t_)[ =_]?[0-9a-f]+\b|\d+", re.I)


class Trip(NamedTuple):
    task_id: str
    key: str          # the one way these runs all ended
    runs: int
    newest_run: int


def run_key(outcome: str | None, error: str | None = "", summary: str | None = "") -> str | None:
    """How ONE run ended, as a key that repeats when the ending repeats; None = not a bad end.
    A failed run keys on its class (`classify`); a blocked / never-verified / reclaimed run keys
    on its outcome plus its reason with the run-specific noise (paths, pids, numbers) cut."""
    oc = (outcome or "").strip().lower()
    if oc in FAILED_OUTCOMES:
        c = classify(oc, error, summary)
        return f"{c.cause} — {c.why}" if c else f"{oc} — no reason recorded"
    if oc not in BREAKER_OUTCOMES:
        return None
    text = " ".join(t for t in (summary or "", error or "") if t).strip().splitlines()
    first = text[0] if text else ""
    if _DESIGNED_WAIT.search(first):
        return None                       # a gate holding it on purpose, or already a question
    reason = _KEY_NOISE.sub("", first).strip(" :;,.-")
    return f"{oc} — {reason[:80]}" if reason else oc


def trips(rows, *, now: float, window_s: float = 6 * 3600, threshold: int = 3) -> list[Trip]:
    """Cards whose last `threshold`+ runs inside the window ended the same way.
    `rows` = (run_id, task_id, outcome, error, summary, ended_at). Pure."""
    seen: dict[tuple[str, str], list[int]] = {}
    for run_id, tid, outcome, error, summary, ended in rows:
        if ended is None or float(ended) < now - window_s:
            continue
        k = run_key(outcome, error, summary)
        if k is None:
            continue
        seen.setdefault((str(tid), k), []).append(int(run_id))
    out = [Trip(tid, k, len(ids), max(ids)) for (tid, k), ids in seen.items() if len(ids) >= threshold]
    return sorted(out, key=lambda t: (-t.runs, t.task_id))


def breaker_line(t: Trip, window_h: float) -> str:
    """The one line the breaker writes (act mode). `breaker:` is its mark."""
    return (f"breaker: {t.runs} runs in {window_h:g} h ended the same way — {t.key} "
            f"(newest run {t.newest_run}). Held for a ruling instead of another run. "
            "First word: try (run it again) | park | reassign <profile> | hands.")


def comment(c: Cause, run_id: int | None = None, runs: int = 1) -> str:
    """The one line written to the card. `lib/kanban_kpis` counts the prefix and
    `lib/kanban_liveness` reads `cause: verb`, so the prefix is load-bearing — it is a
    grammar, not prose.

    `runs` is how many runs of this card ended the same way. It belongs on the line because
    "28 runs" is the difference between a blip and a card being eaten alive — t_0a2789dc
    burned 28 against the 2026-09-22 tool cap.
    """
    bits = []
    if run_id is not None:
        bits.append(f"run {run_id}")
    if runs > 1:
        bits.append(f"{runs} runs")
    tail = f" ({', '.join(bits)})" if bits else ""
    return f"cause: {c.cause} — {c.why}{tail}."
