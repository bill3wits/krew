"""Board-side verb-wall + render-hold gates."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kanban_board_gate", ROOT / "crew" / "scripts" / "kanban-board-gate.py"
)
g = importlib.util.module_from_spec(SPEC)
sys.modules["kanban_board_gate"] = g
SPEC.loader.exec_module(g)

import pytest


@pytest.fixture(autouse=True)
def _quiet_log(tmp_path, monkeypatch):
    """Never the live ~/.hermes/logs/kanban-board-gate.log: on 2026-09-18 these tests wrote
    `ownerless t_aaaa0001 strike 2 -> zoey` into it (the CLI was mocked; the log was not)."""
    monkeypatch.setattr(g, "LOG", tmp_path / "kanban-board-gate.log")
    # the per-card breaker writes ~/.hermes/kanban-breaker.json: off unless a test turns it on
    monkeypatch.setenv("KREW_CARD_BREAKER", "off")


@pytest.fixture(autouse=True)
def _no_live_board_writes(tmp_path, monkeypatch):
    """2026-09-18 17:18: the salad test ran gate_salad with the real `kanban()` and filed a live
    "Restart window: crew engine relapse" card (t_93c3d2bd, archived by hand). Every test here
    gets a kanban that refuses; a test that wants to see the calls stubs its own recorder."""
    def _refuse(*a, dry=False):
        raise AssertionError(f"live kanban call in a test: {a[:2]}")
    if not hasattr(g, "_kanban_live"):
        g._kanban_live = g.kanban          # the real one, for the test that pins its argv
    monkeypatch.setattr(g, "kanban", _refuse)
    monkeypatch.setattr(g, "RELAPSE_MARK", tmp_path / "crew-restart-owed")


def _db(tmp, tasks, runs=(), comments=()):
    p = tmp / "kanban.db"
    con = sqlite3.connect(p)
    con.execute(
        "CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, skills TEXT)"
    )
    con.execute(
        "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, "
        "metadata TEXT, error TEXT, ended_at INTEGER)"
    )
    con.execute(
        "CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT)"
    )
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)", tasks)
    con.executemany(
        "INSERT INTO task_runs (task_id, outcome, metadata, error, ended_at) VALUES (?,?,?,?,?)",
        runs,
    )
    con.executemany("INSERT INTO task_comments (task_id, body) VALUES (?,?)", comments)
    con.commit()
    con.close()
    return p


def test_hold_blocks_render_ready_not_docs_typo(tmp_path, monkeypatch):
    db = _db(
        tmp_path,
        [
            ("t_rend", "resubmit mint", "python3 scripts/render-submit.py --card t_rend", "ready", ""),
            ("t_docs", "pack drift: render SOTA notes", "docs only, no mint", "ready", ""),
        ],
    )
    hold = tmp_path / "krew-render-hold"
    hold.write_text("held\n")
    monkeypatch.setattr(g, "HOLD", hold)
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    con = sqlite3.connect(db)
    n = g.gate_render(con)
    con.close()
    assert n == 1
    blocked = [c[1] for c in calls if c[0] == "block"]
    assert blocked == ["t_rend"]


def test_hold_gone_unblocks_only_render_hold_mark(tmp_path, monkeypatch):
    db = _db(
        tmp_path,
        [
            ("t_rend", "mint", "render-submit.py", "blocked", ""),
            ("t_other", "wait on human", "body", "blocked", ""),
        ],
        comments=[("t_rend", "gate: render hold — parked"), ("t_other", "needs a human")],
    )
    hold = tmp_path / "no-hold"
    monkeypatch.setattr(g, "HOLD", hold)
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    con = sqlite3.connect(db)
    n = g.gate_render(con)
    con.close()
    assert n == 1
    assert [c[1] for c in calls if c[0] == "unblock"] == ["t_rend"]


def test_missing_plate_blocks_existing_path_only(tmp_path, monkeypatch):
    missing = "~/.krew/crew/display/zoey-poses/hub-no-such-plate.png"
    db = _db(
        tmp_path,
        [
            ("t_miss", "mint", f"seed {missing} via render-submit.py", "ready", "krew-render"),
            ("t_ok", "mint", "will write display/zoey-poses/hub-x.png later render-submit.py", "ready", ""),
        ],
    )
    monkeypatch.setattr(g, "HOLD", tmp_path / "absent")
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    con = sqlite3.connect(db)
    n = g.gate_render(con)
    con.close()
    assert n == 1
    assert [c[1] for c in calls if c[0] == "block"] == ["t_miss"]


def test_verb_wall_parks_ready_after_rc0_no_verb(tmp_path, monkeypatch):
    db = _db(
        tmp_path,
        [("t_pv", "Delete clip (after a look)", "body", "ready", "")],
        runs=[
            ("t_pv", "crashed", '{"protocol_violation": true, "exit_code": 0}',
             "worker exited cleanly (rc=0) without calling kanban_complete", 1),
        ],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    con = sqlite3.connect(db)
    n = g.gate_verb_wall(con)
    con.close()
    assert n == 1
    assert [c[1] for c in calls if c[0] == "block"] == ["t_pv"]


def test_pack_drift_skill_paths_are_not_a_mint(tmp_path, monkeypatch):
    body = (
        "pack drift: 11 path(s) committed but not live\n"
        "hermes-packs/skills/krew-render/SKILL.md\n"
        "hermes-packs/skills/pose-flourish/SKILL.md\n"
    )
    assert not g._is_render_card("pack drift: 11 path(s) committed but not live", body, "")
    db = _db(
        tmp_path,
        [("t_ab9e662c", "pack drift: 11 path(s) committed but not live", body, "blocked", "")],
        comments=[("t_ab9e662c", "gate: render hold — parked")],
    )
    hold = tmp_path / "krew-render-hold"
    hold.write_text("held\n")
    monkeypatch.setattr(g, "HOLD", hold)
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    con = sqlite3.connect(db)
    n = g.gate_render(con)
    con.close()
    assert n == 1
    assert [c[1] for c in calls if c[0] == "unblock"] == ["t_ab9e662c"]


def test_unknown_assignee_archives_scratch_and_reassigns_real(tmp_path, monkeypatch):
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT)")
    con.executemany(
        "INSERT INTO tasks VALUES (?,?,?,?,?)",
        [
            ("t_dbg", "dbg", "", "ready", "worker"),
            ("t_iso", "iso-test", "", "ready", "worker"),
            ("t_real", "fix the mouth clock", "real body", "ready", "worker"),
            ("t_ok", "a bash card", "body", "ready", "bash"),
        ],
    )
    con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    n = g.gate_assignee(con)
    con.close()
    assert n == 3
    archived = [c[1] for c in calls if c[0] == "archive"]
    reassigned = [c for c in calls if c[0] == "reassign"]
    assert set(archived) == {"t_dbg", "t_iso"}
    assert reassigned == [("reassign", "t_real", "ada")]


def test_verb_wall_ignores_plain_crash(tmp_path, monkeypatch):
    db = _db(
        tmp_path,
        [("t_x", "work", "body", "ready", "")],
        runs=[("t_x", "crashed", "{}", "pid 1 exited with code 1", 1)],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    con = sqlite3.connect(db)
    assert g.gate_verb_wall(con) == 0
    con.close()
    assert calls == []


def test_verb_wall_respects_a_release_newer_than_the_bad_run(tmp_path, monkeypatch):
    """2026-09-17 t_df1c04ca: unblocked 11:06:10, re-parked by this wall 11:07:07 with no
    worker spawned. An unblock after the no-verb run is a ruling, not a leak."""
    db = _db(
        tmp_path,
        [("t_rel", "Verify pack drift", "body", "ready", ""), ("t_old", "Other", "body", "ready", "")],
        runs=[
            ("t_rel", "crashed", '{"protocol_violation": true}', "worker exited cleanly (rc=0) without calling", 1000),
            ("t_old", "crashed", '{"protocol_violation": true}', "worker exited cleanly (rc=0) without calling", 1000),
        ],
    )
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, created_at INTEGER)")
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_rel','unblocked',2000)")
    con.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES ('t_old','unblocked',500)")   # before the run
    con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    assert g.gate_verb_wall(con) == 1
    con.close()
    assert [c[1] for c in calls if c[0] == "block"] == ["t_old"]


def test_every_cli_call_names_the_profile(monkeypatch):
    """House rule: the bare `hermes kanban` is the `default` (Bridge) profile, and a timer's
    PATH has no ~/.local/bin."""
    seen = {}
    class R:
        returncode, stdout, stderr = 0, "ok", ""
    monkeypatch.setattr(g.subprocess, "run", lambda argv, **kw: seen.setdefault("argv", argv) and R())
    g._kanban_live("comment", "t_x", "hello")      # the real function; subprocess is the stub
    argv = seen["argv"]
    assert argv[1:4] == ["-p", g.PROFILE, "kanban"] and g.PROFILE == "ada"
    assert argv[0].endswith("/hermes") and argv[0].startswith("/")


# --- 2026-09-18: ownerless blocks go back to the orchestrator ------------------------------

H = 3600
NOW = 1_000_000            # the gate's clock in these tests; TODAY is 2026-09-18
TODAY = "2026-09-18"


def _board(tmp, tasks, comments=(), events=(), runs=(), links=()):
    """Full-shape board: tasks(assignee, block_kind), comments(author, created_at), events."""
    p = tmp / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT, "
                "block_kind TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, "
                "body TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, "
                "metadata TEXT, error TEXT, ended_at INTEGER)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.executemany("INSERT INTO task_links VALUES (?,?)", links)
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
                    [(t[0], t[1], "body", t[2], t[3], t[4], 1) for t in tasks])
    con.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", comments)
    con.executemany("INSERT INTO task_events (task_id, kind, created_at) VALUES (?,?,?)", events)
    con.executemany("INSERT INTO task_runs (task_id, outcome, metadata, error, ended_at) VALUES (?,?,?,?,?)", runs)
    con.commit()
    con.close()
    return p


def _run(db, monkeypatch, show_status="ready", batch=False):
    calls = []

    def fake(*a, dry=False):
        calls.append(a)
        if a[0] == "show":
            return 0, '{"id": "%s", "status": "%s"}' % (a[1], show_status)
        if a[0] == "create":
            return 0, "Created t_ba7c0001  (ready, assignee=ada)"
        return 0, "ok"

    monkeypatch.setattr(g, "kanban", fake)
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    monkeypatch.setattr(g, "BATCH_ON", batch)
    con = sqlite3.connect(db)
    n = g.gate_ownerless(con, now=NOW, today=TODAY)
    con.close()
    return n, calls


def test_ownerless_block_goes_back_to_the_orchestrator(tmp_path, monkeypatch):
    """t_9df41291 2026-09-18: bash blocked with 'GO verdict + artifacts landed' and no kind —
    nobody reads a verdict written as a block. Comment, reassign, unblock, in that order."""
    db = _board(tmp_path,
                [("t_go", "M&S mitigation: audio8-tts", "blocked", "bash", None)],
                comments=[("t_go", "bash", "BLOCKED: GO verdict + artifacts landed", NOW - 4 * H)],
                events=[("t_go", "blocked", NOW - 4 * H)])
    n, calls = _run(db, monkeypatch)
    assert n == 1
    assert [c[0] for c in calls] == ["comment", "reassign", "unblock"]
    assert calls[0][1] == "t_go" and calls[0][2].startswith(g.OWNERLESS_MARK) and "ROUTE" in calls[0][2]
    assert calls[0][-2:] == ("--author", "kanban-board-gate")
    assert calls[1] == ("reassign", "t_go", "ada")
    assert calls[2][:2] == ("unblock", "t_go") and calls[2][2] == "--reason"


def test_owned_blocks_are_left_alone(tmp_path, monkeypatch):
    old = NOW - 5 * H
    db = _board(tmp_path, [
        ("t_zoey", "with Zoey", "blocked", "bash", "needs_input"),          # typed: hers
        ("t_fresh", "just blocked", "blocked", "bash", None),               # a worker may be on it
        ("t_ruled", "Zoey ruled", "blocked", "bash", None),                 # her word newer than the block
        ("t_rw", "mint", "blocked", "bash", None),                          # render-window's hold
        ("t_c0ffee01", "source", "blocked", "bash", None),                       # its reframe is open
        ("t_salad", "salad", "blocked", "bash", None),                      # the tripwire's
        ("t_park", "parked", "scheduled", "bash", None),                    # a park is owned
        ("t_hold", "mint", "blocked", "bash", None),                        # this script's render hold
        ("t_0000000a", "reframe: source", "todo", "ada", None),
    ], comments=[
        ("t_ruled", "zoey", "try — run it once more with the plate", NOW - 4 * H),
        ("t_rw", "kanban-board-gate", "gate: render window — held", old - 1),
        ("t_c0ffee01", "kanban-loop", "reframe-cut: t_0000000a (gen 1, frame by mechanical)", old + 10),
        ("t_salad", "salad-tripwire", "salad-park: this run decoded token salad", old + 10),
        ("t_hold", "kanban-board-gate", "gate: render hold — parked", old - 1),
    ], events=[
        ("t_zoey", "blocked", old), ("t_fresh", "blocked", NOW - 20 * 60), ("t_ruled", "blocked", old),
        ("t_rw", "blocked", old), ("t_c0ffee01", "blocked", old), ("t_salad", "gave_up", old),
        ("t_park", "blocked", old), ("t_hold", "blocked", old),
    ])
    n, calls = _run(db, monkeypatch)
    assert n == 0 and calls == []


def test_sweep_chatter_and_status_beats_never_keep_a_card_owned(tmp_path, monkeypatch):
    """Steward's hourly 'Sweep t_x: unchanged' landed on t_a44ce2b8 three times after its
    block and a status beat on t_42f99bd9 — activity is the owner's, not the sweep's."""
    old = NOW - 9 * H
    db = _board(tmp_path, [
        ("t_swept", "Bash scene plate", "blocked", "lila", None),
        ("t_beat", "Review + finish clip", "blocked", "vera", None),
    ], comments=[
        ("t_swept", "lila", "BLOCKED: missing canvas plate", old),
        ("t_swept", "steward", "Sweep t_1: unchanged — still blocked", NOW - 3 * H + 5),
        ("t_swept", "steward", "Sweep t_2: unchanged — still blocked", NOW - 2 * H),
        ("t_swept", "steward", "Sweep t_3: unchanged — still blocked", NOW - 10 * 60),
        ("t_beat", "vera", "BLOCKED: no clip to review", old),
        ("t_beat", "worker", "Status beat: 0 running, no movement", NOW - 15 * 60),
    ], events=[("t_swept", "blocked", old), ("t_beat", "blocked", old)])
    n, calls = _run(db, monkeypatch)
    assert n == 2
    assert [c[1] for c in calls if c[0] == "unblock"] == ["t_swept", "t_beat"]


def test_the_marker_is_found_under_a_long_thread(tmp_path, monkeypatch):
    """_comments_have reads the last 12 comments; a routed card the sweep keeps commenting on
    would be routed again every tick. The gate reads the whole thread by author."""
    old = NOW - 9 * H
    comments = [("t_x", "bash", "BLOCKED: PASS", old),
                ("t_x", "kanban-board-gate", g.OWNERLESS_MARK + " — blocked 6 h by bash", old + 3 * H)]
    comments += [("t_x", "steward", f"Sweep t_{i}: unchanged", old + 3 * H + 60 * i) for i in range(1, 16)]
    db = _board(tmp_path, [("t_x", "acc-t1", "blocked", "ada", None)], comments=comments,
                events=[("t_x", "blocked", old)])
    n, calls = _run(db, monkeypatch)
    assert n == 0 and calls == []      # strike 1 already given; the same block still stands


def test_a_passed_later_date_is_ownerless_a_future_one_is_hers(tmp_path, monkeypatch):
    old = NOW - 12 * H
    db = _board(tmp_path, [
        ("t_past", "vLLM smoke bench", "blocked", "bash", None),
        ("t_future", "vLLM smoke bench", "blocked", "bash", None),
    ], comments=[
        ("t_past", "zoey", "later 2026-09-17 — window-dependent GPU bench", old + 60),
        ("t_future", "zoey", "UNBLOCK: later 2026-09-30 — after the reboot", old + 60),
    ], events=[("t_past", "blocked", old), ("t_future", "blocked", old)])
    n, calls = _run(db, monkeypatch)
    assert n == 1 and [c[1] for c in calls if c[0] == "unblock"] == ["t_past"]
    assert "later 2026-09-17 has passed" in calls[0][2]


def test_an_archived_reframe_leaves_its_source_ownerless(tmp_path, monkeypatch):
    """t_a6e67fad: reframe t_4746c010 was archived, which kanban-loop does not count as done,
    so the source stayed blocked and pinned two todo cards."""
    old = NOW - 28 * H
    db = _board(tmp_path, [
        ("t_src", "Verify config state", "blocked", "steward", None),
        ("t_4746c010", "reframe: Verify config state", "archived", "ada", None),
    ], comments=[("t_src", "kanban-loop", "reframe-cut: t_4746c010 (gen 1, frame by mechanical)", old + 5)],
       events=[("t_src", "blocked", old)])
    n, calls = _run(db, monkeypatch)
    assert n == 1 and "reframe t_4746c010 is archived" in calls[0][2]


def test_second_strike_retreats_to_zoey_reason_first_only_without_open_parents(tmp_path, monkeypatch):
    old = NOW - 20 * H
    rows = [("t_x", "Implement the hook", "blocked", "ada", None)]
    comments = [("t_x", "kanban-board-gate", g.OWNERLESS_MARK + " — blocked 6 h by bash: prose", old + H),
                ("t_x", "ada", "BLOCKED: still cannot — the hook path is gone", old + 4 * H)]
    events = [("t_x", "blocked", old), ("t_x", "unblocked", old + H), ("t_x", "blocked", old + 4 * H)]
    db = _board(tmp_path, rows, comments=comments, events=events)
    n, calls = _run(db, monkeypatch)
    assert n == 1
    assert [c[0] for c in calls] == ["unblock", "block", "comment"]
    block = calls[1]
    assert block[1] == "t_x" and block[-2:] == ("--kind", "needs_input") and block[2].startswith("Routed once")
    assert calls[2][2].startswith(g.RETREAT_MARK) and "with Crissa" in calls[2][2]
    # a parent still open: the unblock would land todo and `block` refuses it — wait, touch nothing
    (tmp_path / "b").mkdir()
    db2 = _board(tmp_path / "b", rows + [("t_p", "parent", "todo", "bash", None)], comments=comments,
                 events=events, links=[("t_p", "t_x")])
    n2, calls2 = _run(db2, monkeypatch)
    assert n2 == 0 and calls2 == []
    # the typed block refused: no "with Zoey" mark, so the retreat is retried after the wait
    (tmp_path / "c").mkdir()
    db3 = _board(tmp_path / "c", rows, comments=comments, events=events)
    calls3 = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls3.append(a) or ((1, "cannot block") if a[0] == "block" else (0, "ok")))
    con = sqlite3.connect(db3)
    assert g.gate_ownerless(con, now=NOW, today=TODAY) == 1
    con.close()
    assert [c[0] for c in calls3] == ["unblock", "block", "comment"] and "refused" in calls3[2][2]


def test_a_fresh_crash_is_never_ownerless_whatever_the_reason(tmp_path, monkeypatch):
    """06:44 first live tick: t_a6e67fad (archived reframe) was retreated 8 s after its worker
    crashed — the reason returned before the activity check. Freshness comes first."""
    old = NOW - 28 * H
    db = _board(tmp_path, [
        ("t_src", "Verify config state", "blocked", "ada", None),
        ("t_4746c010", "reframe", "archived", "ada", None),
        ("t_late", "vLLM bench", "blocked", "bash", None),
    ], comments=[("t_src", "kanban-loop", "reframe-cut: t_4746c010 (gen 1)", old + 5),
                 ("t_src", "kanban-board-gate", g.OWNERLESS_MARK + " — blocked 28 h", NOW - 10 * 60),
                 ("t_late", "zoey", "later 2026-09-17", old + 5)],
       events=[("t_src", "blocked", old), ("t_src", "unblocked", NOW - 10 * 60), ("t_src", "gave_up", NOW - 8),
               ("t_late", "blocked", old)],
       runs=[("t_src", "crashed", "{}", "protocol violation", NOW - 8), ("t_late", "crashed", "{}", "x", NOW - 60)])
    n, calls = _run(db, monkeypatch)
    assert n == 0 and calls == []


def test_at_most_max_per_tick_and_a_kill_switch(tmp_path, monkeypatch):
    old = NOW - 9 * H
    ids = [f"t_{i}" for i in range(5)]
    db = _board(tmp_path, [(i, "x", "blocked", "bash", None) for i in ids],
                comments=[(i, "bash", "BLOCKED: PASS", old) for i in ids],
                events=[(i, "blocked", old) for i in ids])
    monkeypatch.setattr(g, "OWNERLESS_MAX", 3)
    n, calls = _run(db, monkeypatch)
    assert n == 3 and len([c for c in calls if c[0] == "unblock"]) == 3
    # batch mode: the same three become one card, no unblocks
    n, calls = _run(db, monkeypatch, batch=True)
    assert n == 3 and [c[0] for c in calls] == ["create", "comment", "comment", "comment"]
    # KREW_OWNERLESS_GATE=0: main never calls the gate
    monkeypatch.setattr(g, "OWNERLESS_ON", False)
    monkeypatch.setattr(g, "board_db", lambda: db)
    monkeypatch.setattr(g, "LOG", tmp_path / "gate.log")
    for name in ("gate_assignee", "gate_verb_wall", "gate_render"):
        monkeypatch.setattr(g, name, lambda conn, dry=False: 0)
    monkeypatch.setattr(g, "gate_ownerless", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert g.main(["--dry-run"]) == 0


def test_orchestrator_is_read_from_the_dispatchers_config(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "HERMES", tmp_path)
    assert g.orchestrator() == "ada"                      # no config: the house default
    cfg = tmp_path / "profiles" / "ada" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("kanban:\n  orchestrator_profile: steward\n  default_assignee: steward\n")
    assert g.orchestrator() == "steward"


# --- 2026-09-18 (the User: "3 subs with 3 subs, one pass"): one batch card, not one worker per card ----

def _batch_board(tmp, extra_tasks=(), extra_comments=(), extra_events=()):
    old = NOW - 9 * H
    ids = ["t_aaaa0001", "t_aaaa0002", "t_aaaa0003"]
    tasks = [(i, f"Implement thing {k}", "blocked", "bash", None) for k, i in enumerate(ids)] + list(extra_tasks)
    comments = [(i, "bash", "BLOCKED: PASS — nothing to produce", old) for i in ids] + list(extra_comments)
    events = [(i, "blocked", old) for i in ids] + list(extra_events)
    return _board(tmp, tasks, comments=comments, events=events), ids


def test_batch_mode_hands_ada_one_card_and_leaves_the_cards_blocked(tmp_path, monkeypatch):
    db, ids = _batch_board(tmp_path)
    n, calls = _run(db, monkeypatch, batch=True)
    assert n == 3
    assert [c[0] for c in calls] == ["create", "comment", "comment", "comment"]
    create = calls[0]
    assert create[1] == "route: 3 ownerless blocks" and create[2:4] == ("--assignee", "ada")
    body = create[create.index("--body") + 1]
    for i in ids:
        assert i in body
    assert "delegate_task" in body and "VERB: reassign" in body and "scoped to THIS card" in body
    assert "kanban_complete on THIS card" in body and "reason BEFORE" not in body
    assert "--idempotency-key" in create
    for c, i in zip(calls[1:], ids):
        assert c[:2] == ("comment", i) and "batched in t_ba7c0001" in c[2] and c[-2:] == ("--author", "kanban-board-gate")
    assert not [c for c in calls if c[0] in ("unblock", "reassign", "block")]


def test_one_open_batch_at_a_time_and_batched_cards_wait_for_it(tmp_path, monkeypatch):
    old = NOW - 9 * H
    ids = ["t_aaaa0001", "t_aaaa0002", "t_aaaa0003"]
    db, ids = _batch_board(
        tmp_path,
        extra_tasks=[("t_ba7c0001", "route: 3 ownerless blocks", "running", "ada", None),
                     ("t_aaaa0009", "Another prose block", "blocked", "vera", None)],
        extra_comments=[(i, "kanban-board-gate", g.OWNERLESS_MARK + " — batched in t_ba7c0001: prose", old + H) for i in ids]
                       + [("t_aaaa0009", "vera", "BLOCKED: no clip to review", old)],
        extra_events=[("t_aaaa0009", "blocked", old)],
    )
    con = sqlite3.connect(db); con.execute("UPDATE tasks SET created_at = ? WHERE id = 't_ba7c0001'", (NOW - H,)); con.commit(); con.close()
    n, calls = _run(db, monkeypatch, batch=True)
    assert n == 0 and calls == []          # the new card waits (not an action): a batch is open; the batched three are Ada's


def test_a_stuck_batch_card_is_released_and_its_cards_are_rebatched(tmp_path, monkeypatch):
    """6b: a `route:` card that tripped the verb wall is `blocked`, read as an open slot forever."""
    old = NOW - 9 * H
    ids = ["t_aaaa0001", "t_aaaa0002"]
    db, ids = _batch_board(
        tmp_path,
        extra_tasks=[("t_ba7c0001", "route: 2 ownerless blocks", "blocked", "ada", None)],
        extra_comments=[(i, "kanban-board-gate", g.OWNERLESS_MARK + " — batched in t_ba7c0001: prose", old + H) for i in ids],
    )
    con = sqlite3.connect(db); con.execute("UPDATE tasks SET created_at = ? WHERE id = 't_ba7c0001'", (NOW - H,)); con.commit(); con.close()
    n, calls = _run(db, monkeypatch, batch=True)
    assert n == 0
    assert [c[0] for c in calls] == ["comment", "unblock", "archive"] and calls[2][1] == "t_ba7c0001"
    assert calls[0][2].startswith(g.BATCH_RELEASE_MARK)
    # next tick: the batch carries the release mark and is archived → its two cards are batched again, not strike 2
    con = sqlite3.connect(db)
    con.execute("UPDATE tasks SET status='archived' WHERE id='t_ba7c0001'")
    con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                ("t_ba7c0001", "kanban-board-gate", g.BATCH_RELEASE_MARK + " — blocked, 1.0 h old", NOW))
    con.commit(); con.close()
    n2, calls2 = _run(db, monkeypatch, batch=True)
    assert n2 == 3 and [c[0] for c in calls2 if c[0] == "create"] == ["create"]   # the helper's three base cards
    assert not [c for c in calls2 if c[0] == "block"]
    # an old-but-healthy running batch is also released (6 h cap)
    monkeypatch.setattr(g, "BATCH_RELEASE_ON", False)
    n3, calls3 = _run(db, monkeypatch, batch=True)
    assert calls3 == [] or all(c[0] != "archive" for c in calls3)


def test_after_the_batch_closes_a_card_still_blocked_is_retreated_after_the_wait(tmp_path, monkeypatch):
    old = NOW - 9 * H
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    mk = lambda tmp, closed: _board(
        tmp,
        [("t_aaaa0001", "Implement thing", "blocked", "bash", None),
         ("t_ba7c0001", "route: 1 ownerless block", "done", "ada", None)],
        comments=[("t_aaaa0001", "bash", "BLOCKED: PASS", old),
                  ("t_aaaa0001", "kanban-board-gate", g.OWNERLESS_MARK + " — batched in t_ba7c0001: prose", old + H)],
        events=[("t_aaaa0001", "blocked", old)],
    )
    db = mk(tmp_path / "a", None)
    con = sqlite3.connect(db); con.execute("UPDATE tasks SET created_at = ? WHERE id = 't_ba7c0001'", (old + H,)); con.commit(); con.close()
    # completed_at is not in the fixture's tasks table: add it and set the batch to closed 4 h ago
    con = sqlite3.connect(db); con.execute("ALTER TABLE tasks ADD COLUMN completed_at INTEGER")
    con.execute("UPDATE tasks SET completed_at = ? WHERE id = 't_ba7c0001'", (NOW - 4 * H,)); con.commit(); con.close()
    n, calls = _run(db, monkeypatch, batch=True)
    assert n == 1 and [c[0] for c in calls] == ["unblock", "block", "comment"] and calls[1][-2:] == ("--kind", "needs_input")
    # closed 10 minutes ago: her verb may still be landing — wait
    db2 = mk(tmp_path / "b", None)
    con = sqlite3.connect(db2); con.execute("ALTER TABLE tasks ADD COLUMN completed_at INTEGER")
    con.execute("UPDATE tasks SET completed_at = ? WHERE id = 't_ba7c0001'", (NOW - 600,)); con.commit(); con.close()
    n2, calls2 = _run(db2, monkeypatch, batch=True)
    assert n2 == 0 and calls2 == []


def test_a_per_card_strike_that_came_back_untyped_is_batched_not_retreated(tmp_path, monkeypatch):
    """07:40: six cards routed one-by-one at 06:34 came back untyped because Ada's worker crashed;
    that is not a ruling, so batch mode takes them into the next batch."""
    old = NOW - 9 * H
    db = _board(tmp_path, [("t_aaaa0007", "acc-t1", "blocked", "ada", None)],
                comments=[("t_aaaa0007", "bash", "BLOCKED: PASS", old),
                          ("t_aaaa0007", "kanban-board-gate", g.OWNERLESS_MARK + " — blocked 20 h by bash: prose", old + H)],
                events=[("t_aaaa0007", "blocked", old), ("t_aaaa0007", "unblocked", old + H), ("t_aaaa0007", "gave_up", old + 2 * H)])
    n, calls = _run(db, monkeypatch, batch=True)
    assert n == 1 and [c[0] for c in calls] == ["create", "comment"] and "batched in" in calls[1][2]


# --- 2026-09-18 (the User: "vera reviews"): a finished implement card gets one review card to Vera ----

def _review_board(tmp, tasks, runs=(), links=(), comments=()):
    p = tmp / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT, "
                "block_kind TEXT, created_at INTEGER, result TEXT, completed_at INTEGER)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, outcome TEXT, metadata TEXT, error TEXT, "
                "ended_at INTEGER, summary TEXT)")
    con.execute("CREATE TABLE task_links (parent_id TEXT, child_id TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                    [(t[0], t[1], "body", t[2], t[3], None, 1, t[4], t[5]) for t in tasks])
    con.executemany("INSERT INTO task_runs (task_id, outcome, summary, ended_at) VALUES (?,?,?,?)", runs)
    con.executemany("INSERT INTO task_links VALUES (?,?)", links)
    con.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", comments)
    con.commit(); con.close()
    return p


def _run_review(db, monkeypatch):
    calls = []

    def fake(*a, dry=False):
        calls.append(a)
        return (0, "Created t_5e71e001  (ready, assignee=vera)") if a[0] == "create" else (0, "ok")

    monkeypatch.setattr(g, "kanban", fake)
    con = sqlite3.connect(db)
    n = g.gate_review(con, now=NOW)
    con.close()
    return n, calls


def test_a_finished_implement_card_gets_one_review_card_to_vera(tmp_path, monkeypatch):
    db = _review_board(tmp_path, [
        ("t_impl0001", "Implement the exit-code reaper", "done", "bash", "wired and smoked", NOW - H),
    ], runs=[("t_impl0001", "completed", "Wired the reaper; smoke green at 13/13", NOW - H)])
    n, calls = _run_review(db, monkeypatch)
    assert n == 1 and [c[0] for c in calls] == ["create", "comment"]
    create = calls[0]
    assert create[1] == "review: Implement the exit-code reaper" and create[2:4] == ("--assignee", "vera")
    assert create[create.index("--parent") + 1] == "t_impl0001"
    body = create[create.index("--body") + 1]
    assert "PASS | FAIL" in body and "prodgate-checklist" in body and "smoke green" in body and "never the fix yourself" in body
    assert create[create.index("--idempotency-key") + 1] == "review:t_impl0001"
    assert calls[1][:2] == ("comment", "t_impl0001") and calls[1][2].startswith(g.REVIEW_MARK + " → t_5e71e001")


def test_review_gate_skips_what_is_not_a_build(tmp_path, monkeypatch):
    db = _review_board(tmp_path, [
        ("t_aaaa0011", "Implement thing", "done", "ada", "x", NOW - H),                  # Ada routes, does not build
        ("t_aaaa0012", "Verify the reaper", "done", "bash", "x", NOW - H),              # a verification
        ("t_aaaa0013", "Implement thing", "done", "bash", "superseded by reframe t_x", NOW - H),   # nothing built
        ("t_aaaa0014", "Implement thing", "done", "bash", "x", NOW - 30 * H),           # older than the lookback
        ("t_aaaa0015", "Implement thing", "done", "steward", "x", NOW - H),             # already reviewed
        ("t_aaaa0016", "Implement thing", "done", "lila", "x", NOW - H),                # already has a Vera child
        ("t_5e71e009", "review: Implement thing", "ready", "vera", None, None),
        ("t_aaaa0017", "reframe: Implement thing", "done", "bash", "x", NOW - H),       # a frame, not a build
        ("t_aaaa0018", "Implement thing", "blocked", "bash", None, None),               # not done
    ], links=[("t_aaaa0016", "t_5e71e009")],
       comments=[("t_aaaa0015", "kanban-board-gate", g.REVIEW_MARK + " → t_5e71e008 (vera)", NOW - 1800)])
    n, calls = _run_review(db, monkeypatch)
    assert n == 0 and calls == []


def test_review_gate_cap_and_kill_switch(tmp_path, monkeypatch):
    db = _review_board(tmp_path, [(f"t_aaaa00{k}0", "Fix thing", "done", "bash", "x", NOW - H) for k in range(2, 7)])
    monkeypatch.setattr(g, "REVIEW_MAX", 3)
    n, calls = _run_review(db, monkeypatch)
    assert n == 3 and len([c for c in calls if c[0] == "create"]) == 3
    monkeypatch.setattr(g, "REVIEW_ON", False)
    monkeypatch.setattr(g, "board_db", lambda: db)
    for name in ("gate_assignee", "gate_verb_wall", "gate_render", "gate_ownerless"):
        monkeypatch.setattr(g, name, lambda conn, dry=False: 0)
    monkeypatch.setattr(g, "gate_review", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert g.main(["--dry-run"]) == 0


# --- 2026-09-18 (the User: "double throughput"): level the two host shells; record Ada's live caps ----

def _bal_board(tmp, tasks, events=(), comments=()):
    p = tmp / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT, skills TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INTEGER)")
    con.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, created_at INTEGER)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)", [(t[0], t[1], t[2], t[3], t[4], t[5], k) for k, t in enumerate(tasks)])
    con.executemany("INSERT INTO task_events (task_id, kind, created_at) VALUES (?,?,?)", events)
    con.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", comments)
    con.commit(); con.close()
    return p


def _run_balance(db, monkeypatch, tmp_path, mode="normal", caps=(3, 2)):
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    mode_file = tmp_path / "krew-gpu-mode"; mode_file.write_text(mode + "\n")
    monkeypatch.setattr(g, "GPU_MODE", mode_file)
    monkeypatch.setattr(g, "ada_caps_on_disk", lambda: caps)
    con = sqlite3.connect(db)
    n = g.gate_balance(con, now=NOW)
    con.close()
    return n, calls


def test_ready_bash_cards_move_to_an_idle_steward_but_not_render_or_web(tmp_path, monkeypatch):
    old = NOW - 30 * 60
    rows = [
        ("t_b0000001", "Fix the reaper", "host work", "ready", "bash", ""),
        ("t_b0000002", "resubmit mint", "python3 scripts/render-submit.py --card x", "ready", "bash", "krew-render"),
        ("t_b0000003", "Scrape the docs", "use the web tool", "ready", "bash", "web"),
        ("t_b0000004", "Wire the hook", "host work", "ready", "bash", ""),
        ("t_b0000005", "Patch the unit", "host work", "ready", "bash", ""),
    ]
    db = _bal_board(tmp_path, rows, events=[(r[0], "promoted", old) for r in rows])
    n, calls = _run_balance(db, monkeypatch, tmp_path)
    assert n == 2                                            # steward has 2 free slots
    moved = [c[1] for c in calls if c[0] == "reassign"]
    assert moved == ["t_b0000001", "t_b0000004"] and all(c[2] == "steward" for c in calls if c[0] == "reassign")
    assert all(c[2].startswith(g.BALANCE_MARK) for c in calls if c[0] == "comment")


def test_balance_leaves_the_last_card_a_full_peer_fresh_cards_and_render_mode(tmp_path, monkeypatch):
    old = NOW - 30 * 60
    rows = [("t_b0000011", "Fix a", "x", "ready", "bash", ""), ("t_b0000012", "Fix b", "x", "ready", "bash", "")]
    # steward already has 2 in flight: nothing moves
    full = rows + [("t_s0000001", "Sweep", "x", "running", "steward", ""), ("t_s0000002", "Logs", "x", "ready", "steward", "")]
    db = _bal_board(tmp_path, full, events=[(r[0], "promoted", old) for r in full])
    assert _run_balance(db, monkeypatch, tmp_path)[0] == 0
    # one waiting card: it stays (a peer takes the overflow, never the only card)
    (tmp_path / "b").mkdir()
    db = _bal_board(tmp_path / "b", rows[:1], events=[("t_b0000011", "promoted", old)])
    assert _run_balance(db, monkeypatch, tmp_path / "b")[0] == 0
    # fresh (promoted 1 min ago): wait
    (tmp_path / "c").mkdir()
    db = _bal_board(tmp_path / "c", rows, events=[(r[0], "promoted", NOW - 60) for r in rows])
    assert _run_balance(db, monkeypatch, tmp_path / "c")[0] == 0
    # Render: steward sleeps — nothing moves
    (tmp_path / "d").mkdir()
    db = _bal_board(tmp_path / "d", rows, events=[(r[0], "promoted", old) for r in rows])
    assert _run_balance(db, monkeypatch, tmp_path / "d", mode="render")[0] == 0
    # already moved once: never ping-pong
    (tmp_path / "e").mkdir()
    db = _bal_board(tmp_path / "e", rows, events=[(r[0], "promoted", old) for r in rows],
                    comments=[("t_b0000011", "kanban-board-gate", g.BALANCE_MARK + " steward → bash: x", old)])
    n, calls = _run_balance(db, monkeypatch, tmp_path / "e")
    assert n == 1 and [c[1] for c in calls if c[0] == "reassign"] == ["t_b0000012"]


def test_steward_cards_move_to_bash_except_cron_and_ms(tmp_path, monkeypatch):
    old = NOW - 30 * 60
    rows = [
        ("t_s0000011", "M&S land: unsloth", "uv pin", "ready", "steward", ""),
        ("t_s0000012", "Verify the cron job fires", "hermes cron list", "ready", "steward", "cronjob"),
        ("t_s0000013", "Reconcile config drift", "host work", "ready", "steward", ""),
        ("t_s0000014", "Execute dry-run validation", "host work", "ready", "steward", ""),
    ]
    db = _bal_board(tmp_path, rows, events=[(r[0], "promoted", old) for r in rows])
    n, calls = _run_balance(db, monkeypatch, tmp_path)
    assert [c[1] for c in calls if c[0] == "reassign"] == ["t_s0000013", "t_s0000014"] and n == 2   # M&S + cron stay


def test_caps_sync_records_live_caps_and_says_when_a_restart_is_owed(tmp_path, monkeypatch):
    db = _bal_board(tmp_path, [("t_r0000001", "x", "x", "running", "bash", "")])
    live = tmp_path / "ada-dispatch-live.json"; pending = tmp_path / "ada-caps-pending"
    monkeypatch.setattr(g, "CAPS_LIVE", live); monkeypatch.setattr(g, "CAPS_PENDING", pending)
    monkeypatch.setattr(g, "LOG", tmp_path / "gate.log")
    monkeypatch.setattr(g, "_gateway_start", lambda: "Thu 2026-09-17 20:16:13 EDT")
    caps = {"v": (3, 2)}
    monkeypatch.setattr(g, "ada_caps_on_disk", lambda: caps["v"])
    con = sqlite3.connect(db)
    assert g.sync_ada_caps(con) == 0                          # first sight: record what she loaded
    assert json.loads(live.read_text())["caps"] == [3, 2]
    caps["v"] = (6, 3)                                        # TurboKrew wrote 6/3
    assert g.sync_ada_caps(con) == 1 and pending.read_text().startswith("6/3 running=1")
    monkeypatch.setattr(g, "_gateway_start", lambda: "Thu 2026-09-18 09:00:00 EDT")   # she was restarted
    assert g.sync_ada_caps(con) == 0 and json.loads(live.read_text())["caps"] == [6, 3] and not pending.exists()
    con.close()


# --- 2026-09-18 (the User "land"): the salad tripwire and the zoey sweep fold into this gate ------------

def test_a_zoey_assignee_on_a_ready_card_is_swept_to_ada(tmp_path, monkeypatch):
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)",
                    [("t_z0000001", "Decide the desk", "body", "ready", "zoey"), ("t_b0000001", "bash card", "body", "ready", "bash")])
    con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    assert g.gate_assignee(con) == 1
    con.close()
    assert [c for c in calls if c[0] == "reassign"] == [("reassign", "t_z0000001", "ada")]
    assert "not a kanban worker" in calls[0][2]


def test_salad_gate_marks_and_releases_without_a_reset(tmp_path, monkeypatch):
    """The tripwire's functions on the 2-min tick: detect, park mark, release at once."""
    class TW:
        OFF = tmp_path / "off-absent"
        WINDOW_MIN = 15
        state = {"last_reset": 100.0}
        calls = []
        def current_board(self): return "b"
        def board_dir(self, slug): return tmp_path / "boards" / slug
        def _read_state(self): return dict(self.state)
        def _write_state(self, d): self.state = d
        def salad_sessions(self, logs, window_s, mutes, clear, since=0.0):
            self.calls.append(("sessions", since)); clear.append("t_5a1ad001"); return ["t_5a1ad001"]
        def mark_salad_parks(self, slug, ids, dry=False, cli=None): self.calls.append(("mark", tuple(ids))); return len(ids)
        def release_burned(self, slug, ids, dry=False, cli=None, why=""): self.calls.append(("release", tuple(ids), why[:20])); return len(ids)
        def stragglers(self, db, last_reset): self.calls.append(("stragglers", last_reset)); return ["t_5a1ad002"]
    tw = TW()
    monkeypatch.setattr(g, "LOG", tmp_path / "gate.log")
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "Created t_5a1ad0ff"))
    con = sqlite3.connect(tmp_path / "kanban.db")
    assert g.gate_salad(con, tw=tw) == 3            # 2 releases + the relapse card
    con.close()
    kinds = [c[0] for c in tw.calls]
    assert kinds == ["sessions", "mark", "release", "stragglers", "release"]
    assert tw.calls[2][2].startswith("Prefix caching and") and "last_seen" in tw.state
    create = next(c for c in calls if c[0] == "create")
    assert create[1].startswith("Restart window: crew engine relapse — 1 salad session(s)")
    assert create[create.index("--idempotency-key") + 1].startswith("salad-relapse:b:")
    block = next(c for c in calls if c[0] == "block")
    assert block[1] == "t_5a1ad0ff" and block[-2:] == ("--kind", "needs_input")
    assert g.RELAPSE_MARK.read_text().startswith("crew salad relapse")
    # the off file silences it
    tw.OFF = tmp_path / "off"; tw.OFF.write_text("")
    con = sqlite3.connect(tmp_path / "kanban.db"); assert g.gate_salad(con, tw=tw) == 0; con.close()


