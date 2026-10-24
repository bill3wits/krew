"""lib/kanban_kpis — one assertion per KPI on a fixture board (Phase 0 of KANBAN-SELF-HEALING-20260918)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import kanban_kpis as kk  # noqa: E402

DAY = 86400.0
NOW = 1_790_000_000.0
SINCE = NOW - DAY


def _board(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "kanban.db")
    con.executescript("""
    CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT, status TEXT, assignee TEXT, block_kind TEXT,
        created_by TEXT, created_at INTEGER, completed_at INTEGER, result TEXT, idempotency_key TEXT);
    CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, created_at INTEGER);
    CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
    CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id TEXT, profile TEXT, status TEXT, outcome TEXT,
        started_at INTEGER, ended_at INTEGER, error TEXT);
    CREATE TABLE task_links (parent_id TEXT, child_id TEXT);
    """)
    return con


def _task(con, tid, title, status, *, created_by="user", created_at=SINCE + 100, completed_at=None, result=None):
    con.execute("INSERT INTO tasks (id,title,body,status,assignee,block_kind,created_by,created_at,completed_at,result) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", (tid, title, "", status, "bash", None, created_by, created_at, completed_at, result))


def _c(con, tid, author, body, at=SINCE + 200):
    con.execute("INSERT INTO task_comments (task_id,author,body,created_at) VALUES (?,?,?,?)", (tid, author, body, at))


def _e(con, tid, kind, at=SINCE + 150, payload=""):
    con.execute("INSERT INTO task_events (task_id,kind,payload,created_at) VALUES (?,?,?,?)", (tid, kind, payload, at))


def _run(con, tid, outcome, ended=SINCE + 300):
    con.execute("INSERT INTO task_runs (task_id,profile,status,outcome,started_at,ended_at) VALUES (?,?,?,?,?,?)",
                (tid, "bash", outcome, outcome, ended - 60, ended))


def test_births_finishes_and_who_minted(tmp_path):
    con = _board(tmp_path)
    _task(con, "t_00000001", "Implement x", "done", created_by="user", completed_at=SINCE + 500)
    _task(con, "t_00000002", "reframe: Implement x", "todo", created_by="kanban-loop")
    _task(con, "t_00000003", "Verify y", "ready", created_by="auto-decomposer")
    _task(con, "t_00000004", "old card", "done", created_at=SINCE - DAY, completed_at=SINCE - DAY + 10)
    k = kk.compute(con, SINCE, NOW)
    assert (k["kanban_minted"], k["kanban_completed"]) == (3, 1)
    assert k["kanban_mint_ratio"] == 3.0
    assert k["kanban_mechanical_share"] == round(2 / 3, 3)


def test_runs_ending_in_nothing_is_a_share_of_runs_that_ended_in_the_window(tmp_path):
    con = _board(tmp_path)
    _task(con, "t_00000001", "a", "ready")
    for o in ("completed", "completed", "crashed", "timed_out"):
        _run(con, "t_00000001", o)
    _run(con, "t_00000001", "crashed", ended=SINCE - 10)   # yesterday, not counted
    assert kk.compute(con, SINCE, NOW)["kanban_runs_nothing_share"] == 0.5


def test_reframe_cuts_mechanical_share_linked_share_and_chain_depth(tmp_path):
    con = _board(tmp_path)
    for i in range(1, 5):
        _task(con, f"t_0000000{i}", f"card {i}", "blocked")
    # chain 1 → 2 → 3 (depth 2); 4 → 1 is a separate edge into the chain (depth 3 from 4)
    _c(con, "t_00000001", "kanban-loop", "reframe-cut: t_00000002 (gen 1, frame by mechanical)")
    _c(con, "t_00000002", "kanban-loop", "reframe-cut: t_00000003 (gen 2, frame by model)")
    _c(con, "t_00000004", "kanban-loop", "reframe-cut: t_00000001 (gen 1, frame by worker)", at=SINCE - 10)  # old, not in window
    con.execute("INSERT INTO task_links VALUES ('t_00000003','t_00000002')")   # Phase 1 shape: cut is the parent
    k = kk.compute(con, SINCE, NOW)
    assert k["kanban_reframe_cuts"] == 2
    assert k["kanban_reframe_mechanical_share"] == 0.5
    assert k["kanban_reframe_linked_share"] == 0.5
    assert k["kanban_chain_depth_max"] == 3


def test_close_time_splits_reframed_from_plain_by_first_failure(tmp_path):
    con = _board(tmp_path)
    _task(con, "t_00000001", "plain", "done", completed_at=SINCE + 3600 * 2)
    _e(con, "t_00000001", "crashed", at=SINCE + 3600)                       # 1 h
    _task(con, "t_00000002", "reframed", "done", completed_at=SINCE + 3600 * 11)
    _e(con, "t_00000002", "gave_up", at=SINCE + 3600)                       # 10 h
    _c(con, "t_00000002", "kanban-loop", "reframe-cut: t_00000003 (gen 1, frame by model)")
    _task(con, "t_00000003", "reframe: reframed", "done", completed_at=SINCE + 3600 * 6, created_by="kanban-loop")
    _e(con, "t_00000003", "timed_out", at=SINCE + 3600 * 3)                 # 3 h, a frame by title
    _task(con, "t_00000004", "never failed", "done", completed_at=SINCE + 100)   # no failure → not in either
    k = kk.compute(con, SINCE, NOW)
    assert k["kanban_plain_close_h"] == 1.0
    assert k["kanban_reframed_close_h"] == 6.5    # median of 10 h and 3 h


def test_frames_open_and_frames_run_as_work(tmp_path):
    con = _board(tmp_path)
    _task(con, "t_00000001", "reframe: a", "todo", created_by="kanban-loop")
    _task(con, "t_00000002", "reframe: b", "done", created_by="kanban-loop", completed_at=SINCE + 500, result="")
    _task(con, "t_00000003", "reframe: c", "done", created_by="kanban-loop", completed_at=SINCE + 500,
          result="children done — frame is not work")
    k = kk.compute(con, SINCE, NOW)
    assert k["kanban_frames_open"] == 1
    assert k["kanban_frames_run_as_work"] == 1


def test_liveness_counts_come_from_the_gate_file_and_leaves_skip_parks_and_held_children(tmp_path):
    con = _board(tmp_path)
    _task(con, "t_00000001", "leaf", "todo", created_at=NOW - 10 * 3600)
    _task(con, "t_00000002", "park", "scheduled", created_at=NOW - 100 * 3600)          # a park is not a leaf
    _task(con, "t_00000003", "frame", "todo", created_at=NOW - 50 * 3600)              # waits on 1 → not a leaf
    con.execute("INSERT INTO task_links VALUES ('t_00000001','t_00000003')")
    k = kk.compute(con, SINCE, NOW, liveness={"none": ["t_00000002"], "overdue": ["t_00000001", "t_00000003"]})
    assert (k["kanban_no_reader"], k["kanban_overdue"]) == (1, 2)
    assert k["kanban_oldest_leaf_h"] == 10.0


def test_a_park_without_a_wake_token_is_counted(tmp_path):
    con = _board(tmp_path)
    for i, reason in enumerate(["park — duplicate of last week", "off-hours render window: window:render",
                                "park ruled by zoey", "later 2026-10-01 — dep park"], 1):
        _task(con, f"t_0000000{i}", f"park {i}", "scheduled")
        _e(con, f"t_0000000{i}", "scheduled", payload='{"reason": "%s"}' % reason)
    _c(con, "t_00000003", "zoey", "until:t_00000009 then try")     # a ruler's wake on the comment counts
    assert kk.compute(con, SINCE, NOW)["kanban_parks_no_wake"] == 1


def test_salad_causes_verbs_and_fix_cards_are_counted_in_the_window(tmp_path):
    con = _board(tmp_path)
    _task(con, "t_00000001", "a", "blocked")
    _c(con, "t_00000001", "kanban-board-gate", "salad-park: garbage ratio 0.2")
    _c(con, "t_00000001", "kanban-loop", "cause: harness — iteration budget 80/80 (run 3)")
    _c(con, "t_00000001", "kanban-loop", "cause: verb — coherent rc0, 14 tool calls (run 4)")
    _c(con, "t_00000001", "kanban-loop", "cause: real — same error x3 at terminal (run 5)")
    _c(con, "t_00000001", "kanban-board-gate", "gate: routed → archive")
    _c(con, "t_00000001", "kanban-board-gate", "gate: routed → park", at=SINCE - 5)   # yesterday
    _task(con, "t_00000002", "fix: the thing Vera found", "ready", created_by="vera")
    k = kk.compute(con, SINCE, NOW)
    assert k["kanban_salad_sessions"] == 1
    assert (k["kanban_cause_harness"], k["kanban_cause_verb"], k["kanban_cause_real"]) == (1, 1, 1)
    assert k["kanban_verbs_landed"] == 1
    assert k["kanban_fix_cards"] == 1


def test_duplicate_open_titles_are_counted_as_extra_copies(tmp_path):
    con = _board(tmp_path)
    _task(con, "t_00000001", "Implement terminal state validation in worker lifecycle", "blocked")
    _task(con, "t_00000002", "reframe: Implement terminal state validation in worker lifecycle", "todo")
    _task(con, "t_00000003", "Implement terminal-state validation in worker lifecycle!", "scheduled")
    _task(con, "t_00000004", "Implement terminal state validation in worker lifecycle", "done")   # closed, not counted
    _task(con, "t_00000005", "x", "todo")                                                          # too short to key
    _task(con, "t_00000006", "x", "todo")
    assert kk.compute(con, SINCE, NOW)["kanban_dup_open"] == 2


def test_a_thin_board_never_raises(tmp_path):
    con = sqlite3.connect(tmp_path / "thin.db")
    con.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at INTEGER, completed_at INTEGER)")
    con.execute("INSERT INTO tasks VALUES ('t_00000001','only','todo',?,NULL)", (SINCE + 1,))
    k = kk.compute(con, SINCE, NOW)
    assert k["kanban_minted"] == 1 and k["kanban_reframe_cuts"] is None
    assert set(k) == set(kk.KPIS)
    assert "+1 / −0" in kk.headline(k)


def test_every_kpi_has_a_direction_the_registry_accepts():
    for kid, (name, unit, direction, _t) in kk.KPIS.items():
        assert kid.startswith("kanban_") and name and unit and direction in ("up", "down")


def test_user_touches_counts_distinct_cards_across_all_four_channels(tmp_path):
    """The one row this whole effort is for: a card the board could not move without a human.
    One long thread is ONE touch, and the three author channels all count (2026-09-22:
    `dashboard` alone read 2 on a day 30 cards carried his word)."""
    con = _board(tmp_path)
    for tid in ("t_a0000001", "t_a0000002", "t_a0000003", "t_a0000004", "t_a0000005"):
        _task(con, tid, "work", "blocked", created_by="ada")
    _c(con, "t_a0000001", "dashboard", "land that upgrade")          # he typed it himself
    _c(con, "t_a0000001", "dashboard", "and study why")              # same card, still one touch
    _c(con, "t_a0000002", "user", "done — cancelled on the User's word")   # a session relaying him
    _c(con, "t_a0000003", "default", "UNBLOCK: user ruling: verified live")  # the bare CLI
    _c(con, "t_a0000004", "zoey", "hands — this one needs the room")  # a ruler handed it over
    con.execute("UPDATE tasks SET block_kind='capability' WHERE id='t_a0000005'")
    _e(con, "t_a0000005", "blocked", payload="needs sudo to install the unit")
    con.commit()
    k = kk.compute(con, SINCE, NOW)
    assert k["kanban_user_touches"] == 5


def test_user_touches_ignores_the_krew_talking_to_itself(tmp_path):
    con = _board(tmp_path)
    _task(con, "t_b0000001", "work", "blocked", created_by="ada")
    _task(con, "t_b0000002", "work", "blocked", created_by="ada")
    _c(con, "t_b0000001", "kanban-loop", "hold: frame waits on children")
    _c(con, "t_b0000001", "ada", "routed: 1 ownerless block")
    _c(con, "t_b0000001", "steward", "VERB: archive")
    # a ruler word that is NOT `hands` is the board ruling itself, not the User being pulled in
    _c(con, "t_b0000002", "zoey", "archive — superseded")
    con.commit()
    assert kk.compute(con, SINCE, NOW)["kanban_user_touches"] == 0


def test_a_needs_human_title_is_a_touch_even_with_no_comment(tmp_path):
    """t_bab4d0f4 on 2026-09-21: a card filed AT the User. It reached him by existing."""
    con = _board(tmp_path)
    _task(con, "t_c0000001", "NEEDS HUMAN: Comfy v0.37.0 smoke — render window won't open", "blocked")
    _task(con, "t_c0000002", "Verify worker death cause via journalctl", "blocked")
    con.commit()
    assert kk.compute(con, SINCE, NOW)["kanban_user_touches"] == 1


def test_a_capability_block_that_is_only_a_misroute_is_not_the_users(tmp_path):
    """lib/krew_daily.py:395 — 4 of 4 capability blocks on 09-17 were the WRONG ROLE for the
    tool, which is a re-route for Ada. Only root / a password / hands is his."""
    con = _board(tmp_path)
    _task(con, "t_d0000001", "Read the frame", "blocked")
    con.execute("UPDATE tasks SET block_kind='capability' WHERE id='t_d0000001'")
    _e(con, "t_d0000001", "blocked", payload="no terminal in this profile's sandbox")
    con.commit()
    assert kk.compute(con, SINCE, NOW)["kanban_user_touches"] == 0


def test_a_recurring_vehicle_card_is_not_the_board_spending_itself_on_itself(tmp_path):
    """The hourly steward sweep carries its whole policy in the card body: a worker runs it and
    the card is archived. Archiving is its correct ending. 135 of them in 4 days were counted as
    waste, against a target they made unreachable, pointing the reader at the decomposer — which
    completes at the same rate as the User's own cards."""
    con = _board(tmp_path)
    for i in range(5):                                   # the vehicle: repeats, never completes
        _task(con, f"t_v{i:07d}", "steward sweep: blocked cards + stuck-card diagnostics",
              "archived", created_by="steward-cron")
    for i in range(3):                                   # real machinery-born work
        _task(con, f"t_m{i:07d}", f"Implement thing {i}", "done",
              created_by="auto-decomposer", completed_at=SINCE + 500)
    _task(con, "t_h0000001", "A human idea", "done", created_by="user", completed_at=SINCE + 500)
    con.commit()
    k = kk.compute(con, SINCE, NOW)
    assert k["kanban_vehicle_births"] == 5
    assert k["kanban_mechanical_share"] == 0.75         # 3 of 4 REAL births, vehicles excluded


