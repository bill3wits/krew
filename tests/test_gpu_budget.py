"""gpu_budget.py: the render worker's GPU memory pre-check (the User 2026-09-17).

Acceptance under test (from the card):
  1. Simulated low GPU memory  -> the guard returns ok=False with
     'Insufficient GPU memory' + the stats in the reason, and the exit code
     is EXIT_INSUFFICIENT (4). The render is NOT attempted.
  2. Simulated ample VRAM      -> ok=True, no block call, exit 0.
  3. No VRAM source at all     -> fail-CLOSED: ok=False, 'could not be
     measured', exit EXIT_UNMEASURABLE (5).
  4. The standalone probe is READ-ONLY (adversarial review, attack 3): it
     never calls `hermes kanban block` — the worker import path is the only
     blocker.

The test is hermetic: fake `nvidia-smi` / fake `hermes` executables on PATH,
and KREW_COMFY_URL pointed at a dead port so Comfy never answers. The
worker-level wiring (that render-submit.py actually calls precheck and blocks)
is covered by tests/test_render_submit_gpu_precheck.py.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "crew" / "scripts" / "gpu_budget.py"
TOTAL = 97887  # MiB, the card on this house


def _bins(tmp: Path, free_mib: int | None) -> Path:
    """Fake `nvidia-smi` (prints a budget, or dies) + fake `hermes` (logs)."""
    bin_ = tmp / "bin"
    bin_.mkdir(parents=True)
    ns = bin_ / "nvidia-smi"
    if free_mib is None:
        ns.write_text("#!/usr/bin/env bash\nexit 9\n")
    else:
        used = TOTAL - free_mib
        ns.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"$*\" == *compute-apps* ]]; then exit 0; fi\n"
            f"echo \"{TOTAL},{used},{free_mib}\"\n"
        )
    ns.chmod(ns.stat().st_mode | stat.S_IEXEC)
    hermes = bin_ / "hermes"
    log = tmp / "hermes-calls.log"
    hermes.write_text("#!/usr/bin/env bash\n" + f'printf \'%s\\n\' "$@" >> {log}\n')
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    return bin_


def _run(tmp: Path, free_mib: int | None) -> subprocess.CompletedProcess:
    bin_ = _bins(tmp, free_mib)
    env = dict(os.environ)
    env["PATH"] = f"{bin_}:{env['PATH']}"
    env["KREW_COMFY_URL"] = "http://127.0.0.1:1"   # dead port: no comfy stats
    env.pop("KREW_RENDER_WAIT_SEC", None)
    env.pop("HERMES_KANBAN_TASK", None)
    # Hermetic guard (review nit 7): resolve the fakes from THIS env's PATH
    # before trusting the shadow.
    assert shutil.which("nvidia-smi", path=env["PATH"]) == str(bin_ / "nvidia-smi")
    assert shutil.which("hermes", path=env["PATH"]) == str(bin_ / "hermes")
    return subprocess.run(
        [sys.executable, str(MOD)],
        capture_output=True, text=True, env=env, cwd=str(tmp),
    )


def test_simulated_low_memory_refuses(tmp_path):
    """Card AC #1 (guard level): low VRAM -> ok=False, stats in the reason,
    exit 4. The standalone probe does NOT block the board (read-only)."""
    r = _run(tmp_path, 3694)
    assert r.returncode == 4, r.stdout + r.stderr
    assert '"ok": false' in r.stdout
    assert '"free_mib": 3694.0' in r.stdout
    assert "Insufficient GPU memory" in r.stdout
    assert "3694 MiB free of 97887" in r.stdout
    assert "Not rendering" in r.stdout
    # the probe is read-only: no kanban call, even though the fake hermes logs
    assert not (tmp_path / "hermes-calls.log").exists()


def test_simulated_ample_memory_passes(tmp_path):
    """Plenty free -> ok, exit 0, no kanban call."""
    r = _run(tmp_path, 50000)
    assert r.returncode == 0, r.stdout + r.stderr
    assert '"ok": true' in r.stdout
    assert not (tmp_path / "hermes-calls.log").exists()


def test_no_source_fails_closed(tmp_path):
    """Nothing measurable -> fail-closed, exit 5, 'could not be measured'."""
    r = _run(tmp_path, None)
    assert r.returncode == 5, r.stdout + r.stderr
    assert '"ok": false' in r.stdout
    assert "could not be measured" in r.stdout
    assert not (tmp_path / "hermes-calls.log").exists()


def test_fraction_term_dominates(tmp_path):
    """97887 MiB @ 10% = 9788.7 MiB > 4096 floor: 5120 MiB free (above the
    floor, below the fraction) must still refuse."""
    r = _run(tmp_path, 5120)
    assert r.returncode == 4
    assert "9788.7" in r.stdout


def test_just_under_threshold_refuses(tmp_path):
    """9700 MiB free < 9788.7 threshold -> refuse, reported verbatim."""
    r = _run(tmp_path, 9700)
    assert r.returncode == 4
    assert '"free_mib": 9700.0' in r.stdout
