"""render-submit.py: the POST-PARK VRAM shortfall (park ran but the resident
still holds the GPU) must block the card with the numbers, not die silently.

Failure class (frame spec, t_542ebba9, framing A): the park path frees what it
can, and if free VRAM is still < NEED_GIB the old code marked the jobs row
"failed" and returned 3 WITHOUT a terminal kanban call. The worker sees rc=3,
retries with a fresh budget, dies 0 on the wall — the card looks alive while
the run budget burns. 40/40 iteration exhaustion is the shape of the burn.

Hermetic: fake nvidia-smi + fake hermes on PATH, dead Comfy port, fake
krew-mode on PATH that prints a low free-GiB number (comfy_up() is also down,
so the poll loop breaks on the first iteration). The shortfall branch is
reached before any POST to Comfy, before the jobs-file write past "preparing".
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = ROOT / "crew" / "scripts" / "render-submit.py"
TOTAL = 97887


class _ComfyHandler(http.server.BaseHTTPRequestHandler):
    """Answers every GET with a 200 JSON body — enough for comfy_up()."""
    body: bytes = b"{}"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format, *a):
        pass



def _comfy_root(tmp):
    """krew: the test carries its own ComfyUI folder (the reference box read its live /comfy tree); the graph is the
    wan22-i2v recipe's node ids with empty inputs, which is all render-submit touches before the POST."""
    root = tmp / "ComfyUI"
    (root / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "input").mkdir(exist_ok=True)
    graph = {n: {"class_type": "stub", "inputs": {}} for n in ("9", "10", "11", "12", "13", "14", "16", "17")}
    (root / "workflows" / "apple_split_wan_i2v.json").write_text(json.dumps(graph))
    return str(root)

def _start_fake_comfy(body: bytes) -> tuple[http.server.HTTPServer, int]:
    """A live HTTP server so comfy_up() returns True hermetically.
    Returns (server, port) — call server.shutdown() to stop it."""
    _ComfyHandler.body = body
    srv = http.server.HTTPServer(("127.0.0.1", 0), _ComfyHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for _ in range(50):
        try:
            c = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            c.close()
            return srv, port
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("fake comfy did not come up")


def _mkbins(tmp: Path, free_gib: float | None) -> Path:
    """fake nvidia-smi (free derived from the GiB number), fake hermes (logs
    its argv), and a fake krew-mode that prints the free-GiB number —
    krew-mode is the thing that reports VRAM back to render-submit."""
    bin_ = tmp / "bin"
    bin_.mkdir(parents=True)
    # free MiB that the poll reads: render-submit polls free_vram_gib(), which
    # shells out to nvidia-smi directly. comfy_up() hits a dead port -> False,
    # so the poll breaks after one iteration with free = free_gib.
    free_mib = int(free_gib * 1024) if free_gib is not None else None
    ns = bin_ / "nvidia-smi"
    if free_mib is None:
        ns.write_text("#!/usr/bin/env bash\nexit 9\n")
    else:
        ns.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"$*\" == *compute-apps* ]]; then exit 0; fi\n"
            # The probe asks for ONE field — `--query-gpu=memory.free --format=csv,noheader,nounits`
        # — so it float()s a single number. This stub used to echo three comma-separated
        # fields (total,used,free) from an older probe, float() raised, free came back None,
        # and every test here took the "shortfall" branch: two of them failed in the nightly
        # battery for days and the message read "only None GiB free". Verified 2026-09-22
        # against the live probe, which returns a bare number.
        f"echo \"{free_mib}\"\n"
        )
    ns.chmod(ns.stat().st_mode | stat.S_IEXEC)
    hermes = bin_ / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {tmp / 'hermes-calls.log'}\n"
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    return bin_


def _run(tmp: Path, free_gib: float | None, comfy_port: int | None = None) -> subprocess.CompletedProcess:
    seed = tmp / "seed.png"; seed.write_bytes(b"\x89PNG-fake")
    env = dict(os.environ)
    env["PATH"] = f"{_mkbins(tmp, free_gib)}:{env['PATH']}"
    env["KREW_COMFY_URL"] = f"http://127.0.0.1:{comfy_port}"
    env["KREW_HERMES"] = str(tmp)
    env["KREW_COMFY_ROOT"] = _comfy_root(tmp)
    env["KREW_RENDER_NOW"] = "1"          # exempt the test card from the render-window gate
    env["KREW_RENDER_POLL_N"] = "1"
    env["KREW_RENDER_POLL_SLEEP"] = "0"
    env.pop("KREW_RENDER_WAIT_SEC", None)
    env.pop("HERMES_KANBAN_TASK", None)
    return subprocess.run(
        [sys.executable, str(SUBMIT),
         "--card", "t_selftest", "--name", "selftest",
         "--seed", str(seed), "--prompt", "selftest prompt",
         "--no-park"],
        capture_output=True, text=True, env=env, cwd=str(tmp),
        timeout=300,
    )