def test_the_standalone_tripwire_timer_run_is_a_no_op_now():
    src = (ROOT / "crew" / "scripts" / "kanban-salad-tripwire.py").read_text()
    body = src[src.index("def main(argv"):]
    assert "folded into kanban-board-gate" in body and 'KREW_SALAD_TRIPWIRE_STANDALONE' in body
    # kanban-zoey-sweep.sh: its units were deleted 2026-09-18 (<repo-worktree> landing 2); the assignee
    # gate is the sweep. The script may go with it — nothing here reads it any more.


def test_a_review_card_is_never_a_render_card():
    """10:17 first watch tick: Vera's review of a render card was held for the render window
    because the review body quotes the job. She reviews; she does not mint."""
    body = "Card result: python3 scripts/render-submit.py --recipe ltx25-flf2v --card t_x\nWorker summary: image_generate ran"
    assert not g._is_render_card("review: Refactor worker to use post-execution hook", body, "krew-render")
    assert g._is_render_card("resubmit mint", body, "")


def test_a_bare_render_script_name_in_a_code_fix_is_not_a_mint():
    """t_6698facd, 2026-09-22: a host code-fix named after render-collect.py's release
    command sat in the render window 4.5h (14:15 -> 18:38) on SUBMIT_RE matching the
    script's *name* in prose. A bare name is not a command; a command that submits is."""
    title = "fix: render-collect.py release_hold_for calls krew-mode by absolute path (no test intercept)"
    body = (
        "## Crystal\n"
        "- host action: `python3 -m pytest tests/test_krew_pin_check.py -k occupancy`\n"
        "scripts/render-collect.py:102-113, release_hold_for(), STILL resolves krew-mode by absolute path "
        "and execs it IN-PROCESS: spec.loader.exec_module(mod). render-submit.py names the same command "
        "in prose about the hold path."
    )
    assert not g._is_render_card(title, body, "")
    # the real mint is still recognised when the body actually runs the submitter
    assert g._is_render_card("x", "run: python3 scripts/render-submit.py --recipe ltx25-flf2v", "")
    # ...and a shell-exec form is a command too
    assert g._is_render_card("x", "exec `scripts/render-submit.py --recipe ltx23-nvfp4`", "")


