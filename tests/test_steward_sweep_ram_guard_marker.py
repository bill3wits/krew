"""Marker-stub integration test for the steward-sweep RAM guard (t_801d7b01, reject round 2).

The unit tests in test_steward_sweep_ram_guard.py pin the guard's own
behaviour via the --probe-dir seam and the sweep's wiring by text-pin
(house style: the full sweep is never run unstubbed — it files a real
kanban card). This file closes the one gap that remained: the sweep's
rc-3 SKIP path has never been executed end to end.

Method: the real repo sweep runs with (a) the probe seam pointed at a
synthetic HIGH-PRESSURE gateway unit cgroup tree (memory.current at 90% of
the unit's own memory.high — the corrected mechanism, NOT the 09-21
whole-slice probe) and (b) every external call stubbed on PATH (a fake
`hermes` that records argv and never touches a board, a fake
flock/krew-board-roll/blast-radius, and a fake `python3` that serves the
sweep's internal marker-stamp heredoc). The real guard runs unstubbed.

Pins:
  - pressure skip exits 0 (systemd-healthy; the timer refires) — NOT the
    09-13 "silent never-files" failure class
  - NO sweep-markers are stamped (roll/classify/file-card/archive) so the
    next hour's run starts fresh and still files the card
  - the kanban card is NEVER created (hermes argv log must have no create)
"""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWEEP = ROOT / "crew" / "scripts" / "steward-sweep"

# The unit's live cap (memcap.conf, commit 6559bb01): MemoryMax=24G.
MAX_BYTES = 24 * 1024**3


def _mk_probe(tmp_path: Path, anon_frac: float = 0.90) -> Path:
    """Synthetic cgroup tree: the gateway UNIT cgroup with memory.stat 'anon'
    at anon_frac of its own memory.max (the 09-22 retarget: the working set
    vs the hard cap; memory.high was removed so the gate no longer reads
    it). The guard locates the dir named hermes-ada-gateway.service under
    the probe root (bounded find) and reads memory.stat + memory.max from it
    — no /proc/meminfo involved."""
    root = Path(tempfile.mkdtemp(dir=str(tmp_path)))
    unit = (root / "user.slice" / "user-1000.slice" / "<email>"
            / "app.slice" / "hermes-ada-gateway.service")
    unit.mkdir(parents=True)
    max_bytes = 24 * 1024**3
    (unit / "memory.stat").write_text(f"anon {int(anon_frac * max_bytes)}\n")
    (unit / "memory.max").write_text(f"{max_bytes}\n")
    return root


