"""A card's leftover worker processes — found by the run that spawned them, stopped only on the User's YES.

2026-09-19: card t_25bf04d1's worker ran hermes's whole CLI suite; the card was held (10:17) and
blocked (11:38) but the suite kept running 1 h 38 min under hermes-ada-gateway.service and filled
84 GB of swap. Blocking a card does not stop the processes its worker started. Live the same hour:
t_b1170887's bash run 1111 ended 11:45:43 (review_requested) and its worker, pid 142085, kept working.

The rule was adversarially reviewed before it was built (REVISE, 7 must-fix). What it does:

* A candidate carries HERMES_KANBAN_TASK=<card> (and, when present, this board's HERMES_KANBAN_BOARD),
  lives in a ``hermes-*-gateway.service`` cgroup OR its own ``kanban-worker-*.scope`` (2026-09-22:
  scripts/run_kanban_task.sh runs each worker in krew-kanban-worker.slice so a runaway can no longer
  OOM its dispatcher — the scope is NAMED by the wrapper, so a bare ``run-rNNN.scope`` from someone's
  hand is still excluded, which is what "never a scope" was protecting against), is not a gateway
  (no ``gateway`` subcommand, not a unit MainPID), is not a zombie, and is not a render/Comfy process.
* It is TIED to a run: workers are session leaders (start_new_session), so the process's session id is
  its worker's spawn pid, and that pid must be the ``spawned`` event of one run of that card whose
  timestamp sits within ``TIE_SKEW_S`` of the session leader's start time (or, leader gone, the process
  started after that spawn). ``tasks.worker_pid`` / ``task_runs.worker_pid`` are NOT used — they are
  NULL for most ended runs. A process that cannot be tied is logged and never signalled.
* It is LEFTOVER when its tied run has ``ended_at`` at least ``GRACE_S`` ago, it is not in the card's
  current run, it started before the current run began, and its session is not any running card's
  live worker.
* Signalling (act mode only): the card row is re-read first; SIGTERM through a pidfd after re-checking
  (pid, starttime); a (pid, starttime) still alive on a later tick gets SIGKILL. Pid by pid, never a
  process group.

A card hermes still claims (claim_lock live — held for a whole run) is skipped: a leftover of an EARLIER
run is reported only after the card's current run ends (the safe side; late review 2026-09-19).

Mode: KREW_ORPHAN_REAP = report (default: log what it WOULD stop) | 1 (act) | 0 (off). Acting also
requires the caller to be krew-hold-sweep.service and to carry no HERMES_KANBAN_TASK of its own.
"""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

GRACE_S = float(os.environ.get("KREW_ORPHAN_GRACE_S", "600"))
TIE_SKEW_S = 5.0
RENDER_WORDS = ("comfy", "main.py", "render-submit", "render-collect", "krew-mode", "ffmpeg")
SWEEP_UNIT = "krew-hold-sweep.service"
# The scope name scripts/run_kanban_task.sh gives each worker: kanban-worker-<card>-<pid>.scope
WORKER_SCOPE_PREFIX = "kanban-worker-"
# The scope hermes itself gives a kanban worker since the 2026-09-26 update (tools/process_registry:
# hermes-worker-kanban-<card>-run-<run>.scope). The wrapper is not called on that tree, so without
# this name every worker reads as "not a worker" and the gate goes blind. `hermes-worker-` alone is
# NOT enough: hermes names its other background workers with it too.
HERMES_WORKER_SCOPE_PREFIX = "hermes-worker-kanban-"


def mode() -> str:
    v = (os.environ.get("KREW_ORPHAN_REAP") or "report").strip().lower()
    if v in ("0", "off", "false", "no"):
        return "off"
    if v in ("1", "on", "true", "yes", "act"):
        return "act"
    return "report"


@dataclass
class Proc:
    pid: int
    ppid: int
    sid: int
    state: str
    start: float           # epoch seconds
    starttime_ticks: int
    cgroup: str
    argv: list[str]
    env: dict[str, str]


@dataclass
class Finding:
    pid: int
    starttime_ticks: int
    card: str
    run_id: int | None
    why: str
    ended_ago: float | None = None
    leftover: bool = False
    descendants: list[tuple[int, int]] = field(default_factory=list)


def _btime(proc: Path) -> float:
    try:
        for ln in (proc / "stat").read_text().splitlines():
            if ln.startswith("btime "):
                return float(ln.split()[1])
    except OSError:
        pass
    return time.time() - time.monotonic()


