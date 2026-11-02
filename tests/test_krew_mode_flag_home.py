"""The flag's way home, and the occupancy stamp the sweep could not touch.

2026-09-20 is the shape these guard: a Render entered by hand stamped the hold
`# card=occupancy`, both sweep gates skipped that name, and `_start_freed` will
not start ops or Crissa while the flag reads render — so the flag stayed latched
and those two stayed down through 58 no-op ticks. No GPU, no systemd: every
call that would touch the live house is patched here, per-test, on purpose.
"""
from __future__ import annotations

import json
import time
import pathlib
import pytest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import krew_mode_sandbox  # noqa: E402
hm = krew_mode_sandbox.load("krew_mode_flag_home", "crew/scripts/krew-mode")   # never the passwd tree


# The autouse fixture below stubs `_render_pending_cards` for every test in this module.
# The pending-ghost tests at the bottom are ABOUT that function, so they need the real
# one — captured here at import, before any fixture runs.
REAL_RENDER_PENDING = hm._render_pending_cards


@pytest.fixture(autouse=True)
def _never_the_live_house(monkeypatch, tmp_path):
    """Own mocks (CLAUDE.md): nothing here reads or writes the real house."""
    monkeypatch.setattr(hm, "CREW_HOLD", tmp_path / "krew-train-hold")
    monkeypatch.setattr(hm, "FLAG_HOME_STATE", tmp_path / "flag-home.json")
    monkeypatch.setattr(hm, "read_pending", lambda *a, **k: False)
    monkeypatch.setattr(hm, "beast_park_active", lambda *a, **k: False)
    monkeypatch.setattr(hm, "off_park_active", lambda *a, **k: False)
    monkeypatch.setattr(hm, "_render_pending_cards", lambda *a, **k: set())
    monkeypatch.setattr(hm, "comfy_up", lambda *a, **k: False)
    monkeypatch.setattr(hm, "_hold_cards", lambda *a, **k: [])
    monkeypatch.setattr(hm, "SWEEP_FLAG_HOME", True)
    monkeypatch.setattr(hm, "SWEEP_OCCUPANCY", True)
    monkeypatch.setattr(hm, "FLAG_HOME_TICKS", 2)


# --- the occupancy stamp is not a card and not a human ----------------------

def test_occupancy_hold_is_live_while_the_flag_says_render():
    live, why = hm.occupancy_hold_live(pending=set(), mode="render", comfy=False)
    assert live is True and "render" in why


def test_occupancy_hold_is_live_while_a_take_is_on_comfy():
    live, _ = hm.occupancy_hold_live(pending={"t_abc"}, mode="normal", comfy=False)
    assert live is True


def test_occupancy_hold_is_live_while_comfy_serves():
    live, why = hm.occupancy_hold_live(pending=set(), mode="normal", comfy=True)
    assert live is True and "Comfy" in why


def test_occupancy_hold_is_stale_once_the_render_is_over():
    """THE 2026-09-20 CASE: flag already normal, nothing rendering, hold still on."""
    live, why = hm.occupancy_hold_live(pending=set(), mode="normal", comfy=False)
    assert live is False
    assert "no render pending" in why


def test_sweep_no_longer_skips_the_occupancy_stamp_by_name(monkeypatch):
    monkeypatch.setattr(hm, "_hold_cards", lambda *a, **k: [hm.OCCUPANCY_CARD])
    cards = [c for c in hm._hold_cards() if c and (c != hm.OCCUPANCY_CARD or hm.SWEEP_OCCUPANCY)]
    assert cards == [hm.OCCUPANCY_CARD]
    monkeypatch.setattr(hm, "SWEEP_OCCUPANCY", False)          # the kill switch restores the old skip
    cards = [c for c in hm._hold_cards() if c and (c != hm.OCCUPANCY_CARD or hm.SWEEP_OCCUPANCY)]
    assert cards == []


# --- the flag's way home ----------------------------------------------------

def test_flag_home_due_when_the_flag_is_latched(monkeypatch):
    monkeypatch.setattr(hm, "read_mode", lambda *a, **k: "render")
    due, why = hm.flag_home_due(pending=set(), comfy=False)
    assert due is True and "Comfy idle" in why


