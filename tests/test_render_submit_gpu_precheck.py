"""render-submit.py wiring: the gpu_budget precheck runs inside the WORKER,
not just in the standalone probe (adversarial review attack 1, t_55c01d27).

Hermetic: a fake `nvidia-smi` on PATH + a fake `hermes` on PATH + a temp
seed/plate; Comfy pointed at a dead port. We never reach POST /prompt — the
low-VRAM case exits 4 (or 5) inside precheck, before jobs-file writes and
before the park path.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = ROOT / "crew" / "scripts" / "render-submit.py"
TOTAL = 97887



def _comfy_root(tmp):
    """krew: the test carries its own ComfyUI folder (the reference box read its live /comfy tree); the graph is the
    wan22-i2v recipe's node ids with empty inputs, which is all render-submit touches before the POST."""
    root = tmp / "ComfyUI"
    (root / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "input").mkdir(exist_ok=True)
    graph = {n: {"class_type": "stub", "inputs": {}} for n in ("9", "10", "11", "12", "13", "14", "16", "17")}
    (root / "workflows" / "apple_split_wan_i2v.json").write_text(json.dumps(graph))
    return str(root)

def _mkbins(tmp: Path, free_mib: int | None) -> Path:
    bin_ = tmp / "bin"
    bin_.mkdir(parents=True)
    ns = bin_ / "nvidia-smi"
    if free_mib is None:
        ns.write_text("#!/usr/bin/env bash\nexit 9\n")
    else:
        ns.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"$*\" == *compute-apps* ]]; then exit 0; fi\n"
            f"echo \"{TOTAL},{TOTAL - free_mib},{free_mib}\"\n"
        )
    ns.chmod(ns.stat().st_mode | stat.S_IEXEC)
    hermes = bin_ / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$@" >> {tmp / "hermes-calls.log"}\n'
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    return bin_


def _run(tmp: Path, free_mib: int | None) -> subprocess.CompletedProcess:
    seed = tmp / "seed.png"; seed.write_bytes(b"\x89PNG-fake")
    env = dict(os.environ)
    env["PATH"] = f"{_mkbins(tmp, free_mib)}:{env['PATH']}"
    env["KREW_COMFY_URL"] = "http://127.0.0.1:1"
    env["KREW_HERMES"] = str(tmp)
    env["KREW_COMFY_ROOT"] = _comfy_root(tmp)                 # jobs file stays in the tmp
    env.pop("KREW_RENDER_WAIT_SEC", None)
    env.pop("HERMES_KANBAN_TASK", None)
    return subprocess.run(
        [sys.executable, str(SUBMIT),
         "--card", "t_selftest", "--name", "selftest",
         "--seed", str(seed), "--prompt", "selftest prompt",
         "--dry-run", "--no-park"],
        capture_output=True, text=True, env=env, cwd=str(tmp),
    )


def test_low_vram_blocks_the_worker(tmp_path):
    """AC (a) at the worker level: low VRAM -> worker exits 4 (insufficient),
    blocks the card with 'Insufficient GPU memory' + the stats, and does NOT
    reach the dry-run render (no jobs file, no 'dry_run' output)."""
    r = _run(tmp_path, 3694)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "Insufficient GPU memory" in r.stderr
    assert "3694 MiB free of 97887" in r.stderr
    assert '"dry_run"' not in r.stdout
    calls = (tmp_path / "hermes-calls.log").read_text()
    assert "kanban" in calls and "block" in calls and "t_selftest" in calls
    assert "Insufficient GPU memory" in calls
    # the jobs file was never written — the render was not started
    assert not (tmp_path / "render-jobs.json").exists()


def test_unmeasurable_vram_fails_closed_in_worker(tmp_path):
    """nvidia-smi dead -> the worker exits 5 (fail-closed) and still blocks."""
    r = _run(tmp_path, None)
    assert r.returncode == 5, r.stdout + r.stderr
    assert "could not be measured" in r.stderr
    calls = (tmp_path / "hermes-calls.log").read_text()
    assert "block" in calls and "t_selftest" in calls
    assert not (tmp_path / "render-jobs.json").exists()


def test_ample_vram_passes_to_dry_run(tmp_path):
    """Plenty free -> precheck passes, worker proceeds to the dry-run output."""
    r = _run(tmp_path, 50000)
    assert r.returncode == 0, r.stdout + r.stderr
    assert '"dry_run": true' in r.stdout
    assert not (tmp_path / "hermes-calls.log").exists()
