#!/usr/bin/env python3
"""improvement-outcomes — did an agreed improvement move the KPI it named?

the User 2026-09-17 ("yep thats a good one … Let's ignite that loop now"): the team
ranks ideas (team-eval), the top tier becomes a green-gated `team-improve:` card
and a row in improvements/proposals.jsonl, Crissa greens a `try:`, the crew lands
it — and until now nothing asked whether the house got better. The loop closes
here:

  agreed (proposals.jsonl, KPI + baseline at filing — steward-file-architect-cards)
    → on Crissa's queue (the gate card) → parked | try → in work → LANDED (try done)
    → measured MEASURE_DAYS after landing against kpis.json history:
      better / worse / flat in the KPI's own direction.

Reads every board read-only (the standing board and the old dailies), kpis.json
and proposals.jsonl; writes improvements/outcomes.json (the whole picture) and
improvements/outcomes-<day>.md (what the House Daily / team-daily can quote).
No LLM, no board writes. Runs as a window job after team-eval.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import MS_DIR, today  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

IMPROVE = Path(os.environ.get("KREW_IMPROVE_DIR", str(MS_DIR / "improvements")))
BOARDS = Path(os.environ.get("KREW_KANBAN_BOARDS", str(KH.kanban_dir() / "boards")))
MEASURE_DAYS = float(os.environ.get("IMPROVE_MEASURE_DAYS", "7"))
FLAT = float(os.environ.get("IMPROVE_FLAT_FRACTION", "0.05"))
KPI_RE = re.compile(r"\bkpi\s*[:=]\s*([a-z][a-z0-9_]*)", re.I)


def load_proposals(path: Path) -> list[dict]:
    rows: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("kind") != "nnt-agreed" or not r.get("title"):
            continue
        # one row per ITEM: the agreed title also carries the vote count, which changes on a re-rank
        rows.setdefault(item_of(r["title"]).lower(), {}).update({k: v for k, v in r.items() if v not in (None, "")})
    return list(rows.values())


def item_of(title: str) -> str:
    return title.split(" — ")[0].strip().strip("`")


def board_cards(boards: Path = BOARDS) -> list[dict]:
    out = []
    for db in sorted(boards.glob("*/kanban.db")):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            continue
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
            links = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_links'").fetchone() is not None
            body_col = "body" if "body" in cols else "''"
            for tid, title, status, created, completed, body in con.execute(
                    f"SELECT id, title, status, created_at, completed_at, {body_col} FROM tasks "
                    "WHERE title LIKE 'team-improve:%' OR title LIKE 'try: %' OR title LIKE 'architect-try:%'"):
                comments = [{"author": a or "", "body": b or ""} for (a, b) in con.execute(
                    "SELECT author, body FROM task_comments WHERE task_id = ? ORDER BY id", (tid,))]
                # A decomposed try WAITS on its pieces: they are its parents in task_links
                # (parent = prerequisite). A review hangs as a child under the build it reviews.
                if not links or "result" not in cols:
                    out.append({"id": tid, "title": title or "", "status": status or "", "created_at": created,
                                "completed_at": completed, "comments": comments, "board": db.parent.name,
                                "body": body or "", "children": [], "review": ""})
                    continue
                pieces = con.execute(
                    "SELECT t.id, t.status FROM task_links l JOIN tasks t ON t.id = l.parent_id WHERE l.child_id = ?",
                    (tid,)).fetchall()
                kids = [s for (_i, s) in pieces]
                # the newest review verdict on any piece of the try: a closed try whose
                # change Vera FAILed did not land (vLLM 0.29, 09-19)
                # WHERE the verdict is written: the updated hermes puts a reviewer's words in the
                # run's `summary`; `tasks.result` holds them on 48 of 157 reviews (09-27). Read
                # result first, else the newest run summary, and take the NEWEST review of the
                # source: t_157d7918 read "landed" while its newest review was FAIL.
                said = ("COALESCE(NULLIF(t.result, ''), (SELECT r.summary FROM task_runs r WHERE r.task_id = t.id "
                        "AND COALESCE(r.summary, '') != '' ORDER BY r.id DESC LIMIT 1))"
                        if REVIEW_FROM_RUNS and _has_table(con, "task_runs") else "NULLIF(t.result, '')")
                rev = con.execute(
                    "WITH RECURSIVE d(id, n) AS (SELECT parent_id, 1 FROM task_links WHERE child_id = ? "
                    "UNION SELECT l.parent_id, d.n + 1 FROM task_links l JOIN d ON l.child_id = d.id WHERE d.n < 4) "
                    f"SELECT {said} AS said FROM task_links l JOIN tasks t ON t.id = l.child_id "
                    "WHERE l.parent_id IN (SELECT id FROM d) AND t.title LIKE 'review:%' "
                    "AND said IS NOT NULL ORDER BY COALESCE(t.completed_at, t.created_at) DESC LIMIT 1", (tid,)).fetchone()
                out.append({"id": tid, "title": title or "", "status": status or "", "created_at": created,
                            "completed_at": completed, "comments": comments, "board": db.parent.name,
                            "body": body or "", "children": kids, "child_ids": [i for (i, _s) in pieces],
                            "review": (rev[0] or "") if rev else ""})
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return out


def find_cards(item: str, cards: list[dict]) -> tuple[dict | None, dict | None]:
    """(gate card, try card) for an agreed item, newest of each."""
    key = item.lower()[:60]
    gate = [c for c in cards if c["title"].lower().startswith("team-improve:")
            and c["title"][len("team-improve:"):].strip().lower().startswith(key)]
    tries = [c for c in cards if c["title"].lower().startswith("try: team-improve:")
             and c["title"][len("try: team-improve:"):].strip().lower().startswith(key)]
    newest = lambda xs: max(xs, key=lambda c: float(c.get("created_at") or 0)) if xs else None  # noqa: E731
    return newest(gate), newest(tries)


def parked_ruling(c) -> bool:
    """A real park: Crissa's / the User's comment that opens with park, or the loop's
    own record of one. The gate QUESTION ("try | park | later") is not a ruling."""
    if isinstance(c, str):
        c = {"author": "", "body": c}
    body = (c.get("body") or "").strip().lower()
    body = re.sub(r"^unblock\b[\s:,.;!-]*", "", body)
    if body.startswith(("green: parked", "park-sticky:")):
        return True
    return (c.get("author") or "").lower() in tuple(os.environ.get("KREW_RULERS", "crissa,user,human,dashboard").split(",")) and re.match(r"^park\b", body) is not None


def value_near(kpi: dict, ts: float) -> float | None:
    hist = [h for h in (kpi.get("history") or []) if isinstance(h, dict) and h.get("value") is not None]
    if not hist:
        return None
    best = min(hist, key=lambda h: abs(float(h.get("ts") or 0) - ts))
    try:
        return float(best["value"])
    except (TypeError, ValueError):
        return None


def verdict(direction: str, before: float, after: float) -> str:
    # self-loop 09-28: a KPI with no direction is not measured at all. It used to
    # default to "up", so a missing field could publish a fall as "better".
    d = (direction or "").strip().lower()
    if d not in ("up", "down"):
        return "KPI has no direction: unmeasured"
    delta = after - before
    if abs(delta) <= FLAT * max(abs(before), 1.0):
        return "flat"
    better = delta < 0 if d == "down" else delta > 0
    return "better" if better else "worse"


def outcome(p: dict, cards: list[dict], kpis: dict, now: float) -> dict:
    item = item_of(p["title"])
    m = KPI_RE.search(p["title"])
    kid = (p.get("kpi_id") or (m.group(1).lower() if m else "")).lower()
    row = {"item": item, "agreed": p.get("ts"), "kpi_id": kid or "unnamed"}
    if p.get("status") == "withdrawn":
        row["stage"] = "withdrawn"
        row["measure"] = p.get("why", "")
        return row
    gate, tried = find_cards(item, cards)
    if tried:
        row["try"] = tried["id"]
        if tried["status"] in LANDED_STATUSES and tried.get("completed_at"):
            row.update(land_verdict(tried))
        elif tried["status"] == "archived":
            row["stage"] = "try archived"
        else:
            row["stage"] = f"in work ({tried['status']})"
    elif gate:
        row["gate"] = gate["id"]
        parked = gate["status"] == "scheduled" or any(parked_ruling(c) for c in gate["comments"])
        row["stage"] = ("parked by Crissa" if parked else
                        "on Crissa's queue" if gate["status"] in ("blocked", "todo", "ready", "triage") else
                        f"gate {gate['status']}, no try")
    else:
        row["stage"] = "agreed, not filed"
    return measure(row, p, kpis, kid, now)


# A landed try stays landed after the day-close archives it. kanban-day-close archives a done
# card at 36 h and the measure is due at 7 days, so until 2026-09-27 every try lost its landing
# about five days before it could be measured: ten reports, all "0 measured". An archived card
# WITH a completion time was completed and then filed away; one without was retired unbuilt.
# Kill: IMPROVE_MEASURE_ARCHIVED=0.
LANDED_STATUSES = (("done", "archived") if os.environ.get("IMPROVE_MEASURE_ARCHIVED", "1") != "0" else ("done",))
# Kill: IMPROVE_REVIEW_FROM_RUNS=0 (the verdict is read from tasks.result only, as before).
REVIEW_FROM_RUNS = os.environ.get("IMPROVE_REVIEW_FROM_RUNS", "1") != "0"


def _has_table(con, name: str) -> bool:
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


# --- landed = reviewed + committed (self-loop step 3, the User 09-28) -------------------------
# "Landed" meant the try card closed: tries closed in 9-60 min, and the NNT dedupe try "landed" at
# 23:37 before Vera's REVISE, its code still uncommitted. Now a closed try is landed only with
#   * a review word: PASS/APPROVED, or `review not armed` while KREW_REVIEW_GATE is off (the Krew
#     fence has it off; a missing review then is not the try's fault, and it takes no KPI slot);
#     FAIL/REVISE/REJECTED is not landed; armed and no review yet is not landed;
#   * a commit JOINED BY RULE: a `sha: <hex>` line on the card (body, comments, review) that git
#     knows, or the try's or a piece's card id TAGGING a commit subject - `t_x: ...` or `(t_x ...)`
#     / `(card t_x)` / `(t_a, t_x)`, the shape workers write. A card id in running prose is not a
#     ship: the first cut joined the NNT dedupe try to a ledger commit that said "stay with
#     t_68837b13's owner". Never HEAD, never a log search for similar words.
# Kill: IMPROVE_LANDED_STRICT=0 (closed = landed, the review word alone refuses, as before).
LANDED_STRICT = os.environ.get("IMPROVE_LANDED_STRICT", "1").strip().lower() not in ("0", "false", "no", "off")
BOARD_WIDE_UNMEASURED = os.environ.get("IMPROVE_BOARD_WIDE_UNMEASURED", "1").strip().lower() not in ("0", "false", "no", "off")
SHA_RE = re.compile(r"(?im)^\s*sha:\s*([0-9a-f]{7,40})\b")
REVIEW_WORD = re.compile(r"\W*(PASS|FAIL|REVISE|REJECTED|APPROVED)\b", re.I)
COMMIT_REPOS = [Path(x) for x in os.environ.get(
    "IMPROVE_COMMIT_REPOS", f"{ROOT}:{Path.home() / 'projects' / 'hermes-agent'}").split(":") if x]
_ARMED: list = []


def kpi_scope(kid: str) -> str:
    try:
        sys.path.insert(0, str(ROOT / "lib"))
        import idea_ledger
        return idea_ledger.kpi_scope(kid)
    except Exception:  # noqa: BLE001
        return "board" if (kid or "").startswith("kanban_") else "unit"


def review_armed() -> bool:
    """Is the board gate cutting review cards? Read from the unit that runs it (the fence turns it
    off in a drop-in), once per run. IMPROVE_REVIEW_ARMED=0|1 overrides (tests)."""
    env = os.environ.get("IMPROVE_REVIEW_ARMED", "").strip()
    if env in ("0", "1"):
        return env == "1"
    if not _ARMED:
        import subprocess
        try:
            out = subprocess.run(["systemctl", "--user", "show", "krew-hold-sweep.service", "-p", "Environment"],
                                 capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _ARMED.append("KREW_REVIEW_GATE=0" not in out.split())
    return bool(_ARMED[0])


def _git(repo: Path, *args: str) -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def commit_for(card: dict, repos: list[Path] | None = None, git=_git) -> str:
    """The commit this try shipped, joined by rule (see above), or ''."""
    repos = [r for r in (repos or COMMIT_REPOS) if (r / ".git").exists()]
    text = "\n".join([card.get("body") or "", card.get("review") or "",
                      *[c.get("body") or "" for c in card.get("comments") or []]])
    for m in SHA_RE.finditer(text):
        for repo in repos:
            full = git(repo, "rev-parse", "--verify", "--quiet", f"{m.group(1)}^{{commit}}")
            if full:
                return full[:12]
    ids = [i for i in [card.get("id"), *(card.get("child_ids") or [])] if i]
    if ids:
        tag = re.compile(r"(?:^|[(,]\s*(?:card\s+)?)(" + "|".join(map(re.escape, ids)) + r")\b(?!')")
        for repo in repos:
            for line in _subjects(repo, git):
                h, _, subj = line.partition("\t")
                if tag.search(subj):
                    return h[:12]
    return ""


_SUBJECTS: dict[str, list[str]] = {}


def _subjects(repo: Path, git=_git) -> list[str]:
    """`<sha>\t<subject>` for the repo's commits (all refs, newest first), read once per run."""
    key = str(repo)
    if key not in _SUBJECTS:
        _SUBJECTS[key] = [l for l in git(repo, "log", "--all", "-n", "20000", "--format=%H%x09%s").splitlines() if l]
    return _SUBJECTS[key]


