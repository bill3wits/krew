"""kanban_kpis — the board's self-measurement, one pure function over the board db.

Phase 0 of docs/KANBAN-SELF-HEALING-20260918.md (the User 2026-09-18 "self-improving, self-healing"):
measure first, so every later landing is judged against a number and not a feeling. Read by
lib/krew_daily.py (the "Board flow" section) and scripts/kanban-snapshot.py (nightly record into
the KPI store so improvement-outcomes can judge a proposal by kpi_id).

Everything here is a read-only query on the current board (`~/.hermes/kanban/current` →
`boards/<slug>/kanban.db`). No LLM, no clock but the one handed in, no side effects.

Baselines measured 2026-09-18 14:45–15:25 (the numbers to beat):
  reframe cuts 338 since 09-12 · 142 mechanical · 4 by a worker · 0 of 113 linked to a source
  reframed cards close in 11.1 h median vs 1.2 h plain · 52 frames run as work · chain depth 6
  runs ending in nothing: 08h 20/46 before the engine fix, 0 after · no reader: 28 (09-18 night)
  parks without a wake 9 of 15 · VERB: lines ever 0 · fix cards ever 0 · dup open cards 6 of 25
"""
from __future__ import annotations

import os
import re
import sqlite3
import statistics
from typing import Any

# The wake grammar (Phase 2 reader gate). One token in a block/schedule reason or a ruler's newest
# comment. A park that carries none is a park with no reader.
WAKE_RE = re.compile(
    r"(?:\blater\s+\d{4}-\d{2}-\d{2}\b|\bwindow:(?:render|serving-stop|off-hours)\b|\buntil:t_[0-9a-f]{8}\b"
    r"|\bengine-ok\b|\bengine-reset\b|\bgateway-up\b|\bdrop\b)",
    re.I,
)
OPEN = ("triage", "todo", "ready", "running", "review", "blocked")   # scheduled is a park, not a leaf
NOTHING = ("crashed", "timed_out", "gave_up")                          # a run that ended in nothing
MECHANICAL_AUTHORS = ("kanban-loop", "kanban-board-gate", "auto-decomposer", "decomposer", "steward-cron",
                      "board-carry")
FAIL_EVENTS = ("crashed", "timed_out", "gave_up", "protocol_violation")
CUT_RE = re.compile(r"^\s*reframe-cut:\s*(t_[0-9a-f]{8})", re.I)
TITLE_NOISE = re.compile(r"[^a-z0-9]+")

# The User's own burden (`kanban_user_touches`): distinct cards the board could not move
# without him. The three author channels mean different things and the ledger cannot separate
# them (2026-09-22): `dashboard` is the man typing in the UI, `user` is a session relaying a
# decision he made, `default` is the bare CLI, i.e. a terminal someone is sitting at. All three
# are counted, because all three mean a card waited on a human judgement — measured 2026-09-21,
# `dashboard` alone reads 2 for a day on which 30 cards carried his word, and a metric that
# undercounts the thing it exists to drive to zero is worse than one that rounds up.
# RULERS is a sixth verbatim copy of the tuple in kanban-loop, board-gate, day-close,
# kanban_liveness and restart_window — de-duplicating them rides with T0 card t_93771b36.
HUMAN_AUTHORS = ("dashboard", "user", "default")
RULERS = tuple(os.environ.get("KREW_RULERS", "zoey,user,human,dashboard").split(","))
# A capability block is the User's only when it names something no role can be given: root, a
# password, or hands in the room. Anything else is a re-route for Ada (lib/krew_daily.py:395).
HANDS_RE = re.compile(r"sudo|root kernel|needs human|password|passphrase|polkit|assent|physical"
                      r"|plug|unplug|hands|in the room", re.I)
NEEDS_HUMAN_RE = re.compile(r"needs?[ _-]human|user must|the user\'s call|by hand", re.I)
HANDS_RULING_RE = re.compile(r"^\s*(?:unblock:\s*)?hands\b", re.I)

