"""krew's All OS Off / On (G7, the owner 2026-11-01: "How is someone supposed to shut down completly?"): every running
krew unit off (triggers first), the snapshot, On brings back exactly those; the kit's crissa-* units are left to the kit.
A fake service manager only: no test reaches systemctl."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))


def _mod():
    spec = importlib.util.spec_from_file_location("krewtest_os_power", ROOT / "janitor" / "krew_os_power.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Manager:
    """systemctl show / stop / start over a dict of states; records every call."""

    def __init__(self, states: dict):
        self.states, self.calls = dict(states), []

    def __call__(self, argv, timeout=60.0):
        self.calls.append(list(argv))
        scope = "user" if "--user" in argv else "system"
        verb = next(a for a in argv if a in ("show", "stop", "start"))
        names = [a for a in argv[argv.index(verb) + 1:] if not a.startswith("-p")]
        if verb == "show":
            return 0, "\n\n".join(f"Id={n}\nActiveState={self.states.get((scope, n), 'inactive')}" for n in names), ""
        for n in names:
            self.states[(scope, n)] = "inactive" if verb == "stop" else "active"
        return 0, "", ""


REG = {"units": [
    {"unit": "krew-keeper.service", "scope": "user", "role": "service"},
    {"unit": "krew-kanban-loop.timer", "scope": "user", "role": "timer"},
    {"unit": "krew-kanban-loop.service", "scope": "user", "role": "service"},
    {"unit": "hermes-gateway.service", "scope": "user", "role": "service"},
    {"unit": "krew-engine-crew.service", "scope": "system", "role": "service"},
    {"unit": "crissa-launcher.service", "scope": "user", "role": "service"},       # the kit's: its own Off
    {"unit": "krew-board-roll.service", "scope": "user", "role": "manual"},        # nothing starts it
]}


def test_off_stops_every_running_krew_unit_triggers_first_and_leaves_the_kits(tmp_path):
    P = _mod()
    m = Manager({("user", "krew-keeper.service"): "active", ("user", "krew-kanban-loop.timer"): "active",
                 ("user", "hermes-gateway.service"): "active", ("system", "krew-engine-crew.service"): "active",
                 ("user", "crissa-launcher.service"): "active"})
    snap = tmp_path / "os-power.json"
    rep = P.off(REG, run=m, snapshot=snap)
    assert rep["ok"] and not rep["failed"]
    stops = [c for c in m.calls if "stop" in c]
    assert [c[-1] for c in stops][0] == "krew-kanban-loop.timer"             # the trigger before what it starts
    assert {c[-1] for c in stops} == {"krew-kanban-loop.timer", "krew-keeper.service", "hermes-gateway.service",
                                      "krew-engine-crew.service"}
    assert ["systemctl", "--no-ask-password", "stop", "krew-engine-crew.service"] in stops     # system scope, no prompt
    assert m.states[("user", "crissa-launcher.service")] == "active"          # the kit's Off is the kit's
    assert not any("krew-kanban-loop.service" in c for c in stops)            # not running: not stopped, not snapshotted
    s = json.loads(snap.read_text())
    assert {x["unit"] for x in s["stopped"]} == {c[-1] for c in stops}


def test_on_starts_exactly_what_off_stopped_services_first_then_forgets(tmp_path):
    P = _mod()
    m = Manager({("user", "krew-keeper.service"): "active", ("user", "krew-kanban-loop.timer"): "active"})
    snap = tmp_path / "os-power.json"
    P.off(REG, run=m, snapshot=snap)
    m.calls.clear()
    rep = P.turn_on(run=m, snapshot=snap)
    assert rep["ok"]
    starts = [c[-1] for c in m.calls if "start" in c]
    assert starts == ["krew-keeper.service", "krew-kanban-loop.timer"]
    assert not snap.exists()
    assert P.turn_on(run=m, snapshot=snap)["note"] == "no Off to undo"


def test_a_unit_that_will_not_stop_is_named_and_the_snapshot_keeps_it(tmp_path):
    P = _mod()

    class Stuck(Manager):
        def __call__(self, argv, timeout=60.0):
            if "stop" in argv and argv[-1] == "hermes-gateway.service":
                self.calls.append(list(argv))
                return 1, "", "Access denied"
            return super().__call__(argv, timeout)

    m = Stuck({("user", "hermes-gateway.service"): "active", ("user", "krew-keeper.service"): "active"})
    rep = P.off(REG, run=m, snapshot=tmp_path / "s.json")
    assert not rep["ok"] and rep["failed"] == [{"unit": "hermes-gateway.service", "scope": "user", "state": "active"}]
    assert next(x for x in rep["stopped"] if x["unit"] == "hermes-gateway.service")["err"] == "Access denied"


def test_a_second_off_adds_to_the_first_and_never_forgets_it(tmp_path):
    P = _mod()
    snap = tmp_path / "s.json"
    m = Manager({("user", "krew-keeper.service"): "active"})
    P.off(REG, run=m, snapshot=snap)
    m.states[("user", "hermes-gateway.service")] = "active"           # something came back by hand
    P.off(REG, run=m, snapshot=snap)
    assert {x["unit"] for x in json.loads(snap.read_text())["stopped"]} == {"krew-keeper.service", "hermes-gateway.service"}


def test_dry_run_and_the_kill_switch_change_nothing(tmp_path, monkeypatch):
    P = _mod()
    m = Manager({("user", "krew-keeper.service"): "active"})
    rep = P.off(REG, run=m, dry=True, snapshot=tmp_path / "s.json")
    assert rep["stopped"][0]["would"] == "systemctl --user stop krew-keeper.service"
    assert not [c for c in m.calls if "stop" in c] and not (tmp_path / "s.json").exists()
    monkeypatch.setenv("KREW_OS_POWER", "0")
    assert P.off(REG, run=m)["refused"] == "KREW_OS_POWER=0" and P.turn_on(run=m)["refused"] == "KREW_OS_POWER=0"


def test_a_test_never_reaches_the_real_service_manager():
    P = _mod()
    with pytest.raises(RuntimeError, match="under a test"):
        P.safe_run(["systemctl", "--user", "stop", "krew-keeper.service"])
