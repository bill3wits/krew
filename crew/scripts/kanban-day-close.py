#!/usr/bin/env python3
"""kanban-day-close — the standing board's nightly housekeeping (no more daily roll).

the User 2026-09-17: "standing board it is … we will get accurate completion stamps. No jams.
Is there a cache cleanse or something?" The day board used to roll: krew-board-carry
re-minted every open card (30 of 33 respawned at once on 09-16, parks included) to buy a
dated name. One board stays; this job keeps it lean, started by krew-window-tick when the
User's off-hours open:

  ARCHIVE  `done` cards older than DAY_CLOSE_MIN_AGE_H (36 — the 01:00 team-daily and the
           07:45 digest list `--status done` by completed_at, so a card stays visible
           through the next day's reports) whose tree is CLOSED. Archive keeps the thread
           and the completed_at stamp; hermes counts an archived parent as satisfied. But
           kanban-loop counts only `done`, so a done card is held while:
             - an open card waits on it (task_links parent → open child: dependents AND
               the frame that was decomposed into it),
             - the root it was decomposed from is still open,
             - an open card carries `reframe-cut: <it>` (the superseded source closes
               only when its reframe reads `done`).
  GC       `hermes kanban gc` — events and worker logs of terminal cards past 30 days.
           Nothing is purged (`archive --rm` is never called): archive is the only
           terminal verb and it keeps the thread.

           gc's 30 days is the house's event horizon: anything that needs longer history
           (ledgers, funnel counts) keeps its own file, not board events.

Exit 1 when an archive was refused or gc failed — the unit reads failed and
krew-window-tick records it; a run that did its work exits 0.
Writes only through `hermes -p ada kanban`. `--dry-run` prints and changes nothing.
Kill: ~/.hermes/kanban-day-close.off or KANBAN_DAY_CLOSE=0. DAY_CLOSE_MAX (200) per night.
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HERMES = os.environ.get("KREW_HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
PROFILE = os.environ.get("KANBAN_DAY_CLOSE_PROFILE", "ada")
KANBAN = Path(os.environ.get("KREW_KANBAN_HOME", str(KH.kanban_dir())))
OFF = KH.hermes_home() / "kanban-day-close.off"
LOG = KH.hermes_home() / "logs" / "kanban-day-close.log"
MIN_AGE_H = float(os.environ.get("DAY_CLOSE_MIN_AGE_H", "36"))
MAX_PER_NIGHT = int(os.environ.get("DAY_CLOSE_MAX", "200"))
CLOSED = ("done", "archived")
CUT_RE = re.compile(r"^reframe-cut:\s*(t_[0-9a-f]{8})", re.I)
# THE PARK BACKSTOP (2026-09-21). `scheduled` had no exit at all: this script read
# `done` only, so a parked card lived forever even after its own ruler, the steward
# sweep or Ada wrote `archive` on it in as many words. Ten cards on the 09-16 board
# were in that state, six of them carrying an unexecuted `VERB: archive`. A park
# gets a longer rope than a done card (three days, not 36 h) and the same family
# guards, and nothing is purged — `archive --rm` is still never called.
ARCHIVE_VERB_RE = re.compile(r"^\s*(?:\*\*)?VERB:(?:\*\*)?\s*archive\b", re.I | re.M)
RULER_ARCHIVE_RE = re.compile(r"^\s*(?:unblock\b[\s:,.;!-]*)?(?:archive|drop)\b", re.I)
RULERS = set(tuple(os.environ.get("KREW_RULERS", "crissa,user,human,dashboard").split(",")))
SWEEPERS = {"steward", "ada", "kanban-board-gate", "gate"}
PARK_AGE_H = float(os.environ.get("DAY_CLOSE_PARK_AGE_H", "72"))


def archive_parks_on() -> bool:
    return os.environ.get("DAY_CLOSE_ARCHIVE_PARKS", "1").strip().lower() not in ("0", "false", "no", "off")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} kanban-day-close: {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def board_db(kanban: Path = KANBAN) -> Path | None:
    cur = kanban / "current"
    slug = cur.read_text(encoding="utf-8").strip() if cur.is_file() else ""
    p = kanban / "boards" / slug / "kanban.db"
    return p if slug and p.is_file() else None


def archivable_parks(db: Path, *, now: float | None = None,
                     park_age_h: float = PARK_AGE_H) -> tuple[list[str], dict[str, int]]:
    """(parked ids that already carry an archive verdict, why the rest were held).

    Read-only. A card counts when a RULER's own word was archive/drop, or when a
    sweeper (the steward sweep, Ada's routing) wrote `VERB: archive` — the verdict
    that nothing downstream ever executed. Family guards match `archivable`: a park
    an open card waits on, or that an open source still reads as its reframe, stays.
    """
    now = time.time() if now is None else now
    if not archive_parks_on():
        return [], {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        status = dict(con.execute("SELECT id, status FROM tasks").fetchall())
        parked = [r[0] for r in con.execute(
            "SELECT id FROM tasks WHERE status = 'scheduled' ORDER BY created_at, id").fetchall()]
        waits_on: dict[str, list[str]] = {}
        for parent, child in con.execute("SELECT parent_id, child_id FROM task_links").fetchall():
            waits_on.setdefault(parent, []).append(child)
        superseding: set[str] = set()
        for tid, body in con.execute("SELECT task_id, body FROM task_comments WHERE body LIKE 'reframe-cut:%'"):
            m = CUT_RE.match((body or "").strip())
            if m and status.get(tid) not in CLOSED:
                superseding.add(m.group(1))
        threads: dict[str, list[tuple[int, str, str]]] = {}
        for tid, author, body, at in con.execute(
                "SELECT task_id, author, body, created_at FROM task_comments ORDER BY created_at, id"):
            if tid in set(parked):
                threads.setdefault(tid, []).append((int(at or 0), (author or "").lower(), body or ""))
    finally:
        con.close()
    held = {"no archive verdict": 0, "young": 0, "open card waits on it": 0, "open source reads it": 0}
    out: list[str] = []
    for tid in parked:
        thread = threads.get(tid, [])
        verdict_at = 0
        for at, author, body in thread:
            if ARCHIVE_VERB_RE.search(body) and (author in SWEEPERS or author in RULERS):
                verdict_at = max(verdict_at, at)
            elif author in RULERS and RULER_ARCHIVE_RE.match(body.strip()):
                verdict_at = max(verdict_at, at)
        last = max([at for at, _a, _b in thread] or [0])
        if not verdict_at:
            held["no archive verdict"] += 1
        elif (now - float(last)) < park_age_h * 3600:
            held["young"] += 1
        elif any(status.get(c) not in CLOSED for c in waits_on.get(tid, [])):
            held["open card waits on it"] += 1
        elif tid in superseding:
            held["open source reads it"] += 1
        else:
            out.append(tid)
    return out, {k: v for k, v in held.items() if v}


def archivable(db: Path, *, now: float | None = None, min_age_h: float = MIN_AGE_H) -> tuple[list[str], dict[str, int]]:
    """(ids to archive oldest first, why the rest were held). Read-only."""
    now = time.time() if now is None else now
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        status = dict(con.execute("SELECT id, status FROM tasks").fetchall())
        done = con.execute(
            "SELECT id, completed_at FROM tasks WHERE status = 'done' ORDER BY completed_at, id").fetchall()
        waits_on: dict[str, list[str]] = {}
        for parent, child in con.execute("SELECT parent_id, child_id FROM task_links").fetchall():
            waits_on.setdefault(parent, []).append(child)
        root_of: dict[str, str] = {}
        for tid, payload in con.execute(
                "SELECT task_id, payload FROM task_events WHERE kind = 'created' AND payload LIKE '%from_decompose_of%'"):
            try:
                root = (json.loads(payload) or {}).get("from_decompose_of")
            except (TypeError, json.JSONDecodeError):
                root = None
            if root:
                root_of[tid] = str(root)
        superseding: set[str] = set()          # reframes an OPEN source still waits to read `done`
        for tid, body in con.execute("SELECT task_id, body FROM task_comments WHERE body LIKE 'reframe-cut:%'"):
            m = CUT_RE.match((body or "").strip())
            if m and status.get(tid) not in CLOSED:
                superseding.add(m.group(1))
    finally:
        con.close()
    held = {"young": 0, "open card waits on it": 0, "root still open": 0, "open source reads it": 0, "no stamp": 0}
    out: list[str] = []
    for tid, completed_at in done:
        if not completed_at:
            held["no stamp"] += 1
        elif (now - float(completed_at)) < min_age_h * 3600:
            held["young"] += 1
        elif any(status.get(c) not in CLOSED for c in waits_on.get(tid, [])):
            held["open card waits on it"] += 1
        elif tid in root_of and status.get(root_of[tid]) not in CLOSED + (None,):
            held["root still open"] += 1
        elif tid in superseding:
            held["open source reads it"] += 1
        else:
            out.append(tid)
    return out, {k: v for k, v in held.items() if v}


def kanban(*args: str, timeout: int = 120) -> tuple[int, str]:
    p = subprocess.run([HERMES, "-p", PROFILE, "kanban", *args], capture_output=True, text=True,
                       timeout=timeout, check=False)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_archive(ids: list[str], cli=None) -> tuple[int, int]:
    """(archived, refused). A refused chunk is counted, logged and fails the run."""
    cli = cli or kanban
    archived = refused = 0
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        rc, out = cli("archive", *chunk)
        if rc == 0:
            archived += len(chunk)
        else:
            refused += len(chunk)
            log(f"archive refused for {chunk[0]}… ({len(chunk)}): {out.strip()[:160]}")
    return archived, refused


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if [a for a in argv if a != "--dry-run"]:
        print(__doc__)
        print("usage: kanban-day-close.py [--dry-run]")
        return 0 if set(argv) <= {"-h", "--help", "--dry-run"} else 2
    dry = "--dry-run" in argv
    if not dry and (OFF.is_file() or os.environ.get("KANBAN_DAY_CLOSE", "1").strip().lower() in ("0", "false", "no", "off")):
        log("off switch set; nothing done")
        return 0
    db = board_db()
    if db is None:
        log("no current board db; nothing done")
        return 0
    ids, held = archivable(db)
    ids = ids[:MAX_PER_NIGHT]
    note = ", ".join(f"{v} {k}" for k, v in held.items()) or "none held"
    parks, pheld = archivable_parks(db)
    parks = parks[:max(0, MAX_PER_NIGHT - len(ids))]
    pnote = ", ".join(f"{v} {k}" for k, v in pheld.items()) or "none held"
    if dry:
        log(f"dry: would archive {len(ids)} done card(s) on {db.parent.name}; held: {note}")
        log(f"dry: would retire {len(parks)} park(s) carrying an archive verdict; held: {pnote}")
        return 0
    archived, refused = run_archive(ids)
    parked_out = parked_refused = 0
    if parks:
        parked_out, parked_refused = run_archive(parks)
    rc, out = kanban("gc")
    log(f"{db.parent.name}: archived {archived} closed done card(s), {refused} refused; held: {note}; "
        f"gc {'ok' if rc == 0 else 'failed: ' + out.strip()[:120]}")
    log(f"{db.parent.name}: retired {parked_out} park(s) with an archive verdict, "
        f"{parked_refused} refused; held: {pnote}")
    return 1 if (refused or parked_refused or rc != 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
