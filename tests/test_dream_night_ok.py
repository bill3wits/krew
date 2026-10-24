"""dream-night's `ok` says what happened (2026-09-17 reporting audit)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import os
KREW_MEMORY_ROOT = os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory")  # krew: the house kept this on a data disk

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("krew_memory", ROOT / "crew" / "scripts" / "krew_memory.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

import sys  # noqa: E402

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _memory_paths_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the memory root and its folders stay in tmp, never the real home (tests/krew_home_guard.py)."""
    keep_in_tmp(mod, tmp_path, monkeypatch)


def test_the_0916_night_is_not_ok():
    steps = [
        {"krew_dream_innate": {"ok": True}},
        {"roll_day": (KREW_MEMORY_ROOT + "/day/2026-09-16.md")},
        {"retain_freeair_error": "<urlopen error [Errno 111] Connection refused>"},
        {"consolidate_error": "<urlopen error [Errno 111] Connection refused>"},
        {"reflect_error": "<urlopen error [Errno 111] Connection refused>"},
        {"workspace": (KREW_MEMORY_ROOT + "/workspace/TODAY.md")},
    ]
    assert mod.dream_failed_steps(steps) == ["retain_freeair", "consolidate", "reflect"]


def test_a_clean_night_and_a_skipped_empty_day_are_ok():
    steps = [
        {"krew_dream_innate": {"ok": True, "rc": 0}},
        {"retain_freeair_day": {"ok": True, "skipped": True, "reason": "empty day"}},
        {"consolidate": {"ok": True}},
        {"reflect": {"ok": True, "chars": 900}},
    ]
    assert mod.dream_failed_steps(steps) == []


def test_an_innate_step_that_says_no_is_a_failure():
    assert mod.dream_failed_steps([{"krew_dream_innate": {"ok": False, "error": "krew-dream.py missing"}}]) == ["krew_dream_innate"]


def test_catchup_flag_marks_the_report(monkeypatch, tmp_path):
    """--catchup (t_da72d39a): a re-run of a closed day says so in the report."""
    # Keep it offline: every step stubbed, dry so nothing touches the network.
    import datetime as dt

    class _FakeDream:
        rc = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _FakeDream())
    monkeypatch.setattr(mod, "DREAM_DIR", tmp_path)
    monkeypatch.setattr(mod, "DAY_DIR", tmp_path)
    # nothing to roll, nothing to retain, nothing to reflect (dry short-circuits)
    rep = mod.dream_night(day=(dt.date.today() - dt.timedelta(days=1)).isoformat(),
                          dry=True, catchup=True)
    assert rep["catchup"] is True
    rep2 = mod.dream_night(day=(dt.date.today() - dt.timedelta(days=1)).isoformat(), dry=True)
    assert "catchup" not in rep2


# --- synchronous retain (the User 2026-10-02; KREW_DREAM_RETAIN_SYNC, ON) -------------------------

def _capture_retain(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "_day_content_for_retain", lambda d: "House day log 2026-10-01 (5 events).\n- a")
    monkeypatch.setattr(mod, "_http_json", lambda method, url, body=None, timeout=120.0:
                        sent.update(url=url, body=body, timeout=timeout) or {"success": True})
    return sent


def test_retain_is_synchronous_by_default_so_reflect_never_overlaps_it(monkeypatch):
    monkeypatch.delenv("KREW_DREAM_RETAIN_SYNC", raising=False)
    sent = _capture_retain(monkeypatch)
    out = mod.hindsight_retain_day("2026-10-01")
    assert sent["body"]["async"] is False and sent["timeout"] == mod.RETAIN_SYNC_TIMEOUT_S and out["sync"] is True


def test_the_kill_switch_puts_back_the_async_retain(monkeypatch):
    monkeypatch.setenv("KREW_DREAM_RETAIN_SYNC", "0")
    sent = _capture_retain(monkeypatch)
    out = mod.hindsight_retain_day("2026-10-01")
    assert sent["body"]["async"] is True and sent["timeout"] == 90 and out["sync"] is False


def test_retain_runs_before_reflect_in_the_night():
    src = (ROOT / "crew" / "scripts" / "krew_memory.py").read_text(encoding="utf-8")
    body = src.split("def dream_night(", 1)[1].split("\ndef ", 1)[0]
    assert body.index("hindsight_retain_day(") < body.index("hindsight_reflect_day(")


# --- every reflect attempt is timed and named (the User 2026-10-02) ---------------------------------

def test_a_client_timeout_is_named_and_the_attempts_reach_the_report(monkeypatch, tmp_path):
    import socket
    monkeypatch.setattr(mod, "DREAM_DIR", tmp_path)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    def boom(*a, **k):
        raise socket.timeout("timed out")
    monkeypatch.setattr(mod, "_http_json", boom)
    try:
        mod.hindsight_reflect_day("2026-10-01")
        raise AssertionError("no failure")
    except mod.ReflectFailed as e:
        assert [a["end"] for a in e.attempts] == ["client_timeout", "client_timeout"]
        assert all("s" in a for a in e.attempts)


def test_a_server_error_is_not_a_client_timeout():
    assert mod.reflect_end_kind(RuntimeError("HTTP 500 http://x/reflect: boom")) == "server_error"
    import urllib.error, socket
    assert mod.reflect_end_kind(urllib.error.URLError(socket.timeout("t"))) == "client_timeout"
    assert mod.reflect_end_kind(ValueError("x")) == "error"


def test_a_good_reflect_reports_its_attempts(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "DREAM_DIR", tmp_path)
    monkeypatch.setattr(mod, "ARCHIVE_DIR", tmp_path / "a")
    monkeypatch.setattr(mod, "_http_json", lambda *a, **k: {"text": "- a fact"})
    out = mod.hindsight_reflect_day("2026-10-01")
    assert out["attempts"][0]["end"] == "ok" and len(out["attempts"]) == 1


def test_the_night_keeps_the_attempts_when_reflect_fails():
    src = (ROOT / "crew" / "scripts" / "krew_memory.py").read_text(encoding="utf-8")
    assert 'step["reflect_attempts"] = e.attempts' in src