# kpi_id → (name, unit, direction, target). `snapshot` sample: kanban-snapshot records them nightly.
KPIS: dict[str, tuple[str, str, str, Any]] = {
    "kanban_minted": ("Cards born in the day", "count", "down", None),
    "kanban_completed": ("Cards finished in the day", "count", "up", None),
    "kanban_mint_ratio": ("Cards born per card finished", "ratio", "down", 1.5),
    # NO TARGET, deliberately (2026-09-22). The 0.5 came from the 09-18 scorecard, where
    # "mechanical share -> 0" reads as "the machinery should stop minting". But decomposition
    # IS the design: Ada turns one request into children, and those children are real work —
    # measured, decomposer-born cards complete at 37.3 %, the same rate as the User's own
    # (39.1 %) and the gate's (36.6 %). With recurring vehicles excluded the share is 0.63-0.84,
    # and it cannot be otherwise in a house that decomposes. A target nobody can meet is an
    # alarm that trains people to ignore the page, and this one pointed at the decomposer,
    # which is innocent. **`kanban_mint_ratio` is the health metric here** — born per finished,
    # target 1.5, currently 1.54. Keep this one as a descriptive number.
    "kanban_mechanical_share": ("Share of REAL births by loop / gate / decomposer", "ratio", "down", None),
    "kanban_vehicle_births": ("Recurring vehicle cards born (excluded from the share)", "count", "down", None),
    "kanban_respawn_share": ("Share of the day's spawns that re-ran a card already spawned", "ratio", "down", None),
    "kanban_runs_nothing_share": ("Runs ending in nothing (crashed, timed out, gave up)", "ratio", "down", 0.25),
    "kanban_reframe_cuts": ("Reframe cuts in the day", "count", "down", None),
    "kanban_reframe_mechanical_share": ("Reframe cuts with a mechanical frame", "ratio", "down", 0.0),
    "kanban_reframe_linked_share": ("Reframe cuts linked to their source", "ratio", "up", 1.0),
    "kanban_chain_depth_max": ("Longest reframe chain touched in the day", "count", "down", 1),
    "kanban_reframed_close_h": ("Median first failure → close, reframed cards", "h", "down", None),
    "kanban_plain_close_h": ("Median first failure → close, cards never reframed", "h", "down", None),
    "kanban_frames_open": ("Open frame cards", "count", "down", 0),
    "kanban_frames_run_as_work": ("Frames a worker ran as work (done, empty result) in the day", "count", "down", 0),
    "kanban_no_reader": ("Open cards with no reader (liveness gate)", "count", "down", 0),
    "kanban_overdue": ("Open cards past their reader's deadline", "count", "down", 0),
    "kanban_oldest_leaf_h": ("Oldest open leaf (not a park, no open prerequisite)", "h", "down", 48),
    "kanban_parks_no_wake": ("Parked cards without a wake token", "count", "down", 0),
    "kanban_salad_sessions": ("Salad sessions the tripwire marked in the day", "count", "down", 0),
    "kanban_cause_harness": ("Failures classified harness in the day", "count", "down", None),
    "kanban_cause_verb": ("Failures classified verb in the day", "count", "down", None),
    "kanban_cause_real": ("Failures classified real in the day", "count", "down", None),
    "kanban_verbs_landed": ("VERB: lines the routed gate landed in the day", "count", "up", None),
    "kanban_fix_cards": ("Fix cards a review FAIL filed in the day", "count", "up", None),
    "kanban_dup_open": ("Open cards that duplicate another open card's title", "count", "down", 0),
    "kanban_user_touches": ("Cards that took an act only the user can do", "count", "down", 0),
}