def land_verdict(card: dict) -> dict:
    """The stage of a CLOSED try: landed only when reviewed (or review not armed) AND committed."""
    word = (REVIEW_WORD.match(card.get("review") or "") or [None, ""])[1].upper()
    if word in ("FAIL", "REVISE", "REJECTED"):
        return {"stage": f"closed, not landed (review {word})"}
    if not LANDED_STRICT:
        return {"stage": "landed", "landed_at": float(card["completed_at"])}
    review = "PASS" if word in ("PASS", "APPROVED") else ("" if review_armed() else "review not armed")
    if not review:
        return {"stage": "closed, not landed (no review yet)"}
    sha = commit_for(card)
    if not sha:
        return {"stage": "closed, not landed (no commit found)", "review": review}
    return {"stage": "landed", "landed_at": float(card["completed_at"]), "review": review, "sha": sha}


def measure(row: dict, p: dict, kpis: dict, kid: str, now: float) -> dict:
    """The 7-day verdict of a landed row against its KPI (baseline from `p`, else history)."""
    k = kpis.get(kid) if kid and kid not in ("none", "unnamed") else None
    if row.get("stage") != "landed":
        return row
    if not k:
        row["measure"] = "no KPI named" if kid in ("", "none", "unnamed") else f"KPI {kid} not registered"
        return row
    if BOARD_WIDE_UNMEASURED and kpi_scope(kid) == "board":
        # self-loop step 3: anything on the board moves a kanban_* count (the 09-26 hermes update
        # took kanban_blocked 2 -> 34), so no try can claim it. Named, never counted as better.
        row["measure"] = f"KPI {kid} is board-wide: unmeasured"
        return row
    landed = row["landed_at"]
    before = p.get("baseline")
    try:
        before = float(before) if before is not None else value_near(k, landed)
    except (TypeError, ValueError):
        before = value_near(k, landed)
    due = landed + MEASURE_DAYS * 86400
    if now < due:
        row["measure"] = f"due {dt.date.fromtimestamp(due).isoformat()}"
        row["baseline"] = before
        return row
    after = value_near(k, due)
    if before is None or after is None:
        row["measure"] = "no KPI samples around the landing — the KPI's source is not recording"
        return row
    row.update(baseline=before, after=after, delta=round(after - before, 3),
               direction=k.get("direction"), measure=verdict(str(k.get("direction") or ""), before, after))
    return row