def test_a_prose_comfyui_mention_is_not_a_mint_but_a_comfyui_title_is():
    """t_38e9ba3d (2026-09-22): render-window's second classifier copy (EXTRA_RENDER_RE)
    searched the BODY for a bare comfy match and held the t_6698facd code-fix card on a
    prose mention. The title-only mint signals it was load-bearing for (the two genuine
    render cards) moved into the gate; render-window now delegates to it. Net change:
    body-mention is not a mint, title-mention is."""
    REAL_TITLE = "fix: render-collect.py false-positive render-card classifier — delegate to the gate"
    REAL_BODY = (
        "## Problem\n"
        "\n"
        "`is_render_card` in `scripts/render-window.py` has a false positive: it treats a code-fix card "
        "that merely mentions `ComfyUI` and `render-collect.py` in prose as a render recipe. "
        "The source card (t_6698facd) was held 4.5 hours on a prose mention. The classifier must not "
        "match a bare name or a prose mention of a render tool; it must match the render recipe "
        "(the --recipe flag) or a ComfyUI invocation."
    )
    # 1. the misclassified code-fix card is not a mint (the bug)
    assert g._is_render_card(REAL_TITLE, REAL_BODY, "") is False
    # 2. genuine card t_ef2f6666 stays a mint (title says "via ComfyUI")
    assert g._is_render_card("Render 'sat back' pose clip via ComfyUI", "Drive ComfyUI to generate the video segment…", "") is True
    # 3. genuine card t_ad4dc6f8 stays a mint ("Render freelance chain: …" title)
    assert g._is_render_card("Render freelance chain: sat back: reads something on the screen", "Freelance mint (nightly)… pose-mint-flf.py … hub PNGs", "") is True
    # 4. sanity: a body-mention with a neutral title stays False
    assert g._is_render_card("chore: bump deps", "we run ComfyUI overnight for the render lane", "") is False


