"""gate_strikes — a patrol that reaches the same verdict three times has learned something.

The User, 2026-09-22: *"why would anything fail 60 or 70 times?"* and *"we offer them a reframe
tool for multiple failures so they do not continue banging on same exact attempts."*

He is describing the upgrade the WORKERS got and the PATROLS never did. A card has four rungs
already — `REFRAME_AT=3` earns a frame, `REFRAME_MAX_GEN=2` goes to Crissa, `NOFRAME_RETREAT_AT=3`
retreats to Crissa, hermes' `BLOCK_RECURRENCE_LIMIT=2` routes to triage. A gate or a sweep has
none, so every tick it re-derives its verdict from scratch with no memory of having already
reached it, and re-reporting is the only way it knows how to fail. Measured over three days:

    liveness: none=[] overdue=[same ids]   623   (139 of them today)
    caps … restart owed                    265   (cured 09-19 by giving it an actuator)
    orphan … would TERM (report-only)      179   (cured 09-22 by arming it)
    skills inherit … rc=3                   61   (cured 09-22 by marking terminal refusals)

The cured rows are the argument: where a repeating verdict was given something to DO, the
repetition died. This is that, generalised — one place to count, so the next patrol does not
have to grow its own.

    st = load(path)
    n  = strike(st, "liveness", card_id, verdict)   # 1, 2, 3, … while the verdict holds
    if n == ESCALATE_AT:  ...act once...
    if n >= ESCALATE_AT:  ...and stay quiet...
    prune(st, "liveness", live_ids)                 # a subject that left is forgotten
    save(st, path)

Semantics copied from `gate_salad`'s `left` map (scripts/kanban-board-gate.py), which already
does "speak once per (subject, verdict), silent while it holds, prune when it leaves" and has a
passing test — this adds only the count. `state_file` is an explicit parameter, the
`lib/kanban_orphans.act()` signature, because it is the one shape in this repo that is
test-injectable without monkeypatching a module global.

A WRITE FAILURE IS LOUD. A strike counter that silently reads back 0 never reaches 3 and never
escalates — a latch with no symptom, which is the exact bug this module exists to end. It is
also why `save` returns a bool instead of swallowing OSError like most of its neighbours.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

ESCALATE_AT = int(os.environ.get("KREW_GATE_STRIKES_AT", "3") or 3)
# A verdict nobody has repeated in this long is stale: the patrol stopped seeing it, and the
# subject may legitimately come back. Without this a one-off from last week suppresses a real
# repeat today.
FORGET_S = float(os.environ.get("KREW_GATE_STRIKES_FORGET_S", "86400") or 86400)


def _key(gate: str, subject: str) -> str:
    return f"{gate}:{subject}"


def load(path: Path) -> dict[str, Any]:
    """Never raises. An unreadable store starts empty — the cost is re-counting to 3, not a
    wrong act, so failing open here is safe in a way that failing open on WRITE is not."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def strike(st: dict, gate: str, subject: str, verdict: str, *, now: float | None = None) -> int:
    """Count this verdict. Returns the strike number, 1 on the first or a changed verdict.

    A CHANGED verdict resets to 1 on purpose: the patrol is saying something new, and the
    point of the count is repetition of the same thing, not of the subject.
    """
    now = time.time() if now is None else now
    k = _key(gate, subject)
    prev = st.get(k)
    if (isinstance(prev, dict) and prev.get("verdict") == verdict
            and (now - float(prev.get("ts") or 0)) <= FORGET_S):
        n = int(prev.get("n") or 0) + 1
    else:
        n = 1
    st[k] = {"verdict": verdict, "n": n, "ts": now}
    return n


def seen(st: dict, gate: str, subject: str) -> int:
    v = st.get(_key(gate, subject))
    return int(v.get("n") or 0) if isinstance(v, dict) else 0


def quiet(st: dict, gate: str, subject: str, verdict: str) -> bool:
    """True once this exact verdict has been escalated — the caller should stop reporting it."""
    v = st.get(_key(gate, subject))
    return (isinstance(v, dict) and v.get("verdict") == verdict
            and int(v.get("n") or 0) >= ESCALATE_AT)


def prune(st: dict, gate: str, live: Iterable[str], *, now: float | None = None) -> int:
    """Forget subjects the patrol no longer sees, and anything older than FORGET_S.

    Dropping a subject that left is what lets it escalate again if it comes back — the same
    self-cleaning `gate_salad`'s `left` map does.
    """
    now = time.time() if now is None else now
    keep = {_key(gate, s) for s in live}
    gone = [k for k, v in st.items()
            if (k.startswith(f"{gate}:") and k not in keep)
            or (isinstance(v, dict) and (now - float(v.get("ts") or 0)) > FORGET_S)]
    for k in gone:
        st.pop(k, None)
    return len(gone)


def save(st: dict, path: Path) -> bool:
    """Atomic. Returns False on failure so the CALLER can say so out loud.

    Deliberately not `except OSError: pass`. A counter that cannot persist reads back 0 every
    tick, never reaches ESCALATE_AT, and the escalation silently never happens — the precise
    failure `scripts/krew-mode`'s hysteresis counter had to be taught to shout about.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False