def read_procs(proc: Path = Path("/proc")) -> list[Proc]:
    tck = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    bt = _btime(proc)
    out: list[Proc] = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text()
            env_raw = (d / "environ").read_bytes()
            argv = [a.decode(errors="replace") for a in (d / "cmdline").read_bytes().split(b"\0") if a]
            cg = (d / "cgroup").read_text().strip().splitlines()
        except OSError:
            continue
        rest = stat[stat.rfind(")") + 2:].split()           # fields from 3 (state) on
        try:
            state, ppid, sid, st = rest[0], int(rest[1]), int(rest[3]), int(rest[19])
        except (IndexError, ValueError):
            continue
        env = {}
        for kv in env_raw.split(b"\0"):
            if b"=" in kv:
                k, v = kv.split(b"=", 1)
                env[k.decode(errors="replace")] = v.decode(errors="replace")
        leaf = cg[-1].split("/")[-1] if cg else ""
        out.append(Proc(int(d.name), ppid, sid, state, bt + st / tck, st, leaf, argv, env))
    return out


def _worker_cgroup(leaf: str) -> bool:
    """Where a kanban worker is allowed to live.

    Its dispatcher's gateway (the shape before 2026-09-22, and still the wrapper's fallback when
    systemd-run cannot make a scope), or the scope the wrapper names for it. The named prefix is
    load-bearing: it keeps a hand's ``systemd-run`` (``run-rNNN.scope``), a login session and
    Comfy out, which is exactly what the original "never a scope" rule existed to do.
    """
    if leaf.startswith("hermes-") and leaf.endswith("-gateway.service"):
        return True
    return leaf.startswith((WORKER_SCOPE_PREFIX, HERMES_WORKER_SCOPE_PREFIX)) and leaf.endswith(".scope")


def _is_gateway(p: Proc) -> bool:
    return "gateway" in p.argv[2:] or any(a == "gateway" for a in p.argv)


def _is_render(p: Proc) -> bool:
    joined = " ".join(p.argv).lower()
    return any(w in joined for w in RENDER_WORDS)


def _spawns(conn: sqlite3.Connection, card: str) -> list[tuple[int | None, int, float]]:
    rows = conn.execute(
        "SELECT run_id, payload, created_at FROM task_events WHERE task_id = ? AND kind = 'spawned'",
        (card,)).fetchall()
    out = []
    for run_id, payload, ts in rows:
        try:
            pid = int(json.loads(payload or "{}").get("pid"))
        except (TypeError, ValueError):
            continue
        out.append((run_id, pid, float(ts)))
    return out


def find(conn: sqlite3.Connection, procs: Iterable[Proc], *, board: str = "", now: float | None = None,
         gateway_mainpids: Iterable[int] = (), grace_s: float = GRACE_S) -> list[Finding]:
    now = time.time() if now is None else now
    procs = list(procs)
    by_pid = {p.pid: p for p in procs}
    mains = set(gateway_mainpids)
    # live workers of running cards: their session must never be touched
    live_sessions: set[int] = set()
    for (tid,) in conn.execute("SELECT id FROM tasks WHERE status = 'running'"):
        cur = conn.execute("SELECT current_run_id FROM tasks WHERE id = ?", (tid,)).fetchone()
        for run_id, pid, _ts in _spawns(conn, tid):
            if cur and run_id == cur[0]:
                live_sessions.add(pid)
    out: list[Finding] = []
    for p in procs:
        card = p.env.get("HERMES_KANBAN_TASK", "").strip()
        if not card:
            continue
        if not _worker_cgroup(p.cgroup):
            continue
        if p.pid in mains or _is_gateway(p) or p.state == "Z" or _is_render(p):
            continue
        b = p.env.get("HERMES_KANBAN_BOARD", "").strip()
        if board and b and b != board:
            continue
        row = conn.execute("SELECT status, current_run_id FROM tasks WHERE id = ?", (card,)).fetchone()
        if row is None:
            continue
        status, cur_run = row
        f = Finding(p.pid, p.starttime_ticks, card, None, "")
        # hermes holds a live claim on the card: its own reclaim/terminate path owns the worker — never race
        # it (late review 2026-09-19, should-fix). Tolerant of boards without the columns (tests).
        try:
            cl = conn.execute("SELECT claim_lock, claim_expires FROM tasks WHERE id = ?", (card,)).fetchone()
        except sqlite3.OperationalError:
            cl = None
        if cl and cl[0] and (cl[1] is None or float(cl[1]) > now):
            f.why = "hermes holds a live claim on the card — its reclaim owns the worker"
            out.append(f)
            continue
        if p.sid in live_sessions:
            f.why = "session of a running card's live worker"
            out.append(f)
            continue
        # tie to a run: the session leader is the worker the dispatcher spawned
        leader = by_pid.get(p.sid)
        tied = None
        for run_id, pid, ts in _spawns(conn, card):
            if pid != p.sid:
                continue
            if leader is not None and abs(leader.start - ts) <= TIE_SKEW_S:
                tied = (run_id, ts)
            elif leader is None and p.start >= ts - TIE_SKEW_S:
                tied = (run_id, ts)
        if tied is None:
            f.why = "untied (no spawn of this card matches its session) — never signalled"
            out.append(f)
            continue
        f.run_id = tied[0]
        if cur_run is not None and f.run_id == cur_run and status == "running":
            f.why = "the card's current run"
            out.append(f)
            continue
        cur_start = None
        if cur_run is not None:
            r = conn.execute("SELECT started_at FROM task_runs WHERE id = ?", (cur_run,)).fetchone()
            cur_start = float(r[0]) if r and r[0] is not None else None
        if status == "running" and cur_start is not None and p.start >= cur_start - TIE_SKEW_S:
            f.why = "started after the current run began"
            out.append(f)
            continue
        r = conn.execute("SELECT ended_at FROM task_runs WHERE id = ?", (f.run_id,)).fetchone()
        ended = float(r[0]) if r and r[0] is not None else None
        if ended is None:
            f.why = "its run has not ended"
            out.append(f)
            continue
        f.ended_ago = now - ended
        if f.ended_ago < grace_s:
            f.why = f"run ended {f.ended_ago:.0f}s ago (< grace {grace_s:.0f}s)"
            out.append(f)
            continue
        f.leftover = True
        f.why = f"leftover of run {f.run_id} (ended {f.ended_ago:.0f}s ago, card {status})"
        f.descendants = _descendants(p.pid, procs)
        out.append(f)
    return out


