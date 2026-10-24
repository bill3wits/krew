"""The morning restart-window report (the User 2026-10-01): restarted / FAILED / held / not restarted per unit,
still-owed from the window's own facts, busy_now holds counted. Read-only."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rwmr", ROOT / "window" / "scripts" / "restart-window-morning-report.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
SINCE = dt.datetime(2026, 10, 1, 19, 55)
LOG = """2026-10-01 21:03:00 user busy: the ear was in a turn 12 s ago (quiet for 90 s needed)
2026-10-01 21:08:00 user busy: the ear was in a turn 40 s ago (quiet for 90 s needed)
2026-10-01 23:40:02 rail-align.service: OK rail-align restarted · running
2026-10-01 23:41:10 rail-camera.service: FAILED did not come back on :8192
- deferred krew-conductor.service: the User is mid-conversation
"""


def test_each_state_is_named():
    enter = {"rail-align": dt.datetime(2026, 10, 1, 23, 40), "rail-camera": dt.datetime(2026, 10, 1, 23, 41),
             "krew-conductor": dt.datetime(2026, 9, 30, 4, 28), "door-stamp": dt.datetime(2026, 9, 30, 4, 28)}
    owed = {"krew-conductor": "1 env package(s) newer than the process", "door-stamp": "x"}
    rows = {r["unit"]: r for r in m.classify(list(enter), SINCE, LOG, enter, owed)}
    assert rows["rail-align"]["state"] == "restarted 23:40 (executor OK)" and rows["rail-align"]["still_owed"] == ""
    assert rows["rail-camera"]["state"].startswith("FAILED 23:41")
    assert rows["krew-conductor"]["state"] == "held: the User is mid-conversation"
    assert rows["door-stamp"]["state"] == "not restarted" and rows["door-stamp"]["still_owed"] == "x"


def test_holds_are_counted_and_the_render_says_unknown_owed():
    h = m.holds(LOG)
    assert sum(h.values()) == 2 and len(h) == 1
    text = m.render("2026-10-02", SINCE, [{"unit": "x", "state": "not restarted", "still_owed": None}], h, False)
    assert "| x | not restarted | ? |" in text and "2x the ear was in a turn N s ago" in text


def test_confirm_clear_says_clear_and_names_overnight_restarts():
    text = m.confirm_clear(SINCE, LOG, {}, {}, True)
    assert "CLEAR - nothing owed" in text and "removed now - the window is back on" in text
    assert "rail-align OK 23:40" in text and "rail-camera FAILED 23:41" in text
    text = m.confirm_clear(SINCE, "", {"ops": "x"}, {"ops.service": {}}, False)
    assert "1 owed: ops (x)" in text and "1 row(s): ops.service" in text and "restarts by the window" in text


def test_no_units_named_reads_the_window_s_owed_list(tmp_path, monkeypatch):
    """krew: the house named its night's thirteen units in the script; krew reads KREW_MORNING_REPORT_UNITS, else
    the window's own owed list."""
    monkeypatch.setattr(m, "OUT", tmp_path / "out")
    monkeypatch.setattr(m, "journal", lambda since: LOG)
    monkeypatch.setattr(m, "still_owed", lambda: {"rail-align": "x"})
    monkeypatch.setattr(m, "active_enter", lambda u: dt.datetime(2026, 10, 1, 23, 40))
    assert m.main(["--since", "2026-10-01 19:55", "--units", ""]) == 0
    assert "| rail-align | restarted 23:40 (executor OK) | x |" in (tmp_path / "out" / "LATEST.md").read_text()


def test_confirm_clear_puts_the_window_back_on(tmp_path, monkeypatch):
    flag = tmp_path / "restart-window.off"
    flag.write_text("off for the night")
    monkeypatch.setattr(m, "OFF_FLAG", flag)
    monkeypatch.setattr(m, "OWED_FILE", tmp_path / "owed.json")
    monkeypatch.setattr(m, "OUT", tmp_path / "out")
    monkeypatch.setattr(m, "journal", lambda since: "")
    monkeypatch.setattr(m, "still_owed", lambda: {})
    assert m.main(["--confirm-clear"]) == 0
    assert not flag.exists() and "CLEAR" in (tmp_path / "out" / "LATEST.md").read_text()
