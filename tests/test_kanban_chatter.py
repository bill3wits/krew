"""Tests for kanban-chatter._to_epoch (created_at → epoch float).

Regression: the board stores created_at as ISO-8601 strings
('2026-09-13T05:11:13Z'); the old code did float(ev["created_at"]) which
crashed the whole pass (krew-kanban-chatter.service crash-looped ~178x/hr).
_to_epoch must accept ISO-8601 (with or without Z), numeric epochs, and blank,
without ever raising.
"""
import importlib.util
import time
from pathlib import Path

import pytest

MOD = Path(__file__).resolve().parents[1] / "crew" / "scripts" / "kanban-chatter.py"


@pytest.fixture(scope="module")
def ch():
    spec = importlib.util.spec_from_file_location("kanban_chatter", MOD)
    assert spec is not None
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _approx_epoch(s) -> float:
    from datetime import datetime
    return datetime.fromisoformat(s).timestamp()


def test_iso_z(ch):
    ts = ch._to_epoch("2026-09-13T05:11:13Z")
    assert ts == pytest.approx(_approx_epoch("2026-09-13T05:11:13+00:00"))


def test_iso_offset(ch):
    ts = ch._to_epoch("2026-09-13T05:11:13+00:00")
    assert ts == pytest.approx(_approx_epoch("2026-09-13T05:11:13+00:00"))


def test_numeric_epoch_int(ch):
    assert ch._to_epoch(1789320000) == 1789320000.0


def test_numeric_epoch_float(ch):
    assert ch._to_epoch(1789320000.5) == 1789320000.5


def test_numeric_epoch_string(ch):
    assert ch._to_epoch("1789320000") == 1789320000.0


def test_none_falls_back_to_now(ch):
    before = time.time()
    ts = ch._to_epoch(None)
    after = time.time()
    assert before <= ts <= after + 1.0


def test_blank_falls_back_to_now(ch):
    before = time.time()
    ts = ch._to_epoch("   ")
    after = time.time()
    assert before <= ts <= after + 1.0


def test_garbage_falls_back_to_now(ch):
    # Must not raise; should land near now.
    before = time.time()
    ts = ch._to_epoch("not-a-timestamp")
    after = time.time()
    assert before <= ts <= after + 1.0