# A VEHICLE is a recurring card that IS the mechanism, not work the mechanism created: the
# hourly steward sweep carries its whole policy in the card body, a worker runs it, and the
# card is archived. Archiving is its correct ending, not a failure — but `mechanical_share`
# counted all 135 of them in 4 days as "the board spending itself on itself", against a 0.5
# target it could never meet, and pointed the reader at the decomposer, which is innocent:
# decomposer cards complete at 37.3 %, the same rate as the User's own (39.1 %).
#
# TWO conditions, both measured, because "it repeats" alone is not enough — `route: 1 ownerless
# block` repeats 22 times and is the gate doing real routing, and it completes. A vehicle also
# has to essentially never finish.
VEHICLE_MIN_REPEATS = int(os.environ.get("KREW_KPI_VEHICLE_REPEATS", "3") or 3)
VEHICLE_MAX_DONE = float(os.environ.get("KREW_KPI_VEHICLE_DONE", "0.10") or 0.10)


def vehicle_keys(rows: list[tuple]) -> set[tuple[str, str]]:
    """(author, normalised title) groups that are recurring vehicles, not work.

    `rows` = (created_by, title, status) for every card born in the window.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for who, title, status in rows:
        groups.setdefault(((who or "").strip().lower(), norm_title(title or "")), []).append(
            (status or "").strip().lower())
    out = set()
    for key, sts in groups.items():
        if not key[1] or len(sts) < VEHICLE_MIN_REPEATS:
            continue
        if (sum(1 for x in sts if x == "done") / len(sts)) <= VEHICLE_MAX_DONE:
            out.add(key)
    return out


def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _tables(con: sqlite3.Connection) -> set[str]:
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error:
        return set()


def _one(con: sqlite3.Connection, sql: str, args: tuple = ()) -> Any:
    try:
        row = con.execute(sql, args).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _ratio(a: int | float | None, b: int | float | None) -> float | None:
    if not b:
        return None
    return round(float(a or 0) / float(b), 3)


def norm_title(t: str) -> str:
    """One idea, one card: the filer's key. Prefix verbs and ids stripped, punctuation folded."""
    t = (t or "").lower()
    t = re.sub(r"^\s*(?:reframe|try|review|route|verify|fix|implement|smoke)\s*:\s*", "", t)
    t = re.sub(r"\bt_[0-9a-f]{8}\b", "", t)
    return TITLE_NOISE.sub(" ", t).strip()


def chain_depth(edges: dict[str, str]) -> int:
    """Longest source → cut → cut … path. `edges[source] = cut`. Cycle-safe."""
    best = 0
    for start in edges:
        seen, n, cur = {start}, 0, start
        while cur in edges and edges[cur] not in seen:
            cur = edges[cur]
            seen.add(cur)
            n += 1
        best = max(best, n)
    return best


