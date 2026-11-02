"""janitor restart verdict: narrow, and a dry run until armed (2026-09-17)."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    loader = SourceFileLoader(name, str(ROOT / "janitor" / "krew-janitor.py"))
    mod = module_from_spec(spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


jan = _load("crew_janitor_restart")
LONG = jan.REST_COOLDOWN + 1


def test_only_a_failed_unit_outside_a_hold_is_in_order():
    assert jan.restart_refusal("hermes-ada-gateway.service", "failed", False, LONG, hold=False) == ""
    # the 17 of 23: systemd was already restarting it
    assert jan.restart_refusal("hermes-ada-gateway.service", "deactivating", False, LONG, hold=False) == "state deactivating"
    assert jan.restart_refusal("hermes-ada-gateway.service", "inactive", False, LONG, hold=False) == "state inactive"
    assert jan.restart_refusal("hermes-ada-gateway.service", "failed", True, LONG, hold=False) == "crash loop"
    assert jan.restart_refusal("hermes-ada-gateway.service", "failed", False, 60, hold=False) == "cooldown"
    assert jan.restart_refusal("hermes-ada-gateway.service", "failed", False, LONG, hold=True) == "gpu hold"


def test_the_wall_and_observe_only_units_are_never_restarted():
    for unit in ("crew-conductor.service", "crew-wall-ascii.service"):
        assert jan.restart_refusal(unit, "failed", False, LONG, hold=False) == "wall unit"
    for unit in jan.OBSERVE_ONLY:
        assert jan.restart_refusal(unit, "failed", False, LONG, hold=False) == "observe-only"


def test_it_ships_unarmed(monkeypatch):
    monkeypatch.delenv("KREW_JANITOR_RESTART", raising=False)
    fresh = _load("crew_janitor_unarmed")
    assert fresh.RESTART_ARMED is False


def test_weekly_reads_past_a_line_torn_by_a_power_loss(tmp_path, monkeypatch, capsys):
    """09-18's power loss left 147 NUL bytes as one line of janitor-20260918.jsonl;
    weekly() raised on it and failed every run until the day left the window."""
    from datetime import date
    (tmp_path / f"janitor-{date.today():%Y%m%d}.jsonl").write_bytes(b"\x00" * 147 + b"\n")
    monkeypatch.setattr(jan, "METRICS_DIR", tmp_path)
    jan.weekly()
    out = capsys.readouterr().out
    assert "skipped 1 unreadable metrics line" in out
    assert "no samples" in out