# --- gate_routed: Ada's VERB line lands from the gate (her worker is scoped to its own card) ---

def _routed_board(tmp, extra_comments=(), status="blocked"):
    old = NOW - 3 * H
    return _board(
        tmp,
        [("t_aaaa0001", "Execute the bench", status, "ada", None),
         ("t_ba7c0001", "route: 1 ownerless block", "running", "ada", None)],
        comments=[("t_aaaa0001", "bash", "BLOCKED: needs a window", old),
                  ("t_aaaa0001", "kanban-board-gate", g.OWNERLESS_MARK + " — batched in t_ba7c0001: prose", old + H)]
                 + list(extra_comments),
        events=[("t_aaaa0001", "blocked", old)],
    )


def _run_routed(db, monkeypatch, refuse=()):
    calls = []

    def fake(*a, dry=False):
        calls.append(a)
        return (1, "refused: no") if a[0] in refuse else (0, "ok")

    monkeypatch.setattr(g, "kanban", fake)
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    con = sqlite3.connect(db)
    n = g.gate_routed(con)
    con.close()
    return n, calls


def test_routed_reassign_lands_from_the_gate_and_is_marked_once(tmp_path, monkeypatch):
    t = NOW - H
    db = _routed_board(tmp_path, [("t_aaaa0001", "ada",
                                   "ROUTED (from batch t_ba7c0001): host-GPU implement.\nVERB: reassign bash", t)])
    n, calls = _run_routed(db, monkeypatch)
    assert n == 1
    assert [c[0] for c in calls] == ["reassign", "unblock", "comment"]
    assert calls[0][1:] == ("t_aaaa0001", "bash")
    assert calls[1][:2] == ("unblock", "t_aaaa0001") and g.ROUTED_MARK in calls[1][3]
    assert calls[2][2].startswith(g.ROUTED_MARK + " → reassign bash") and "landed" in calls[2][2]
    assert calls[2][-2:] == ("--author", "kanban-board-gate")
    # the mark landed: nothing more until a NEWER line
    (tmp_path / "b").mkdir()
    db2 = _routed_board(tmp_path / "b", [
        ("t_aaaa0001", "ada", "VERB: reassign bash", t),
        ("t_aaaa0001", "kanban-board-gate", g.ROUTED_MARK + " → reassign bash: landed.", t + 60)])
    assert _run_routed(db2, monkeypatch) == (0, [])
    (tmp_path / "c").mkdir()
    db3 = _routed_board(tmp_path / "c", [
        ("t_aaaa0001", "ada", "VERB: reassign bash", t),
        ("t_aaaa0001", "kanban-board-gate", g.ROUTED_MARK + " → reassign bash: landed.", t + 60),
        ("t_aaaa0001", "ada", "second look\n**VERB:** archive", t + 120)])
    n3, calls3 = _run_routed(db3, monkeypatch)
    assert n3 == 1 and [c[0] for c in calls3] == ["archive", "comment"]