# --- every try, not only the agreed ones (2026-09-19) -----------------------------------
# The 09-18 tries (vLLM 0.29 re-baseline, the adhd skill, terminal-state) came from Scout's
# note, not the agreed tier, so nothing followed them. A `try:` card is now a row of its own;
# kanban-loop stamps `watch: kpi=<id> baseline=<v>` at Crissa's ruling. IMPROVE_ALL_TRIES=0 = agreed only.
ALL_TRIES = os.environ.get("IMPROVE_ALL_TRIES", "1").strip().lower() not in ("0", "false", "no", "off")
WATCH_RE = re.compile(r"^watch:\s*kpi=([a-z][a-z0-9_]*)(?:\s+baseline=([-0-9.eE]+))?", re.M)
VOTE_RE = re.compile(r"team vote: ([^\n\]]+)")
TRY_PREFIX = re.compile(r"^try:\s*(?:team-improve|architect-try)?\s*:?\s*", re.I)


def try_rows(cards: list[dict], covered: set[str], kpis: dict, now: float) -> list[dict]:
    """A row per `try:` card no agreed proposal already follows (newest per item)."""
    seen: dict[str, dict] = {}
    for c in sorted((c for c in cards if c["title"].lower().startswith("try: ") and c["id"] not in covered),
                    key=lambda c: float(c.get("created_at") or 0)):
        seen[item_of(TRY_PREFIX.sub("", c["title"])).lower()[:60]] = c
    rows = []
    for c in seen.values():
        body = c.get("body") or ""
        w = WATCH_RE.search(body)
        m = KPI_RE.search(c["title"]) or KPI_RE.search(body)
        kid = (w.group(1) if w else (m.group(1) if m else "")).lower()
        p = {"title": TRY_PREFIX.sub("", c["title"]), "baseline": (w.group(2) if w and w.group(2) else None)}
        row = {"item": item_of(p["title"]), "agreed": None, "kpi_id": kid or "unnamed", "try": c["id"], "source": "try",
               "ruled": dt.date.fromtimestamp(float(c.get("created_at") or 0)).isoformat()}
        kids = c.get("children") or []
        if c["status"] in LANDED_STATUSES and c.get("completed_at"):
            row.update(land_verdict(c))
        elif c["status"] == "archived":
            row["stage"] = "try archived"
        else:
            row["stage"] = f"in work ({c['status']}" + (f", {kids.count('done')}/{len(kids)} children done" if kids else "") + ")"
        row = measure(row, p, kpis, kid, now)
        v = VOTE_RE.search(c["title"] + "\n" + body)
        if v:
            row["vote"] = v.group(1).strip()[:120]
        rows.append(row)
    return rows


