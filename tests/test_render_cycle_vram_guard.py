"""render-cycle.py pre-loop VRAM guard (t_724afc97).

The render loop (generate a still, Lila/Vera judge via the 27B crew vision
call, iterate) must not start a round its judge cannot fit. The guard runs
before round 1, reads free VRAM via gpu_budget, and fails fast with a clear
message when below the floor (default 6 GB; fail-closed when unmeasurable).

Hermetic: a fake `nvidia-smi` on PATH (free or dead) + a fake mode file
pointing at "render" + the Comfy plugin pointed at a dead URL. The test
targets the guard function `_vram_ok` directly and the `cycle` wiring via
the skip seam, so it never reaches the Comfy server or the 27B judge.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CYCLE = ROOT / "crew" / "scripts" / "render-cycle.py"
TOTAL = 97887  # the house's card, MiB


def _load_cycle():
    """Load render-cycle.py as a module (it's a script, not a package)."""
    spec = importlib.util.spec_from_file_location("render_cycle_under_test", CYCLE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_smi_bin(tmp: Path, free_mib: int | None) -> Path:
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
    return bin_


def _env_patch(bin_: Path, mode_file: Path | None = None, *, keep_skip: bool = False):
    """Set the PATH/mode/comfy env the guard reads; return restore fn."""
    old = {k: os.environ.get(k) for k in
           ("PATH", "KREW_COMFY_URL", "KREW_GPU_MODE_FILE",
            "KREW_RENDER_SKIP_VRAM_GUARD", "KREW_RENDER_LOOP_MIN_FREE_MIB",
            "KREW_RENDER_MIN_FREE_MIB", "KREW_RENDER_MIN_FREE_FRAC")}
    os.environ["PATH"] = f"{bin_}:{os.environ.get('PATH', '')}"
    os.environ["KREW_COMFY_URL"] = "http://127.0.0.1:1"  # dead — no live Comfy in tests
    os.environ.pop("KREW_RENDER_SKIP_VRAM_GUARD", None)
    os.environ.pop("KREW_RENDER_LOOP_MIN_FREE_MIB", None)
    os.environ.pop("KREW_RENDER_MIN_FREE_MIB", None)
    os.environ.pop("KREW_RENDER_MIN_FREE_FRAC", None)
    if mode_file is not None:
        os.environ["KREW_GPU_MODE_FILE"] = str(mode_file)
    else:
        os.environ.pop("KREW_GPU_MODE_FILE", None)
    if keep_skip:
        os.environ["KREW_RENDER_SKIP_VRAM_GUARD"] = "1"

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return restore


def test_guard_refuses_below_floor(tmp_path):
    """Below the floor -> the guard returns a clear refusal message."""
    mod = _load_cycle()
    bin_ = _fake_smi_bin(tmp_path, 5945)  # the measured 2026-09-20 state
    restore = _env_patch(bin_)
    try:
        msg = mod._vram_ok()
    finally:
        restore()
    assert msg, "the guard should refuse at 5945 MiB free (below the floor)"
    assert "5945" in msg
    assert "27B vision judge" in msg  # the clear reason names the consumer


def test_guard_passes_at_6_5gb(tmp_path):
    """Card acceptance target: 6.5 GB free -> the loop runs (no refusal)."""
    mod = _load_cycle()
    bin_ = _fake_smi_bin(tmp_path, 6656)  # 6.5 GiB
    restore = _env_patch(bin_)
    try:
        msg = mod._vram_ok()
    finally:
        restore()
    assert msg == "", f"6.5 GiB free should pass the guard, got: {msg}"


def test_guard_fails_closed_when_unmeasurable(tmp_path):
    """nvidia-smi dead AND Comfy dead -> fail-closed refusal, not a silent start."""
    mod = _load_cycle()
    bin_ = _fake_smi_bin(tmp_path, None)
    restore = _env_patch(bin_)
    try:
        msg = mod._vram_ok()
    finally:
        restore()
    assert msg, "unmeasurable VRAM must refuse (fail-closed)"
    assert "could not be measured" in msg


def test_cycle_wiring_exits_3_below_floor(tmp_path):
    """The guard is wired into cycle(): below the floor, cycle exits 3
    before round 1. The mode file is set BEFORE the module loads (MODE_FILE
    is a module-level constant). _load_comfy is patched to a fake so the
    loop reaches the guard, not the Comfy-down exit."""
    mode_file = tmp_path / "krew-gpu-mode"
    mode_file.write_text("render\n")
    bin_ = _fake_smi_bin(tmp_path, 5945)
    restore = _env_patch(bin_, mode_file)
    try:
        mod = _load_cycle()

        class _Comfy:
            @staticmethod
            def comfy_up():
                return True
        mod._load_comfy = lambda: _Comfy
        try:
            mod.cycle("test spec", model="flux2-turbo", aspect="landscape",
                      max_rounds=3)
            raised = None
        except SystemExit as e:
            raised = e.code
    finally:
        restore()
    assert raised == 3, f"cycle should exit 3 below the floor, got {raised!r}"


def test_skip_seam_bypasses_guard(tmp_path):
    """KREW_RENDER_SKIP_VRAM_GUARD=1 lets the loop start (test seam)."""
    mode_file = tmp_path / "krew-gpu-mode"
    mode_file.write_text("render\n")
    bin_ = _fake_smi_bin(tmp_path, 5945)  # below the floor, but the seam skips
    restore = _env_patch(bin_, mode_file, keep_skip=True)
    try:
        mod = _load_cycle()

        class _Comfy:
            @staticmethod
            def comfy_up():
                return True

        gen_called = {"hit": False}

        def _fake_generate(comfy, prompt, model, aspect):
            gen_called["hit"] = True
            raise SystemExit(99)  # stop after proving generate was reached

        orig_load = mod._load_comfy
        orig_gen = mod.generate
        mod._load_comfy = lambda: _Comfy
        mod.generate = _fake_generate
        try:
            mod.cycle("test spec", model="flux2-turbo", aspect="landscape",
                      max_rounds=1)
            raised = None
        except SystemExit as e:
            raised = e.code
        finally:
            mod._load_comfy = orig_load
            mod.generate = orig_gen
    finally:
        restore()
        os.environ.pop("KREW_RENDER_SKIP_VRAM_GUARD", None)
    assert gen_called["hit"], "the skip seam must let the loop reach round 1"
    assert raised == 99


def test_guard_floor_honours_env_override(tmp_path):
    """KREW_RENDER_LOOP_MIN_FREE_MIB=2048 lets a 3 GB-free machine through."""
    mod = _load_cycle()
    bin_ = _fake_smi_bin(tmp_path, 3072)  # 3 GiB — below the default 6096 floor
    restore = _env_patch(bin_)
    os.environ["KREW_RENDER_LOOP_MIN_FREE_MIB"] = "2048"
    try:
        msg = mod._vram_ok()
    finally:
        restore()
    assert msg == "", f"3 GiB free should pass with a 2048 MiB floor, got: {msg}"


def test_silo_still_refuses_non_render_mode(tmp_path):
    """The guard does not weaken the occupancy silo: non-render mode still exits."""
    mode_file = tmp_path / "krew-gpu-mode"
    mode_file.write_text("normal\n")
    bin_ = _fake_smi_bin(tmp_path, 90000)  # plenty of VRAM
    restore = _env_patch(bin_, mode_file)
    try:
        mod = _load_cycle()

        class _Comfy:
            @staticmethod
            def comfy_up():
                return True
        mod._load_comfy = lambda: _Comfy
        try:
            mod.cycle("test spec", model="flux2-turbo", aspect="landscape",
                      max_rounds=1)
            raised = None
        except SystemExit as e:
            raised = e.code
    finally:
        restore()
    assert raised is not None and raised != 0, \
        f"non-render mode must refuse (silo), got {raised!r}"


def test_cli_refuses_below_floor(tmp_path):
    """The CLI entrypoint also refuses below the floor (exit 3, clear stderr).

    The guard runs after the Comfy liveness check (silo order: mode -> Comfy
    up -> VRAM), so a live Comfy on a dead port is not enough to reach the
    guard — the subprocess is run with a stub Comfy that answers /system_stats
    so the loop's first Comfy check passes and the guard gets its turn.
    """
    mode_file = tmp_path / "krew-gpu-mode"
    mode_file.write_text("render\n")
    bin_ = _fake_smi_bin(tmp_path, 5945)
    # Stub Comfy server: /system_stats answers (free below floor), /queue
    # reports an empty queue (comfy_up passes).
    port = 18999
    import http.server

    class _Stub(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/system_stats":
                body = json.dumps({"devices": [{"name": "stub", "total_memory": TOTAL * 1048576, "free_memory": 5945 * 1048576}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    env = {
        "PATH": f"{bin_}:{os.environ.get('PATH', '')}",
        "KREW_GPU_MODE_FILE": str(mode_file),
        "KREW_COMFY_URL": f"http://127.0.0.1:{port}",
        "RENDER_CYCLE_LOG": str(tmp_path / "cycle.jsonl"),
    }
    try:
        r = subprocess.run(
            [sys.executable, str(CYCLE), "a test still",
             "--model", "flux2-turbo", "--max-rounds", "1"],
            capture_output=True, text=True, env={**os.environ, **env}, timeout=60,
        )
        assert r.returncode == 3, f"CLI should exit 3 below the floor, got {r.returncode}"
        assert "27B vision judge" in r.stderr
    finally:
        srv.shutdown()