def test_a_repeating_card_that_actually_completes_is_not_a_vehicle(tmp_path):
    """`route: 1 ownerless block` repeats 22 times in 4 days and is the gate doing real
    routing. Repetition alone is not the test — it also has to essentially never finish."""
    con = _board(tmp_path)
    for i in range(5):
        _task(con, f"t_r{i:07d}", "route: 1 ownerless block", "done",
              created_by="kanban-board-gate", completed_at=SINCE + 500)
    con.commit()
    assert kk.compute(con, SINCE, NOW)["kanban_vehicle_births"] == 0


def test_respawn_share_says_whether_the_day_was_representative(tmp_path):
    """The User, 2026-09-22: "most of that was caused by dev restarts with a packed kanban."
    A restart re-spawns everything in flight, so births inflate for a reason that has nothing
    to do with board health. krew-tool-count learned this in prose; this is the number."""
    con = _board(tmp_path)
    _task(con, "t_00000001", "x", "running")
    for _ in range(3):
        _e(con, "t_00000001", "spawned")
    _e(con, "t_00000002", "spawned")
    con.commit()
    assert kk.compute(con, SINCE, NOW)["kanban_respawn_share"] == 0.5    # 2 re-runs of 4 spawns


def test_the_mechanical_share_carries_no_target(tmp_path):
    """Decomposition IS the design; a target nobody can meet trains people to ignore the page.
    kanban_mint_ratio is the health metric here."""
    assert kk.KPIS["kanban_mechanical_share"][3] is None
    assert kk.KPIS["kanban_mint_ratio"][3] == 1.5