def _park_run(tmp: Path) -> subprocess.CompletedProcess:
    """The PARKING run (the failing class): no --no-park. Comfy is down so
    render-submit shells out to `krew-mode render wan` — a fake one that
    prints the residual free GiB and leaves Comfy down. free_vram_gib() then
    reads the fake nvidia-smi (same low number), the poll breaks, and the
    shortfall branch must fire the board call."""
    seed = tmp / "seed.png"; seed.write_bytes(b"\x89PNG-fake")
    free_gib = 6.0                      # park ran, but the resident still holds 91 GiB
    free_mib = int(free_gib * 1024)
    bin_ = tmp / "bin"
    bin_.mkdir(parents=True)
    ns = bin_ / "nvidia-smi"
    ns.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *compute-apps* ]]; then exit 0; fi\n"
        # The probe asks for ONE field — `--query-gpu=memory.free --format=csv,noheader,nounits`
        # — so it float()s a single number. This stub used to echo three comma-separated
        # fields (total,used,free) from an older probe, float() raised, free came back None,
        # and every test here took the "shortfall" branch: two of them failed in the nightly
        # battery for days and the message read "only None GiB free". Verified 2026-09-22
        # against the live probe, which returns a bare number.
        f"echo \"{free_mib}\"\n"
    )
    ns.chmod(ns.stat().st_mode | stat.S_IEXEC)
    hermes = bin_ / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {tmp / 'hermes-calls.log'}\n"
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    # fake krew-mode: `krew-mode render wan` prints the free-GiB number and
    # exits 0 — it "ran the park" but Comfy never came up (dead port) and free
    # VRAM is still short.
    hm = bin_ / "krew-mode"
    hm.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"free after park {free_gib:.1f} GiB\"\n"
        "exit 0\n"
    )
    hm.chmod(hm.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bin_}:{env['PATH']}"
    # The park is invoked by ABSOLUTE PATH inside render-submit, so the PATH stub
    # above never intercepted it: every run of this file entered Render on the LIVE
    # machine, parking ops and crew under `t_selftest` — a card no board knows, so
    # hold_card_live answers "status unknown — keep" and the sweep can NEVER release
    # it. Both brains sat dead until a hand cleared them (2026-09-22).
    env["KREW_MODE_BIN"] = str(hm)
    env["KREW_COMFY_URL"] = "http://127.0.0.1:1"
    env["KREW_HERMES"] = str(tmp)
    env["KREW_COMFY_ROOT"] = _comfy_root(tmp)
    env["KREW_RENDER_NOW"] = "1"
    env["KREW_RENDER_POLL_N"] = "1"
    env["KREW_RENDER_POLL_SLEEP"] = "0"
    env.pop("KREW_RENDER_WAIT_SEC", None)
    env["HERMES_KANBAN_TASK"] = "t_selftest"     # live card present -> board calls expected
    return subprocess.run(
        [sys.executable, str(SUBMIT),
         "--card", "t_selftest", "--name", "selftest",
         "--seed", str(seed), "--prompt", "selftest prompt"],
        capture_output=True, text=True, env=env, cwd=str(tmp),
        timeout=300,
    )


