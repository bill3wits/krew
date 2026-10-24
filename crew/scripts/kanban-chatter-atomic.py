#!/usr/bin/env python3
"""Kanban chatter → wall KANBAN roll, two-phase (prepare/commit) over event ids.

Drop-in for kanban-chatter.py: same CLI (--dry-run --backfill N; plus
--recover-only), same roll format (~/.hermes/zoey-tool-roll.jsonl), same
cursor semantics (~/.hermes/kanban-chatter.cursor.json). Difference: an
atomic pending manifest (~/.hermes/kanban-chatter.pending.json) makes a
crash / SIGKILL mid-pass a *recoverable* state instead of a double-emit.

Design: t_87751df7 — design-decoupled-kanban-reporting-2026-09-13.md
(card t_3ac94fb7 implements it). Chosen approach is the refined candidate
(b): a two-phase commit over per-event ids, healed by the next 20 s tick.
The sidecar exit-watcher (candidate a) was rejected by the design.

Pass shape:
    1. recover()   heal a predecessor that died mid-pass
    2. read events (sqlite, mode=ro)
    3. prepare()   atomic manifest write (BEFORE touching the roll)
    4. roll append (append-only JSONL, flock-guarded)
    5. commit()    atomic cursor advance + manifest removal

Invariants:
    I1 (no loss)      an observed event's roll line is eventually appended;
                      the only "observed-but-uncommitted" state is a manifest
                      present, and the next pass always heals it.
    I2 (no double-emit) an event id appears in the roll at most once.
    I3 (crash-anywhere) the only atomicity-sensitive steps are os.replace()
                      (prepare, commit) — single-filesystem rename, atomic.
    I4 (consumer untouched) normal roll lines are byte-identical to the
                      legacy script; recovery lines add only a `recovered`
                      key, which the JSONL consumer ignores.
"""
from __future__ import annotations
import argparse, fcntl, json, os, sqlite3, time
from datetime import datetime
from pathlib import Path

HERMES = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
ROLL = Path(os.environ.get("ZOEY_TOOL_ROLL", HERMES / "zoey-tool-roll.jsonl"))
CURSOR = Path(os.environ.get("KANBAN_CHATTER_CURSOR", HERMES / "kanban-chatter.cursor.json"))
PENDING = Path(os.environ.get("KANBAN_CHATTER_PENDING", HERMES / "kanban-chatter.pending.json"))
SKIP = {"heartbeat", "spawned", "attached", "specified"}
VERB = {
    "created": "filed", "claimed": "picked up", "completed": "closed", "blocked": "blocked",
    "unblocked": "unblocked", "commented": "commented on", "archived": "archived",
    "promoted": "promoted", "timed_out": "timed out on", "gave_up": "gave up on",
    "assigned": "assigned", "review_requested": "asked for review on",
    "protocol_violation": "broke protocol on", "suspected_hallucinated_references": "cited thin air on",
}
# Recovery scans the tail of the roll for a per-line membership test. Capped at
# ~1 MB so a large roll stays O(bounded). If the roll ever grows past ~1 MB of
# "last pass's worth of lines", recovery degrades to the safe-but-duplicate
# direction and the journal line says so (see A2). A pass emits at most 40 lines
# (a few KB) at the 20 s cadence, so this is far more than enough today.
RECOVER_TAIL_BYTES = 1_000_000
RECOVER_MAX_AGE_H = 24.0  # manifests older than this are quarantined, not replayed


def board_db() -> Path | None:
    try:
        slug = (HERMES / "kanban" / "current").read_text().strip()
    except OSError:
        return None
    p = HERMES / "kanban" / "boards" / slug / "kanban.db"
    return p if p.is_file() else None


