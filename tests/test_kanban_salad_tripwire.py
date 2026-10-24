"""kanban-salad-tripwire: the detector and the trip's dry path (2026-09-16).

Live shapes it must catch: Steward 09-15 06–09h ("大概 你 几乎 是 差不多 …",
zero tool calls) and Steward 09-16 03:54 ("33A0647327A …" after 61 tool
calls). Shapes it must leave alone: a healthy run that ends in prose, and a
no-verb exit that is plain English (that one is the verb wall's business).
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tw(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_HERMES", str(tmp_path / ".hermes"))
    spec = importlib.util.spec_from_file_location(
        "salad_tripwire", ROOT / "crew" / "scripts" / "kanban-salad-tripwire.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _session(body: str, tools: int) -> str:
    return (
        f"Query: work kanban task t_x\nInitializing agent...\n{body}\n"
        f"Session: 20260916_040000_abcdef\nMessages: 6 (3 user, {tools} tool calls)\n"
    )


def test_cjk_salad_is_salad(tw):
    body = "…大致你.几乎権那 roughly是几乎的基本我は差不多你是本的几乎你大概是是大概近乎是 " * 20
    assert tw.is_salad_session(_session(body, 0)) is True


def test_digit_pattern_salad_is_salad_even_after_tool_calls(tw):
    body = "\n".join(["48/33A0647 38:414A06|4733A0 5233A0647:5733A0", "88:33A073A", "45:334A00547A0057"] * 10)
    assert tw.is_salad_session(_session(body, 61)) is True


def test_english_prose_exit_is_not_salad(tw):
    body = ("Key finding: I am run 810 — the implementer run that already landed "
            "kanban_request_review. The nudge's premise is stale for my run. ") * 20
    assert tw.is_salad_session(_session(body, 0)) is False


def test_healthy_completion_is_not_salad(tw):
    body = ("Wrote dep_map.json (10 venvs, 1844 packages) and called kanban_complete "
            "with the artifact list. ") * 20
    assert tw.is_salad_session(_session(body, 12)) is False


def test_salad_sessions_only_inside_window_and_last_segment(tw, tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    salad = "大概 你 几乎 是 差不多 你 大致 " * 60
    (logs / "t_new.log").write_text(_session(salad, 0), encoding="utf-8")
    # An old log with the same salad is outside the window.
    old = logs / "t_old.log"
    old.write_text(_session(salad, 0), encoding="utf-8")
    past = time.time() - 3 * 3600
    os.utime(old, (past, past))
    # A log whose LAST session recovered is clean even though an earlier one was salad.
    (logs / "t_recovered.log").write_text(
        _session(salad, 0) + _session("Completed the spec and called kanban_complete. " * 20, 4),
        encoding="utf-8",
    )
    assert tw.salad_sessions(logs, 15 * 60) == ["t_new"]


def test_off_switch_and_trip_threshold(tw, tmp_path, monkeypatch, capsys):
    home = tmp_path / ".hermes"
    (home / "kanban" / "boards" / "b1" / "logs").mkdir(parents=True)
    (home / "kanban").joinpath("current").write_text("b1\n", encoding="utf-8")
    logs = home / "kanban" / "boards" / "b1" / "logs"
    salad = "大概 你 几乎 是 差不多 你 大致 " * 60
    (logs / "t_a.log").write_text(_session(salad, 0), encoding="utf-8")
    # One salad session is below TRIP_AT (2): nothing happens, no lock touched.
    assert tw.main(["--dry-run"]) == 0
    assert not (home / "kanban" / "boards" / "b1" / "kanban.db.dispatch.lock").exists()
    (logs / "t_b.log").write_text(_session(salad, 0), encoding="utf-8")
    # Two: a dry run reports the trip without holding or resetting.
    assert tw.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would wait" in out and "t_a" in out and "t_b" in out
    # The off switch silences everything.
    (home / "kanban-salad-tripwire.off").write_text("", encoding="utf-8")
    assert tw.main(["--dry-run"]) == 0
    assert "would wait" not in capsys.readouterr().out


# --- 2026-09-17: gibberish is not only CJK ---------------------------------------

_TAIL = "\nSession:        20260917_105100_abc\nDuration:       7s\nMessages:       6 (3 user, 0 tool calls)\n"
_DIGITS = "2026-05-11 11:05-1311:05-17:05-17:0551.105-17:05.57105-171520205-17155-17155 " * 6
_PROSE = ("I need to think through this carefully. The user asked me to work a kanban task and "
          "I should first understand what the task refers to before deciding how to act on it. ") * 3


def _tw():
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("salad_tw", root / "crew" / "scripts" / "kanban-salad-tripwire.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # ISOLATION (<repo-worktree>, 2026-09-17: test ids appeared in the LIVE tripwire log). The
    # module logs to ~/.hermes/logs and its CLI is the live board: a test gets a temp log, and
    # a test that forgets `cli=` fails loudly instead of running `hermes -p ada kanban …`.
    import tempfile
    m.LOG = Path(tempfile.mkdtemp(prefix="salad-tw-test-")) / "tripwire.log"
    def _no_live_cli(*a, **k):
        raise AssertionError(f"test reached the LIVE kanban CLI: {a}")
    m._kanban = _no_live_cli
    return m


def test_tests_cannot_reach_the_live_board_or_log():
    import pytest
    m = _tw()
    assert "/.hermes/" not in str(m.LOG)
    with pytest.raises(AssertionError):
        m._kanban("unblock", "t_x")


def test_digit_and_punctuation_salad_is_garbage_prose_is_not():
    """09-17 10:47-10:59: five zero-tool exits in 12 min decoded digits; the CJK test saw one."""
    m = _tw()
    assert not m.is_salad_session(_DIGITS + _TAIL)                     # the old test is blind to it
    assert m.zero_tool_shape(_DIGITS + _TAIL) == "garbage"
    assert m.zero_tool_shape(_PROSE + _TAIL) == ""                     # a reset does not cure prose exits
    assert m.zero_tool_shape("⚠️ nudging\n" + _TAIL) == "mute"
    worked = _DIGITS + _TAIL.replace("0 tool calls", "12 tool calls")
    assert m.zero_tool_shape(worked) == ""                              # only zero-tool exits
    down = _DIGITS + "\n📝 Error: Connection error.\n" + _TAIL
    assert m.zero_tool_shape(down) == ""                                # engine down is not the cache


def test_mutes_help_the_count_but_never_trip_alone(tmp_path, monkeypatch):
    m = _tw()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "t_aaaaaaaa.log").write_text("Query: work kanban task t_aaaaaaaa\n" + _TAIL)
    (logs / "t_bbbbbbbb.log").write_text("Query: work kanban task t_bbbbbbbb\n" + _TAIL)
    mutes: list[str] = []
    assert m.salad_sessions(logs, 900, mutes) == [] and sorted(mutes) == ["t_aaaaaaaa", "t_bbbbbbbb"]
    (logs / "t_cccccccc.log").write_text("Query: work kanban task t_cccccccc\n" + _DIGITS + _TAIL)
    mutes = []
    assert m.salad_sessions(logs, 900, mutes) == ["t_cccccccc"] and len(mutes) == 2
    assert not m.should_trip([], ["t_aaaaaaaa", "t_bbbbbbbb"], [])      # by behaviour, not source text


# --- 2026-09-17 pm: one clear hit trips; burned cards are released after the reset ---------

def test_one_clearly_garbage_session_trips_a_murky_one_needs_company():
    m = _tw()
    assert m.is_clear_garbage(_DIGITS + _TAIL)
    murky = ("alpha %% beta ## 11 gamma 22 33 " * 12) + _TAIL             # 0.375 word-like
    r = m.word_ratio(murky)
    assert m.CLEAR_RATIO <= r < m.WORD_RATIO and not m.is_clear_garbage(murky)
    assert m.should_trip(["t_a"], [], ["t_a"])                            # one clear hit
    assert not m.should_trip(["t_a"], [], [])                             # one murky hit waits
    assert m.should_trip(["t_a"], ["t_b"], [])                            # murky + a mute
    assert not m.should_trip([], ["t_a", "t_b", "t_c"], [])               # mutes never trip alone


def test_only_logs_written_after_the_last_reset_count(tmp_path):
    """One hit now trips, so a stale garbage log inside the 15-min window must not re-trip
    after every cooldown."""
    import os, time
    m = _tw()
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "t_aaaaaaaa.log"
    old.write_text("Query: work kanban task t_aaaaaaaa\n" + _DIGITS + _TAIL)
    reset_at = time.time() - 60
    os.utime(old, (reset_at - 120, reset_at - 120))
    new = logs / "t_bbbbbbbb.log"
    new.write_text("Query: work kanban task t_bbbbbbbb\n" + _DIGITS + _TAIL)
    clear: list[str] = []
    assert m.salad_sessions(logs, 900, [], clear, since=reset_at) == ["t_bbbbbbbb"] and clear == ["t_bbbbbbbb"]
    assert sorted(m.salad_sessions(logs, 900)) == ["t_aaaaaaaa", "t_bbbbbbbb"]


def _board(tmp_path, tasks, events=(), comments=()):
    import sqlite3
    d = tmp_path / "kanban" / "boards" / "b"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?,?)", tasks)
    con.executemany("INSERT INTO task_events (task_id, kind) VALUES (?,?)", events)
    con.executemany("INSERT INTO task_comments (task_id, body) VALUES (?,?)", comments)
    con.commit()
    con.close()
    return d / "kanban.db"


def test_release_is_for_breaker_parks_once_and_never_a_replaced_card(tmp_path, monkeypatch):
    m = _tw()
    db = _board(tmp_path,
                [("t_park", "blocked"), ("t_done", "done"), ("t_worker", "blocked"),
                 ("t_cut", "blocked"), ("t_twice", "blocked"), ("t_freed", "blocked")],
                events=[("t_park", "gave_up"), ("t_cut", "gave_up"), ("t_twice", "gave_up"),
                        ("t_worker", "blocked"), ("t_freed", "gave_up"), ("t_freed", "unblocked")],
                comments=[("t_cut", "reframe-cut: t_aaaaaaaa (gen 1, frame by model)"),
                          ("t_twice", "salad-release: 1"), ("t_twice", "salad-release: 2"),
                          ("t_twice", "salad-release: 3")])
    go, left = m.releasable(db, ["t_park", "t_done", "t_worker", "t_cut", "t_twice", "t_freed", "t_park"])
    assert go == ["t_park"]
    assert "reframe" in left["t_cut"] and "3x" in left["t_twice"] and "breaker" in left["t_worker"]
    monkeypatch.setattr(m, "HERMES", tmp_path)
    calls = []
    n = m.release_burned("b", ["t_park", "t_cut"], cli=lambda *a: (calls.append(a) or (0, "ok")))
    assert n == 1 and [c[0] for c in calls] == ["comment", "unblock"]
    assert calls[0][1] == "t_park" and calls[0][2].startswith(m.RELEASE_MARK)


def test_release_follows_only_a_verified_reset_and_has_a_kill_switch(monkeypatch):
    m = _tw()
    src = "\n".join(l for l in open(m.__file__).read().splitlines() if not l.lstrip().startswith("#"))
    tail = src[src.index("rc = trip(slug, hits, dry=dry)"):]
    assert tail.index("if rc == 0:") < tail.index("release_burned(slug, hits + mutes")
    monkeypatch.setattr(m, "RELEASE_ON", False)
    assert m.release_burned("b", ["t_x"]) == 0


def test_a_release_spent_into_a_relapse_is_not_the_cards_last(tmp_path):
    """13:45 release -> respawn 13:52 -> garbage again -> "already released once": the card
    went to the reframe loop with a protocol-violation frame. Up to MAX_RELEASES now."""
    m = _tw()
    db = _board(tmp_path, [("t_again", "blocked")], events=[("t_again", "gave_up")],
                comments=[("t_again", "salad-release: first")])
    assert m.releasable(db, ["t_again"])[0] == ["t_again"] and m.MAX_RELEASES == 3


def test_cooldown_allows_a_tick_of_slack():
    m = _tw()
    src = "\n".join(l for l in open(m.__file__).read().splitlines() if not l.lstrip().startswith("#"))
    assert "COOLDOWN_MIN * 60 - TICK_SLACK_S" in src and 0 < m.TICK_SLACK_S < 300
    assert 599 >= m.COOLDOWN_MIN * 60 - m.TICK_SLACK_S          # 13:55:25 "599s ago" would have fired


def test_salad_park_is_written_on_the_card_once_per_burn(tmp_path, monkeypatch):
    m = _tw()
    _board(tmp_path, [("t_new", "blocked"), ("t_marked", "blocked"), ("t_reburn", "blocked")],
           comments=[("t_marked", "salad-park: x"),
                     ("t_reburn", "salad-park: x"), ("t_reburn", "salad-release: x")])
    monkeypatch.setattr(m, "HERMES", tmp_path)
    calls = []
    n = m.mark_salad_parks("b", ["t_new", "t_marked", "t_reburn"], cli=lambda *a: (calls.append(a) or (0, "")))
    assert n == 2 and sorted(c[1] for c in calls) == ["t_new", "t_reburn"]
    assert all(c[2].startswith(m.PARK_MARK) and "NOT a protocol failure" in c[2] for c in calls)


# --- 2026-09-18: a card the reset left parked is released on the next tick -------------------

def test_a_card_parked_before_the_last_reset_is_a_straggler(tmp_path, monkeypatch):
    """23:34 t_6d71e5b6 was running when the cache reset; 'release: leave — status running' and
    then nothing: the next tick only looks at logs newer than the reset."""
    import sqlite3
    m = _tw()
    d = tmp_path / "kanban" / "boards" / "b"
    d.mkdir(parents=True)
    con = sqlite3.connect(d / "kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT, block_kind TEXT)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT, created_at INTEGER)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?)",
                    [("t_left", "blocked", None), ("t_after", "blocked", None), ("t_freed", "todo", None),
                     ("t_zoey", "blocked", "needs_input")])          # an old park under Zoey's block: hers
    con.executemany("INSERT INTO task_events (task_id, kind) VALUES (?,?)",
                    [("t_left", "gave_up"), ("t_after", "gave_up"), ("t_freed", "gave_up"), ("t_zoey", "gave_up")])
    con.executemany("INSERT INTO task_comments (task_id, body, created_at) VALUES (?,?,?)",
                    [("t_left", m.PARK_MARK + " salad", 100), ("t_after", m.PARK_MARK + " salad", 300),
                     ("t_freed", m.PARK_MARK + " salad", 100), ("t_zoey", m.PARK_MARK + " salad", 100)])
    con.commit(); con.close()
    assert m.stragglers(d / "kanban.db", last_reset=200) == ["t_left"]      # t_after burned after the reset
    assert m.stragglers(d / "kanban.db", last_reset=0) == []                 # never reset: nothing to release
    monkeypatch.setattr(m, "HERMES", tmp_path)
    calls = []
    n = m.release_burned("b", ["t_left"], cli=lambda *a: (calls.append(a) or (0, "ok")),
                         why="The engine was reset after this park.")
    assert n == 1 and [c[0] for c in calls] == ["comment", "unblock"] and "after this park" in calls[0][2]


def test_stragglers_are_released_every_tick_before_the_trip_decision():
    m = _tw()
    src = open(m.__file__).read()
    body = src[src.index("def main(argv"):]
    assert body.index("stragglers(") < body.index("if not should_trip(")


# --- 2026-09-18 (the User "a task should not be able to crash the dispatcher"): never wait on the lock ---

def test_trip_never_holds_the_dispatch_lock_while_waiting(tmp_path, monkeypatch):
    """08:53: the tripwire took Ada's dispatch lock, then waited 480 s for crew to idle; at six
    seats crew never idles, so the dispatcher missed every tick. Now: wait unlocked, lock for
    the reset instant, re-check idle under the lock."""
    m = _tw()
    (tmp_path / "kanban" / "boards" / "b").mkdir(parents=True)
    monkeypatch.setattr(m, "HERMES", tmp_path)
    monkeypatch.setattr(m, "IDLE_WAIT_S", 30)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    loads = iter([(3.0, 1.0), (2.0, 0.0), (1.0, 0.0), (0.0, 0.0), (0.0, 0.0)])
    trace = []
    monkeypatch.setattr(m, "crew_load", lambda: (trace.append("load") or next(loads)))
    monkeypatch.setattr(m.fcntl, "flock", lambda fd, op: trace.append("lock" if op & m.fcntl.LOCK_EX else "unlock"))
    monkeypatch.setattr(m, "reset_prefix_cache", lambda: (trace.append("reset") or True))
    monkeypatch.setattr(m, "_read_state", lambda: {})
    monkeypatch.setattr(m, "_write_state", lambda d: None)
    assert m.trip("b", ["t_x"], dry=False) == 0
    first_lock = trace.index("lock")
    assert trace[:first_lock] == ["load", "load", "load", "load"]      # three busy polls, no lock taken
    assert trace[first_lock:] == ["lock", "load", "reset", "unlock"]    # lock, re-check idle, reset, release


def test_trip_gives_up_without_a_reset_when_crew_never_idles(tmp_path, monkeypatch):
    m = _tw()
    (tmp_path / "kanban" / "boards" / "b").mkdir(parents=True)
    monkeypatch.setattr(m, "HERMES", tmp_path)
    monkeypatch.setattr(m, "IDLE_WAIT_S", 1)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    monkeypatch.setattr(m, "crew_load", lambda: (5.0, 0.0))
    locks = []
    monkeypatch.setattr(m.fcntl, "flock", lambda fd, op: locks.append(op))
    monkeypatch.setattr(m, "reset_prefix_cache", lambda: (_ for _ in ()).throw(AssertionError("reset while busy")))
    assert m.trip("b", ["t_x"], dry=False) == 1 and locks == []