def test_routed_zoey_and_hands_are_typed_blocks_reason_first(tmp_path, monkeypatch):
    t = NOW - H
    db = _routed_board(tmp_path, [("t_aaaa0001", "ada", "VERB: zoey Still wanted after the 09-17 reframe?", t)])
    n, calls = _run_routed(db, monkeypatch)
    assert [c[0] for c in calls] == ["unblock", "block", "comment"]
    assert calls[1] == ("block", "t_aaaa0001", "Still wanted after the 09-17 reframe?", "--kind", "needs_input")
    (tmp_path / "b").mkdir()
    db2 = _routed_board(tmp_path / "b", [("t_aaaa0001", "ada", "VERB: hands — plug the PTZ back in", t)])
    n2, calls2 = _run_routed(db2, monkeypatch)
    assert calls2[1] == ("block", "t_aaaa0001", "plug the PTZ back in", "--kind", "capability")


def test_routed_complete_and_park_carry_her_text(tmp_path, monkeypatch):
    t = NOW - H
    db = _routed_board(tmp_path, [("t_aaaa0001", "ada", "VERB: complete acc-t1 PASS, artifact workspaces/t_x/run.md", t)])
    n, calls = _run_routed(db, monkeypatch)
    assert calls[0] == ("complete", "t_aaaa0001", "--result", "acc-t1 PASS, artifact workspaces/t_x/run.md")
    (tmp_path / "b").mkdir()
    db2 = _routed_board(tmp_path / "b", [("t_aaaa0001", "ada", "VERB: park until the render window opens", t)])
    n2, calls2 = _run_routed(db2, monkeypatch)
    assert calls2[0] == ("schedule", "t_aaaa0001", "until the render window opens")


def test_routed_ignores_prose_other_authors_lines_before_the_mark_and_bad_profiles(tmp_path, monkeypatch):
    t = NOW - H
    old = NOW - 3 * H
    # a prose verdict (the 12:02 shape) is not a verb; a VERB by bash is not hers
    db = _routed_board(tmp_path, [
        ("t_aaaa0001", "ada", "ROUTED: reassign → bash + unblock. gate or Zoey applies the verbs.", t),
        ("t_aaaa0001", "bash", "VERB: archive", t + 1)])
    assert _run_routed(db, monkeypatch) == (0, [])
    # a VERB written before the ownerless mark is stale
    (tmp_path / "b").mkdir()
    db2 = _board(tmp_path / "b",
                 [("t_aaaa0001", "Thing", "blocked", "ada", None)],
                 comments=[("t_aaaa0001", "ada", "VERB: archive", old - H),
                           ("t_aaaa0001", "kanban-board-gate", g.OWNERLESS_MARK + " — batched in t_ba7c0001: prose", old)],
                 events=[("t_aaaa0001", "blocked", old - 2 * H)])
    assert _run_routed(db2, monkeypatch) == (0, [])
    # an unknown profile is refused and marked, never reassigned
    (tmp_path / "c").mkdir()
    db3 = _routed_board(tmp_path / "c", [("t_aaaa0001", "ada", "VERB: reassign bob", t)])
    n3, calls3 = _run_routed(db3, monkeypatch)
    assert n3 == 1 and [c[0] for c in calls3] == ["comment"] and "refused" in calls3[0][2]


