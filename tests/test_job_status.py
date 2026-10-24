"""A window job's own word on whether it did its work (self-loop step 1, the User 09-28).

krew: the two window/lib/job_status.py tests of the house's self-loop step 1 suite. The rest of that suite replays the
architect runner through the ticker into the Daily (crew/scripts/steward-architect-run.sh, crew/lib/krew_daily.py),
the crew loops' files; it is not here.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "window" / "lib"))

import job_status  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("krew_window_tick_js", ROOT / "window" / "scripts" / "krew-window-tick.py")
wt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(wt)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from krew_home_guard import keep_in_tmp  # noqa: E402


@pytest.fixture(autouse=True)
def _tick_paths_in_tmp(tmp_path, monkeypatch):
    """krew 2026-10-10: the module's import-time paths stay in tmp, never the real home (tests/krew_home_guard.py)."""
    keep_in_tmp(wt, tmp_path, monkeypatch)


def test_status_verdict_is_pure_and_strict():
    assert wt.status_verdict("ok", None, None) == "ok", "a row with no status line is systemd's word alone"
    assert wt.status_verdict("ok", "world", None).startswith("failed: exited 0 but wrote no status line")
    assert wt.status_verdict("ok", "world", {"status": "ok"}) == "ok"
    assert wt.status_verdict("ok", "world", {"status": "skipped", "reason": "occupancy=beast"}) == "skipped: occupancy=beast"
    assert wt.status_verdict("ok", "world", {"status": "failed", "reason": "no note"}) == "failed: no note"
    assert wt.status_verdict("failed: result=exit-code exit=4", "team-eval", {"status": "ok"}).startswith("failed: result")


def test_a_stale_line_is_not_tonights_word(tmp_path):
    job_status.write("world", "2026-09-27", "ok", d=tmp_path)
    p = job_status.path_for("world", "2026-09-27", tmp_path)
    rec = json.loads(p.read_text())
    rec["ts"] = 1000.0
    p.write_text(json.dumps(rec))
    assert job_status.read("world", "2026-09-27", since=5000.0, d=tmp_path) is None
    assert job_status.read("world", "2026-09-27", since=0, d=tmp_path)["status"] == "ok"


def test_the_status_dir_is_under_krew_home(krew_home, monkeypatch):
    monkeypatch.delenv("KREW_JOB_STATUS_DIR", raising=False)
    assert job_status.status_dir() == krew_home / "job-status"
