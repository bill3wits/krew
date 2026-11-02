"""gpu_budget.py + its render-submit.py integration: the render worker's GPU
memory pre-check (the User 2026-09-17, rework of the 21:17 blocked run 500).

Card t_55c01d27:
  * precheck BEFORE park/staging/POST — here proven hermetically by running
    render-submit.py with a STUB scripts/gpu_budget.py on a sandbox tree
    (render-submit.py does `sys.path.insert(0, str(ROOT / "crew" / "lib"))` — ROOT is
    this file's parents[1] — so a sandbox copy of the real script imports the
    stub; the real guard's logic is covered by test_gpu_budget.py).
  * free < required  -> ok=False, block issues 'Insufficient GPU memory'
    with the stats, exit 4.
  * free >= required -> ok=True, no block, normal path proceeds.
  * unmeasurable     -> ok=False, fail-closed block, exit 5.
  * the render is NOT attempted: the stub's block() sets a flag file; the
    low-VRAM run must set it and must never reach the park/stage/POST
    markers (the real render-submit.py prints them on its way through).
  * threshold: max(KREW_RENDER_MIN_FREE_MIB, KREW_RENDER_MIN_FREE_FRAC *
    total) — with the card's defaults 4096 / 0.10 on a 97887 MiB card the
    bar is 9788.7 MiB, NOT NEED_GIB (24 GiB).

Hermetic: fake nvidia-smi on PATH, a stub render-submit.py copy that carries
the real precheck call, a sandbox HOME/KREW_HERMES so no real jobs file or
board is touched. No live Comfy.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REAL_SUBMIT = ROOT / "crew" / "scripts" / "render-submit.py"
TOTAL = 97887  # MiB, the card on this house
THRESHOLD = f"{TOTAL * 0.10:.1f}"  # 9788.7

# The real precheck call, transcribed VERBATIM from scripts/render-submit.py
# (kept in sync; the test asserts it is still present there).
#
# 2026-09-18 (t_dd9ed1df, Option C): the terminal block() call is now gated on
# `not a.under_hook` — under the post-execution hook the worker has no kanban
# authority and the hook (run_render_submit.sh) issues the block. The stub
# below has no --under-hook flag, so `a.under_hook` is False and the block()
# is issued exactly as before (these tests exercise the standalone path).
PRECHECK_CALL = """\
    # GPU pre-check (t_55c01d27; scoped 2026-09-19, User note on t_6260608e
    # "check VRAM only when the job will not park ops"): the budget gate is a
    # hard gate at the top of the task, before anything is written to the jobs
    # file — but it applies ONLY when the run will not run `krew-mode render`.
    # When it parks, the park frees ops' VRAM, so a "busy now" reading is the
    # expected state (the 2026-09-18 15:50 synthetic smoke exited 4 on exactly
    # that), and the 24 GiB NEED_GIB wait below the park is the
    # run-specific gate. ok -> proceed; not ok and no park allowed -> the
    # block is issued HERE (one owner, per the wrapper protocol) and the
    # worker exits 4/5; the render is not attempted. The standalone
    # `gpu_budget.py` probe is read-only and never blocks.
    if a.no_park:
        _g = gpu_budget.precheck(a.card)
        if not _g.ok:
            print(_g.reason, file=sys.stderr)
            brc, bout = _g.block(a.card)
            if brc != 0:
                print(f"kanban block failed rc={brc}: {bout}", file=sys.stderr)
            sys.exit(gpu_budget.EXIT_UNMEASURABLE if _g.free_mib is None else gpu_budget.EXIT_INSUFFICIENT)
"""

SUBMIT_STUB_TEMPLATE = """#!/usr/bin/env python3
\"\"\"STUB of scripts/render-submit.py for the gpu_budget integration test.