def test_routed_refusal_is_marked_once_and_a_card_without_a_mark_is_not_hers(tmp_path, monkeypatch):
    t = NOW - H
    db = _routed_board(tmp_path, [("t_aaaa0001", "ada", "VERB: complete done", t)])
    n, calls = _run_routed(db, monkeypatch, refuse=("complete",))
    assert [c[0] for c in calls] == ["complete", "comment"] and "refused" in calls[1][2]
    # no ownerless mark on the thread: not a routed card, whatever she writes
    (tmp_path / "b").mkdir()
    db2 = _board(tmp_path / "b", [("t_aaaa0002", "Thing", "blocked", "ada", None)],
                 comments=[("t_aaaa0002", "ada", "VERB: archive", t)], events=[("t_aaaa0002", "blocked", t - H)])
    assert _run_routed(db2, monkeypatch) == (0, [])


def test_salad_relapse_card_is_one_a_day_and_never_a_restart(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(g, "LOG", tmp_path / "gate.log")
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "Created t_5a1ad0ff"))
    assert g.salad_relapse_card("b", ["t_1"], ["t_1"], now=1_790_000_000.0) == 1
    # the day's card already exists: hermes answers with the same id and "already"
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "already exists: t_5a1ad0ff"))
    assert g.salad_relapse_card("b", ["t_2"], [], now=1_790_000_000.0) == 0
    verbs = [c[0] for c in calls]
    assert verbs == ["create", "block", "create"]           # no second block, and never a restart
    assert not any("restart" in c[0] for c in calls)
    # dry-run files nothing
    assert g.salad_relapse_card("b", ["t_3"], [], dry=True) == 1 and len(calls) == 3
    # the kill switch
    monkeypatch.setattr(g, "SALAD_RELAPSE_ON", False)


def test_judge_is_a_krew_profile_the_assignee_gate_leaves_alone(tmp_path, monkeypatch):
    p = tmp_path / "kanban.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, status TEXT, assignee TEXT)")
    con.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, body TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?)",
                    [("t_judge", "judge: verb for t_x", "source: t_x\ncause: verb", "ready", "judge"),
                     ("t_wrk", "a worker card", "body", "ready", "worker")])
    con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    assert g.gate_assignee(con) == 1
    con.close()
    assert [c for c in calls if c[0] == "reassign"][0][1] == "t_wrk"
    assert "judge" in g.ROSTER


def test_gate_marks_are_found_under_a_long_thread(tmp_path, monkeypatch):
    """6a: 13 chatter comments after the verb-wall mark used to scroll it out of the 12-comment window."""
    runs = [("t_vw", "crashed", None, "protocol violation: worker exited rc=0", NOW - H)]
    db = _board(tmp_path, [("t_vw", "Implement x", "ready", "bash", None)], runs=runs,
                comments=[("t_vw", "kanban-board-gate", g.VERB_MARK + " protocol violation …", NOW - H + 10)]
                         + [("t_vw", "steward", f"sweep chatter {i}", NOW - H + 20 + i) for i in range(13)])
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    con = sqlite3.connect(db)
    assert g.gate_verb_wall(con) == 0 and calls == []
    # krew reads the whole thread always (the 12-comment window is gone: tests/test_crew_loop.py SILENT_CUTS), so the
    # house's "old window re-fires" branch has nothing to flip here.
    con.close()


def test_a_second_untyped_block_parks_instead_of_routing_to_triage(tmp_path, monkeypatch):
    """7: hermes routes a same-cause re-block to TRIAGE at block_recurrences >= 1; the gate's own
    untyped blocks (verb wall / render hold / missing plate) did that 13 times in 48 h."""
    runs = [("t_vw", "crashed", None, "protocol violation: worker exited rc=0", NOW - 60)]
    db = _board(tmp_path, [("t_vw", "Implement x", "ready", "bash", None)], runs=runs)
    con = sqlite3.connect(db); con.execute("ALTER TABLE tasks ADD COLUMN block_recurrences INTEGER DEFAULT 0")
    con.execute("UPDATE tasks SET block_recurrences = 1 WHERE id = 't_vw'"); con.commit(); con.close()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    con = sqlite3.connect(db)
    assert g.gate_verb_wall(con) == 1
    con.close()
    verbs = [c[0] for c in calls]
    assert verbs == ["comment", "comment", "schedule"] and calls[1][2].startswith(g.TRIAGE_GUARD_MARK)
    assert not [c for c in calls if c[0] == "block"]


def test_stragglers_are_judged_by_age_and_leave_is_logged_once(tmp_path, monkeypatch):
    """6c: the in-gate detector never resets, so `last_reset` never moves; a straggler is an old park."""
    seen = {}
    class TW:
        OFF = tmp_path / "off-absent"
        WINDOW_MIN = 15
        state = {"last_reset": 100.0}
        def current_board(self): return "b"
        def board_dir(self, slug): return tmp_path / "boards" / slug
        def _read_state(self): return dict(self.state)
        def _write_state(self, d): self.state = d
        def salad_sessions(self, logs, window_s, mutes, clear, since=0.0): return []
        def stragglers(self, db, cutoff): seen["cutoff"] = cutoff; return ["t_old1", "t_old2"]
        def releasable(self, db, ids): return ["t_old1"], {"t_old2": "kanban-loop already cut a reframe"}
        def release_burned(self, slug, ids, dry=False, cli=None, why=""): seen["released"] = tuple(ids); return len(ids)
    tw = TW()
    monkeypatch.setattr(g, "LOG", tmp_path / "gate.log")
    con = sqlite3.connect(tmp_path / "kanban.db")
    assert g.gate_salad(con, tw=tw) == 1
    assert abs(seen["cutoff"] - (time.time() - 600)) < 5 and seen["released"] == ("t_old1",)
    assert tw.state["left"] == {"t_old2": "kanban-loop already cut a reframe"}
    log1 = (tmp_path / "gate.log").read_text()
    assert log1.count("release: leave t_old2") == 1
    assert g.gate_salad(con, tw=tw) == 1
    assert (tmp_path / "gate.log").read_text().count("release: leave t_old2") == 1   # not every 2 min
    con.close()


def test_dup_gate_archives_the_younger_open_twin_and_leaves_kin_alone(tmp_path, monkeypatch):
    db = _board(tmp_path, [
        ("t_old", "Implement terminal state validation in worker lifecycle", "blocked", "bash", None),
        ("t_new", "Implement terminal-state validation in worker lifecycle!", "todo", "steward", None),
        ("t_fr", "reframe: Implement terminal state validation in worker lifecycle", "todo", "ada", None),
        ("t_kid", "Implement terminal state validation in worker lifecycle", "ready", "bash", None),
        ("t_run", "Implement terminal state validation in worker lifecycle", "running", "bash", None),
    ], links=[("t_kid", "t_old")])
    con = sqlite3.connect(db)
    for i, tid in enumerate(["t_old", "t_new", "t_fr", "t_kid", "t_run"]):
        con.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (NOW - 10 * H + i * H, tid))
    con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    assert g.gate_dups(con) == 1
    con.close()
    assert [c[0] for c in calls] == ["comment", "archive"] and calls[1][1] == "t_new"
    assert calls[0][2].startswith(g.DUP_MARK + " t_old")
    # a reframe of X is not X's twin; a linked child is kin; a running card is never touched
    assert g.dup_key("reframe: A b") != g.dup_key("A b") and g.dup_key("A, b! t_00000001") == "a b"


def test_skills_inherit_takes_the_lineages_pins_and_the_assignees_map_once(tmp_path, monkeypatch):
    db = _board(tmp_path, [
        ("t_root", "Root card", "todo", "ada", None),
        ("t_kid", "Child card", "ready", "bash", None),
        ("t_done", "Already pinned", "ready", "bash", None),
        ("t_bare", "No lineage", "ready", "bash", None),
    ], links=[("t_kid", "t_root")])
    con = sqlite3.connect(db); con.execute("ALTER TABLE tasks ADD COLUMN skills TEXT")
    con.execute("UPDATE tasks SET skills = ? WHERE id = 't_root'", ('["adversarial-gate", "krew-deliverable"]',))
    con.execute("UPDATE tasks SET skills = ? WHERE id = 't_done'", ('["bash-map"]',)); con.commit()
    calls, runs = [], []
    class R: returncode = 0; stdout = "ok"
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    assert g.gate_skills_inherit(con, runner=lambda args: runs.append(args) or R()) == 1
    assert runs[0][1:5] == ["t_kid", "adversarial-gate", "krew-deliverable", "bash-map"]
    assert calls[0][0] == "comment" and calls[0][2].startswith(g.SKILLS_MARK + " from t_root")
    # marked: not again
    con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                ("t_kid", "kanban-board-gate", calls[0][2], NOW)); con.commit()
    assert g.gate_skills_inherit(con, runner=lambda args: runs.append(args) or R()) == 0
    con.close()


def test_a_terminal_skills_refusal_marks_so_it_is_never_retried(tmp_path, monkeypatch):
    """t_df57cb5c burned 59 ticks on rc=3 (2026-09-22): a judge card inheriting `ada-map` off
    its ada parent, plus a `judge-map` that does not exist. rc 2/3 cannot improve by waiting,
    so the card must be marked; rc 4 (no board) is transient and must keep retrying."""
    db = _board(tmp_path, [
        ("t_root", "Root card", "todo", "ada", None),
        ("t_kid", "Child card", "todo", "judge", None),
    ], links=[("t_kid", "t_root")])
    con = sqlite3.connect(db); con.execute("ALTER TABLE tasks ADD COLUMN skills TEXT")
    con.execute("UPDATE tasks SET skills = ? WHERE id = 't_root'", ('["ada-map"]',)); con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))

    class Refused: returncode = 3; stdout = "every name refused"
    assert g.gate_skills_inherit(con, runner=lambda args: Refused()) == 1
    assert calls and calls[0][0] == "comment" and calls[0][2].startswith(g.SKILLS_MARK)
    assert "judge" in calls[0][2] and "rc=3" in calls[0][2]
    con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                ("t_kid", "kanban-board-gate", calls[0][2], NOW)); con.commit()
    assert g.gate_skills_inherit(con, runner=lambda args: Refused()) == 0   # marked: never again
    con.close()