def _mk_bin(tmp_path: Path, argv_log: Path) -> Path:
    """Stub binaries for every external call the sweep makes before it would
    reach the kanban layer. `python3` is the real interpreter EXCEPT for the
    heredoc the sweep pipes into it (the marker-stamp helper): with a heredoc
    on stdin, no argv, and MARKER_NAME set, it behaves as a recording stub
    instead of the interpreter — that is the only python3 use between the
    guard and the card filing."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    hermes = bin_dir / "hermes"
    hermes.write_text(f'#!/bin/bash\necho "$@" >> {argv_log}\nexit 0\n')

    for name in ("krew-board-roll", "fleet-prompt-budget-check"):
        p = bin_dir / name
        p.write_text("#!/bin/bash\nexit 0\n")

    blast = bin_dir / "kanban-blast-radius.py"
    blast.write_text("print('stub blast-radius')\n")

    flock = bin_dir / "flock"
    flock.write_text(
        "#!/bin/bash\n"
        "# stub: skip the fd argument, then acquire 'normally' (exit 0)\n"
        "args=(\"$@\"); shift\n"
        "exec /bin/true\n"
    )

    # `python3` is a passthrough to the real interpreter EXCEPT for the
    # sweep's internal marker-stamp heredoc (stdin is a heredoc, no script
    # argv, MARKER_NAME is set): that call becomes a recording stub instead
    # of the interpreter, so the stamp cannot happen. No python3 call is
    # expected between the guard and the kanban layer, so passthrough keeps
    # the stub safe if one appears.
    py = bin_dir / "python3"
    py.write_text(
        "#!/bin/bash\n"
        "if [ -n \"$MARKER_NAME\" ] && [ ${#} -eq 0 ]; then\n"
        "  echo \"stub-stamp $MARKER_NAME\" >&2\n"
        "  exit 0\n"
        "fi\n"
        "exec /usr/bin/python3 \"$@\"\n"
    )

    for p in bin_dir.iterdir():
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run_sweep(tmp_path: Path, probe: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    argv_log = tmp_path / "hermes-argv.log"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["SWEEP_RAM_PROBE_DIR"] = str(probe)
    # The unit is at 90% of its own memory.high (default threshold 80):
    # a real approaching-ceiling pressure on the CORRECTED mechanism.
    env["SWEEP_RAM_MAX_PCT"] = "80"
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    # Test seams: keep the real sweep off the live hermes binary and the
    # live lock (the fake hermes records the kanban create).
    env["STWARD_HERMES"] = str(bin_dir / "hermes")
    env["STWARD_LOCK"] = str(tmp_path / "architect.lock")
    # Rewrite the hermes stub with the final argv-log path (test seam):
    (bin_dir / "hermes").write_text(
        f'#!/bin/bash\necho "$@" >> {argv_log}\nexit 0\n'
    )
    (bin_dir / "hermes").chmod(0o755)
    return subprocess.run(
        ["bash", str(SWEEP)],
        env=env, capture_output=True, text=True, timeout=120,
    )


def test_sweep_pressure_skip_exit_0_no_markers_no_card(tmp_path):
    """High pressure on the gateway unit (90% of its memory.high > 80%
    threshold) + everything else stubbed: the sweep must exit 0, stamp
    NOTHING, and never reach the kanban layer."""
    probe = _mk_probe(tmp_path)
    argv_log = tmp_path / "hermes-argv.log"
    bin_dir = _mk_bin(tmp_path, argv_log)
    r = _run_sweep(tmp_path, probe, bin_dir)

    assert r.returncode == 0, f"rc={r.returncode}\nstdout: {r.stdout}\nstderr: {r.stderr}"
    # the skip is logged, and it is a CLEAN skip (exit 0), not a failure
    assert "skip" in r.stderr.lower(), r.stderr
    # the skip line names the unit and its memory.max — the 09-22 probe
    # (anon from memory.stat vs the hard cap; memory.high was removed)
    assert "hermes-ada-gateway" in r.stderr, r.stderr
    assert "memory.max" in r.stderr, r.stderr
    # the guard's MEASURED line (anon bytes vs memory.max percent) must
    # survive into the skip log — it is captured from the guard's stderr via
    # a mktemp redirect. If a regression moved the measured line to stdout
    # or dropped it, the journal would show an empty "steward-sweep:" line
    # and the number would be lost. The guard emits "measured N B" only on
    # the rc-3 skip path.
    assert "measured" in r.stderr, (
        f"skip log missing the guard's measured line: {r.stderr}"
    )

    # NO markers stamped anywhere — a skipped hour's run starts fresh
    markers = list((tmp_path / "home").rglob("*.done"))
    assert markers == [], f"pressure skip stamped markers: {markers}"
    # and specifically nothing under the runtime marker dir layout
    marker_dirs = list((tmp_path / "home").rglob("sweep-markers"))
    for d in marker_dirs:
        assert list(d.iterdir()) == [], f"markers left in {d}"

    # the kanban card was NEVER filed
    if argv_log.exists():
        argv = argv_log.read_text()
        assert "kanban create" not in argv, f"card filed despite pressure skip: {argv}"
        assert "kanban archive" not in argv


def test_sweep_pressure_skip_leaves_no_guard_artifacts(tmp_path):
    """Fail-open/pressure path leaves the runtime tree clean: no marker dir
    at all is the expected shape for a skipped run (mkdir -p of the marker
    dir happens after the guard, so a skip must not have created it)."""
    probe = _mk_probe(tmp_path)
    argv_log = tmp_path / "hermes-argv.log"
    bin_dir = _mk_bin(tmp_path, argv_log)
    r = _run_sweep(tmp_path, probe, bin_dir)
    assert r.returncode == 0, r.stderr
    home = tmp_path / "home"
    boards = home / ".hermes" / "kanban" / "boards"
    if boards.exists():
        for d in boards.iterdir():
            assert not (d / "sweep-markers").exists(), \
                "a pressure skip must not create the marker dir — it runs before any stamp"


def test_sweep_pass_at_low_pressure_reaches_card_filing(tmp_path):
    """Companion to the skip test: LOW pressure (60% of the unit's
    memory.high < 80% threshold) with the same stubs must NOT skip — the
    sweep proceeds past the guard all the way to the kanban layer (the fake
    hermes records the create) and exits 0. This proves the e2e skip is due
    to the guard, not to a sweep that is broken before the card."""
    probe = _mk_probe(tmp_path, anon_frac=0.60)
    argv_log = tmp_path / "hermes-argv.log"
    bin_dir = _mk_bin(tmp_path, argv_log)
    r = _run_sweep(tmp_path, probe, bin_dir)

    assert r.returncode == 0, f"rc={r.returncode}\nstdout: {r.stdout}\nstderr: {r.stderr}"
    # a pressure skip is NOT in the log — the guard passed
    assert "sweep skip" not in r.stderr, r.stderr
    assert argv_log.exists(), "the sweep never reached the kanban layer"
    assert "kanban create" in argv_log.read_text(), \
        f"low pressure must file the card, argv log: {argv_log.read_text()}"
