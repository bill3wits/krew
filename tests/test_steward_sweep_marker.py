"""steward-sweep idempotent completion marker (2026-09-21, t_6c42bc2a).

The sweep is a systemd oneshot; a mid-run kill (the "pid not alive" failure
class from t_11b5c766) used to mean a re-run re-filed the card and re-archived.
Now each phase stamps an atomic marker (tmp + fsync + os.replace) into the
runtime board state dir; a re-run skips every phase whose marker carries "done".

Tests here exercise the marker machinery (the python writer embedded in the
bash script) plus the script's pin-level properties. They do NOT run the full
sweep (which would file a real kanban card).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "crew" / "scripts" / "steward-sweep"


def _stamp_code() -> str:
    """Return the python marker writer embedded in the sweep script.

    It lives in a single-quoted heredoc: from "python3 - <<'PYEOF'" to the
    next line that is exactly "PYEOF". The writer uses only os.environ — no argv.
    """
    src = SWEEP.read_text()
    m = re.search(r"python3 - <<'PYEOF'\n(.*?)\nPYEOF", src, re.DOTALL)
    assert m, "marker writer not found in steward-sweep"
    return m.group(1)


def _run_stamp(tmp: Path, phase: str) -> int:
    name_map = {"roll": "roll", "file-card": "file-card"}
    if phase.startswith("archive:"):
        marker = "archive-" + phase.split(":", 1)[1] + ".done"
    elif phase in name_map:
        marker = name_map[phase] + ".done"
    else:
        raise ValueError(f"unknown phase {phase!r}")
    env = dict(os.environ, SWEEP_MARKERS=str(tmp), MARKER_NAME=marker)
    r = subprocess.run(
        [sys.executable, "-c", _stamp_code()],
        env=env, capture_output=True, text=True,
    )
    return r.returncode


def test_stamp_creates_marker(tmp_path):
    assert _run_stamp(tmp_path, "roll") == 0
    assert (tmp_path / "roll.done").exists()
    assert "done" in (tmp_path / "roll.done").read_text()


def test_stamp_is_atomic_no_tmp_leftover(tmp_path):
    tmp_path = tmp_path / "markers"          # krew: the conftest's KREW_HOME also sits in tmp_path
    tmp_path.mkdir()
    for ph in ("roll", "file-card", "archive:t_123abcde"):
        assert _run_stamp(tmp_path, ph) == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["archive-t_123abcde.done", "file-card.done", "roll.done"]
    assert not any(p.name.endswith(".tmp") for p in tmp_path.iterdir())


def test_stamp_is_idempotent(tmp_path):
    _run_stamp(tmp_path, "file-card")
    before = (tmp_path / "file-card.done").read_text()
    assert _run_stamp(tmp_path, "file-card") == 0
    assert (tmp_path / "file-card.done").read_text() == before


def test_script_writes_marker_per_phase():
    code = "\n".join(l for l in SWEEP.read_text().splitlines() if not l.lstrip().startswith("#"))
    # each phase has a post-phase stamp
    for token in ("sweep_phase_stamp roll",
                  "sweep_phase_stamp file-card",
                  'sweep_phase_stamp "archive:$PREV_SWEEP_ID"',
                  "sweep_phase_stamp classify"):
        assert token in code, f"missing idempotency wiring: {token}"


def test_classify_marker_is_stamped_before_the_action():
    """The acceptance criterion: the classification marker is written
    IMMEDIATELY AFTER classification and BEFORE the card is filed (the action).
    A kill -9 in that window must still leave classify.done on disk."""
    code = "\n".join(l for l in SWEEP.read_text().splitlines() if not l.lstrip().startswith("#"))
    i_classify = code.find("sweep_phase_stamp classify")
    i_action = code.find("kanban create")
    assert i_classify != -1, "classify stamp missing"
    assert i_action != -1, "card-creation action missing"
    assert i_classify < i_action, (
        "classify.done must be stamped BEFORE the action (kanban create), "
        "so a kill -9 between classification and action leaves the marker on disk")


def test_classify_marker_is_stamped_before_the_action():
    """The acceptance criterion: the classification marker is written
    IMMEDIATELY AFTER classification and BEFORE the card is filed (the action).
    A kill -9 in that window must still leave classify.done on disk."""
    code = "\n".join(l for l in SWEEP.read_text().splitlines() if not l.lstrip().startswith("#"))
    i_classify = code.find("sweep_phase_stamp classify")
    i_action = code.find("kanban create")
    assert i_classify != -1, "classify stamp missing"
    assert i_action != -1, "card-creation action missing"
    assert i_classify < i_action, (
        "classify.done must be stamped BEFORE the action (kanban create), "
        "so a kill -9 between classification and action leaves the marker on disk")


def test_marker_dir_is_runtime_not_repo():
    code = "\n".join(l for l in SWEEP.read_text().splitlines() if not l.lstrip().startswith("#"))
    assert 'MARKER_DIR="$HOME/.hermes/kanban/boards/$SWEEP_BOARD/sweep-markers"' in code  # 9071e53: the board's name
    # the marker dir must never be inside the repo tree
    assert ".krew/crew" not in code.split("MARKER_DIR=")[1].split("\n")[0]


def test_kill_9_between_phases_leaves_marker():
    """The acceptance criterion: a kill -9 after classification (i.e. after a
    phase finished) but before the next phase must leave that phase's marker
    on disk. We simulate by stamping then asserting the file is durable and
    readable — which is exactly what the re-run's sweep_phase_done checks."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        assert _run_stamp(tmp, "file-card") == 0
        # process "dies" here; marker must already be on disk
        assert tmp.joinpath("file-card.done").is_file()
        # a re-run detects it
        assert (tmp / "file-card.done").exists()
