"""steward-sweep files the hourly stewardship card — ungated, and it does not roll the board.

2026-09-17: the installed copy carried a crew-written VRAM gate (mode normal AND
>= 8192 MiB free) that Standard can never meet, so Steward was absent for a whole
jam; and the day-board roll rode inside it, so no sweep meant no roll.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "crew" / "scripts" / "steward-sweep"
UNITS = ROOT / "crew" / "units"


def _code() -> str:
    """The script without comment lines — pins must not match our own prose."""
    return "\n".join(l for l in SWEEP.read_text().splitlines() if not l.lstrip().startswith("#"))


def test_sweep_has_no_gpu_gate():
    code = _code()
    assert "sweep_gate" not in code and "free_vram" not in code and "nvidia-smi" not in code
    assert "krew-gpu-mode" not in code


def test_board_roll_is_opt_in_only():
    code = _code()
    roll = [l for l in code.splitlines() if "krew-board-roll" in l and "if " in l]
    assert roll and all("KREW_SWEEP_ROLLS_BOARD" in l for l in roll)
    assert '${KREW_SWEEP_ROLLS_BOARD:-0}' in code


def test_standing_board_nothing_rolls_on_a_clock():
    """the User 2026-09-17: "standing board it is." No timer; the service stays for one
    deliberate migration and still asks the drained-board gate."""
    assert not (UNITS / "krew-board-roll.timer").exists()
    svc = (UNITS / "krew-board-roll.service").read_text()
    assert "/usr/local/bin/krew-board-roll" in svc and "Type=oneshot" in svc
    body = _code()
    assert "ONE standing krew board" in SWEEP.read_text() and "DONE CARDS ARE NOT YOURS TO ARCHIVE" in body


def test_script_parses():
    assert subprocess.run(["bash", "-n", str(SWEEP)], check=False).returncode == 0


def test_installed_copy_is_the_repo_copy():
    """krew: the timer runs the copy krew-crew-install writes ($KREW_HOME/crew/scripts/steward-sweep) through the
    launcher, never a hand-installed /usr/local/bin copy that could drift from it (the reference install's trap)."""
    svc = (UNITS / "steward-sweep.service").read_text()
    assert "krew-crew-run steward-sweep" in svc and "/usr/local/bin/steward-sweep" not in svc


# --- 2026-09-17: the roll waits until the day before is finished ----------------

def _gate():
    import importlib.util
    spec = importlib.util.spec_from_file_location("roll_ready", ROOT / "crew" / "scripts" / "krew-board-roll-ready.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _kanban_home(tmp_path, rows):
    import sqlite3
    (tmp_path / "boards" / "daily-x").mkdir(parents=True)
    (tmp_path / "current").write_text("daily-x\n")
    con = sqlite3.connect(tmp_path / "boards" / "daily-x" / "kanban.db")
    con.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
    con.executemany("INSERT INTO tasks VALUES (?,?)", rows)
    con.commit()
    con.close()
    return tmp_path


def test_roll_waits_for_every_open_status_parks_included(tmp_path, monkeypatch):
    m = _gate()
    monkeypatch.delenv("KREW_BOARD_ROLL_GATE", raising=False)
    home = _kanban_home(tmp_path, [("t_1", "done"), ("t_2", "archived"), ("t_3", "scheduled")])
    ok, why = m.verdict(home)
    assert not ok and "scheduled=1" in why           # a carried park is an un-parked card
    assert set(m.OPEN) >= {"todo", "ready", "blocked", "running", "scheduled", "triage"}


def test_roll_goes_when_drained_and_kill_switch(tmp_path, monkeypatch):
    m = _gate()
    monkeypatch.delenv("KREW_BOARD_ROLL_GATE", raising=False)
    (tmp_path / "a").mkdir()
    assert m.verdict(_kanban_home(tmp_path / "a", [("t_1", "done"), ("t_2", "archived")]))[0]
    (tmp_path / "b").mkdir()
    jammed = _kanban_home(tmp_path / "b", [("t_1", "blocked")])
    assert not m.verdict(jammed)[0]
    monkeypatch.setenv("KREW_BOARD_ROLL_GATE", "0")
    assert m.verdict(jammed)[0]


def test_service_asks_the_gate_first():
    svc = (UNITS / "krew-board-roll.service").read_text()
    lines = [l for l in svc.splitlines() if not l.startswith("#")]
    assert any(l.startswith("ExecCondition=") and "krew-board-roll-ready.py" in l for l in lines)
