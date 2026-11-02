"""A check the Janitor could not run is named, never skipped quietly: the pin tick without
krew-pin-check, the code canary without code_canary.py, and whatever krew-pin-check itself could not
check come back as `missing`, the Janitor tile goes warn with "Not checked: ...", and one log line
says it when the list changes (not every tick)."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))
import krew_watch as hw  # noqa: E402


def _janitor():
    spec = importlib.util.spec_from_file_location("crew_janitor_missing_t", ROOT / "janitor" / "krew-janitor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_default_pin_check_is_the_one_crew_ships():
    j = _janitor()
    if "pin_check" not in (j._P or {}):
        assert j.PIN_CHECK == ROOT / "crew" / "scripts" / "krew-pin-check.py"
    assert (ROOT / "crew" / "scripts" / "krew-pin-check.py").is_file()


def test_a_missing_pin_check_is_named_and_logged_once(tmp_path, monkeypatch, capsys):
    j = _janitor()
    monkeypatch.setattr(j, "PIN_CHECK", tmp_path / "nope" / "krew-pin-check.py")
    state: dict = {"cards": {}}
    out = j.pin_tick(state, 1000.0)
    assert out["ok"] and out["missing"] and "nope/krew-pin-check.py" in out["missing"][0]
    assert "skipped" not in out
    j.pin_tick(state, 1300.0)
    log = capsys.readouterr().out
    assert log.count("pins: not checked:") == 1, log


def test_what_the_pin_check_could_not_check_comes_through(tmp_path, monkeypatch, capsys):
    j = _janitor()
    pc = tmp_path / "krew-pin-check.py"
    pc.write_text("def check():\n    return {'ok': True, 'failures': [], 'missing': ['T0 enforce not checked']}\n")
    monkeypatch.setattr(j, "PIN_CHECK", pc)
    out = j.pin_tick({"cards": {}}, 1000.0)
    assert out["missing"] == ["T0 enforce not checked"]
    assert "T0 enforce not checked" in capsys.readouterr().out


def test_a_missing_code_canary_is_named(monkeypatch):
    j = _janitor()
    import builtins
    real = builtins.__import__

    def no_canary(name, *a, **k):
        if name == "code_canary":
            raise ImportError("No module named 'code_canary'")
        return real(name, *a, **k)

    monkeypatch.delitem(sys.modules, "code_canary", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_canary)
    out = j.code_canary_tick()
    assert "skipped" not in out and "code_canary.py" in out["missing"][0]


def test_the_tile_goes_warn_and_names_what_was_not_checked(tmp_path, monkeypatch, capsys):
    j = _janitor()
    monkeypatch.setenv("KREW_JANITOR_WATCH", "1")
    snap = tmp_path / "snap.json"
    fake = SimpleNamespace(
        on=lambda: True,
        check_all=lambda parked=None, skip=frozenset(): [],
        write_snapshot=lambda s: hw.write_snapshot(s, snap), read_snapshot=lambda: None, gaps=lambda prev=None: [])
    monkeypatch.setattr(j, "journal_tail", lambda *a, **k: "")
    state: dict = {"cards": {}}
    row = {"ts": "2026-10-06T02:50:00", "probes": {}, "gpu_mode": "normal",
           "pins": {"ok": True, "failures": [], "missing": ["pin check not installed: /x/krew-pin-check.py"]},
           "code_canary": {"missing": ["code_canary.py not importable beside /x"]}}
    j.watch_tick(state, 1_000_000.0, "normal", False, {}, row, hw=fake, card=lambda t, b: True)
    s = json.loads(snap.read_text())
    assert s["level"] == "warn", s
    assert s["headline"].startswith("Not checked: pin check not installed") and "+1 more" in s["headline"]
    assert {m["from"] for m in s["missing"]} == {"pins", "code_canary"}
    assert "code_canary: not checked:" in capsys.readouterr().out
    row2 = {"ts": "2026-10-06T02:55:00", "probes": {}, "gpu_mode": "normal", "pins": {"ok": True, "failures": []}}
    j.watch_tick(state, 1_000_300.0, "normal", False, {}, row2, hw=fake, card=lambda t, b: True)
    s = json.loads(snap.read_text())
    assert s["level"] == "ok" and s["missing"] == []
    assert "code_canary: everything is checked again" in capsys.readouterr().out