def open_actions(path: Path) -> list[dict]:
    """Ops note action items still open (actions.jsonl, filed to Steward)."""
    rows: dict[str, dict] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ops_action_id"):
                rows[str(r["ops_action_id"])] = r
    except OSError:
        return []
    out = []
    for r in rows.values():
        if r.get("status", "open") != "open":
            continue
        title = str(r.get("title") or "")
        m = KPI_RE.search(title)
        out.append({"item": title[:160], "kpi_id": m.group(1).lower() if m else "unnamed",
                    "stage": "ops-action → Steward", "source": "ops"})
    return out


STAGE_ORDER = ("landed", "closed, not landed", "in work", "on Crissa's queue", "ops-action", "agreed, not filed", "gate", "parked", "try archived", "withdrawn")


def round_key(r: dict) -> tuple:
    """Rank: measured movers first (by size), then landed, in work, Crissa's queue, ops actions."""
    st = str(r.get("stage") or "")
    order = next((i for i, s in enumerate(STAGE_ORDER) if st.startswith(s)), len(STAGE_ORDER))
    moved = r.get("measure") in ("better", "worse")
    size = abs(float(r.get("delta") or 0)) / max(abs(float(r.get("baseline") or 1) or 1), 1e-9) if moved else 0.0
    return (0 if moved else 1, order, -size, str(r.get("item") or "").lower())