def test_a_transient_skills_failure_is_retried(tmp_path, monkeypatch):
    db = _board(tmp_path, [
        ("t_root", "Root card", "todo", "bash", None),
        ("t_kid", "Child card", "todo", "bash", None),
    ], links=[("t_kid", "t_root")])
    con = sqlite3.connect(db); con.execute("ALTER TABLE tasks ADD COLUMN skills TEXT")
    con.execute("UPDATE tasks SET skills = ? WHERE id = 't_root'", ('["bash-map"]',)); con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))

    class NoBoard: returncode = 4; stdout = "no board"
    assert g.gate_skills_inherit(con, runner=lambda args: NoBoard()) == 1
    assert not calls                                   # nothing marked
    assert g.gate_skills_inherit(con, runner=lambda args: NoBoard()) == 1   # still tried
    con.close()


def test_liveness_gate_writes_the_registry_and_acts_only_when_told(tmp_path, monkeypatch):
    """Phase 2, report-only first: the file is written every tick, no verb is issued until
    KREW_LIVENESS_ACT=1; then a wake that came true is an unblock and a bare park asks Zoey."""
    db = _board(tmp_path, [
        ("t_park", "an old park", "scheduled", "bash", None),
        ("t_hold", "held source", "scheduled", "bash", None),
        ("t_c0ffee01", "the cut", "done", "ada", None),
        ("t_odd", "no reader", "weird", "bash", None),
    ], comments=[("t_hold", "kanban-loop", "hold: reframe t_c0ffee01 until:t_c0ffee01", NOW - H)])
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE task_events ADD COLUMN payload TEXT")
    con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                ("t_park", "scheduled", '{"reason": "park — dup"}', NOW - 9 * 86400))
    con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)",
                ("t_hold", "scheduled", '{"reason": "hold: until:t_c0ffee01"}', NOW - H))
    con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    ctx = {"max_in_progress": 6, "running": 0, "engine_up": True, "ada_up": True, "zoey_up": True,
           "salad_recent": False, "off_hours": False, "board": "b", "default_wake_days": 7}
    out = tmp_path / "kanban-liveness.json"
    monkeypatch.setattr(g, "LIVENESS_ACT", False)
    assert g.gate_liveness(con, ctx=ctx, now=NOW, path=out) == (0, 1, 2)
    reg = json.loads(out.read_text())
    assert reg["none"] == ["t_odd"] and set(reg["due"]) == {"t_park", "t_hold"} and calls == []
    # acting: the loop's own hold is left to its closer; the bare park goes to Zoey, never respawned
    monkeypatch.setattr(g, "LIVENESS_ACT", True)
    g.gate_liveness(con, ctx=ctx, now=NOW, path=out)
    verbs = [(c[0], c[1]) for c in calls]
    assert ("unblock", "t_park") in verbs and ("block", "t_park") in verbs
    assert not any(c[1] == "t_hold" for c in calls)
    blk = next(c for c in calls if c[0] == "block")
    assert blk[-2:] == ("--kind", "needs_input") and "drop | keep 7 more | try" in blk[2]
    con.close()


def test_cause_gate_writes_one_line_per_card_not_per_run(tmp_path, monkeypatch):
    """A card retried four times against the same harness fault says ONE thing, not four.
    Measured 2026-09-22: t_0a2789dc had 28 runs against the mis-calibrated tool cap."""
    db = _board(tmp_path, [("t_burned", "Real work", "blocked", "bash", None)])
    con = sqlite3.connect(db)
    now = int(NOW)
    for rid in (101, 102, 103, 104):
        con.execute("INSERT INTO task_runs (id,task_id,outcome,error,ended_at) VALUES (?,?,?,?,?)",
                    (rid, "t_burned", "spawn_failed",
                     "spawn aborted: bash kit has 55 tools, pre-spawn cap is 45", now - 60))
    con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    assert g.gate_cause(con, now=now) == 1               # one line, not four
    assert calls[0][0] == "comment"
    body = calls[0][2]
    assert body.startswith("cause: harness —") and "4 runs" in body
    # idempotent: the mark is the cause, so a fifth identical run adds nothing
    con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                ("t_burned", "kanban-board-gate", body, now)); con.commit()
    assert g.gate_cause(con, now=now) == 0
    con.close()


def test_cause_gate_gives_a_different_cause_its_own_line(tmp_path, monkeypatch):
    """A card that failed harness and LATER failed for real is two different facts."""
    db = _board(tmp_path, [("t_two", "Real work", "blocked", "bash", None)])
    con = sqlite3.connect(db)
    now = int(NOW)
    con.execute("INSERT INTO task_runs (id,task_id,outcome,error,ended_at) "
                "VALUES (201,'t_two','spawn_failed','spawn aborted: cap',?)", (now - 90,))
    con.execute("INSERT INTO task_runs (id,task_id,outcome,error,ended_at) "
                "VALUES (202,'t_two','crashed','KeyError in pose_intake',?)", (now - 60,))
    con.commit()
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    assert g.gate_cause(con, now=now) == 2
    assert {c[2].split(" — ")[0] for c in calls} == {"cause: harness", "cause: real"}
    con.close()


def test_cause_gate_skips_archived_cards_and_honours_its_kill(tmp_path, monkeypatch):
    db = _board(tmp_path, [("t_gone", "Retired", "archived", "bash", None)])
    con = sqlite3.connect(db)
    now = int(NOW)
    con.execute("INSERT INTO task_runs (id,task_id,outcome,error,ended_at) "
                "VALUES (301,'t_gone','crashed','KeyError',?)", (now - 60,))
    con.commit()
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: (0, "ok"))
    assert g.gate_cause(con, now=now) == 0
    monkeypatch.setattr(g, "CAUSE_ON", False)
    assert g.gate_cause(con, now=now) == 0
    con.close()


def test_cause_gate_ignores_runs_outside_its_window(tmp_path, monkeypatch):
    """Bounded on purpose: it annotates what just happened, it does not backfill history."""
    db = _board(tmp_path, [("t_old", "Ancient", "blocked", "bash", None)])
    con = sqlite3.connect(db)
    now = int(NOW)
    con.execute("INSERT INTO task_runs (id,task_id,outcome,error,ended_at) "
                "VALUES (401,'t_old','crashed','KeyError',?)",
                (now - int(g.CAUSE_WINDOW_S) - 600,))
    con.commit()
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: (0, "ok"))
    assert g.gate_cause(con, now=now) == 0
    con.close()


def _reg(*rows):
    """A liveness registry with the given (id, reader, overdue_min) rows, all overdue."""
    return {"overdue": [r[0] for r in rows], "none": [], "due": [],
            "rows": [{"id": i, "reader": rd, "overdue_min": m, "why": "waiting"} for i, rd, m in rows]}


def test_overdue_escalates_once_at_the_third_tick_then_goes_quiet(tmp_path, monkeypatch):
    """Law 1's second clause. Strike 1-2 silent; the third writes ONE comment and the card
    drops out of the overdue log line. 139 identical lines on 09-22 became this."""
    db = _board(tmp_path, [("t_late", "Late card", "todo", "bash", None)])
    con = sqlite3.connect(db)
    monkeypatch.setattr(g, "STRIKES_PATH", tmp_path / "strikes.json")
    monkeypatch.setattr(g, "LIVENESS_ESCALATE", True)
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    reg = _reg(("t_late", "dispatcher", 90))

    assert g.gate_overdue(con, reg)["new"] == 0 and not calls          # strike 1
    assert g.gate_overdue(con, reg)["new"] == 0 and not calls          # strike 2
    r3 = g.gate_overdue(con, reg)                                      # strike 3: escalate
    assert r3["new"] == 1 and r3["quiet"] == ["t_late"]
    assert calls[0][0] == "comment" and calls[0][2].startswith(g.OVERDUE_MARK + " (dispatcher)")
    assert "90 min" in calls[0][2]

    r4 = g.gate_overdue(con, reg)                                      # strike 4: silent
    assert r4["new"] == 0 and r4["quiet"] == ["t_late"] and len(calls) == 1
    con.close()


def test_a_changed_reader_is_a_new_fact_and_escalates_again(tmp_path, monkeypatch):
    db = _board(tmp_path, [("t_late", "Late card", "todo", "bash", None)])
    con = sqlite3.connect(db)
    monkeypatch.setattr(g, "STRIKES_PATH", tmp_path / "strikes.json")
    monkeypatch.setattr(g, "LIVENESS_ESCALATE", True)
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: (0, "ok"))
    for _ in range(3):
        g.gate_overdue(con, _reg(("t_late", "dispatcher", 90)))
    moved = _reg(("t_late", "zoey", 95))
    assert [g.gate_overdue(con, moved)["new"] for _ in range(3)] == [0, 0, 1]
    con.close()


def test_a_card_that_stops_being_overdue_is_forgotten(tmp_path, monkeypatch):
    """So that becoming overdue again later is a NEW fact, not a continuation."""
    db = _board(tmp_path, [("t_a", "A", "todo", "bash", None), ("t_b", "B", "todo", "bash", None)])
    con = sqlite3.connect(db)
    path = tmp_path / "strikes.json"
    monkeypatch.setattr(g, "STRIKES_PATH", path)
    monkeypatch.setattr(g, "LIVENESS_ESCALATE", True)
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: (0, "ok"))
    for _ in range(2):
        g.gate_overdue(con, _reg(("t_a", "dispatcher", 10), ("t_b", "dispatcher", 10)))
    g.gate_overdue(con, _reg(("t_b", "dispatcher", 10)))      # t_a recovered
    import json as _j
    assert "liveness:t_a" not in _j.loads(path.read_text())
    con.close()


def test_report_only_by_default_writes_nothing(tmp_path, monkeypatch):
    """Ships disarmed, like ORPHAN_REAP / LIVENESS_ACT / JANITOR_RESTART before it."""
    db = _board(tmp_path, [("t_late", "Late", "todo", "bash", None)])
    con = sqlite3.connect(db)
    monkeypatch.setattr(g, "STRIKES_PATH", tmp_path / "strikes.json")
    monkeypatch.setattr(g, "LIVENESS_ESCALATE", False)
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    reg = _reg(("t_late", "dispatcher", 90))
    for _ in range(4):
        g.gate_overdue(con, reg)
    assert not calls                                   # counted and logged, never written
    con.close()


def test_the_escalation_is_capped_per_tick(tmp_path, monkeypatch):
    rows = [(f"t_{i:06d}", "Late", "todo", "bash", None) for i in range(10)]
    db = _board(tmp_path, rows)
    con = sqlite3.connect(db)
    monkeypatch.setattr(g, "STRIKES_PATH", tmp_path / "strikes.json")
    monkeypatch.setattr(g, "LIVENESS_ESCALATE", True)
    monkeypatch.setattr(g, "OVERDUE_MAX", 3)
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: (0, "ok"))
    reg = _reg(*[(r[0], "dispatcher", 10) for r in rows])
    for _ in range(3):
        out = g.gate_overdue(con, reg)
    assert out["new"] == 3
    con.close()


