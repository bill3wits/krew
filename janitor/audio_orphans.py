"""Audio clients whose spawner is gone — the PipeWire client leak (2026-09-19).

PipeWire-pulse serves 64 clients (`max-clients`); at the cap a new client is
refused and the house reads it as "no tiles" / Say doing nothing. On 09-19 05:10
it stood at 46 of 64, 31 of them orphaned `pactl subscribe`: every test that
exec_module'd scripts/voiceonly-home-server.py started its subscriber thread at
import, the test exited, and pactl lived on. pactl ignores SIGPIPE (SigIgn bit 13,
measured on the live child) and does not check its writes, so a dead reader never
ends it — and on a quiet bus it has nothing to write anyway.

The rule is keyed on the CGROUP, not the parent pid: an orphan reparents to the
nearest subreaper (systemd --user, or a Claude session inside a terminal scope),
so "parent is systemd" misses half of them.
  * inside a `.service` cgroup  -> the unit owns it; never an orphan (the ear's
    parec, Cosy, the live :8099 subscriber are untouchable by construction);
  * in a session `.scope` (or anything else) -> an orphan when its stdout is a
    pipe no other process holds: whoever read it is gone.
A client writing to a file or a tty is never reaped here; a recorder of that kind
older than SUSPECT_AGE_S is listed as a suspect, for a human.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

AUDIO_CLIENTS = frozenset({"pactl", "parec", "parecord", "pacat", "paplay",
                           "pw-cat", "pw-record", "pw-play", "pw-mon"})
RECORDERS = frozenset({"parec", "parecord", "pacat", "pw-record", "pw-cat", "pw-mon"})
SUSPECT_AGE_S = 600
DEFAULT_CAP = 64
PULSE_CONFS = (
    Path.home() / ".config/pipewire/pipewire-pulse.conf",
    Path("/etc/pipewire/pipewire-pulse.conf"),
    Path("/usr/share/pipewire/pipewire-pulse.conf"),
)
_PIPE_RE = re.compile(r"^pipe:\[(\d+)\]$")


def client_cap(confs=PULSE_CONFS) -> int:
    """`max-clients` from the first pulse config that sets it (uncommented), else 64."""
    env = os.environ.get("KREW_PW_CLIENT_CAP", "").strip()
    if env.isdigit():
        return int(env)
    paths: list[Path] = []
    for c in confs:
        d = Path(str(c) + ".d")
        if d.is_dir():
            paths += sorted(d.glob("*.conf"))
        paths.append(Path(c))
    for p in paths:
        try:
            for line in p.read_text(errors="replace").splitlines():
                m = re.match(r"^\s*max-clients\s*=\s*(\d+)", line)
                if m:
                    return int(m.group(1))
        except OSError:
            continue
    return DEFAULT_CAP


def client_count() -> tuple[int | None, dict[str, int]]:
    """(clients, {name: n}) from `pactl list clients short`; None when pactl cannot answer.
    The listing is itself one client for its own lifetime and is subtracted."""
    try:
        r = subprocess.run(["pactl", "list", "clients", "short"], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None, {}
    if r.returncode != 0:
        return None, {}
    names: dict[str, int] = {}
    n = 0
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        n += 1
        names[parts[2]] = names.get(parts[2], 0) + 1
    if names.get("pactl"):
        names["pactl"] -= 1
        n -= 1
        if not names["pactl"]:
            del names["pactl"]
    return n, names


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _pipe_holders(proc: Path, uid: int) -> dict[str, set[int]]:
    """{pipe inode: pids holding it} over every process of this uid."""
    out: dict[str, set[int]] = {}
    for e in proc.iterdir():
        if not e.name.isdigit():
            continue
        try:
            if e.stat().st_uid != uid:
                continue
            fds = list((e / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                m = _PIPE_RE.match(os.readlink(fd))
            except OSError:
                continue
            if m:
                out.setdefault(m.group(1), set()).add(int(e.name))
    return out


def _age_s(stat: str, proc: Path) -> float:
    try:
        start_ticks = int(stat.rsplit(")", 1)[1].split()[19])
        up = float(_read(proc / "uptime").split()[0])
        return max(0.0, up - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (IndexError, ValueError, OSError):
        return 0.0


def scan(proc: Path = Path("/proc"), uid: int | None = None) -> dict:
    """{"orphans": [...], "suspects": [...], "owned": n} — no side effects."""
    uid = os.getuid() if uid is None else uid
    holders = None
    orphans: list[dict] = []
    suspects: list[dict] = []
    owned = 0
    for e in sorted(proc.iterdir(), key=lambda x: x.name):
        if not e.name.isdigit():
            continue
        try:
            if e.stat().st_uid != uid:
                continue
        except OSError:
            continue
        comm = _read(e / "comm").strip()
        if comm not in AUDIO_CLIENTS:
            continue
        pid = int(e.name)
        cg = _read(e / "cgroup").strip().splitlines()
        cgroup = cg[-1].split(":", 2)[-1] if cg else ""
        leaf = cgroup.rsplit("/", 1)[-1]
        stat = _read(e / "stat")
        row = {"pid": pid, "comm": comm,
               "args": _read(e / "cmdline").replace("\0", " ").strip()[:160],
               "cgroup": leaf, "age_s": round(_age_s(stat, proc)),
               "ppid": int(stat.rsplit(")", 1)[1].split()[1]) if ")" in stat else 0}
        if leaf.endswith(".service"):
            owned += 1
            continue
        try:
            m = _PIPE_RE.match(os.readlink(e / "fd" / "1"))
        except OSError:
            m = None
        if m:
            if holders is None:
                holders = _pipe_holders(proc, uid)
            others = holders.get(m.group(1), set()) - {pid}
            if not others:
                row["why"] = "stdout pipe has no reader"
                orphans.append(row)
            continue
        if comm in RECORDERS and row["age_s"] > SUSPECT_AGE_S:
            row["why"] = "recording to a file/tty outside any unit"
            suspects.append(row)
    return {"orphans": orphans, "suspects": suspects, "owned": owned}


def reap(rows: list[dict], grace_s: float = 2.0) -> list[int]:
    """SIGTERM, then SIGKILL after grace. Re-checks comm first (pid reuse)."""
    done: list[int] = []
    for r in rows:
        if _read(Path(f"/proc/{r['pid']}/comm")).strip() != r["comm"]:
            continue
        try:
            os.kill(r["pid"], signal.SIGTERM)
            done.append(r["pid"])
        except OSError:
            pass
    deadline = time.monotonic() + grace_s
    left = list(done)
    while left and time.monotonic() < deadline:
        left = [p for p in left if Path(f"/proc/{p}").exists()]
        if left:
            time.sleep(0.1)
    for p in left:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    return done