def compute(con: sqlite3.Connection, since: float, now: float, liveness: dict | None = None) -> dict[str, Any]:
    """All KPIs for the window [since, now). Missing tables/columns read as None, never raise."""
    tabs = _tables(con)
    tcols = _cols(con, "tasks")
    ccols = _cols(con, "task_comments") if "task_comments" in tabs else set()
    ecols = _cols(con, "task_events") if "task_events" in tabs else set()
    rcols = _cols(con, "task_runs") if "task_runs" in tabs else set()
    has_links = "task_links" in tabs
    out: dict[str, Any] = {k: None for k in KPIS}

    # --- births and finishes ---------------------------------------------------------------
    minted = _one(con, "SELECT count(*) FROM tasks WHERE created_at >= ? AND created_at < ?", (since, now)) or 0
    completed = _one(con, "SELECT count(*) FROM tasks WHERE completed_at >= ? AND completed_at < ?", (since, now)) or 0
    out["kanban_minted"], out["kanban_completed"] = minted, completed
    out["kanban_mint_ratio"] = _ratio(minted, completed)
    if "created_by" in tcols:
        born = _rows(con, "SELECT coalesce(created_by,''), title, status FROM tasks "
                          "WHERE created_at >= ? AND created_at < ?", (since, now))
        vehicles = vehicle_keys(born)
        # Excluded from BOTH sides: the question is "of the real work minted, how much did the
        # machinery mint", and a vehicle is neither the machinery's output nor anyone's work.
        real = [(w, t) for w, t, _ in born
                if ((w or "").strip().lower(), norm_title(t or "")) not in vehicles]
        out["kanban_vehicle_births"] = len(born) - len(real)
        mech = sum(1 for w, _ in real if (w or "").strip().lower() in MECHANICAL_AUTHORS)
        out["kanban_mechanical_share"] = _ratio(mech, len(real))

    # IS THIS A REPRESENTATIVE DAY? (the User, 2026-09-22: "most of that was caused by dev
    # restarts with a packed kanban. some days like this will show more.") A restart on a full
    # board re-spawns everything in flight, so births and mint_ratio inflate for a reason that
    # has nothing to do with board health. krew-tool-count already learned this and wrote it
    # down — "gate the report on a representative day per profile, not on a date" — but it was
    # prose there. This makes it a number: the share of the day's spawns that re-ran a card
    # already spawned that day. A quiet day sits near zero; a restart day does not, and a
    # reader can then discount the mint numbers instead of acting on them.
    if ecols and "created_at" in ecols:
        spawns = _rows(con, "SELECT task_id FROM task_events WHERE kind = 'spawned' "
                            "AND created_at >= ? AND created_at < ?", (since, now))
        seen: set = set()
        again = 0
        for (tid,) in spawns:
            if tid in seen:
                again += 1
            seen.add(tid)
        out["kanban_respawn_share"] = _ratio(again, len(spawns))

    # --- runs ending in nothing --------------------------------------------------------------
    if rcols:
        ended = _one(con, "SELECT count(*) FROM task_runs WHERE ended_at >= ? AND ended_at < ?", (since, now)) or 0
        nothing = _one(con, "SELECT count(*) FROM task_runs WHERE ended_at >= ? AND ended_at < ? AND outcome IN (%s)"
                       % ",".join("?" * len(NOTHING)), (since, now, *NOTHING)) or 0
        out["kanban_runs_nothing_share"] = _ratio(nothing, ended)

    # --- reframe ---------------------------------------------------------------------------
    if ccols and "created_at" in ccols:
        cuts = _rows(con, "SELECT task_id, body FROM task_comments WHERE body LIKE 'reframe-cut: t_%' AND created_at >= ? AND created_at < ?",
                     (since, now))
        out["kanban_reframe_cuts"] = len(cuts)
        if cuts:
            mech_cuts = sum(1 for _s, b in cuts if "by mechanical" in b)
            out["kanban_reframe_mechanical_share"] = _ratio(mech_cuts, len(cuts))
            if has_links:
                linked = 0
                for src, body in cuts:
                    m = CUT_RE.match(body)
                    if not m:
                        continue
                    cut = m.group(1)
                    # Phase 1 links `link <cut> <source>` (cut = parent). Either direction counts as linked.
                    if _one(con, "SELECT 1 FROM task_links WHERE (parent_id=? AND child_id=?) OR (parent_id=? AND child_id=?) LIMIT 1",
                            (cut, src, src, cut)):
                        linked += 1
                out["kanban_reframe_linked_share"] = _ratio(linked, len(cuts))
        # chains: every cut edge ever, depth among chains touching a card cut in the window
        edges: dict[str, str] = {}
        for src, body in _rows(con, "SELECT task_id, body FROM task_comments WHERE body LIKE 'reframe-cut: t_%'"):
            m = CUT_RE.match(body)
            if m:
                edges[src] = m.group(1)
        out["kanban_chain_depth_max"] = chain_depth(edges) if edges else 0

    # --- first failure → close, reframed vs plain ---------------------------------------------
    if ecols and "created_at" in ecols and "completed_at" in tcols:
        closed = _rows(con, "SELECT id, completed_at, title FROM tasks WHERE status IN ('done','archived') "
                       "AND completed_at >= ? AND completed_at < ?", (since, now))
        reframed_ids: set[str] = set()
        if ccols:
            for (tid,) in _rows(con, "SELECT DISTINCT task_id FROM task_comments WHERE body LIKE 'reframe-cut:%' OR body LIKE 'reframe-gen:%'"):
                reframed_ids.add(tid)
        r_h, p_h = [], []
        for tid, done_at, title in closed:
            first = _one(con, "SELECT min(created_at) FROM task_events WHERE task_id=? AND kind IN (%s)"
                         % ",".join("?" * len(FAIL_EVENTS)), (tid, *FAIL_EVENTS))
            if not first or not done_at or done_at < first:
                continue
            h = (done_at - first) / 3600.0
            (r_h if (tid in reframed_ids or (title or "").lower().startswith("reframe:")) else p_h).append(h)
        out["kanban_reframed_close_h"] = round(statistics.median(r_h), 2) if r_h else None
        out["kanban_plain_close_h"] = round(statistics.median(p_h), 2) if p_h else None

    # --- frames ------------------------------------------------------------------------------
    out["kanban_frames_open"] = _one(con, "SELECT count(*) FROM tasks WHERE title LIKE 'reframe:%' AND status NOT IN ('done','archived')") or 0
    if "result" in tcols:
        out["kanban_frames_run_as_work"] = _one(
            con, "SELECT count(*) FROM tasks WHERE title LIKE 'reframe:%' AND status='done' AND coalesce(result,'')='' "
                 "AND completed_at >= ? AND completed_at < ?", (since, now)) or 0

    # --- liveness (from the reader gate's file when it exists) ---------------------------------
    if liveness:
        out["kanban_no_reader"] = len(liveness.get("none") or [])
        out["kanban_overdue"] = len(liveness.get("overdue") or [])

    # --- leaves and parks ----------------------------------------------------------------------
    if has_links:
        oldest = _one(con, "SELECT min(t.created_at) FROM tasks t WHERE t.status IN (%s) AND NOT EXISTS ("
                      "SELECT 1 FROM task_links l JOIN tasks p ON p.id=l.parent_id WHERE l.child_id=t.id "
                      "AND p.status NOT IN ('done','archived'))" % ",".join("?" * len(OPEN)), OPEN)
    else:
        oldest = _one(con, "SELECT min(created_at) FROM tasks WHERE status IN (%s)" % ",".join("?" * len(OPEN)), OPEN)
    out["kanban_oldest_leaf_h"] = round((now - oldest) / 3600.0, 1) if oldest else 0.0
    if ecols and "payload" in ecols:
        parks = _rows(con, "SELECT t.id, coalesce((SELECT e.payload FROM task_events e WHERE e.task_id=t.id AND e.kind='scheduled' "
                           "ORDER BY e.id DESC LIMIT 1), '') FROM tasks t WHERE t.status='scheduled'")
        no_wake = 0
        for tid, payload in parks:
            text = payload or ""
            if ccols:
                text += " " + " ".join(b for (b,) in _rows(
                    con, "SELECT body FROM task_comments WHERE task_id=? AND lower(coalesce(author,'')) IN "
                         "(" + ",".join("?" * (len(RULERS) + 2)) + ") ORDER BY id DESC LIMIT 3",
                    (tid, *RULERS, "kanban-loop", "kanban-board-gate")))
            if not WAKE_RE.search(text):
                no_wake += 1
        out["kanban_parks_no_wake"] = no_wake
    else:
        out["kanban_parks_no_wake"] = _one(con, "SELECT count(*) FROM tasks WHERE status='scheduled'") or 0

    # --- salad, causes, verbs, fixes ----------------------------------------------------------
    if ccols and "created_at" in ccols:
        def _n(like: str) -> int:
            return _one(con, "SELECT count(*) FROM task_comments WHERE body LIKE ? AND created_at >= ? AND created_at < ?",
                        (like, since, now)) or 0
        out["kanban_salad_sessions"] = _n("salad-park:%")
        out["kanban_cause_harness"] = _n("cause: harness%")
        out["kanban_cause_verb"] = _n("cause: verb%")
        out["kanban_cause_real"] = _n("cause: real%")
        out["kanban_verbs_landed"] = _n("gate: routed%")
    out["kanban_fix_cards"] = _one(con, "SELECT count(*) FROM tasks WHERE title LIKE 'fix:%' AND created_at >= ? AND created_at < ?",
                                   (since, now)) or 0

    # --- the User's own burden: the one row this whole effort is for -----------------------------
    # Four ways a card reaches him, deduped to DISTINCT CARDS so one long thread is one touch.
    # Fable's §9 scorecard named this row ("User touches per day") and KPIS never got it, so
    # every landing since has been judged on board health instead of on the thing it is for.
    touched: set[str] = set()
    if ccols and "created_at" in ccols:
        for (tid,) in _rows(con, "SELECT DISTINCT task_id FROM task_comments WHERE created_at >= ? "
                                 "AND created_at < ? AND lower(coalesce(author,'')) IN (%s)"
                                 % ",".join("?" * len(HUMAN_AUTHORS)), (since, now, *HUMAN_AUTHORS)):
            touched.add(tid)                                   # 1. his word landed on the card
        for tid, body in _rows(con, "SELECT task_id, body FROM task_comments WHERE created_at >= ? "
                                    "AND created_at < ? AND lower(coalesce(author,'')) IN (%s)"
                                    % ",".join("?" * len(RULERS)), (since, now, *RULERS)):
            if HANDS_RULING_RE.match(body or ""):
                touched.add(tid)                               # 2. a ruler said `hands`
    if ecols and "payload" in ecols and "block_kind" in tcols:
        for tid, payload in _rows(con, "SELECT t.id, e.payload FROM tasks t JOIN task_events e "
                                       "ON e.task_id = t.id WHERE t.block_kind = 'capability' "
                                       "AND e.kind = 'blocked' AND e.created_at >= ? AND e.created_at < ?",
                                  (since, now)):
            if HANDS_RE.search(payload or ""):
                touched.add(tid)                               # 3. root, a password, or hands
    for tid, title in _rows(con, "SELECT id, title FROM tasks WHERE created_at >= ? AND created_at < ?",
                            (since, now)):
        if NEEDS_HUMAN_RE.search(title or ""):
            touched.add(tid)                                   # 4. a card filed AT him by title
    out["kanban_user_touches"] = len(touched)

    # --- duplicates: extra open copies of one normalized title ----------------------------------
    seen: dict[str, int] = {}
    for (title,) in _rows(con, "SELECT title FROM tasks WHERE status IN (%s) OR status='scheduled'" % ",".join("?" * len(OPEN)), OPEN):
        k = norm_title(title)
        if len(k) >= 8:
            seen[k] = seen.get(k, 0) + 1
    out["kanban_dup_open"] = sum(n - 1 for n in seen.values() if n > 1)
    return out


def headline(k: dict[str, Any]) -> str:
    """One line for the Daily's card: births/finishes, wasted runs, reframe health, readers."""
    parts = [f"+{k.get('kanban_minted') or 0} / −{k.get('kanban_completed') or 0}"]
    if k.get("kanban_runs_nothing_share") is not None:
        parts.append(f"{int(100 * k['kanban_runs_nothing_share'])} % of runs ended in nothing")
    cuts = k.get("kanban_reframe_cuts")
    if cuts:
        ms = k.get("kanban_reframe_mechanical_share") or 0
        parts.append(f"{cuts} reframe cut(s), {int(100 * ms)} % mechanical")
    if k.get("kanban_no_reader"):
        parts.append(f"{k['kanban_no_reader']} card(s) with no reader")
    if k.get("kanban_user_touches"):
        parts.append(f"{k['kanban_user_touches']} card(s) needed the user")
    return "; ".join(parts)