def with_now(r: dict, kpis: dict) -> dict:
    """The KPI's value now beside its baseline: the bump so far, before the 7-day verdict."""
    k = kpis.get(str(r.get("kpi_id") or ""))
    if isinstance(k, dict) and k.get("current") is not None:
        r["now"] = k.get("current")
        r.setdefault("direction", k.get("direction"))
    return r


# A verdict says the KPI moved; it does not say the TRY moved it. The KPIs are board-wide counts,
# so a measure is marked `confounded` when something else could have moved the same number inside
# the try's own window: another landed try watching the same KPI, or a house event from
# config/improvement-confounders.json (the 09-26 hermes update took kanban_open 13 -> 49 by
# itself). The verdict stays on the row; the lede counts how many can be read as the try's own.
# Kill: IMPROVE_CONFOUNDED=0.
CONFOUNDERS = Path(os.environ.get("IMPROVE_CONFOUNDERS",
                                  str(Path(__file__).resolve().parents[1] / "config" / "improvement-confounders.json")))


def krew_events(path: Path = CONFOUNDERS) -> list[tuple[float, str]]:
    try:
        rows = json.loads(path.read_text()).get("events") or []
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for r in rows:
        try:
            out.append((dt.datetime.fromisoformat(str(r["at"])).timestamp(), str(r.get("what") or "house event")))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def mark_confounded(rows: list[dict], events: list[tuple[float, str]] | None = None) -> list[dict]:
    if os.environ.get("IMPROVE_CONFOUNDED", "1").strip().lower() in ("0", "false", "no", "off"):
        return rows
    events = krew_events() if events is None else events
    span = MEASURE_DAYS * 86400
    landed = [r for r in rows if r.get("stage") == "landed" and r.get("landed_at")]
    for r in landed:
        if r.get("measure") not in ("better", "worse", "flat"):
            continue
        t0 = float(r["landed_at"])
        why = [f"{what} ({dt.datetime.fromtimestamp(at):%m-%d %H:%M})" for at, what in events if t0 <= at <= t0 + span]
        same = [o for o in landed if o is not r and o.get("kpi_id") == r.get("kpi_id")
                and abs(float(o["landed_at"]) - t0) < span]
        if same:
            why.append(f"{len(same)} other landed tr{'y' if len(same) == 1 else 'ies'} watch {r.get('kpi_id')} in the same "
                       f"{MEASURE_DAYS:g} days ({', '.join(str(o.get('try') or o.get('item'))[:24] for o in same[:3])})")
        if why:
            r["confounded"] = why
    return rows


