"""A unit caught mid-stop/start is carded on its FINAL state (t_d5c76f93, 2026-09-27).

crew-lila-conductor read `deactivating` for ~1 s on six clean restarts in a day and the
tick carded each sample. settle_state() re-reads `is-active` until the transient ends
(10 s max, 5 s a call); a unit still in transition after the window keeps its transient
state and cards as before."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    loader = SourceFileLoader(name, str(ROOT / "janitor" / "krew-janitor.py"))
    mod = module_from_spec(spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


jan = _load("crew_janitor_transition")


def _fake(monkeypatch, answers):
    calls = []

    def sysctl(manager, *args, timeout=30):
        calls.append((manager, args, timeout))
        return SimpleNamespace(stdout=answers.pop(0) + "\n" if answers else "deactivating\n")

    monkeypatch.setattr(jan, "sysctl", sysctl)
    monkeypatch.setattr(jan.time, "sleep", lambda s: None)
    return calls


def test_only_the_two_transients_are_inert():
    assert jan.transition_inert("deactivating") and jan.transition_inert("activating")
    for s in ("active", "inactive", "failed", "unknown", "reloading"):
        assert not jan.transition_inert(s), s


def test_a_clean_restart_settles_to_active(monkeypatch):
    calls = _fake(monkeypatch, ["deactivating", "activating", "active"])
    assert jan.settle_state("crew-lila-conductor.service", "user") == "active"
    assert len(calls) == 3
    assert all(c[1] == ("is-active", "crew-lila-conductor.service") and c[2] == 5 for c in calls)


def test_a_stop_that_ends_down_stays_red(monkeypatch):
    _fake(monkeypatch, ["deactivating", "failed"])
    assert jan.settle_state("x.service", "user") == "failed"


def test_a_unit_wedged_in_transition_keeps_its_transient_after_the_window(monkeypatch):
    calls = _fake(monkeypatch, [])
    assert jan.settle_state("x.service", "user") == "deactivating"
    assert len(calls) == jan.IN_TRANSITION_SETTLE_MAX_S // jan.IN_TRANSITION_POLL_S + 1
