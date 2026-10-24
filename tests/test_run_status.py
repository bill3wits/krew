"""krew-run-status: the report a timer-driven crew job leaves after every run, for the panel's crew/kanban tile.

The tile's timer stays `active (waiting)` while the loop fails each tick, so the run itself has to say how it ended.
Every test writes under tmp_path; journalctl is a fake on PATH.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT

SCRIPT = ROOT / "crew" / "scripts" / "krew-run-status.py"
UNIT = "krew-kanban-loop.service"


@pytest.fixture
def R():
    spec = importlib.util.spec_from_file_location("krewtest_run_status", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_clean_run_is_ok(R):
    r = R.report(UNIT, {"SERVICE_RESULT": "success", "EXIT_CODE": "exited", "EXIT_STATUS": "0"}, now=1000.0, lines=[])
    assert r["level"] == "ok" and r["headline"].startswith("last run ok at ") and r["epoch"] == 1000.0
    assert r["unit"] == UNIT


def test_a_failed_run_names_the_result_and_the_error_line(R):
    lines = ["kanban-loop: 3 cards", "Traceback (most recent call last):", '  File "x.py", line 3',
             "ConnectionRefusedError: [Errno 111] Connection refused", ""]
    r = R.report(UNIT, {"SERVICE_RESULT": "exit-code", "EXIT_CODE": "exited", "EXIT_STATUS": "1"}, lines=lines)
    assert r["level"] == "bad" and "exit 1" in r["headline"]
    assert r["headline"].endswith("ConnectionRefusedError: [Errno 111] Connection refused")
    t = R.report(UNIT, {"SERVICE_RESULT": "timeout", "EXIT_CODE": "killed", "EXIT_STATUS": "TERM"}, lines=["tick"])
    assert t["level"] == "bad" and "timed out" in t["headline"] and t["headline"].endswith("tick")
    n = R.report(UNIT, {"SERVICE_RESULT": "signal", "EXIT_CODE": "killed", "EXIT_STATUS": "KILL"}, lines=[])
    assert "killed by KILL" in n["headline"] and "its journal has no line" in n["headline"]


def test_the_off_switch_is_a_named_warning(R, tmp_path):
    off = tmp_path / "kanban-loop.off"
    off.write_text("")
    r = R.report(UNIT, {"SERVICE_RESULT": "success", "EXIT_STATUS": "0"}, off=str(off), lines=[])
    assert r["level"] == "warn" and "off by choice" in r["headline"] and str(off) in r["headline"]


def test_the_journal_read_is_that_run_only(R, tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "journalctl").write_text('#!/bin/sh\necho "args: $*"\necho "PermissionError: [Errno 13] denied"\n')
    (bin_ / "journalctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    assert R.journal_lines(UNIT, "abc123")[0].endswith("_SYSTEMD_INVOCATION_ID=abc123")
    r = R.report(UNIT, {"SERVICE_RESULT": "exit-code", "EXIT_STATUS": "2", "INVOCATION_ID": "abc123"})
    assert r["headline"].endswith("PermissionError: [Errno 13] denied") and r["invocation"] == "abc123"


def test_cli_writes_where_the_tile_reads(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "journalctl").write_text("#!/bin/sh\necho 'kanban-loop: board unreachable'\nexit 0\n")
    (bin_ / "journalctl").chmod(0o755)
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp_path / "home"), "SERVICE_RESULT": "exit-code",
           "EXIT_CODE": "exited", "EXIT_STATUS": "3", "INVOCATION_ID": "i1"}
    r = subprocess.run([sys.executable, str(SCRIPT), UNIT, "--home", str(tmp_path / "kh")], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rep = json.loads((tmp_path / "kh" / "run-status" / f"{UNIT}.json").read_text())
    assert rep["level"] == "bad" and "exit 3" in rep["headline"] and "board unreachable" in rep["headline"]
    assert not list((tmp_path / "kh" / "run-status").glob("*.tmp"))
    env = dict(env, KREW_HOME=str(tmp_path / "kh2"), SERVICE_RESULT="success", EXIT_STATUS="0")
    assert subprocess.run([sys.executable, str(SCRIPT), UNIT], env=env, capture_output=True).returncode == 0
    assert json.loads((tmp_path / "kh2" / "run-status" / f"{UNIT}.json").read_text())["level"] == "ok"
    env.pop("KREW_HOME")
    assert subprocess.run([sys.executable, str(SCRIPT), UNIT], env=env, capture_output=True).returncode == 0
    assert (tmp_path / "home" / ".krew" / "run-status" / f"{UNIT}.json").is_file()


def test_the_report_is_the_shape_the_panel_reads(R):
    r = R.report(UNIT, {"SERVICE_RESULT": "exit-code", "EXIT_STATUS": "1"}, lines=["x" * 500])
    assert r["level"] in ("ok", "warn", "bad") and isinstance(r["headline"], str) and isinstance(r["epoch"], float)