@pytest.mark.parametrize("mode", ["normal", "standard", "beast"])
def test_flag_home_never_fires_when_the_flag_is_not_render(monkeypatch, mode):
    monkeypatch.setattr(hm, "read_mode", lambda *a, **k: mode)
    assert hm.flag_home_due(pending=set(), comfy=False)[0] is False


def test_flag_home_holds_off_while_a_take_is_still_on_comfy(monkeypatch):
    monkeypatch.setattr(hm, "read_mode", lambda *a, **k: "render")
    assert hm.flag_home_due(pending={"t_abc"}, comfy=False)[0] is False
    # The `comfy` ARGUMENT is liveness, and liveness is what latched the flag for three
    # hours on 09-21, so this gate deliberately ignores it and asks the queue itself.
    # (This assertion used to pass `comfy=True` and expect a hold-off; it had been stale
    # since that fix landed and was failing at HEAD — corrected 2026-09-22.)
    monkeypatch.setattr(hm, "comfy_busy", lambda *a, **k: True)
    assert hm.flag_home_due(pending=set(), comfy=False)[0] is False
    monkeypatch.setattr(hm, "comfy_busy", lambda *a, **k: False)
    assert hm.flag_home_due(pending=set(), comfy=True)[0] is True


def test_flag_home_holds_off_for_a_live_card_hold(monkeypatch):
    monkeypatch.setattr(hm, "read_mode", lambda *a, **k: "render")
    monkeypatch.setattr(hm, "_hold_cards", lambda *a, **k: ["t_live"])
    monkeypatch.setattr(hm, "hold_card_live", lambda *a, **k: (True, "running worker alive"))
    due, why = hm.flag_home_due(pending=set(), comfy=False)
    assert due is False and "t_live" in why


def test_flag_home_holds_off_mid_switch_and_under_a_beast_park(monkeypatch):
    monkeypatch.setattr(hm, "read_mode", lambda *a, **k: "render")
    monkeypatch.setattr(hm, "read_pending", lambda *a, **k: True)
    assert hm.flag_home_due(pending=set(), comfy=False)[0] is False
    monkeypatch.setattr(hm, "read_pending", lambda *a, **k: False)
    monkeypatch.setattr(hm, "beast_park_active", lambda *a, **k: True)
    assert hm.flag_home_due(pending=set(), comfy=False)[0] is False


def test_flag_home_kill_switch(monkeypatch):
    monkeypatch.setattr(hm, "read_mode", lambda *a, **k: "render")
    monkeypatch.setattr(hm, "SWEEP_FLAG_HOME", False)
    assert hm.flag_home_due(pending=set(), comfy=False) == (False, "KREW_SWEEP_FLAG_HOME=0")


# --- it takes two ticks, and it launches the real restore -------------------

def test_way_home_waits_one_tick_then_launches_krew_mode_normal(monkeypatch):
    monkeypatch.setattr(hm, "read_mode", lambda *a, **k: "render")
    calls = []
    monkeypatch.setattr(hm.subprocess, "run",
                        lambda cmd, *a, **k: calls.append([str(c) for c in cmd])
                        or type("R", (), {"returncode": 0, "stderr": ""})())

    assert hm.maybe_bring_flag_home(pending=set(), comfy=False) is False     # tick 1: arm
    assert calls == []
    assert json.loads(hm.FLAG_HOME_STATE.read_text())["ticks"] == 1

    assert hm.maybe_bring_flag_home(pending=set(), comfy=False) is True      # tick 2: fire
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "systemd-run" and argv[-1] == "normal"
    assert "krew-mode" in argv[-2]
    assert not hm.FLAG_HOME_STATE.exists()