def _descendants(pid: int, procs: list[Proc]) -> list[tuple[int, int]]:
    kids: dict[int, list[Proc]] = {}
    for q in procs:
        kids.setdefault(q.ppid, []).append(q)
    out, stack = [], [pid]
    while stack:
        for q in kids.get(stack.pop(), []):
            if q.state != "Z":
                out.append((q.pid, q.starttime_ticks))
                stack.append(q.pid)
    return out


def _pidfd_signal(pid: int, starttime: int, sig: int, proc: Path = Path("/proc")) -> bool:
    """Signal only if (pid, starttime) still names the same process; through a pidfd, so a reused
    pid between the check and the signal cannot be hit."""
    try:
        fd = os.pidfd_open(pid)
    except OSError:
        return False
    try:
        rest = (proc / str(pid) / "stat").read_text()
        st = int(rest[rest.rfind(")") + 2:].split()[19])
        if st != starttime:
            return False
        signal.pidfd_send_signal(fd, sig)
        return True
    except (OSError, ValueError, IndexError):
        return False
    finally:
        os.close(fd)


def may_act(env: dict[str, str] | None = None, cgroup_leaf: str | None = None) -> tuple[bool, str]:
    env = os.environ if env is None else env
    if env.get("HERMES_KANBAN_TASK"):
        return False, "caller carries HERMES_KANBAN_TASK (a worker must not run the reaper)"
    if cgroup_leaf is None:
        try:
            cgroup_leaf = Path("/proc/self/cgroup").read_text().strip().splitlines()[-1].split("/")[-1]
        except OSError:
            cgroup_leaf = ""
    if cgroup_leaf != SWEEP_UNIT:
        return False, f"caller is {cgroup_leaf or '?'}, not {SWEEP_UNIT}"
    return True, ""


def act(findings: list[Finding], state_file: Path, recheck: Callable[[str], str | None],
        signaller: Callable[[int, int, int], bool] = _pidfd_signal) -> list[str]:
    """TERM the leftovers (and their descendants); KILL any (pid, starttime) TERMed on an earlier tick
    that is still a leftover now. ``recheck(card)`` re-reads the card's status just before signalling."""
    try:
        prev = {tuple(x) for x in json.loads(state_file.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        prev = set()
    now_termed: set[tuple[int, int]] = set()
    lines = []
    for f in findings:
        if not f.leftover:
            continue
        st = recheck(f.card)
        if st is None:
            lines.append(f"orphan {f.card} pid={f.pid}: card vanished on re-read — skipped")
            continue
        targets = [(f.pid, f.starttime_ticks)] + f.descendants
        for pid, sttime in targets:
            sig = signal.SIGKILL if (pid, sttime) in prev else signal.SIGTERM
            ok = signaller(pid, sttime, sig)
            now_termed.add((pid, sttime))
            lines.append(f"orphan {f.card} pid={pid} run={f.run_id} -> {signal.Signals(sig).name} "
                         f"{'sent' if ok else 'not sent (gone or reused)'}")
    try:
        state_file.write_text(json.dumps(sorted(now_termed)), encoding="utf-8")
    except OSError:
        pass
    return lines