def render(day: str, rows: list[dict]) -> str:
    landed = [r for r in rows if r.get("stage") == "landed"]
    moved = [r for r in landed if r.get("measure") in ("better", "worse", "flat")]
    agreed_n = sum(1 for r in rows if r.get("source", "agreed") == "agreed")
    tries_n = sum(1 for r in rows if r.get("source") == "try")
    lines = [f"# Improvements — did they move the KPI? ({day})", "",
             f"lede: {agreed_n} agreed · {tries_n} other tries · {len(landed)} landed · {len(moved)} measured "
             f"({sum(1 for r in moved if r['measure'] == 'better')} better, "
             f"{sum(1 for r in moved if r['measure'] == 'worse')} worse, "
             f"{sum(1 for r in moved if r['measure'] == 'flat')} flat)"
             + (f" · {sum(1 for r in moved if r.get('confounded'))} of them confounded, "
                f"{sum(1 for r in moved if not r.get('confounded'))} readable as the try's own"
                if any(r.get("confounded") for r in moved) else "") + ".", "",
             "| item | KPI | stage | measure |", "|---|---|---|---|"]
    for r in rows:
        meas = r.get("measure", "")
        if r.get("after") is not None:
            meas = f"{meas}: {r.get('baseline')} → {r.get('after')}"
        if r.get("confounded"):
            meas += " (confounded)"
        lines.append(f"| {r['item'][:70]} | {r.get('kpi_id')} | {r.get('stage')} | {meas} |")
    rnd = [r for r in rows if not str(r.get("stage") or "").startswith(("withdrawn", "parked", "try archived"))]
    if any(r.get("source") for r in rows):
        lines += ["", "## The round — ranked", "",
                  "| # | item | stage | vote | KPI | baseline → now | 7-day |", "|---|---|---|---|---|---|---|"]
        for i, r in enumerate(sorted(rnd, key=round_key), 1):
            b, n = r.get("baseline"), r.get("now")
            bn = f"{b if b is not None else '?'} → {n}" if n is not None else ("" if b is None else str(b))
            lines.append(f"| {i} | {r['item'][:80]} | {r.get('stage')} | {r.get('vote', '')[:60]} | "
                         f"{r.get('kpi_id')} | {bn} | {r.get('measure', '')} |")
    mixed = [r for r in rows if r.get("confounded")]
    if mixed:
        lines += ["", "## Confounded — the KPI moved, the try may not have moved it", "",
                  "| item | KPI | measure | what else moved the number |", "|---|---|---|---|"]
        for r in mixed:
            lines.append(f"| {r['item'][:70]} | {r.get('kpi_id')} | {r.get('measure')} | {'; '.join(r['confounded'])[:300]} |")
    lines += ["", f"_Measured {MEASURE_DAYS:g} days after the try card lands, in the KPI's own direction; "
              f"within {FLAT:.0%} is flat. scripts/improvement-outcomes.py_"]
    return "\n".join(lines) + "\n"