def test_a_render_starting_up_resets_the_counter(monkeypatch):
    """Armed on one tick, a take lands on Comfy the next: never fire."""
    monkeypatch.setattr(hm, "read_mode", lambda *a, **k: "render")
    calls = []
    monkeypatch.setattr(hm.subprocess, "run",
                        lambda cmd, *a, **k: calls.append(cmd)
                        or type("R", (), {"returncode": 0, "stderr": ""})())
    assert hm.maybe_bring_flag_home(pending=set(), comfy=False) is False
    assert hm.maybe_bring_flag_home(pending={"t_abc"}, comfy=False) is False   # gate closed
    assert not hm.FLAG_HOME_STATE.exists()
    assert hm.maybe_bring_flag_home(pending=set(), comfy=False) is False       # back to tick 1
    assert calls == []


# --- "unknown" must not be a permanent latch (2026-09-22) -------------------------------------

def _fake_queue(monkeypatch, *, up=True, raises=True, body=b'{"queue_running":[],"queue_pending":[]}'):
    monkeypatch.setattr(hm, "comfy_up", lambda *a, **k: up)

    class _R:
        status = 200
        def read(self): return body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _open(*a, **k):
        if raises:
            raise OSError("connection reset")
        return _R()
    monkeypatch.setattr(hm.urllib.request, "urlopen", _open)


def test_an_unreadable_queue_is_busy_at_first(tmp_path, monkeypatch):
    """A blip must not end someone's render: unknown is busy."""
    monkeypatch.setattr(hm, "COMFY_UNKNOWN_STATE", tmp_path / "unknown.json")
    _fake_queue(monkeypatch)
    assert hm.comfy_busy() is True
    assert (tmp_path / "unknown.json").is_file()          # the clock started


def test_an_unreadable_queue_stops_being_busy_once_it_is_just_broken(tmp_path, monkeypatch):
    """A Comfy alive on the socket and too sick to answer /queue held the flag in Render
    forever, with no owner and no alarm. After COMFY_UNKNOWN_MAX_S it is not a render."""
    state = tmp_path / "unknown.json"
    monkeypatch.setattr(hm, "COMFY_UNKNOWN_STATE", state)
    monkeypatch.setattr(hm, "COMFY_UNKNOWN_MAX_S", 900.0)
    _fake_queue(monkeypatch)
    assert hm.comfy_busy() is True
    state.write_text(json.dumps({"since": time.time() - 901}))
    assert hm.comfy_busy() is False


def test_a_readable_queue_clears_the_unknown_clock(tmp_path, monkeypatch):
    state = tmp_path / "unknown.json"
    state.write_text(json.dumps({"since": time.time() - 5000}))
    monkeypatch.setattr(hm, "COMFY_UNKNOWN_STATE", state)
    _fake_queue(monkeypatch, raises=False)
    assert hm.comfy_busy() is False
    assert not state.exists()


def test_a_dead_comfy_clears_the_clock_and_is_never_busy(tmp_path, monkeypatch):
    state = tmp_path / "unknown.json"
    state.write_text(json.dumps({"since": time.time() - 5000}))
    monkeypatch.setattr(hm, "COMFY_UNKNOWN_STATE", state)
    monkeypatch.setattr(hm, "comfy_up", lambda *a, **k: False)
    assert hm.comfy_busy() is False
    assert not state.exists()


def test_an_unwritable_clock_stays_busy_rather_than_inventing_an_expiry(tmp_path, monkeypatch):
    """The fix must not become the bug: if the state file cannot be written we cannot know
    how long unknown has lasted, so we stay on the safe side and say so."""
    monkeypatch.setattr(hm, "COMFY_UNKNOWN_STATE", tmp_path / "nope" / "unknown.json")
    monkeypatch.setattr(pathlib.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    _fake_queue(monkeypatch)
    assert hm.comfy_busy() is True


# --- a pending render on a card nobody will collect (2026-09-22) -------------------------------

def _jobs(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(hm, "HOUSE", tmp_path)
    (tmp_path / "render-jobs.json").write_text(json.dumps(rows))
    return tmp_path / "render-jobs.json"


def test_a_pending_render_on_an_archived_card_is_a_ghost(tmp_path, monkeypatch):
    """ONE row is enough to pin the whole house in Render. Measured: t_6e948919 was archived
    on 09-19 and its pending row made flag_home_due say "render pending" on every tick for
    days — ops and Crissa's gateway down, Ada capped at the render 1/1, one card at a time."""
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3 * 86400))
    path = _jobs(tmp_path, monkeypatch, [
        {"card": "t_dead", "status": "pending", "prompt_id": "abc", "take": 2, "submitted": old},
        {"card": "t_live", "status": "pending", "prompt_id": "def", "take": 1, "submitted": old},
    ])
    monkeypatch.setattr(hm, "card_status",
                        lambda c: {"t_dead": "archived", "t_live": "running"}.get(c))
    monkeypatch.setattr(hm, "_hold_log", lambda *a, **k: None)
    assert REAL_RENDER_PENDING() == {"t_live"}
    rows = json.loads(path.read_text())
    assert rows[0]["status"] == "failed" and "archived" in rows[0]["error"]
    assert rows[1]["status"] == "pending"          # the live one is untouched