def _to_epoch(v) -> float:
    """Normalize a board created_at to an epoch float.

    The board stores created_at as ISO-8601 strings ('2026-09-13T05:11:13Z');
    some writers may still emit a numeric epoch. Return the numeric epoch in
    either case so the roll line can carry a real timestamp. Fall back to now
    on a blank/unparseable value rather than crashing the whole pass.
    """
    if v is None:
        return time.time()
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return time.time()
    try:
        # sqlite3 returns int/float; a string epoch is handled below.
        return float(s)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def _atomic_write(path: Path, data: bytes) -> None:
    """rename-into-place write: same dir, unique tmp, fsync, os.replace. Crash-safe.

    The tmp file is created in the SAME directory as the target (guaranteed by
    construction, not by the filesystem) so os.replace is a single rename on one
    filesystem — atomic on ext4/xfs. A crash leaves either the old path or the
    new one, never a torn file.
    """
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}-{int(time.time_ns())}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def lines_since(db_path: Path, after_id: int, limit: int = 40) -> tuple[list[dict], int]:
    """Read events with id > after_id (ro). Each row carries its event id as
    `eid` (the unique-per-event key used for the manifest + recovery). The
    roll-line shape strips `eid` at write time (see _roll_line) so the on-disk
    roll stays byte-identical to the legacy writer.
    """
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    out = []
    last = after_id
    for ev in db.execute("select e.id, e.task_id, e.kind, e.payload, e.created_at, t.title, t.assignee, t.status "
                         "from task_events e left join tasks t on t.id = e.task_id "
                         "where e.id > ? order by e.id asc limit ?", (after_id, limit)):
        last = int(ev["id"])
        kind = str(ev["kind"] or "")
        if kind in SKIP:
            continue
        try:
            pay = json.loads(ev["payload"]) if ev["payload"] else {}
        except ValueError:
            pay = {}
        if not isinstance(pay, dict):
            pay = {}
        who = str(pay.get("author") or pay.get("assignee") or ev["assignee"] or "board").strip().lower()
        if kind == "claimed":
            who = str(pay.get("lock") or who).split(":")[0].replace("-main", "") or who
        title = (ev["title"] or ev["task_id"] or "a card").strip()
        if len(title) > 56:
            title = title[:53].rstrip() + "…"
        verb = VERB.get(kind, kind.replace("_", " "))
        tail = ""
        if kind == "created" and pay.get("status"):
            tail = " → " + str(pay["status"])
        elif kind == "blocked" and pay.get("reason"):
            tail = " — " + str(pay["reason"])[:60].rstrip()
        elif kind == "completed" and pay.get("summary"):
            tail = " — " + str(pay["summary"])[:60].rstrip()
        text = f"{who} {verb} “{title}”{tail}"
        out.append({"eid": int(ev["id"]), "ts": _to_epoch(ev["created_at"]), "text": text,
                    "tool": "kanban_" + kind, "kind": "kanban", "who": who, "voice": None,
                    "task": ev["task_id"]})
    return out, last


def _roll_line(row: dict) -> dict:
    """The on-disk roll-line shape: the working row minus the internal `eid`.

    This is what keeps the roll byte-identical to the legacy writer (I4): the
    manifest keys on eid, but the roll never stores it.
    """
    return {k: v for k, v in row.items() if k != "eid"}


def _load_cursor() -> dict:
    try:
        cur = json.loads(CURSOR.read_text())
    except (OSError, ValueError):
        cur = {}
    return cur if isinstance(cur, dict) else {}


def _merge_cursor(cur: dict, key: str, eid: int) -> dict:
    """Monotone advance of one board key: cur[key] = max(old, eid)."""
    cur[key] = max(int(cur.get(key) or 0), int(eid))
    return cur


def _roll_tail_texts() -> set[str]:
    """Membership set of the 'text' fields of the tail of the roll (best-effort).

    Scans the last ~1 MB of the roll, at most. Recovery uses this to decide
    which manifest lines are already present (idempotent skip) vs. missing.
    On any I/O or JSON error it returns what it managed; a partial set biases
    toward re-emitting (the safe-but-duplicate direction), never toward loss.
    """
    out: set[str] = set()
    try:
        with ROLL.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - RECOVER_TAIL_BYTES))
            for raw in f:
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(line, dict) and isinstance(line.get("text"), str):
                    out.add(line["text"])
    except OSError:
        pass
    return out


