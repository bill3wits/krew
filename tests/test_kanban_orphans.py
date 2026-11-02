"""lib/kanban_orphans — a card's leftover worker processes (2026-09-19, adversarially reviewed first).

Each case below is one of the review's must-fix items: tie by the spawn event + start time (never
worker_pid), sessions not ancestry, never Comfy/render or a gateway, (pid, starttime) against reuse,
report-only unless the User says act, and only from the sweep unit."""
from __future__ import annotations

import json
import signal
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import kanban_orphans as ko  # noqa: E402

NOW = 1_000_000.0
GW = "hermes-ada-gateway.service"


def _board(tmp_path, tasks, runs, spawns):
    p = tmp_path / "kanban.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE tasks (id TEXT, status TEXT, current_run_id INTEGER)")
    c.execute("CREATE TABLE task_runs (id INTEGER, task_id TEXT, started_at REAL, ended_at REAL)")
    c.execute("CREATE TABLE task_events (task_id TEXT, run_id INTEGER, kind TEXT, payload TEXT, created_at REAL)")
    c.executemany("INSERT INTO tasks VALUES (?,?,?)", tasks)
    c.executemany("INSERT INTO task_runs VALUES (?,?,?,?)", runs)
    c.executemany("INSERT INTO task_events VALUES (?,?,'spawned',?,?)",
                  [(t, r, json.dumps({"pid": pid}), ts) for t, r, pid, ts in spawns])
    c.commit()
    return c


def P(pid, *, sid=None, ppid=1, start=NOW - 3000, card="t_a", cg=GW, argv=None, state="S", board=""):
    env = {"HERMES_KANBAN_TASK": card} if card else {}
    if board:
        env["HERMES_KANBAN_BOARD"] = board
    return ko.Proc(pid, ppid, pid if sid is None else sid, state, start, int(start), cg,
                   argv or ["python", "hermes", "-p", "bash", "--cli"], env)


def test_the_live_case_a_worker_past_its_ended_run_is_a_leftover(tmp_path):
    """t_b1170887: bash run 1111 ended, the card moved to Vera's run, pid 142085 kept working."""
    c = _board(tmp_path, [("t_a", "running", 2)],
               [(1, "t_a", NOW - 3000, NOW - 900), (2, "t_a", NOW - 800, None)],
               [("t_a", 1, 142085, NOW - 3000), ("t_a", 2, 619147, NOW - 800)])
    procs = [P(142085, start=NOW - 3000), P(619147, start=NOW - 800)]
    got = {f.pid: f for f in ko.find(c, procs, now=NOW)}
    assert got[142085].leftover and got[142085].run_id == 1
    assert not got[619147].leftover                      # the current run's worker: never


def test_grace_counts_from_the_tied_runs_end_only(tmp_path):
    c = _board(tmp_path, [("t_a", "blocked", 1)], [(1, "t_a", NOW - 3000, NOW - 120)],
               [("t_a", 1, 500, NOW - 3000)])
    f = ko.find(c, [P(500, start=NOW - 3000)], now=NOW)[0]
    assert not f.leftover and "grace" in f.why


def test_untied_processes_are_never_leftovers(tmp_path):
    """worker_pid is NULL for most ended runs, so the spawn event is the only tie; no match, no signal."""
    c = _board(tmp_path, [("t_a", "blocked", 1)], [(1, "t_a", NOW - 3000, NOW - 2000)],
               [("t_a", 1, 999, NOW - 3000)])
    f = ko.find(c, [P(500, start=NOW - 3000)], now=NOW)[0]
    assert not f.leftover and "untied" in f.why


def test_a_reused_spawn_pid_with_the_wrong_start_time_does_not_tie(tmp_path):
    c = _board(tmp_path, [("t_a", "blocked", 1)], [(1, "t_a", NOW - 9000, NOW - 8000)],
               [("t_a", 1, 500, NOW - 9000)])
    f = ko.find(c, [P(500, start=NOW - 300)], now=NOW)[0]   # same pid, a different process
    assert not f.leftover and "untied" in f.why


def test_a_reparented_child_ties_through_its_session(tmp_path):
    """Workers are session leaders; an orphaned pytest is reparented to systemd --user, not the worker."""
    c = _board(tmp_path, [("t_a", "blocked", 1)], [(1, "t_a", NOW - 6000, NOW - 1000)],
               [("t_a", 1, 700, NOW - 6000)])
    child = P(701, sid=700, ppid=3330, start=NOW - 5000, argv=["python", "-m", "pytest", "tests/hermes_cli"])
    f = ko.find(c, [child], now=NOW)[0]                    # the leader (700) is gone
    assert f.leftover and f.run_id == 1


def test_never_the_gateway_comfy_render_a_scope_or_a_zombie(tmp_path):
    c = _board(tmp_path, [("t_a", "blocked", 1)], [(1, "t_a", NOW - 3000, NOW - 2000)],
               [("t_a", 1, 1, NOW - 3000)])
    procs = [
        P(10, sid=1, argv=["python", "hermes", "-p", "ada", "gateway", "run", "--replace"]),
        P(11, sid=1, argv=["~/.krew/envs/comfyui/bin/python", "/data/comfy/main.py", "--port", "8188"]),
        P(12, sid=1, argv=["python3", "scripts/render-submit.py", "--card", "t_a"]),
        P(13, sid=1, cg="krew-comfy-1789.scope"),
        P(14, sid=1, cg="session-3.scope"),
        P(15, sid=1, state="Z"),
        P(16, sid=1, card=""),
    ]
    assert ko.find(c, procs, now=NOW, gateway_mainpids=[10]) == []


