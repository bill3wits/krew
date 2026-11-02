#!/usr/bin/env python3
"""kanban-salad-tripwire — reset crew's prefix cache when workers start
decoding token salad (house 2026-09-16).

Symptom (09-09, 09-15 06–09h, 09-16 03:54): one profile's kanban workers all
emit garbage from token 1 (mixed CJK/Latin, "33A0647…", system-prompt echo),
make zero tool calls, get nudged twice and exit rc=0 — while curl probes on
the same engine answer sanely. Memory `crew-prefix-cache-poisoning`: a cached
prefix block on :8005 is wrong; hits reuse it, misses recompute cleanly. The
only fix that works is ``POST /reset_prefix_cache`` at an idle instant
(``num_requests_running == 0 and num_requests_waiting == 0``); the endpoint
answers 200 even when it fails, so the journal line is the proof.

What this does, once per timer tick (5 min):

1. Look at worker logs on the current board written in the last WINDOW
   minutes; a *salad session* is the log's last session whose tail is > 3 % CJK
   letters or carries the "33A0" digit pattern — or (2026-09-17) a ZERO-TOOL-CALL
   session on a reachable engine whose model text is GIBBERISH: under WORD_RATIO
   (0.5) of its tokens are word-like. Calibrated on 67 zero-tool logs of 09-16/17:
   coherent sessions score >= 0.57, garbage <= 0.48; the CJK test saw 9 of ~38
   (that day's salad was digits and punctuation: "2026-05-11 11:05-1311:05-17…").
   A MUTE exit (zero tools, < 40 chars of model text) counts toward TRIP_AT but
   never trips alone — at least one hit must be real garbage. Coherent prose
   exits are still not counted: a cache reset does not cure those.
2. ONE CLEARLY garbage session trips it (2026-09-17, the User: the 12:26 salad waited 19 min for a
   second hit; each wait parks a card and a reset costs ~40 s). "Clearly" = the CJK/"33A0"
   shapes, or under CLEAR_RATIO (0.35) word-like tokens; a merely low-scoring session
   (0.35–0.5) or a mute still needs company (TRIP_AT, 2). Only logs written AFTER the last
   reset count — otherwise the same stale log re-trips every cooldown. No reset in the last COOLDOWN
   minutes → wait up to IDLE_WAIT seconds for crew to go idle WITHOUT the board's
   dispatch lock (2026-09-18 08:53: holding ``kanban.db.dispatch.lock`` while waiting
   froze Ada's dispatcher for the whole wait — at six seats crew never idles; the User: "a
   task should not be able to crash the dispatcher"), then take the lock for the reset
   instant only (re-check idle under it, else let go and keep waiting), POST the reset, verify the journal
   says "Successfully reset prefix cache", release.
3. RELEASE what the engine burned. After a journal-verified reset, the cards named in this
   trip (garbage and mute) that are still breaker-parked get one `unblock`, once per card ever
   (marker comment `salad-release:`), never a card kanban-loop already replaced
   (`reframe-cut:`). They failed because of the engine and sat parked as protocol violations;
   kanban-board-gate respects a release newer than the bad run. Kill SALAD_RELEASE=0.
4. Log every decision to ``~/.hermes/logs/kanban-salad-tripwire.log`` and
   record the reset in ``~/.hermes/kanban-salad-tripwire.json``.

Retreat: ``~/.hermes/kanban-salad-tripwire.off`` or ``KREW_SALAD_TRIPWIRE=0``
(the tick does nothing). ``--dry-run`` reports without holding or resetting;
``--force`` resets even below TRIP_AT (still waits for idle).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HERMES = Path(os.environ.get("KREW_HERMES") or KH.hermes_home())
CREW = os.environ.get("KREW_CREW_URL", "http://127.0.0.1:8005")
WINDOW_MIN = int(os.environ.get("SALAD_WINDOW_MIN", "15"))
TRIP_AT = int(os.environ.get("SALAD_TRIP_AT", "2"))
COOLDOWN_MIN = int(os.environ.get("SALAD_COOLDOWN_MIN", "10"))   # 09-17: poison re-formed 12 min after a reset
IDLE_WAIT_S = int(os.environ.get("SALAD_IDLE_WAIT_S", "480"))
CJK_RATIO = float(os.environ.get("SALAD_CJK_RATIO", "0.03"))
OFF = HERMES / "kanban-salad-tripwire.off"
STATE = HERMES / "kanban-salad-tripwire.json"
LOG = HERMES / "logs" / "kanban-salad-tripwire.log"

_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_TOOLS = re.compile(r"\((\d+) user, (\d+) tool calls\)")
_SESSION_SPLIT = "Query: work kanban task"
WORD_RATIO = float(os.environ.get("SALAD_WORD_RATIO", "0.5"))
CLEAR_RATIO = float(os.environ.get("SALAD_CLEAR_RATIO", "0.35"))   # one such session trips alone
HERMES_BIN = os.environ.get("KREW_HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
RELEASE_ON = os.environ.get("SALAD_RELEASE", "1").strip().lower() not in {"0", "off", "false", "no"}
RELEASE_MARK = "salad-release:"
PARK_MARK = "salad-park:"          # kanban-loop leaves a card carrying this to the tripwire
MAX_RELEASES = int(os.environ.get("SALAD_MAX_RELEASES", "3"))
TICK_SLACK_S = 45                  # the reset is stamped ~1 s into a 5-min tick: "599 s ago" held fire a whole tick
MUTE_CHARS = 40
_WORDLIKE = re.compile(r"[A-Za-z][A-Za-z'’\-]{1,}[.,;:!?)\"]*")
# Lines the harness prints, not the model.
_HARNESS = re.compile(
    r"^\s*$|^[╭╰│─┊ ]|Hindsight|Resume this|hermes -|^Session:|^Title:|^Duration:|^Messages:|⚠️|^Query:"
    r"|Initializ|Loaded|Model:|Provider|Endpoint|Retrying|API call|Elapsed|Error:|Switched to|Final error"
    r"|^\s*[⏳❌↻💀🔌🌐📝⏱]")


def model_text(session: str) -> str:
    return "\n".join(l for l in session.splitlines() if not _HARNESS.search(l))


def word_ratio(session: str) -> float | None:
    """Share of word-like tokens in the model's text; None for a non-zero-tool, engine-down
    or mute session."""
    tools = _TOOLS.findall(session)
    if not tools or int(tools[-1][1]) != 0 or "Connection error" in session:
        return None
    text = model_text(session)[-4000:]
    if sum(1 for c in text if not c.isspace()) < MUTE_CHARS:
        return None
    toks = text.split()
    return sum(1 for w in toks if _WORDLIKE.fullmatch(w)) / max(1, len(toks))


def zero_tool_shape(session: str) -> str:
    """'garbage' | 'mute' | '' for a session's last exit. Only zero-tool-call exits on a
    reachable engine (an engine-down exit is not the cache's fault)."""
    tools = _TOOLS.findall(session)
    if not tools or int(tools[-1][1]) != 0 or "Connection error" in session:
        return ""
    r = word_ratio(session)
    if r is None:
        return "mute"
    return "garbage" if r < WORD_RATIO else ""


def is_clear_garbage(session: str) -> bool:
    """Garbage nobody would argue with: one such session is enough to reset."""
    if is_salad_session(session):
        return True
    r = word_ratio(session)
    return r is not None and r < CLEAR_RATIO


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} salad-tripwire: {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def current_board() -> str:
    try:
        slug = (HERMES / "kanban" / "current").read_text(encoding="utf-8").strip()
    except OSError:
        slug = ""
    return slug or "default"


def board_dir(slug: str) -> Path:
    return HERMES / "kanban" / "boards" / slug


def is_salad_session(text: str) -> bool:
    """A salad-shaped tail: > CJK_RATIO of the letters in the last 5000 chars
    are CJK, or the "33A0" digit pattern repeats. Tool-call count is not a
    gate — Steward's 03:54 run made 61 tool calls and THEN decoded salad
    (`t_3f00d199`). Prose-only exits with no salad are NOT counted."""
    tail = text[-5000:]
    letters = sum(ch.isalpha() for ch in tail) or 1
    if len(_CJK.findall(tail)) / letters > CJK_RATIO:
        return True
    # The digit salad sits mid-session on t_3f00d199 (263 hits, none in the
    # tail — the run went on to 61 tool calls and a summary). Count the whole
    # session for that shape; ten hits never occur in honest output.
    return text.count("33A0") >= 10


def salad_sessions(logs_dir: Path, window_s: int, mutes: Optional[list[str]] = None,
                   clear: Optional[list[str]] = None, since: float = 0.0) -> list[str]:
    """Task ids whose most recent session (written inside the window, and after `since` — the
    last reset) is salad. Mute exits go to `mutes`, clearly-garbage ids also to `clear`."""
    now = time.time()
    hits: list[str] = []
    if not logs_dir.is_dir():
        return hits
    for p in logs_dir.glob("t_*.log"):
        try:
            mtime = p.stat().st_mtime
            if now - mtime > window_s or mtime <= since:
                continue
            text = p.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        segs = text.split(_SESSION_SPLIT)
        last = segs[-1] if len(segs) > 1 else text
        shape = zero_tool_shape(last)
        if is_salad_session(last) or shape == "garbage":
            hits.append(p.stem)
            if clear is not None and is_clear_garbage(last):
                clear.append(p.stem)
        elif shape == "mute" and mutes is not None:
            mutes.append(p.stem)
    return hits


def crew_load() -> Optional[tuple[float, float]]:
    """(running, waiting) from vLLM metrics, or None when crew is unreachable."""
    try:
        with urllib.request.urlopen(CREW + "/metrics", timeout=3) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    run = wait = None
    for ln in body.splitlines():
        if ln.startswith("vllm:num_requests_running{"):
            run = float(ln.rsplit(" ", 1)[1])
        elif ln.startswith("vllm:num_requests_waiting{"):
            wait = float(ln.rsplit(" ", 1)[1])
    if run is None or wait is None:
        return None
    return run, wait


def reset_prefix_cache() -> bool:
    """POST the reset and confirm from crew's journal (200 is not proof)."""
    try:
        req = urllib.request.Request(CREW + "/reset_prefix_cache", method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=10) as r:
            status = int(getattr(r, "status", 200) or 200)
    except Exception as e:  # noqa: BLE001
        log(f"reset POST failed: {type(e).__name__}: {e}"[:200])
        return False
    time.sleep(1.5)
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", os.environ.get("KREW_CREW_ENGINE_UNIT", "crew.service"), "--since", "-30s", "-o", "cat"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    ok = "Successfully reset prefix cache" in out
    fail = [ln.strip()[:160] for ln in out.splitlines() if "reset prefix cache" in ln.lower()]
    log(f"reset POST {status}; journal={'OK' if ok else 'no success line'} {fail[-1:] if fail else ''}")
    return ok


def _read_state() -> dict:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(d: dict) -> None:
    try:
        STATE.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _try_lock(handle) -> bool:
    for _ in range(5):                        # a dispatcher tick holds it for well under a second
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            time.sleep(1)
    return False


def trip(slug: str, hits: list[str], *, dry: bool) -> int:
    """Reset crew's prefix cache at an idle instant. The dispatch lock is held only for
    that instant — never while waiting (see the module docstring)."""
    lock_path = board_dir(slug) / "kanban.db.dispatch.lock"
    if dry:
        log(f"dry-run: would wait for crew idle, take {lock_path.name} for the reset instant, for {hits}")
        return 0
    log(f"waiting up to {IDLE_WAIT_S}s for crew idle (dispatch lock NOT held)")
    t0 = time.time()
    while time.time() - t0 < IDLE_WAIT_S:
        load = crew_load()
        if load is None:
            log("crew /metrics unreachable — giving up this tick")
            return 1
        if load != (0.0, 0.0):
            time.sleep(2)
            continue
        try:
            handle = lock_path.open("a+b")
        except OSError as e:
            log(f"cannot open dispatch lock: {e}")
            return 1
        held = False
        try:
            held = _try_lock(handle)
            if not held:
                log("dispatch lock busy — retrying at the next idle instant")
                continue
            if crew_load() != (0.0, 0.0):
                continue                      # a tick spawned meanwhile: let go, keep waiting
            ok = reset_prefix_cache()
            st = _read_state()
            st.update({"last_reset": time.time(), "ok": ok, "hits": hits, "board": slug})
            _write_state(st)
            return 0 if ok else 1
        finally:
            if held:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            handle.close()
    log(f"crew never idle in {IDLE_WAIT_S}s (last load {crew_load()}) — no reset; next tick retries")
    return 1


def should_trip(hits: list[str], mutes: list[str], clear: list[str]) -> bool:
    """One clearly garbage session; or real garbage with company up to TRIP_AT. Never mutes alone."""
    if clear:
        return True
    return bool(hits) and len(hits) + len(mutes) >= TRIP_AT


def _kanban(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run([HERMES_BIN, "-p", "ada", "kanban", *args], capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def releasable(db: Path, ids: list[str]) -> tuple[list[str], dict[str, str]]:
    """(cards to release, why each other one is left). Read-only."""
    import sqlite3
    out, left = [], {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for tid in dict.fromkeys(ids):
            row = con.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
            if not row or row[0] != "blocked":
                left[tid] = f"status {row[0] if row else 'missing'}"
                continue
            br = con.execute("SELECT kind FROM task_events WHERE task_id = ? AND kind IN ('gave_up','unblocked') "
                             "ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
            if not br or br[0] != "gave_up":
                left[tid] = "not a breaker park (a worker or a gate blocked it)"
                continue
            marks = [str(b or "") for (b,) in con.execute("SELECT body FROM task_comments WHERE task_id = ?", (tid,))]
            if any(m.lower().startswith("reframe-cut:") for m in marks):
                left[tid] = "kanban-loop already cut a reframe"
            elif sum(RELEASE_MARK in m for m in marks) >= MAX_RELEASES:
                left[tid] = f"released {MAX_RELEASES}x already — now the reframe loop's"
            else:
                out.append(tid)
    finally:
        con.close()
    return out, left


def mark_salad_parks(slug: str, ids: list[str], *, dry: bool = False, cli=None) -> int:
    """Say on the card what really happened, once per burn: hermes records a salad run as a
    protocol violation, and kanban-loop would cut a reframe ABOUT THE PROTOCOL from that text
    (~40 such cards on 09-17). A card carrying PARK_MARK is the tripwire's until its releases
    are spent."""
    import sqlite3
    db = board_dir(slug) / "kanban.db"
    if not ids or not db.is_file():
        return 0
    cli = cli or _kanban
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        todo = []
        for tid in dict.fromkeys(ids):
            marks = [str(b or "") for (b,) in con.execute("SELECT body FROM task_comments WHERE task_id = ?", (tid,))]
            if sum(PARK_MARK in m for m in marks) <= sum(RELEASE_MARK in m for m in marks):
                todo.append(tid)
    finally:
        con.close()
    for tid in todo:
        if not dry:
            cli("comment", tid, f"{PARK_MARK} this run decoded token salad (zero tool calls, garbage text) — a crew "
                "engine fault. It is NOT a protocol failure of this card; do not reframe it for that. The salad "
                "tripwire resets the engine and releases the card.", "--author", "salad-tripwire")
    return len(todo)


def stragglers(db: Path, last_reset: float) -> list[str]:
    """Salad-parked cards a reset left behind: still blocked, park mark older than the last
    journal-verified reset. 2026-09-17 23:34: t_6d71e5b6 was `running` at reset time, the
    release said "leave — status running", the next tick's `since=last_reset` never saw its
    log again, and kanban-loop skipped it as salad-owned — parked by everyone, released by
    nobody (3 such cards on the 09-18 board). `releasable` still applies (breaker park,
    no reframe-cut, releases unspent)."""
    import sqlite3
    if not last_reset or not db.is_file():
        return []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # a typed block is somebody's (needs_input = Crissa's queue): t_ceb0315d carried an old
        # salad-park under a worker's needs_input and must stay hers
        cols = {r[1] for r in con.execute("PRAGMA table_info(tasks)").fetchall()}
        typed = " AND t.block_kind IS NULL" if "block_kind" in cols else ""
        rows = con.execute(
            "SELECT c.task_id, MAX(c.created_at) FROM task_comments c JOIN tasks t ON t.id = c.task_id "
            f"WHERE t.status = 'blocked'{typed} AND c.body LIKE ? GROUP BY c.task_id", (PARK_MARK + "%",)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [str(tid) for tid, ts in rows if ts and float(ts) < last_reset]


def release_burned(slug: str, ids: list[str], *, dry: bool = False, cli=None, why: str = "") -> int:
    if not RELEASE_ON or not ids:
        return 0
    db = board_dir(slug) / "kanban.db"
    if not db.is_file():
        return 0
    cli = cli or _kanban
    go, left = releasable(db, ids)
    for tid, left_why in left.items():
        log(f"release: leave {tid} — {left_why}")
    n = 0
    reset_line = why or "The cache was just reset (journal-verified)."
    for tid in go:
        if dry:
            log(f"dry-run: would release {tid}")
            continue
        cli("comment", tid, f"{RELEASE_MARK} its run decoded token salad — a crew ENGINE fault, not this card's and "
            f"not a protocol failure. {reset_line} Up to {MAX_RELEASES} releases; a "
            "release spent into a relapse is not the card's doing.", "--author", "salad-tripwire")
        rc, out = cli("unblock", tid, "--reason", "salad-parked by an engine fault; prefix cache reset")
        if rc == 0:
            n += 1
            log(f"release: {tid} unblocked")
        else:
            log(f"release: {tid} unblock refused: {out.strip()[:120]}")
    return n


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    force = "--force" in argv
    if not dry and not force and os.environ.get("KREW_SALAD_TRIPWIRE_STANDALONE", "0") != "1":
        # 2026-09-18: folded into kanban-board-gate (2-min tick, no reset — prefix caching and MTP
        # are off on crew). A stray timer run does nothing; `--force` is the hand reset.
        log("folded into kanban-board-gate; nothing done (use --force for a hand reset)")
        return 0
    if os.environ.get("KREW_SALAD_TRIPWIRE", "1").strip().lower() in {"0", "off", "false"} or OFF.is_file():
        return 0
    slug = current_board()
    mutes: list[str] = []
    clear: list[str] = []
    st = _read_state()
    last_reset = float(st.get("last_reset") or 0)
    hits = salad_sessions(board_dir(slug) / "logs", WINDOW_MIN * 60, mutes, clear, since=last_reset)
    since_reset = time.time() - last_reset
    if hits:
        mark_salad_parks(slug, hits, dry=dry)
    if hits or mutes:
        log(f"{slug}: {len(hits)} salad session(s) since the last reset (≤{WINDOW_MIN} min): {hits}"
            + (f" — clearly garbage: {clear}" if clear else "")
            + (f"; {len(mutes)} mute exit(s): {mutes}" if mutes else ""))
    # Cards a past reset left parked (running at reset time, or parked between ticks):
    # every tick, not only the tick that trips. Same release, same cap.
    if last_reset and not force:
        late = stragglers(board_dir(slug) / "kanban.db", last_reset)
        if late:
            n = release_burned(slug, late, dry=dry,
                               why="The engine was reset (journal-verified) after this park and the card was left behind.")
            if n:
                log(f"released {n} straggler(s) parked before the last reset: {late}")
    # A mute exit helps reach the count; it never trips without real garbage beside it.
    if not should_trip(hits, mutes, clear) and not force:
        return 0
    if since_reset < COOLDOWN_MIN * 60 - TICK_SLACK_S and not force:
        log(f"cooldown: last reset {int(since_reset)}s ago (< {COOLDOWN_MIN} min) — holding fire")
        return 0
    rc = trip(slug, hits, dry=dry)
    if rc == 0:      # only after a journal-verified reset (trip returns 1 on anything else)
        n = release_burned(slug, hits + mutes, dry=dry)
        if n:
            log(f"released {n} card(s) the engine burned")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