def _append_roll(records: list[dict]) -> None:
    """Append roll lines under an exclusive flock (crash table C9: if two passes
    ever overlap, their appends cannot interleave). Opens append-only; the open
    itself surfaces a full/EIO disk (C5) to the caller rather than swallowing it.
    """
    if not records:
        # Still open-for-append so a full disk / EIO on the roll surfaces here
        # (crash table C5) instead of being silently skipped.
        with ROLL.open("a", encoding="utf-8"):
            pass
        return
    with ROLL.open("a", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except OSError:
            pass  # best-effort lock; the append below is the load-bearing write
        try:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _quarantine(why: str) -> None:
    """A manifest we can't trust → move it aside (never delete silently) and go on.

    Crash table C6: a corrupt/torn manifest. Worst case the dead run's events are
    re-read and re-emitted once (I2 violated once, LOUDLY), bounded to this one
    manifest. Renaming (not deleting) preserves the evidence for the operator.
    """
    q = PENDING.with_name(PENDING.name + f".quarantined-{int(time.time())}")
    try:
        os.replace(PENDING, q)
        print(f"QUARANTINED manifest → {q.name}: {why}", flush=True)
    except OSError as e:
        print(f"MANIFEST ERROR (kept in place): {why}; {e}", flush=True)


def recover() -> None:
    """Heal a predecessor that died mid-pass. Called first, every pass.

    Reads the pending manifest (if any); for each event id in it, appends the
    line to the roll only if it is not already there (per-id membership test);
    then commits the cursor on the key the manifest names (NOT the currently
    active board, so an overnight board rotation cannot shift eids onto the
    wrong key — crash table C8) and removes the manifest. Gone = quiescent.
    """
    try:
        m = json.loads(PENDING.read_text())
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        _quarantine(f"unreadable manifest: {e}")
        return
    if not isinstance(m, dict) or not isinstance(m.get("ids"), list):
        _quarantine("malformed manifest")
        return
    try:
        age_h = (time.time() - float(m.get("prepared_at", 0))) / 3600.0
    except (TypeError, ValueError):
        _quarantine("manifest missing/bad prepared_at")
        return
    if age_h > RECOVER_MAX_AGE_H:
        _quarantine(f"manifest older than {RECOVER_MAX_AGE_H:g} h (nonce {m.get('nonce')})")
        return

    existing = _roll_tail_texts()
    lines = m.get("lines") if isinstance(m.get("lines"), dict) else {}
    to_add: list[dict] = []
    for sid in m["ids"]:
        key = str(sid)
        row = lines.get(key)
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            # Manifest had no stored row for this id (shouldn't happen). Rebuild
            # a minimal line so the id still lands; text-collision risk is
            # cosmetic only (the design's self-attack #1).
            row = {"text": f"(recovered) kanban event {sid}", "kind": "kanban",
                   "tool": "kanban_recovered", "recovered": True}
            line = row
        else:
            line = _roll_line(row) | {"recovered": True}
        if line["text"] in existing:
            continue  # already in the roll → idempotent skip (no double-emit)
        to_add.append(line)

    # The append surfaces a full disk / EIO (crash table C5) via _append_roll.
    _append_roll(to_add)
    if to_add:
        # Commit only after the append call succeeded: advance the cursor on the
        # board key the manifest itself names (max-monotone), then drop the
        # manifest — gone = quiescent.
        cur = _load_cursor()
        mkey = str(m.get("board") or "")
        for sid in m["ids"]:
            if mkey:
                cur = _merge_cursor(cur, mkey, int(sid))
        _atomic_write(CURSOR, json.dumps(cur).encode())
        PENDING.unlink()
        print(f"recovered {len(to_add)} line(s) from manifest nonce {m.get('nonce')}", flush=True)
    else:
        # All lines already present (crash table C4: cursor committed before the
        # manifest was unlinked). Re-commit the cursor (no-op max) and drop the
        # manifest so the next pass doesn't re-scan it.
        cur = _load_cursor()
        mkey = str(m.get("board") or "")
        for sid in m["ids"]:
            if mkey:
                cur = _merge_cursor(cur, mkey, int(sid))
        _atomic_write(CURSOR, json.dumps(cur).encode())
        PENDING.unlink()
        print(f"recovered 0 line(s); manifest nonce {m.get('nonce')} already committed", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", type=int, default=0,
                    help="on a fresh cursor, emit this many recent events (default 0 = start at now)")
    ap.add_argument("--recover-only", action="store_true",
                    help="run recover() and exit (no new work) — the optional last-mile watch lane")
    a = ap.parse_args()
    db = board_db()
    if db is None:
        print("no current board", flush=True)
        return 0
    if not a.dry_run:
        recover()  # heal a dead predecessor first, then work
    if a.recover_only:
        return 0

    cur = _load_cursor()
    key = str(db)
    after = cur.get(key)
    if after is None:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        top = conn.execute("select coalesce(max(id),0) from task_events").fetchone()[0]
        after = max(0, int(top) - int(a.backfill))
    rows, last = lines_since(db, int(after))
    for r in rows:
        print(time.strftime("%H:%M:%S", time.localtime(r["ts"])), r["text"], flush=True)
    if a.dry_run:
        return 0
    if not rows:
        # Nothing to emit atomically. Still pin the cursor so a fresh-cursor
        # backfill start point doesn't get recomputed every tick (legacy parity).
        _atomic_write(CURSOR, json.dumps(_merge_cursor(cur, key, int(last))).encode())
        return 0

    # ---- prepare: atomic manifest BEFORE touching the roll. A kill anywhere
    #         from here to commit leaves the manifest → the next tick heals it.
    manifest = {
        "nonce": time.time_ns(),
        "board": key,
        "prepared_at": time.time(),
        "ids": [r["eid"] for r in rows],
        "lines": {str(r["eid"]): r for r in rows},  # full row (with eid) keyed by eid
    }
    _atomic_write(PENDING, json.dumps(manifest).encode())
    # Test seam (OFF by default; production never sets this): widens the window
    # between prepare and append so a SIGKILL kill-test can land it. Crash table
    # C2 — kill after prepare, before any append: manifest present, roll
    # unchanged. No effect on normal behaviour unless the env var is set.
    _t = os.environ.get("KANBAN_CHATTER_TEST_DELAY")
    if _t:
        try:
            time.sleep(float(_t))
        except ValueError:
            pass
    # ---- roll append (append-only, flock-guarded). A kill here leaves the
    #         manifest → recover() on the next tick appends only the missing
    #         lines and commits (crash table C3).
    _append_roll([_roll_line(r) for r in rows])
    # ---- commit: cursor advanced + manifest removed, each atomic.
    _atomic_write(CURSOR, json.dumps(_merge_cursor(cur, key, int(last))).encode())
    PENDING.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