It carries the REAL precheck call — the exact code this test also asserts
exists in the real scripts/render-submit.py — and replaces the park/stage/
POST phases with printed markers plus a success exit. It never touches
Comfy, nvidia-smi (beyond the stub the test puts on PATH) or the board.
\"\"\"
from __future__ import annotations
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpu_budget

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True)
    ap.add_argument("--name")
    ap.add_argument("--seed")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--recipe")
    ap.add_argument("--seconds", type=int)
    ap.add_argument("--plate")
    ap.add_argument("--size")
    ap.add_argument("--steps", type=int)
    ap.add_argument("--cfg", type=float)
    ap.add_argument("--noise-seed", type=int)
    ap.add_argument("--out-dir")
    ap.add_argument("--no-park", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--under-hook", action="store_true")
    a = ap.parse_args()
    PRECHECK_PLACEHOLDER
    print("[MARK:precheck-passed]")
    print("[MARK:park-path-reached]")
    print('[MARK:comfy-check]', file=sys.stderr)
    print('[{"ok": true, "prompt_id": "stub", "take": 1, "out_dir": "/stub"}]')
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""


def _stub_guard_source(free_mib: int | None) -> str:
    """Source for the sandbox stub gpu_budget.py — 0-indent, no dedent games."""
    if free_mib is None:
        block_lines = [
            '            flag = pathlib.Path(os.environ.get("GPU_TEST_FLAG", "gpu-test.flag"))',
            '            flag.write_text("blocked:unmeasurable")',
            "            print('[gpu_budget] blocked (unmeasurable)', file=sys.stderr)",
        ]
    else:
        block_lines = [
            '            flag = pathlib.Path(os.environ.get("GPU_TEST_FLAG", "gpu-test.flag"))',
            f'            flag.write_text(f"blocked:insufficient:{free_mib}")',
            "            print('[gpu_budget] blocked (insufficient)', file=sys.stderr)",
        ]
    free_expr = str(free_mib) if free_mib is not None else "None"
    lines = [
        'from __future__ import annotations',
        'import os, pathlib, sys',
        'from dataclasses import dataclass, field',
        f'TOTAL = {TOTAL}',
        'MIN_FREE_MIB = float(os.environ.get("KREW_RENDER_MIN_FREE_MIB", "4096"))',
        'MIN_FREE_FRAC = float(os.environ.get("KREW_RENDER_MIN_FREE_FRAC", "0.10"))',
        'EXIT_INSUFFICIENT = 4',
        'EXIT_UNMEASURABLE = 5',
        'EXIT_OK = 0',
        '@dataclass',
        'class GpuBudget:',
        '    total_mib: float | None',
        '    used_mib: float | None',
        '    free_mib: float | None',
        '    threshold_mib: float | None',
        '    source: str',
        '    per_process: list[str] = field(default_factory=list)',
        '    @property',
        '    def ok(self) -> bool:',
        '        if self.free_mib is None or self.threshold_mib is None:',
        '            return False',
        '        return self.free_mib >= self.threshold_mib',
        '    @property',
        '    def reason(self) -> str:',
        '        if self.free_mib is None:',
        '            return "Insufficient GPU memory — VRAM could not be measured (source=none); refusing to render blind."',
        '        used = (f"{self.used_mib:.0f} MiB" if self.used_mib is not None else "?")',
        '        total = (f"{self.total_mib:.0f} MiB" if self.total_mib is not None else "?")',
        '        return (f"Insufficient GPU memory: {self.free_mib:.0f} MiB free of {total} "',
        '                f"({used} used) < required {self.threshold_mib:.1f} MiB "',
        '                f"(max({MIN_FREE_MIB:.0f} MiB, {MIN_FREE_FRAC:.0%} of total), source=stub).")',
        '    def block(self, card):',
        *block_lines,
        '            return 0, "stub block"',
        'def precheck(card=None):',
        f'    if {free_expr} == None:',
        '        return GpuBudget(None, None, None, None, "none")',
        f'    free = float({free_expr})',
        '    g = GpuBudget(float(TOTAL), float(TOTAL - free), free, None, "stub-nvidia-smi")',
        '    g.threshold_mib = max(MIN_FREE_MIB, MIN_FREE_FRAC * float(TOTAL))',
        '    return g',
    ]
    return "\n".join(lines) + "\n"


def _make_sandbox(tmp: Path, free_mib: int | None) -> Path:
    """Sandbox tree: stub gpu_budget.py, stub render-submit.py, lib/ dir,
    fake nvidia-smi on PATH (or none), fake hermes logger."""
    scripts = tmp / "rails" / "scripts"
    (scripts / "lib").mkdir(parents=True)
    (scripts / "lib" / "render_window_gate.py").write_text(
        "def refuse_reason(card):\n    return None\nMARK = 'x'\n")

    scripts.joinpath("gpu_budget.py").write_text(_stub_guard_source(free_mib))

    submit_src = REAL_SUBMIT.read_text()
    assert PRECHECK_CALL.strip() in submit_src, (
        "scripts/render-submit.py no longer contains the precheck call this "
        "test transcribes — keep them in sync (or update the test's copy)."
    )
    scripts.joinpath("render-submit.py").write_text(
        SUBMIT_STUB_TEMPLATE.replace(
            "PRECHECK_PLACEHOLDER", PRECHECK_CALL.lstrip("\n")))

    bin_ = tmp / "bin"
    bin_.mkdir()
    if free_mib is not None:
        used = TOTAL - free_mib
        ns = bin_ / "nvidia-smi"
        ns.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"$*\" == *compute-apps* ]]; then exit 0; fi\n"
            f"echo \"{TOTAL},{used},{free_mib}\"\n")
        ns.chmod(ns.stat().st_mode | stat.S_IEXEC)
    # fake hermes: log calls, never touch the board
    hermes = bin_ / "hermes"
    log = tmp / "hermes-calls.log"
    hermes.write_text("#!/usr/bin/env bash\n"
                      f"printf '%s\\n' \"$@\" >> {log}\n")
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    return tmp