def test_postpark_shortfall_blocks_with_numbers(tmp_path):
    """The REAL 40/40 failure class (t_542ebba9): the park ran and Comfy came
    back UP, but the resident (vLLM EngineCore) still holds the GPU, so free
    VRAM is < NEED_GIB. The old code marked the jobs row "failed" and returned
    3 with NO terminal kanban call — the card stayed alive on the board while
    the worker burned its run budget on retries (the 40/40 shape).

    Now the card must be BLOCKED with the numbers in the reason. A fake Comfy
    on a live port makes comfy_up() True so the poll loop exits and the
    shortfall branch is reached (it is only reachable when Comfy IS up — the
    Comfy-down branch returns first)."""
    seed = tmp_path / "seed.png"; seed.write_bytes(b"\x89PNG-fake")
    free_gib = 6.0                      # park ran, but the resident still holds ~91 GiB
    free_mib = int(free_gib * 1024)
    bin_ = tmp_path / "bin"; bin_.mkdir(parents=True)
    ns = bin_ / "nvidia-smi"
    ns.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *compute-apps* ]]; then exit 0; fi\n"
        # The probe asks for ONE field — `--query-gpu=memory.free --format=csv,noheader,nounits`
        # — so it float()s a single number. This stub used to echo three comma-separated
        # fields (total,used,free) from an older probe, float() raised, free came back None,
        # and every test here took the "shortfall" branch: two of them failed in the nightly
        # battery for days and the message read "only None GiB free". Verified 2026-09-22
        # against the live probe, which returns a bare number.
        f"echo \"{free_mib}\"\n"
    )
    ns.chmod(ns.stat().st_mode | stat.S_IEXEC)
    hermes = bin_ / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {tmp_path / 'hermes-calls.log'}\n"
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    hm = bin_ / "krew-mode"
    hm.write_text(f"#!/usr/bin/env bash\necho \"free after park {free_gib:.1f} GiB\"\nexit 0\n")
    hm.chmod(hm.stat().st_mode | stat.S_IEXEC)
    # fake Comfy on a live port: comfy_up() -> GET /system_stats 200 JSON,
    # so the poll loop exits with comfy_up()==True and free=6 GiB < 24.
    srv = _start_fake_comfy(b'{"system": {}}')
    port = srv[1] if isinstance(srv, tuple) else srv
    try:
        env = dict(os.environ)
        env["PATH"] = f"{bin_}:{env['PATH']}"
        env["KREW_MODE_BIN"] = str(hm)
        env["KREW_COMFY_URL"] = f"http://127.0.0.1:{port}"
        env["KREW_HERMES"] = str(tmp_path)
        env["KREW_COMFY_ROOT"] = _comfy_root(tmp_path)
        env["KREW_RENDER_NOW"] = "1"
        env["KREW_RENDER_POLL_N"] = "1"
        env["KREW_RENDER_POLL_SLEEP"] = "0"
        env.pop("KREW_RENDER_WAIT_SEC", None)
        env["HERMES_KANBAN_TASK"] = "t_selftest"
        r = subprocess.run(
            [sys.executable, str(SUBMIT),
             "--card", "t_selftest", "--name", "selftest",
             "--seed", str(seed), "--prompt", "selftest prompt"],
            capture_output=True, text=True, env=env, cwd=str(tmp_path),
            timeout=300,
        )
        assert r.returncode == 3, r.stdout + r.stderr
        calls = (tmp_path / "hermes-calls.log").read_text()
        assert "block" in calls and "t_selftest" in calls, calls
        assert "VRAM still short after park" in calls, calls
        assert "6 GiB" in calls, calls
        # the render was NOT attempted: no prompt_id.
        assert "prompt_id" not in r.stdout
        # the jobs row is marked failed (visible to the janitor ghost sweep).
        jobs = tmp_path / "render-jobs.json"
        if jobs.exists():
            rows = json.loads(jobs.read_text())
            assert any(j.get("status") == "failed" for j in rows), rows
    finally:
        srv[0].shutdown(); srv[0].server_close()