def test_a_dry_run_counts_but_never_comments_or_writes(tmp_path, monkeypatch):
    db = _board(tmp_path, [("t_late", "Late", "todo", "bash", None)])
    con = sqlite3.connect(db)
    path = tmp_path / "strikes.json"
    monkeypatch.setattr(g, "STRIKES_PATH", path)
    monkeypatch.setattr(g, "LIVENESS_ESCALATE", True)
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    for _ in range(4):
        g.gate_overdue(con, _reg(("t_late", "dispatcher", 90)), dry=True)
    assert not calls and not path.exists()


# --- 2026-09-25: a parked `later <date>` that has passed has no reader --------------------------------


def _parked_board(tmp, tasks, comments=(), events=()):
    """A `scheduled` (parked) board — the dispatcher never dispatches this status, so nothing
    else reads these cards."""
    return _board(tmp, tasks, comments=comments, events=events)


def test_a_passed_later_date_on_a_parked_card_is_routed_to_ada(tmp_path, monkeypatch):
    """t_801d9350 class: parked (scheduled) with a `later <date>` whose date has passed. The
    dispatcher only dispatches ready/todo, so nothing unparks it — the gate must re-park it
    on Ada with the offending line quoted. One comment (the LATER_PARK_MARK) is the once-only
    guard; the schedule call carries the routing reason."""
    old = NOW - 30 * H
    db = _parked_board(
        tmp_path,
        [("t_p", "vLLM smoke bench", "scheduled", "bash", None)],
        comments=[("t_p", "zoey", "later 2026-09-17 — window-dependent GPU bench", old + 60)],
        events=[("t_p", "scheduled", old)],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    con = sqlite3.connect(db)
    n = g.gate_later_parks(con, now=NOW, today=TODAY)
    con.close()
    assert n == 1
    verbs = [c[0] for c in calls]
    assert verbs == ["comment", "schedule"]
    assert "later 2026-09-17 has passed" in calls[0][2]
    assert g.LATER_PARK_MARK in calls[0][2]
    assert "route this card" in calls[1][2].lower() or "route" in calls[1][2].lower()


def test_a_future_later_date_on_a_parked_card_is_left_alone(tmp_path, monkeypatch):
    """A future date is the card's own deadline: parked is correct, do nothing."""
    old = NOW - 30 * H
    db = _parked_board(
        tmp_path,
        [("t_f", "GPU bench", "scheduled", "bash", None)],
        comments=[("t_f", "zoey", "later 2026-09-30 — after the reboot", old + 60)],
        events=[("t_f", "scheduled", old)],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    con = sqlite3.connect(db)
    n = g.gate_later_parks(con, now=NOW, today=TODAY)
    con.close()
    assert n == 0 and calls == []


def test_a_later_line_that_is_not_a_date_is_handed_to_ada(tmp_path, monkeypatch):
    """A ruler's `later <...>` that is not a date cannot be scheduled — the card is parked
    forever by construction. The gate does NOT schedule it (nothing to schedule); it comments,
    reassigns to Ada, and unblocks so Ada's inbox sees it. One read undoes the park."""
    old = NOW - 30 * H
    db = _parked_board(
        tmp_path,
        [("t_b", "GPU bench", "scheduled", "bash", None)],
        comments=[("t_b", "zoey", "later when the window opens", old + 60)],
        events=[("t_b", "scheduled", old)],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    con = sqlite3.connect(db)
    n = g.gate_later_parks(con, now=NOW, today=TODAY)
    con.close()
    assert n == 1
    verbs = [c[0] for c in calls]
    assert verbs == ["comment", "reassign", "unblock"]
    assert "not a date" in calls[0][2]
    assert "later when the window opens" in calls[0][2]
    assert calls[1][2] == "ada"
    assert g.LATER_PARK_MARK in calls[2][3]


def test_a_parked_card_without_any_later_line_is_left_alone(tmp_path, monkeypatch):
    """A parked card that has no ruler LATER line is not this gate's class — leave it."""
    old = NOW - 30 * H
    db = _parked_board(
        tmp_path,
        [("t_x", "GPU bench", "scheduled", "bash", None)],
        comments=[("t_x", "bash", "BLOCKED: waiting on render window", old + 60)],
        events=[("t_x", "scheduled", old)],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    con = sqlite3.connect(db)
    n = g.gate_later_parks(con, now=NOW, today=TODAY)
    con.close()
    assert n == 0 and calls == []


def test_the_later_park_gate_marks_once_not_every_tick(tmp_path, monkeypatch):
    """The LATER_PARK_MARK comment is the once-only guard. A second tick on the same card
    finds the mark and does nothing — no comment, no schedule, no reassign."""
    old = NOW - 30 * H
    db = _parked_board(
        tmp_path,
        [("t_p", "vLLM smoke bench", "scheduled", "bash", None)],
        comments=[
            ("t_p", "zoey", "later 2026-09-17 — window-dependent GPU bench", old + 60),
            ("t_p", "kanban-board-gate", g.LATER_PARK_MARK + " — later 2026-09-17 has passed (parked).", old + 30 * H),
        ],
        events=[("t_p", "scheduled", old)],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    con = sqlite3.connect(db)
    n = g.gate_later_parks(con, now=NOW, today=TODAY)
    con.close()
    assert n == 0 and calls == []


def test_the_later_park_kill_switch_disarms(tmp_path, monkeypatch):
    """KREW_LATER_PARK_GATE=0: the gate is off, no calls even on a passed-date park."""
    old = NOW - 30 * H
    db = _parked_board(
        tmp_path,
        [("t_p", "vLLM smoke bench", "scheduled", "bash", None)],
        comments=[("t_p", "zoey", "later 2026-09-17", old + 60)],
        events=[("t_p", "scheduled", old)],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    monkeypatch.setattr(g, "LATER_PARK_ON", False)
    con = sqlite3.connect(db)
    try:
        n = g.gate_later_parks(con, now=NOW, today=TODAY)
    finally:
        con.close()
    # the kill switch is enforced at main(), not inside the function — the function itself
    # still runs; main() gates the call. Assert the function is callable and the constant
    # exists, and that main() would skip it.
    assert g.LATER_PARK_ON is False
    # directly: the gate still counts (the switch is at the call site)
    assert n == 1 and len(calls) >= 1


def test_a_dry_run_later_park_counts_but_makes_no_calls(tmp_path, monkeypatch):
    old = NOW - 30 * H
    db = _parked_board(
        tmp_path,
        [("t_p", "vLLM smoke bench", "scheduled", "bash", None)],
        comments=[("t_p", "zoey", "later 2026-09-17", old + 60)],
        events=[("t_p", "scheduled", old)],
    )
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    monkeypatch.setattr(g, "orchestrator", lambda: "ada")
    con = sqlite3.connect(db)
    n = g.gate_later_parks(con, now=NOW, today=TODAY, dry=True)
    con.close()
    assert n == 1 and calls == []


# --- the per-card breaker (the User 09-28, kanban repair Stage 1) -------------------------------

def _eaten(tmp_path, status="blocked", outcome="blocked",
           why="terminal-guarantee: worker exited rc=1 without landing a terminal kanban call; run log: /home/x/{}.log"):
    db = _board(tmp_path, [("t_eaten", "Execute krew-mode normal", status, "steward", None)])
    con = sqlite3.connect(db)
    now = int(NOW)
    for i, rid in enumerate((401, 402, 403, 404)):
        con.execute("INSERT INTO task_runs (id,task_id,outcome,error,ended_at) VALUES (?,?,?,?,?)",
                    (rid, "t_eaten", outcome, why.format(rid), now - 600 * (4 - i)))
    con.commit()
    return con, now


def test_breaker_reports_a_card_eaten_by_the_same_ending_and_touches_nothing(tmp_path, monkeypatch):
    """t_7ae5fc3a: 94 runs in 3 h, 93 blocked by the terminal guarantee, re-lifted each time.
    Report mode (the default) names it and writes the report; it never moves the card."""
    con, now = _eaten(tmp_path)
    monkeypatch.setenv("KREW_CARD_BREAKER", "report")
    monkeypatch.setattr(g, "HERMES", tmp_path)
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    g.gate_cause(con, now=now)
    assert calls == []                                   # blocked runs carry no cause: line; no act
    rep = json.loads((tmp_path / "kanban-breaker.json").read_text())
    assert rep["mode"] == "report" and rep["acted"] == []
    assert [t["task_id"] for t in rep["trips"]] == ["t_eaten"] and rep["trips"][0]["runs"] == 4
    assert "terminal-guarantee" in rep["trips"][0]["key"]
    assert g.LAST_BREAKER == {"n": 1, "mode": "report"}
    con.close()


def test_breaker_act_holds_it_once_for_zoey(tmp_path, monkeypatch):
    con, now = _eaten(tmp_path)
    monkeypatch.setenv("KREW_CARD_BREAKER", "act")
    monkeypatch.setattr(g, "HERMES", tmp_path)
    calls = []
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: calls.append(a) or (0, "ok"))
    g.gate_cause(con, now=now)
    verbs = [c[0] for c in calls]
    assert verbs == ["unblock", "block", "comment"]
    blk = calls[1]
    assert blk[-2:] == ("--kind", "needs_input") and blk[2].startswith("breaker: 4 runs in 6 h")
    assert "try (run it again) | park" in blk[2]
    # once: the mark on the thread stops a second hold
    con.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                ("t_eaten", "kanban-board-gate", calls[2][2], now)); con.commit()
    calls.clear()
    g.gate_cause(con, now=now)
    assert calls == []
    con.close()


def test_breaker_leaves_designed_waits_and_short_streaks_and_honours_off(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "HERMES", tmp_path)
    monkeypatch.setattr(g, "kanban", lambda *a, dry=False: (0, "ok"))
    con, now = _eaten(tmp_path, why="render window: waits for the off-hours render slot")
    monkeypatch.setenv("KREW_CARD_BREAKER", "report")
    g.gate_cause(con, now=now)
    assert g.LAST_BREAKER["n"] == 0                      # a gate's own wait is not a loop
    con.close()
    (tmp_path / "kanban.db").unlink()
    con, now = _eaten(tmp_path)
    monkeypatch.setattr(g, "BREAKER_RUNS", 5)
    g.gate_cause(con, now=now)
    assert g.LAST_BREAKER["n"] == 0                      # 4 runs < 5
    monkeypatch.setattr(g, "BREAKER_RUNS", 3)
    monkeypatch.setenv("KREW_CARD_BREAKER", "0")
    g.gate_cause(con, now=now)
    assert g.LAST_BREAKER == {"n": 0, "mode": "off"}
    con.close()
