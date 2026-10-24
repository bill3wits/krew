"""Control Panel audit fixes (the User 2026-09-17): the restart post-check, the journal reader, a unit's own kind.

krew: the house's window/lib/panel_units.py tests only. The house file also drove the panel page in a JS sim, read
the panel server's source, its polkit rule and the reference install's own search reader; those are the Crissa
panel's (the pack's scripts/zoeyos-panel-server.py) or the reference install's alone, and are not here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "window" / "lib"))
import panel_units  # noqa: E402


# ── who may restart a system unit ─────────────────────────────────────────
def test_user_signal_is_facefinder_auth_or_secure():
    assert panel_units.user_signal({"auth": {"user_auth": True, "user_auth_tier": "fresh"}})[0]
    assert panel_units.user_signal({"auth": {"user_auth": False}, "visit_lamp": "sage"})[0]
    assert panel_units.user_signal({"presence": {"visit_lamp": "sage"}})[0]
    ok, why = panel_units.user_signal({"auth": {"user_auth": False}, "visit_lamp": "amber"})
    assert not ok and "FaceFinder" in why
    assert not panel_units.user_signal(None)[0]


# ── post-check ────────────────────────────────────────────────────────────
class _Units:
    """A fake systemd: a list of states `unit_props` walks through."""
    def __init__(self, states):
        self.states, self.i = states, 0

    def __call__(self, unit, scope):
        s = self.states[min(self.i, len(self.states) - 1)]
        self.i += 1
        return s


def _clock():
    t = [0.0]

    def clock():
        return t[0]

    def sleep(dt):
        t[0] += dt
    return clock, sleep


def test_postcheck_catches_a_unit_that_falls_over_inside_the_settle(monkeypatch):
    up = {"InvocationID": "new", "ActiveState": "active", "SubState": "running"}
    down = {"InvocationID": "new2", "ActiveState": "activating", "SubState": "auto-restart",
            "Result": "exit-code", "ExecMainStatus": "1"}
    monkeypatch.setattr(panel_units, "unit_props", _Units([up, down]))
    monkeypatch.setattr(panel_units, "_last_lines", lambda u, s: ["ImportError: jsonref"])
    clock, sleep = _clock()
    r = panel_units.postcheck("hindsight", "system", 8888, "old", clock=clock, sleep=sleep)
    assert not r["ok"] and r["state"] == "crashed" and r["tail"] == ["ImportError: jsonref"]


def test_postcheck_needs_a_new_run(monkeypatch):
    monkeypatch.setattr(panel_units, "unit_props",
                        _Units([{"InvocationID": "old", "ActiveState": "active"}]))
    clock, sleep = _clock()
    r = panel_units.postcheck("rail-align", "user", 8195, "old", clock=clock, sleep=sleep)
    assert not r["ok"] and r["state"] == "no-restart"


def test_postcheck_waits_for_the_port(monkeypatch):
    up = {"InvocationID": "new", "ActiveState": "active"}
    monkeypatch.setattr(panel_units, "unit_props", _Units([up]))
    answers = iter([False, False, True])
    monkeypatch.setattr(panel_units, "_port_answers", lambda u, p: next(answers))
    clock, sleep = _clock()
    r = panel_units.postcheck("rail-align", "user", 8195, "old", clock=clock, sleep=sleep)
    assert r["ok"] and ":8195 answering" in r["note"]

    monkeypatch.setattr(panel_units, "unit_props", _Units([up]))
    monkeypatch.setattr(panel_units, "_port_answers", lambda u, p: False)
    clock, sleep = _clock()
    r = panel_units.postcheck("rail-align", "user", 8195, "old", clock=clock, sleep=sleep)
    assert not r["ok"] and r["state"] == "no-port"


def test_a_refused_restart_says_so(monkeypatch):
    calls = []

    def run(cmd, timeout=8.0):
        calls.append(cmd)
        if "restart" in cmd:
            return 1, "", "Access denied as the requested operation requires interactive authentication."
        return 0, "InvocationID=old", ""
    monkeypatch.setattr(panel_units, "_run", run)
    monkeypatch.setattr(panel_units, "POSTCHECK", True)
    r = panel_units.start_restart("hindsight", "system", 8888)
    assert r["ok"] is False and "Access denied" in r["error"]
    restart = next(c for c in calls if "restart" in c)
    assert "--no-ask-password" in restart and "--user" not in restart and "--no-block" in restart


# ── journals ──────────────────────────────────────────────────────────────
def test_journal_reads_each_scope_in_its_own_scope(monkeypatch):
    seen = []

    def run(cmd, timeout=8.0):
        seen.append(cmd)
        if cmd[0] == "journalctl":
            if "--user" in cmd:
                return 0, "2026-09-17T05:00:02-04:00 ops-main ops[1]: user line", ""
            return 0, "2026-09-17T05:00:01-04:00 ops-main systemd[1]: system line", ""
        return 0, "", ""
    monkeypatch.setattr(panel_units, "_run", run)
    out = panel_units.read_journal([("ops", "user"), ("hindsight", "system")], files=False)
    jc = [c for c in seen if c[0] == "journalctl"]
    assert any("--user" in c and "ops.service" in c for c in jc)
    assert any("--user" not in c and "hindsight.service" in c for c in jc)
    assert out["lines"] == ["2026-09-17T05:00:01-04:00 ops-main systemd[1]: system line",
                            "2026-09-17T05:00:02-04:00 ops-main ops[1]: user line"]


def test_journal_appends_the_units_own_log_file(monkeypatch, tmp_path):
    err = tmp_path / "api.err.log"
    err.write_text("Traceback\nModuleNotFoundError: No module named 'jsonref'\n")

    def run(cmd, timeout=8.0):
        if cmd[0] == "journalctl":
            return 0, "-- No entries --", ""
        if "cat" in cmd:
            return 0, f"[Service]\nStandardError=append:{err}\n", ""
        return 0, "", ""
    monkeypatch.setattr(panel_units, "_run", run)
    panel_units._FILES_CACHE.clear()
    out = panel_units.read_journal([("hindsight", "system")])
    assert out["lines"][0] == f"── {err} ──"
    assert "No module named 'jsonref'" in out["lines"][-1]


# ── a unit keeps its own kind (the User 2026-10-01) ──────────────────────
@pytest.mark.parametrize("given,name", [
    ("crew", "crew.service"),
    ("crew.service", "crew.service"),
    ("krew-kanban-loop.timer", "krew-kanban-loop.timer"),
    ("krew-kit-reapply.path", "krew-kit-reapply.path"),
    ("postgresql" "@18-main", "postgresql" "@18-main.service"),
])
def test_unit_file_keeps_a_timer_or_path_suffix(given, name):
    assert panel_units.unit_file(given) == name


def _record(monkeypatch, out="LoadState=loaded\nActiveState=active"):
    seen = []
    monkeypatch.setattr(panel_units, "_run", lambda cmd, timeout=8.0: seen.append(cmd) or (0, out, ""))
    return seen


def test_unit_props_asks_the_user_scope_for_the_timer_itself(monkeypatch):
    """Was `krew-kanban-loop.timer.service`: not-found in the user scope, so the executor fell to
    SYSTEM and got Access denied."""
    seen = _record(monkeypatch)
    assert panel_units.unit_props("krew-kanban-loop.timer", "user")["LoadState"] == "loaded"
    assert seen[0][:4] == ["systemctl", "--user", "--no-ask-password", "show"]
    assert seen[0][4] == "krew-kanban-loop.timer"


def test_every_restart_road_keeps_the_suffix(monkeypatch):
    seen = _record(monkeypatch)
    monkeypatch.setattr(panel_units, "POSTCHECK", False)
    monkeypatch.setattr(panel_units, "_shared_rows", lambda: None)
    panel_units.start_restart("krew-kit-reapply.path", "user", None)
    restarts = [c for c in seen if "restart" in c]
    assert restarts and restarts[-1][-1] == "krew-kit-reapply.path"


def test_no_name_in_panel_units_gets_service_glued_on_by_hand():
    src = (ROOT / "window" / "lib" / "panel_units.py").read_text(encoding="utf-8")
    assert src.count('+ ".service"') == 1          # only unit_file() adds it, and only to a bare name