# --- lessons: the loop's memory, handed back to the next night (self-loop step 4) -------------
# Nothing read outcomes back into Scout, the voters or Crissa, so the same repos were re-debated
# every night (agent-browser 8 nights, open-code-review 7). Run #1 of this script (window row
# outcomes-lessons, early) writes lessons-<day>.md: a BOUNDED cooldown list from the idea ledger
# and the last few verdicts. steward-architect-run.sh hands it to Scout (team.md) and the voters
# (the research note, under its own heading); the filer hands an idea's own lines to Crissa on its
# team-improve card. Never into her Decide prompt. Voter weights wait for an attributable verdict.
# Kill: IMPROVE_LESSONS=0 (the row writes an empty "lessons off" file).
LESSONS_COOLDOWN_MIN_NIGHTS = int(os.environ.get("IMPROVE_COOLDOWN_NIGHTS", "2"))
LESSONS_COOLDOWN_DAYS = int(os.environ.get("IMPROVE_COOLDOWN_DAYS", "7"))
LESSONS_MAX = 12
# the kind words a ranker / ledger / filer parse as an item: never written in a lessons line
_KIND_WORDS = re.compile(r"\b(nnt-pick|nnt-agreed|team-improve|architect-try|architect-watch|team-source|idea)\s*:", re.I)


def _plain(text: str) -> str:
    return _KIND_WORDS.sub(lambda m: m.group(1) + " -", str(text or "")).replace("`", "")


def cooldown(state: dict, day: str) -> list[dict]:
    """Ideas debated on >= N nights, never agreed, last debated within the window: the voters
    have answered them. A re-pick needs something new (a release, a change in the house)."""
    cut = (dt.date.fromisoformat(day) - dt.timedelta(days=LESSONS_COOLDOWN_DAYS)).isoformat()
    out = [s for s in state.values()
           if len(s.get("debated_days") or []) >= LESSONS_COOLDOWN_MIN_NIGHTS and not s.get("agreed_days")
           and not s.get("top_days") and not s.get("task") and max(s.get("debated_days") or [""]) >= cut]
    return sorted(out, key=lambda s: (-len(s["debated_days"]), s["key"]))