def test_every_terminal_state_retires_the_row_and_a_dry_run_writes_nothing(tmp_path, monkeypatch):
    for state in ("done", "archived", "cancelled"):
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3 * 86400))
        path = _jobs(tmp_path, monkeypatch,
                     [{"card": "t_x", "status": "pending", "prompt_id": "p", "take": 1,
                       "submitted": old}])
        monkeypatch.setattr(hm, "card_status", lambda c, s=state: s)
        monkeypatch.setattr(hm, "_hold_log", lambda *a, **k: None)
        assert REAL_RENDER_PENDING(dry_run=True) == set()
        assert json.loads(path.read_text())[0]["status"] == "pending"   # dry run: no write
        assert REAL_RENDER_PENDING() == set()
        assert json.loads(path.read_text())[0]["status"] == "failed"


def test_an_unknown_card_keeps_its_render(tmp_path, monkeypatch):
    """card_status None means no board knows it. The 2026-09-14 rail: unknown is not a
    licence to end someone's render — only an explicitly terminal card retires the row."""
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3 * 86400))
    path = _jobs(tmp_path, monkeypatch,
                 [{"card": "t_ghosty", "status": "pending", "prompt_id": "p", "take": 1,
                   "submitted": old}])
    monkeypatch.setattr(hm, "card_status", lambda c: None)
    monkeypatch.setattr(hm, "_hold_log", lambda *a, **k: None)
    assert REAL_RENDER_PENDING() == {"t_ghosty"}
    assert json.loads(path.read_text())[0]["status"] == "pending"


def test_a_terminal_card_does_NOT_retire_a_young_render(tmp_path, monkeypatch):
    """The house's own test taught me this one. krew-board-carry re-mints an open card under
    a new id and archives the old one while its take keeps rendering — so `terminal` alone is
    not a ghost. A real render is minutes to hours old; the row that latched the house on
    2026-09-22 was three days old. Both conditions, or the hold is pulled out from under live
    GPU work (tests/test_krew_mode.py::test_carried_archived_card_with_render_on_comfy_keeps_hold)."""
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 120))
    path = _jobs(tmp_path, monkeypatch,
                 [{"card": "t_carried", "status": "pending", "prompt_id": "6abb", "take": 3,
                   "submitted": fresh}])
    monkeypatch.setattr(hm, "card_status", lambda c: "archived")
    monkeypatch.setattr(hm, "_hold_log", lambda *a, **k: None)
    assert REAL_RENDER_PENDING() == {"t_carried"}
    assert json.loads(path.read_text())[0]["status"] == "pending"


def test_a_row_with_no_submitted_stamp_is_kept(tmp_path, monkeypatch):
    """No stamp means no age, and no age means no evidence it is stale. Keep it."""
    path = _jobs(tmp_path, monkeypatch,
                 [{"card": "t_nostamp", "status": "pending", "prompt_id": "p", "take": 1}])
    monkeypatch.setattr(hm, "card_status", lambda c: "archived")
    monkeypatch.setattr(hm, "_hold_log", lambda *a, **k: None)
    assert REAL_RENDER_PENDING() == {"t_nostamp"}
    assert json.loads(path.read_text())[0]["status"] == "pending"
