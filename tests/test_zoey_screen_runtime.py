"""Her desktop mounts nothing on his runtime folder (the User 2026-10-04).

After the 10-04 reboot her Bot Desktop inherited his /run/user/<uid>: its gvfsd-fuse and xdg-document-portal mounted
gvfs and doc on top of his, systemd SIGKILLed them on a failed start, and his own document portal could not start.
krew's Hermes patch 210 (patches/210-bot-desktop-private-runtime.patch) gives her desktop its own
XDG_RUNTIME_DIR; the isolation check proves no process of hers points under his.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import zoey_screen as zs  # noqa: E402

TREE = Path(os.environ.get("HERMES_TREE", str(Path.home() / ".hermes" / "hermes-agent")))
LAUNCHER = TREE / "tools" / "bot_desktop" / "launcher.sh"
HIS = f"/run/user/{os.getuid()}"


def run_launcher(tmp_path: Path, extra_env: dict | None = None) -> tuple[dict, dict]:
    """Run hermes's real launcher with stub X/Xfce programs; return (published env file, the session's env)."""
    if not LAUNCHER.exists():
        pytest.skip(f"no hermes launcher at {LAUNCHER}")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    seen = tmp_path / "panel-env"
    stubs = {
        "Xvnc": "exec sleep 5",
        "dbus-run-session": 'shift; export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent; exec "$@"',
        "xfce4-panel": f'env > "{seen}"; exit 0',
        "xfwm4": "exit 0", "xfdesktop": "exit 0", "xfsettingsd": "exit 0", "xprop": "exit 0",
        "xdpyinfo": "exit 0", "setxkbmap": "exit 0", "xsetroot": "exit 0", "xset": "exit 0", "xauth": "exit 0",
        "fusermount3": f'echo "$@" >> "{tmp_path / "fusermount-calls"}"; exit 0',
    }
    for name, body in stubs.items():
        (bindir / name).write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        (bindir / name).chmod(0o755)
    for tool in ("mkdir", "sed", "cat", "printf", "dirname", "bash", "sh", "rm", "mv", "ln", "touch", "chmod",
                 "od", "tr", "awk", "grep", "seq", "sleep", "kill", "env"):
        real = shutil.which(tool)
        if real and not (bindir / tool).exists():
            (bindir / tool).symlink_to(real)
    state = tmp_path / "bot-desktop"
    state.mkdir()
    env = {
        "PATH": str(bindir), "HOME": str(tmp_path), "XDG_RUNTIME_DIR": HIS,      # what a unit inherits
        "HERMES_BD_PROFILE": "t", "HERMES_BD_DISPLAY_NUM": "97",
        "HERMES_BD_SOCKET": str(state / "rfb.sock"), "HERMES_BD_XAUTH": str(state / "Xauthority"),
        "HERMES_BD_ENV_FILE": str(state / "env"), "HERMES_BD_CONFIG_HOME": str(state / "xdg"),
        **(extra_env or {}),
    }
    subprocess.run(["bash", str(LAUNCHER)], env=env, check=True, stdin=subprocess.DEVNULL, capture_output=True,
                   timeout=30)
    pub = dict(ln.split("=", 1) for ln in (state / "env").read_text().splitlines() if "=" in ln)
    sess = dict(ln.split("=", 1) for ln in seen.read_text().splitlines() if "=" in ln)
    return pub, sess


def test_her_desktop_runs_on_its_own_runtime_folder_not_his(tmp_path):
    pub, sess = run_launcher(tmp_path)
    mine = str(tmp_path / "bot-desktop" / "runtime")
    assert sess["XDG_RUNTIME_DIR"] == mine, "her Xfce session (where gvfsd-fuse and the portal start) must not inherit his"
    assert pub.get("XDG_RUNTIME_DIR") == mine, "every spawn for her profile reads the env file; it must carry her folder"
    assert not zs.runtime_leaks([{"pid": 1, "comm": "xfce4-panel", "argv": ["xfce4-panel"],
                                  "runtime": sess["XDG_RUNTIME_DIR"]}], HIS)
    assert oct(os.stat(mine).st_mode & 0o777) == "0o700"


def test_a_leak_into_his_folder_fails_the_check():
    procs = [
        {"pid": 11797, "comm": "xdg-document-po", "argv": ["/usr/libexec/xdg-document-portal"], "runtime": HIS},
        {"pid": 11329, "comm": "gvfsd-fuse", "argv": ["/usr/libexec/gvfsd-fuse", f"{HIS}/gvfs", "-f"],
         "runtime": "/home/x/bot-desktop/runtime"},
        {"pid": 11142, "comm": "xfdesktop", "argv": ["xfdesktop"], "runtime": "/home/x/bot-desktop/runtime"},
    ]
    bad = zs.runtime_leaks(procs, HIS)
    assert len(bad) == 2 and "xdg-document-po[11797]" in bad[0] and f"{HIS}/gvfs" in bad[1]
    assert not zs.runtime_leaks(procs[2:], HIS)
    assert not zs.runtime_leaks([{"pid": 2, "comm": "x", "argv": ["x"], "runtime": HIS + "0/y"}], HIS)  # a prefix, not under


def test_check_reports_the_leak_as_not_isolated(tmp_path, monkeypatch):
    ef = tmp_path / "env"
    ef.write_text(f"DISPLAY=:20\nXAUTHORITY={tmp_path}/Xauthority\n")
    monkeypatch.setattr(zs, "her_processes", lambda disp, proc=None: [
        {"pid": 9, "comm": "gvfsd-fuse", "argv": ["gvfsd-fuse", f"{HIS}/gvfs"], "runtime": HIS}])
    rec = zs.check(env_file=ef, user_display=":0", run=lambda argv, env=None, timeout=8: (1, ""), landing=False)
    row = next(c for c in rec["checks"] if c["check"] == "nothing of hers mounts on his runtime folder")
    assert not row["ok"] and "gvfsd-fuse[9]" in row["why"] and not rec["ok"]


def test_her_processes_reads_display_and_runtime_from_proc(tmp_path):
    for pid, disp, rt in ((101, ":20", HIS), (102, ":0", HIS), (103, ":20", "/h/runtime")):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "environ").write_bytes(f"HOME=/h\0DISPLAY={disp}\0XDG_RUNTIME_DIR={rt}\0".encode())
        (d / "cmdline").write_bytes(b"prog\0arg\0")
        (d / "comm").write_text("prog\n")
    got = sorted((p["pid"], p["runtime"]) for p in zs.her_processes(":20", proc=tmp_path))
    assert got == [(101, HIS), (103, "/h/runtime")]
