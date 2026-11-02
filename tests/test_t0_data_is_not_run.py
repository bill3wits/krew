"""A data file is read, not run (the owner 2026-10-01: all 42 report-day unit-control hits were data).

The segment split runs inside `python3 -c` code and heredocs, so `open('/stores/.../decisions.json')` left a bare
path in command position and the ledger's prose read as a script. A path in command position now counts only
when it is executable or starts with #!; an interpreter operand unless it is plainly data; a patch only when it
targets a script. Replay 10-01: 3,824 crew calls since 09-30 14:10 -> unit-control hits 74 -> 0, every other
route unchanged. Kill: KREW_T0_DATA_IS_NOT_RUN=0."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_t0_unit_control import CREW_SHOT, _gate, _hits, _script, enforcing  # noqa: E402,F401

LEDGER = '{"decisions": [{"reason": "15:11:04 clean explicit `systemctl stop` (clean SIGTERM->exit 0)"}]}\n'


def _window(tmp_path):
    return [h for h in _hits(tmp_path) if h.get("route") == "window"]


def test_reading_a_ledger_in_python_c_is_not_running_it(tmp_path):
    led = tmp_path / "decisions.json"
    led.write_text(LEDGER)
    _gate(tmp_path, "terminal", {"command": f"python3 -c \"import json; d=json.load(open('{led}')); print(len(d))\""})
    assert _window(tmp_path) == []


def test_a_heredoc_that_reads_spillover_text_is_not_running_it(tmp_path):
    sp = tmp_path / "chatcmpl-tool-x.txt"
    sp.write_text('{"task": "systemctl --user restart crew was refused"}')
    _gate(tmp_path, "terminal", {"command": f"python3 - <<'EOF'\nraw = open('{sp}').read()\nprint(len(raw))\nEOF"})
    assert _window(tmp_path) == []


def test_execute_code_that_opens_a_board_db_is_not_running_it(tmp_path):
    db = tmp_path / "kanban.db"
    db.write_bytes(b"SQLite format 3\x00 ... systemctl stop crew ...")
    _gate(tmp_path, "execute_code", {"code": f"import sqlite3\nc = sqlite3.connect('{db}')\n"})
    assert _window(tmp_path) == []


def test_a_patch_to_a_doc_is_prose(tmp_path):
    _gate(tmp_path, "patch", {"path": str(tmp_path / "FINDINGS.md"), "old_string": "x",
                              "new_string": "owed hermes-gateway.service a restart via restart_window - not acted"})
    assert _window(tmp_path) == []


def test_the_1049_wrapper_is_still_caught(tmp_path):
    p = _script(tmp_path, "crew-shot.py", CREW_SHOT, mode=0o644)
    _gate(tmp_path, "terminal", {"command": f"python3 {p}; echo shot_exit=$?"})
    assert _window(tmp_path), "an interpreter operand that is a script still runs"


def test_an_executable_script_in_command_position_is_still_caught(tmp_path):
    p = _script(tmp_path, "shot", "#!/bin/bash\nsystemctl --user restart crew\n")
    _gate(tmp_path, "terminal", {"command": str(p)})
    assert _window(tmp_path)


def test_a_patch_to_a_script_is_still_read(tmp_path):
    _gate(tmp_path, "patch", {"path": str(tmp_path / "x.py"), "old_string": "pass",
                              "new_string": "os.execvp('systemctl', ['systemctl', '--user', 'restart', 'crew'])"})
    assert _window(tmp_path)


def test_the_kill_switch_reads_data_again(tmp_path):
    led = tmp_path / "decisions.json"
    led.write_text(LEDGER)
    _gate(tmp_path, "terminal", {"command": f"python3 -c \"import json; d=json.load(open('{led}'))\""},
          extra={"KREW_T0_DATA_IS_NOT_RUN": "0"})
    assert _window(tmp_path), "KREW_T0_DATA_IS_NOT_RUN=0 is the old behaviour"
