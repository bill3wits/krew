"""lib/gate_hold — after a hermes update, a gate whose suite fails runs report-only for 24 h."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "janitor"))
import gate_hold  # noqa: E402

NOW = 1_790_000_000.0


class R:
    def __init__(self, rc):
        self.returncode, self.stdout, self.stderr = rc, "1 failed" if rc else "3 passed", ""


def _repo(tmp_path):
    for f in ("tests/a.py", "tests/b.py"):
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("")
    return tmp_path


def test_a_failing_suite_holds_its_gate_and_a_passing_one_clears(tmp_path, monkeypatch):
    repo, hold = _repo(tmp_path), tmp_path / "hold.json"
    suites = {"kanban-board-gate": ("tests/a.py",), "kanban-loop": ("tests/b.py",), "decomposer": ("tests/none.py",)}
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw))
        return R(1 if "tests/a.py" in argv else 0)
    out = gate_hold.check(repo, label="35272ce28b", now=NOW, path=hold, run=run, suites=suites)
    assert out["kanban-board-gate"].startswith("FAIL") and out["kanban-loop"] == "pass"
    assert out["decomposer"] == "no suites"
    assert all("HERMES_KANBAN_DB" not in kw["env"] for _, kw in seen)
    assert "35272ce28b" in gate_hold.report_only("kanban-board-gate", now=NOW + 60, path=hold)
    assert gate_hold.report_only("kanban-loop", now=NOW + 60, path=hold) == ""
    assert gate_hold.report_only("kanban-board-gate", now=NOW + 25 * 3600, path=hold) == ""   # 24 h
    # the next update's suites pass: the hold is cleared
    gate_hold.check(repo, now=NOW + 3600, path=hold, run=lambda a, **k: R(0), suites=suites)
    assert json.loads(hold.read_text()) == {}


def test_kills(tmp_path, monkeypatch):
    hold = tmp_path / "hold.json"
    hold.write_text(json.dumps({"kanban-loop": {"until": NOW + 99, "why": "x"}}))
    assert gate_hold.report_only("kanban-loop", now=NOW, path=hold) == "x"
    monkeypatch.setenv("KREW_GATE_UPDATE_HOLD", "0")
    assert gate_hold.report_only("kanban-loop", now=NOW, path=hold) == ""
    monkeypatch.setenv("KREW_POST_UPDATE_TESTS", "0")
    assert gate_hold.check(tmp_path, now=NOW, path=hold, run=lambda *a, **k: 1 / 0) == {}


def test_a_broken_hold_file_never_holds(tmp_path):
    hold = tmp_path / "hold.json"
    hold.write_text("{not json")
    assert gate_hold.report_only("kanban-board-gate", now=NOW, path=hold) == ""


def test_the_gate_and_the_loop_ask_at_start():
    gate = (ROOT / "crew" / "scripts" / "kanban-board-gate.py").read_text()
    assert '_update_hold("kanban-board-gate")' in gate
    loop = (ROOT / "crew" / "scripts" / "kanban-loop.py").read_text()
    assert 'gate_hold.report_only("kanban-loop")' in loop


def test_every_named_suite_exists():
    # krew: the decomposer's suite (Ada's profile key) ships with the profiles, not here; a missing suite is skipped
    # by gate_hold, never a failure, so only the two crew gates are held to their files.
    missing = [f for g, fs in gate_hold.SUITES.items() if g != "decomposer" for f in fs if not (ROOT / f).is_file()]
    assert missing == []
