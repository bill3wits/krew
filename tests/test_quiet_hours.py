"""Quiet hours come from the one global, not a clock in the announcer (the User 2026-09-17).

krew: the global is krew.yaml (`off_hours`, `quiet_hours`, `manners.quiet_hours`); the house's down-hours source is
named `off_hours`. The announcer half of the house test (its in_quiet_hours levers) is the Crissa player's and is
not here."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "window" / "lib"))
import krew_hours  # noqa: E402


def _at(h: int, m: int = 0) -> dt.datetime:
    return dt.datetime(2026, 9, 17, h, m)


def test_the_users_own_answer_wins_else_never_quiet():
    """The owner 2026-10-15: quiet hours default to never; the off-hours are no longer a fallback."""
    assert krew_hours.quiet_hours({"manners": {"quiet_hours": "21:30-05:00"}, "off_hours": "20:00-02:00"})["source"] == "manners"
    q = krew_hours.quiet_hours({"off_hours": "20:00-02:00"})
    assert (q["window"], q["source"]) == (None, "none")
    assert krew_hours.quiet_hours({})["source"] == "none"
    assert krew_hours.quiet_hours({"manners": {"quiet_hours": "whenever"}, "off_hours": "20:00-02:00"})["source"] == "none"
    site = {"off_hours": "20:00-02:00"}
    assert not any(krew_hours.is_quiet(_at(h), site) for h in range(24))


def test_the_old_chain_under_the_kill(monkeypatch):
    monkeypatch.setenv("KREW_QUIET_DEFAULT_NEVER", "0")
    q = krew_hours.quiet_hours({"off_hours": "20:00-02:00"})
    assert (q["window"], q["source"]) == ("20:00-02:00", "off_hours")
    assert krew_hours.quiet_hours({})["source"] == "legacy"
    site = {"off_hours": "20:00-02:00"}
    assert krew_hours.is_quiet(_at(1, 59), site) and krew_hours.is_quiet(_at(23), site)
    assert not krew_hours.is_quiet(_at(2, 0), site) and not krew_hours.is_quiet(_at(19, 59), site)


def test_a_top_level_quiet_hours_stands_beside_manners():
    q = krew_hours.quiet_hours({"quiet_hours": "21:00-07:00", "off_hours": "20:00-02:00"})
    assert (q["window"], q["source"]) == ("21:00-07:00", "quiet_hours")
