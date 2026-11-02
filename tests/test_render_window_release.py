"""The release branch of the render window (fix 2026-09-21, t_fc0b585c).

The 09-20 defect: `released` was built from the stale pre-unblock snapshot
(`c["status"]` captured by read_board() before the unblock call) plus the
unblock rc — so a window-held card (status=blocked) whose unblock failed was
dropped with no log line and never entered the slot.

The fix: unblock each fit card, then re-read its CURRENT status from a fresh
read_board() (read-only, same annotate() path). A card whose fresh status is
ready/todo/running OR whose unblock returned rc==0 goes into `released`. A
card still blocked (or absent) goes into `dropped`: logged with its id and
rc, written into state, and surfaced on the review card.

These tests drive tick() through the real release branch with a fake kanban
and a fake board that mutates on unblock (the unblock call is what would
change the DB between the two reads).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))
import krew_hours  # noqa: E402

spec = importlib.util.spec_from_file_location("render_window", ROOT / "crew" / "scripts" / "render-window.py")
rw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw)


def mk_card(tid, status="ready", *, held=False, last_block="", recipe="ltx"):
    c = {"id": tid, "title": f"{recipe} {tid}", "body": "", "status": status, "skills": "",
         "created_at": 1_000_000 - 10 * 3600.0, "block_kind": "", "comments": [],
         "last_block": last_block}
    if held:
        c["status"] = "blocked"
        c["comments"].append({"author": "render-window", "body": rw.MARK + ": held"})
    rw.annotate([c])
    return c


@pytest.fixture
def night(monkeypatch, tmp_path):
    """State/log/ticker paths -> temp; every fake card counts as a render card;
    the window spans the whole clock so tick() is deterministically inside it."""
    monkeypatch.setattr(rw, "STATE", tmp_path / "render-window.json")
    monkeypatch.setattr(rw, "TICK_STATE", tmp_path / "krew-window-tick.json")
    monkeypatch.setattr(rw, "LOG", tmp_path / "render-window.log")
    monkeypatch.setattr(rw, "_GATE", lambda *a: True)
    import datetime as _dt
    import time
    monkeypatch.setattr(time, "time", lambda: 2_000_000_000)
    # Real wall-clock frozen mid-window: left_min is computed from dt.datetime.now(),
    # which is NOT time.time() — freeze it too so the test is deterministic.
    import datetime as _dt
    import time
    from datetime import timedelta as _td
    # tick() reads the board (pre) and then, in the release branch, re-reads it
    # (post); the fake board below returns "before" on the first call and
    # "after" on the second, so the unblock mutation is visible to the fresh read.
    NOW = _dt.datetime(2026, 9, 21, 1, 0, 0)   # 01:00 inside 00:00-23:59 -> left ~1439 min
    class _FrozenDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(rw, "dt", type("mod", (), {"datetime": _FrozenDT}))
    import krew_hours as hh
    monkeypatch.setattr(hh, "off_hours", lambda site=None: dict(
        window="00:00-23:59", start=0, end=1439, source="init", why=""))
    return monkeypatch


def make_night(monkeypatch, tmp_path, pre_status, post_unblock_status, unblock_rc,
               review_until=0, ada_card="t_ada00000"):
    """Wire fakes. `pre_status`/`post_unblock_status` map card-id -> status for the
    board BEFORE the unblocks and AFTER them (the unblock call is what would
    mutate the DB). Returns (key, calls)."""
    # The key comes from the SAME frozen clock tick() reads (the `night` fixture's
    # rw.dt), never the real one: from the real clock it matched only on 09-21.
    key = krew_hours.started_on(rw.dt.datetime.now(), "00:00-23:59")
    (tmp_path / "krew-window-tick.json").write_text(json.dumps({
        "window_key": key,
        "fired": {j: {"status": "ok"} for j in rw.CHAIN_JOBS}}))
    (tmp_path / "render-window.json").write_text(json.dumps(
        {"window_key": key, "phase": "review", "review_until": review_until, "ada_card": ada_card}))

    board_reads = {"n": 0}
    def fake_read_board():
        board_reads["n"] += 1
        src = pre_status if board_reads["n"] == 1 else post_unblock_status
        return [mk_card(tid, src[tid], held=(src[tid] == "blocked")) for tid in src]

    calls = []
    def fake_kanban(*args):
        calls.append(args)
        if args[0] == "unblock":
            return (unblock_rc(args[1]), "ok")
        return (0, "ok")
    monkeypatch.setattr(rw, "read_board", fake_read_board)
    monkeypatch.setattr(rw, "kanban", fake_kanban)
    return key, calls


def test_held_card_unblock_fails_goes_to_dropped_not_released(monkeypatch, tmp_path, night):
    # 09-20 shape: card held by the window gate, unblock returns non-zero, board
    # still shows blocked after the unblock. Pre-fix it was silently dropped;
    # now it is VISIBLE: dropped in state, named in a log line, on the review card.
    pre = {"t_aaaaaaaa": "blocked"}
    post = {"t_aaaaaaaa": "blocked"}   # unblock failed -> still blocked
    key, calls = make_night(monkeypatch, tmp_path, pre, post,
                            unblock_rc=lambda cid: 1)
    r = rw.tick()
    assert r["action"] == "release"
    state = json.loads((tmp_path / "render-window.json").read_text())
    assert state["released"] == [], "a held card whose unblock failed and is still blocked is not released"
    assert state["dropped"] == ["t_aaaaaaaa"], "the drop is written into state so the ledger reflects it"
    # the review card is told exactly which card did not release
    ada_comment = next(c for c in calls if c[0] == "comment" and c[1] == "t_ada00000")
    assert "NOT released (unblock failed)" in ada_comment[2] and "t_aaaaaaaa" in ada_comment[2]


def test_held_card_unblock_succeeds_is_released(monkeypatch, tmp_path, night):
    # The unblock clears the hold: fresh read shows ready, rc==0 -> released.
    pre = {"t_aaaaaaaa": "blocked"}
    post = {"t_aaaaaaaa": "ready"}
    make_night(monkeypatch, tmp_path, pre, post, unblock_rc=lambda cid: 0)
    r = rw.tick()
    assert r["action"] == "release"
    state = json.loads((tmp_path / "render-window.json").read_text())
    assert state["released"] == ["t_aaaaaaaa"]
    assert "dropped" not in state


def test_ready_card_unblock_fails_but_fresh_read_ready_is_released(monkeypatch, tmp_path, night):
    # rc!=0 but the fresh read already shows ready (it was never held) -> released.
    pre = {"t_bbbbbbbb": "ready"}
    post = {"t_bbbbbbbb": "ready"}
    make_night(monkeypatch, tmp_path, pre, post, unblock_rc=lambda cid: 1)
    r = rw.tick()
    assert r["action"] == "release"
    state = json.loads((tmp_path / "render-window.json").read_text())
    assert state["released"] == ["t_bbbbbbbb"]
    assert "dropped" not in state


def test_held_card_running_after_unblock_is_released(monkeypatch, tmp_path, night):
    # rc!=0 but the fresh read shows running (the dispatcher already picked it up)
    # -> released, per the spec's (ready|todo|running) OR rc==0 rule.
    pre = {"t_cccccccc": "blocked"}
    post = {"t_cccccccc": "running"}
    make_night(monkeypatch, tmp_path, pre, post, unblock_rc=lambda cid: 1)
    r = rw.tick()
    assert r["action"] == "release"
    state = json.loads((tmp_path / "render-window.json").read_text())
    assert state["released"] == ["t_cccccccc"]
    assert "dropped" not in state


def test_card_absent_from_fresh_read_is_dropped_not_crash(monkeypatch, tmp_path, night):
    # The card vanishes from the board between the unblock and the re-read
    # (completed/archived) -> treated as not-released, logged, no crash.
    pre = {"t_dddddddd": "blocked"}
    make_night(monkeypatch, tmp_path, pre, {}, unblock_rc=lambda cid: 1)
    # tick() reads the board once at the top (pre) and once in the release
    # branch (post); the second read returns nothing: the card is gone.
    first = [True]
    def fake_read_board():
        if first[0]:
            first[0] = False
            return [mk_card("t_dddddddd", "blocked", held=True)]
        return []
    monkeypatch.setattr(rw, "read_board", fake_read_board)
    r = rw.tick()
    assert r["action"] == "release"
    state = json.loads((tmp_path / "render-window.json").read_text())
    assert state["released"] == []
    assert state["dropped"] == ["t_dddddddd"], "an absent card is dropped, not crashed"


def test_mixed_batch_released_and_dropped_both_surfaced(monkeypatch, tmp_path, night):
    pre = {"t_aaaaaaaa": "blocked", "t_bbbbbbbb": "ready"}
    post = {"t_aaaaaaaa": "blocked", "t_bbbbbbbb": "ready"}
    key, calls = make_night(monkeypatch, tmp_path, pre, post,
                            unblock_rc=lambda cid: 1 if cid == "t_aaaaaaaa" else 0)
    r = rw.tick()
    assert r["action"] == "release"
    state = json.loads((tmp_path / "render-window.json").read_text())
    assert state["released"] == ["t_bbbbbbbb"]
    assert state["dropped"] == ["t_aaaaaaaa"]
    ada_comment = next(c for c in calls if c[0] == "comment" and c[1] == "t_ada00000")
    assert "t_bbbbbbbb" in ada_comment[2] and "NOT released (unblock failed): t_aaaaaaaa" in ada_comment[2]


def test_a_clean_release_clears_an_earlier_drop(monkeypatch, tmp_path, night):
    # Review REVISE on t_fc0b585c: `dropped` was written only when non-empty, so
    # a drop recorded earlier survived a clean release into the next summary.
    pre = {"t_aaaaaaaa": "blocked"}
    post = {"t_aaaaaaaa": "ready"}
    make_night(monkeypatch, tmp_path, pre, post, unblock_rc=lambda cid: 0)
    sf = tmp_path / "render-window.json"
    st = json.loads(sf.read_text())
    st["dropped"] = ["t_0ld00000"]
    sf.write_text(json.dumps(st))
    assert rw.tick()["action"] == "release"
    state = json.loads(sf.read_text())
    assert state["released"] == ["t_aaaaaaaa"]
    assert "dropped" not in state