def test_postpark_comfy_down_blocks_with_recovery(tmp_path):
    """The park ran but Comfy never came up: the card is BLOCKED with the
    recovery command (krew-mode normal), so the worker can restore ops and
    resubmit instead of burning the run budget on silent retries.
    Free VRAM is ample here — Comfy-down is the blocking cause."""
    seed = tmp_path / "seed.png"; seed.write_bytes(b"\x89PNG-fake")
    free_gib = 50.0
    free_mib = int(free_gib * 1024)
    bin_ = tmp_path / "bin"; bin_.mkdir(parents=True)
    ns = bin_ / "nvidia-smi"
    ns.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *compute-apps* ]]; then exit 0; fi\n"
        # The probe asks for ONE field — `--query-gpu=memory.free --format=csv,noheader,nounits`
        # — so it float()s a single number. This stub used to echo three comma-separated
        # fields (total,used,free) from an older probe, float() raised, free came back None,
        # and every test here took the "shortfall" branch: two of them failed in the nightly
        # battery for days and the message read "only None GiB free". Verified 2026-09-22
        # against the live probe, which returns a bare number.
        f"echo \"{free_mib}\"\n"
    )
    ns.chmod(ns.stat().st_mode | stat.S_IEXEC)
    hermes = bin_ / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {tmp_path / 'hermes-calls.log'}\n"
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    hm = bin_ / "krew-mode"
    hm.write_text("#!/usr/bin/env bash\nexit 0\n")
    hm.chmod(hm.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bin_}:{env['PATH']}"
    env["KREW_MODE_BIN"] = str(hm)
    env["KREW_COMFY_URL"] = "http://127.0.0.1:1"   # dead port -> Comfy down
    env["KREW_HERMES"] = str(tmp_path)
    env["KREW_COMFY_ROOT"] = _comfy_root(tmp_path)
    env["KREW_RENDER_NOW"] = "1"
    env["KREW_RENDER_POLL_N"] = "1"
    env["KREW_RENDER_POLL_SLEEP"] = "0"
    env.pop("KREW_RENDER_WAIT_SEC", None)
    env["HERMES_KANBAN_TASK"] = "t_selftest"
    r = subprocess.run(
        [sys.executable, str(SUBMIT),
         "--card", "t_selftest", "--name", "selftest",
         "--seed", str(seed), "--prompt", "selftest prompt"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
        timeout=300,
    )
    assert r.returncode == 3, r.stdout + r.stderr
    calls = (tmp_path / "hermes-calls.log").read_text()
    assert "block" in calls and "t_selftest" in calls, calls
    assert "did not come up" in calls, calls
    assert "krew-mode normal" in calls, calls


def test_ample_vram_comfy_up_proceeds(tmp_path):
    """Sane case unchanged: the park ran, Comfy is UP, free VRAM >= NEED_GIB.
    The poll loop breaks, NO block, NO failed row — the run proceeds to the
    Comfy POST (which fails on the fake server, but AFTER the guards cleared:
    the absence of any board block is the assertion)."""
    seed = tmp_path / "seed.png"; seed.write_bytes(b"\x89PNG-fake")
    free_gib = 50.0
    free_mib = int(free_gib * 1024)
    bin_ = tmp_path / "bin"; bin_.mkdir(parents=True)
    ns = bin_ / "nvidia-smi"
    ns.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *compute-apps* ]]; then exit 0; fi\n"
        # The probe asks for ONE field — `--query-gpu=memory.free --format=csv,noheader,nounits`
        # — so it float()s a single number. This stub used to echo three comma-separated
        # fields (total,used,free) from an older probe, float() raised, free came back None,
        # and every test here took the "shortfall" branch: two of them failed in the nightly
        # battery for days and the message read "only None GiB free". Verified 2026-09-22
        # against the live probe, which returns a bare number.
        f"echo \"{free_mib}\"\n"
    )
    ns.chmod(ns.stat().st_mode | stat.S_IEXEC)
    hermes = bin_ / "hermes"
    hermes.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {tmp_path / 'hermes-calls.log'}\n"
    )
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    # This test stubbed nvidia-smi and hermes but NOT krew-mode, so it entered Render
    # on the live machine like the others (2026-09-22).
    hm = bin_ / "krew-mode"
    hm.write_text("#!/usr/bin/env bash\\nexit 0\\n")
    hm.chmod(hm.stat().st_mode | stat.S_IEXEC)
    # Comfy is up from the start -> render-window gate's first branch checks
    # free VRAM (50 GiB >= 24) -> no park, no board calls at all.
    srv = _start_fake_comfy(b'{"system": {}}')
    port = srv[1] if isinstance(srv, tuple) else srv
    try:
        env = dict(os.environ)
        env["PATH"] = f"{bin_}:{env['PATH']}"
        env["KREW_MODE_BIN"] = str(hm)
        env["KREW_COMFY_URL"] = f"http://127.0.0.1:{port}"
        env["KREW_HERMES"] = str(tmp_path)
        env["KREW_COMFY_ROOT"] = _comfy_root(tmp_path)
        env["KREW_RENDER_NOW"] = "1"
        env["KREW_RENDER_POLL_N"] = "1"
        env["KREW_RENDER_POLL_SLEEP"] = "0"
        env.pop("KREW_RENDER_WAIT_SEC", None)
        env.pop("HERMES_KANBAN_TASK", None)
        r = subprocess.run(
            [sys.executable, str(SUBMIT),
             "--card", "t_selftest", "--name", "selftest",
             "--seed", str(seed), "--prompt", "selftest prompt"],
            capture_output=True, text=True, env=env, cwd=str(tmp_path),
            timeout=300,
        )
        # it got PAST the guards (no rc=3 from a guard) and reached the render
        # POST, which the fake server answers with the same 200 JSON — not a
        # valid prompt object. Whatever the exit, NO board block may have fired.
        calls = (tmp_path / "hermes-calls.log").read_text() if (tmp_path / "hermes-calls.log").exists() else ""
        assert "block" not in calls, calls
        assert "VRAM still short after park" not in calls, calls
    finally:
        srv[0].shutdown(); srv[0].server_close()