def lessons(day: str, rows: list[dict], state: dict) -> str:
    cool_all = cooldown(state, day)
    cool = cool_all[:LESSONS_MAX]
    lines = [f"# Lessons — {day}", "",
             "_From the idea ledger and the outcomes (scripts/improvement-outcomes.py --lessons). "
             "Read before picking or scoring; a repeat needs something NEW, and says what._", "",
             f"## Cooldown ({len(cool)}" + (f" of {len(cool_all)}, the most-debated first; the idea ledger has the rest"
                                         if len(cool_all) > len(cool) else "")
             + f"): debated {LESSONS_COOLDOWN_MIN_NIGHTS}+ nights, never agreed", ""]
    for s in cool:
        name = s["key"][5:] if s["key"].startswith("repo:") else "house change - " + str(s.get("text") or s["key"])[:90]
        d = sorted(s["debated_days"])
        lines.append(f"- {_plain(name)} — {len(d)} nights ({d[0][5:]}..{d[-1][5:]}), best sum "
                     f"{s.get('best_sum') if s.get('best_sum') is not None else '?'}")
    if not cool:
        lines.append("- none")
    landed = [r for r in rows if str(r.get("stage")) == "landed"]
    notl = [r for r in rows if str(r.get("stage") or "").startswith("closed, not landed")]
    lines += ["", "## Last verdicts", ""]
    for r in sorted(landed, key=lambda r: -float(r.get("landed_at") or 0))[:6]:
        m = str(r.get("measure") or "")
        if r.get("confounded") and m == "better":
            m = "better but confounded (not counted)"
        lines.append(f"- {cut(_plain(r.get('item')), 90, 'outcomes.json has it whole')} — landed {r.get('sha') or ''} ({r.get('review') or 'reviewed'}); "
                     f"{r.get('kpi_id')}: {m or 'not measured yet'}")
    if len(landed) > 6:
        lines.append(f"- and {len(landed) - 6} older landed verdict(s): outcomes.json has them all")
    if not landed:
        lines.append("- nothing landed with a commit yet")
    if notl:
        from collections import Counter
        c = Counter(str(r["stage"]).split("(", 1)[-1].rstrip(")") for r in notl)
        lines.append("- closed but not landed: " + ", ".join(f"{n} {k}" for k, n in c.most_common()))
    return "\n".join(lines) + "\n"


def lessons_main(day: str, rows: list[dict]) -> int:
    sys.path.insert(0, str(ROOT / "lib"))
    sys.path.insert(0, str(ROOT.parent / "window" / "lib"))   # krew: job_status lives with the window
    import idea_ledger
    import job_status
    IMPROVE.mkdir(parents=True, exist_ok=True)
    out = IMPROVE / f"lessons-{day}.md"
    if os.environ.get("IMPROVE_LESSONS", "1").strip().lower() in ("0", "false", "no", "off"):
        out.write_text(f"# Lessons — {day}\n\n_lessons off (IMPROVE_LESSONS=0)_\n")
        job_status.write("outcomes-lessons", day, "skipped", "IMPROVE_LESSONS=0", str(out))
        return 0
    text = lessons(day, rows, idea_ledger.fold(idea_ledger.load()))
    tmp = out.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(out)
    n = text.count("\n- ")
    job_status.write("outcomes-lessons", day, "ok", f"{n} line(s)", str(out))
    print(f"lessons: {out} ({n} lines)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    day = today().isoformat()
    props = load_proposals(IMPROVE / "proposals.jsonl")
    try:
        kpis = json.loads((IMPROVE / "kpis.json").read_text()).get("kpis") or {}
    except (OSError, json.JSONDecodeError):
        kpis = {}
    cards = board_cards()
    now = time.time()
    rows = [outcome(p, cards, kpis, now) for p in props]
    for r in rows:
        r.setdefault("source", "agreed")
    if ALL_TRIES:
        covered = {str(r.get("try")) for r in rows if r.get("try")}
        rows += try_rows(cards, covered, kpis, now)
        rows += open_actions(IMPROVE / "actions.jsonl")
    rows = mark_confounded([with_now(r, kpis) for r in rows])
    if "--lessons" in argv:
        # run #1 (early in the window): the lessons for tonight's prompts; the snapshot the House
        # Daily reads is run #2, after improve-file, so it carries tonight's filings
        return lessons_main(day, rows)
    ranked = sorted((r for r in rows if not str(r.get("stage") or "").startswith(("withdrawn", "parked", "try archived"))),
                    key=round_key)
    IMPROVE.mkdir(parents=True, exist_ok=True)
    (IMPROVE / "outcomes.json").write_text(json.dumps({"day": day, "rows": rows, "round": ranked}, indent=2) + "\n")
    md = render(day, rows)
    (IMPROVE / f"outcomes-{day}.md").write_text(md)
    print(md.splitlines()[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