def _run(tmp: Path, free_mib: int | None, *, no_park: bool = True) -> subprocess.CompletedProcess:
    sandbox = _make_sandbox(tmp, free_mib)
    env = dict(os.environ)
    env["PATH"] = f"{sandbox / 'bin'}:{env['PATH']}"
    env.pop("KREW_RENDER_WAIT_SEC", None)
    env["HERMES_KANBAN_TASK"] = "t_selftest"
    env["KREW_HERMES"] = str(tmp / "home")     # jobs file goes to the sandbox
    env["HOME"] = str(tmp / "home")
    env["GPU_TEST_FLAG"] = str(tmp / "gpu-block.flag")
    (tmp / "home").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, str(sandbox / "rails" / "scripts" / "render-submit.py"),
         "--card", "t_selftest", "--name", "stub",
         "--seed", str(sandbox / "seed.png"),
         "--prompt", "stub prompt", "--recipe", "ltx25-flf2v"]
        + (["--no-park"] if no_park else []),
        capture_output=True, text=True, env=env, cwd=str(sandbox),
    )


def test_low_vram_blocks_before_park(tmp_path):
    """Card AC #1: 3694 MiB free < 9788.7 threshold -> exit 4, block() issued
    with 'Insufficient GPU memory' + stats, and NO park/stage/POST reached."""
    r = _run(tmp_path, 3694)
    assert r.returncode == 4, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "Insufficient GPU memory" in out
    assert "3694 MiB free of 97887" in out
    assert THRESHOLD in out                      # 9788.7 MiB required
    flag = (tmp_path / "gpu-block.flag")
    assert flag.is_file() and flag.read_text().startswith("blocked:insufficient"), \
        "gpu_budget.block() was not called"
    # the render itself was not attempted
    assert "[MARK:precheck-passed]" not in r.stdout
    assert "[MARK:park-path-reached]" not in r.stdout
    assert "[MARK:comfy-check]" not in r.stderr
    # no terminal kanban call from inside the submit phase (the wrapper owns it)
    assert not (tmp_path / "hermes-calls.log").exists()


def test_ample_vram_proceeds(tmp_path):
    """Card AC #2: 50000 MiB free >= 9788.7 -> ok, no block, normal path."""
    r = _run(tmp_path, 50000)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[MARK:precheck-passed]" in r.stdout
    assert "[MARK:park-path-reached]" in r.stdout
    assert '"ok": true' in r.stdout
    assert not (tmp_path / "gpu-block.flag").exists(), "block() must not be called"
    assert not (tmp_path / "hermes-calls.log").exists()


def test_unmeasurable_fails_closed(tmp_path):
    """Card AC #3: no VRAM source -> ok=False, block, exit 5 (fail-closed)."""
    r = _run(tmp_path, None)
    assert r.returncode == 5, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "could not be measured" in out
    flag = (tmp_path / "gpu-block.flag")
    assert flag.is_file() and flag.read_text() == "blocked:unmeasurable"
    assert "[MARK:precheck-passed]" not in r.stdout
    assert "[MARK:park-path-reached]" not in r.stdout


def test_threshold_is_max_not_need_gib(tmp_path):
    """5120 MiB free: above the 4096 floor, below the 10% term (9788.7) —
    must still block, and the reason must carry the 10% threshold, not
    NEED_GIB (24 GiB)."""
    r = _run(tmp_path, 5120)
    assert r.returncode == 4
    out = r.stdout + r.stderr
    assert THRESHOLD in out
    assert "24 GiB" not in out


def test_precheck_call_present_in_real_submit():
    """The stub only proves integration if the REAL script has the same
    call, BEFORE the park path and with no dry-run escape."""
    src = REAL_SUBMIT.read_text()
    assert PRECHECK_CALL.strip() in src
    pre_i = src.index("GPU pre-check (t_55c01d27")
    # scoped 2026-09-19: the gate applies only to a run that will not park ops
    assert "    if a.no_park:\n        _g = gpu_budget.precheck(a.card)" in src
    # The post-park shortfall mark (f5d289e6, 09-22: "only None GiB free" became `said`).
    assert pre_i < src.index('_mark("failed", error=f"{said} after krew-mode render wan")'), \
        "precheck must run before the park/VRAM-wait path"
    assert pre_i < src.index("if a.dry_run:"), \
        "precheck must run before the --dry-run escape"
    # no escape hatch that would re-enable OOM-blind renders
    for env in ("KREW_RENDER_GPU_DISABLE", "GPU_DISABLE"):
        assert env not in src


def test_low_vram_does_not_refuse_a_run_that_will_park(tmp_path):
    """2026-09-19 (t_6260608e, User: "check VRAM only when the job will not park
    ops"): from Standard, ops + crew hold ~77 GB and free sits near 7 GB, so the
    pre-park gate refused every render card before it could make room. A run
    that parks goes on to the park path; the post-park 24 GiB wait gates it."""
    r = _run(tmp_path, 3694, no_park=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[MARK:park-path-reached]" in r.stdout
    assert not (tmp_path / "gpu-block.flag").exists()