def test_a_running_cards_live_worker_session_is_never_touched(tmp_path):
    """render-submit sets HERMES_KANBAN_TASK to a card that may not be the caller's own."""
    c = _board(tmp_path, [("t_a", "blocked", 1), ("t_b", "running", 5)],
               [(1, "t_a", NOW - 9000, NOW - 8000), (5, "t_b", NOW - 100, None)],
               [("t_a", 1, 800, NOW - 9000), ("t_b", 5, 900, NOW - 100)])
    borrowed = P(901, sid=900, start=NOW - 50, card="t_a")  # inside t_b's live worker, carrying t_a
    f = ko.find(c, [borrowed], now=NOW)[0]
    assert not f.leftover and "live worker" in f.why


def test_another_boards_process_is_skipped(tmp_path):
    c = _board(tmp_path, [("t_a", "blocked", 1)], [(1, "t_a", NOW - 3000, NOW - 2000)],
               [("t_a", 1, 500, NOW - 3000)])
    assert ko.find(c, [P(500, start=NOW - 3000, board="other")], board="daily-x", now=NOW) == []


def test_report_is_the_default_and_acting_needs_the_sweep_and_no_card(monkeypatch):
    monkeypatch.delenv("KREW_ORPHAN_REAP", raising=False)
    assert ko.mode() == "report"
    monkeypatch.setenv("KREW_ORPHAN_REAP", "0")
    assert ko.mode() == "off"
    assert ko.may_act({"HERMES_KANBAN_TASK": "t_x"}, "krew-hold-sweep.service")[0] is False
    assert ko.may_act({}, "hermes-ada-gateway.service")[0] is False
    assert ko.may_act({}, "krew-hold-sweep.service") == (True, "")


def test_act_terms_then_kills_the_same_pid_and_starttime_only(tmp_path):
    f = ko.Finding(500, 42, "t_a", 1, "leftover", leftover=True, descendants=[(501, 43)])
    sent = []
    sig = lambda pid, st, s: sent.append((pid, st, s)) or True
    state = tmp_path / "termed.json"
    ko.act([f], state, lambda card: "blocked", sig)
    assert sent == [(500, 42, signal.SIGTERM), (501, 43, signal.SIGTERM)]
    sent.clear()
    ko.act([f], state, lambda card: "blocked", sig)          # still there next tick
    assert sent == [(500, 42, signal.SIGKILL), (501, 43, signal.SIGKILL)]
    sent.clear()
    reused = ko.Finding(500, 99, "t_a", 1, "leftover", leftover=True)   # same pid, new process
    ko.act([reused], state, lambda card: "blocked", sig)
    assert sent == [(500, 99, signal.SIGTERM)]


def test_act_skips_a_card_that_vanished_on_re_read(tmp_path):
    f = ko.Finding(500, 42, "t_a", 1, "leftover", leftover=True)
    sent = []
    lines = ko.act([f], tmp_path / "s.json", lambda card: None, lambda *a: sent.append(a) or True)
    assert sent == [] and "vanished" in lines[0]


def test_a_card_hermes_still_claims_is_never_raced(tmp_path):
    """Late review 2026-09-19: while claim_lock is live, hermes's own reclaim owns the worker."""
    c = _board(tmp_path, [("t_a", "blocked", 1)], [(1, "t_a", NOW - 3000, NOW - 2000)],
               [("t_a", 1, 500, NOW - 3000)])
    c.execute("ALTER TABLE tasks ADD COLUMN claim_lock TEXT")
    c.execute("ALTER TABLE tasks ADD COLUMN claim_expires REAL")
    c.execute("UPDATE tasks SET claim_lock = 'ops-main:1', claim_expires = ?", (NOW + 60,))
    f = ko.find(c, [P(500, start=NOW - 3000)], now=NOW)[0]
    assert not f.leftover and "claim" in f.why
    c.execute("UPDATE tasks SET claim_expires = ?", (NOW - 60,))          # an expired claim does not protect
    assert ko.find(c, [P(500, start=NOW - 3000)], now=NOW)[0].leftover


def test_a_worker_may_live_in_hermes_own_scope_and_the_host_gateway():
    """2026-09-27: the updated hermes names the scope itself (hermes-worker-kanban-<card>-run-<run>)
    and the one host gateway is hermes-gateway.service. Neither may read as "not a worker"."""
    assert ko._worker_cgroup("hermes-worker-kanban-t_75b7ff05-run-3466.scope")
    assert ko._worker_cgroup("kanban-worker-t_75b7ff05-3918340.scope")
    assert ko._worker_cgroup("hermes-ada-gateway.service")
    assert ko._worker_cgroup("hermes-gateway.service")
    # hermes names its other background workers hermes-worker-* too: those are not the board's
    assert not ko._worker_cgroup("hermes-worker-terminal-abc.scope")
    assert not ko._worker_cgroup("run-r1234.scope")
    assert not ko._worker_cgroup("hermes-dashboard.service")
