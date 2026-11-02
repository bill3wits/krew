#!/usr/bin/env python3
"""krew: krew-places-watch.py (lifted from the reference install; see crew/README.md)."""

















from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import select
import struct
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("KREW_PLACES_CONFIG") or REPO / "config" / "krew-places.json")

IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x2, 0x4, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_DELETE_SELF, IN_MOVE_SELF, IN_Q_OVERFLOW, IN_IGNORED = 0x400, 0x800, 0x4000, 0x8000
IN_ONLYDIR, IN_ISDIR = 0x01000000, 0x40000000
MASK = (IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
        | IN_ATTRIB | IN_ONLYDIR)
EVENT = struct.Struct("iIII")


def log(msg: str) -> None:
    print(f"krew-places-watch: {msg}", flush=True)


def settings(path: Path | None = None) -> dict:
    try:
        cfg = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    w = {"settle_s": 3, "max_wait_s": 30, "rescan_s": 60, "threads": 2, "timeout_s": 300,
         "index_python": os.environ.get("KREW_DOCS_INDEX_PYTHON") or "~/.krew/embedder-venv/bin/python",
         "index_script": os.environ.get("KREW_DOCS_INDEX_SCRIPT") or str(KH.hermes_home() / "plugins/krew-docs/docs_index.py"), "off_file": str(KH.hermes_home() / "krew-places-watch.off")}
    w.update(cfg.get("watch") or {})
    w["roots"] = [os.path.expanduser(str(p.get("root"))) for p in cfg.get("places") or [] if p.get("root")]
    return w


def off(w: dict) -> bool:
    if os.environ.get("KREW_PLACES", "1").strip().lower() in ("0", "false", "no", "off"):
        return True
    return Path(os.path.expanduser(w["off_file"])).exists()


class Watcher:
    """Recursive inotify over a few roots: wd -> path, new dirs added as they appear."""

    def __init__(self, roots: list[str]):
        self.libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        self.fd = self.libc.inotify_init1(os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        self.roots = roots
        self.wds: dict[int, str] = {}

    def _add(self, path: str) -> None:
        wd = self.libc.inotify_add_watch(self.fd, os.fsencode(path), MASK)
        if wd >= 0:
            self.wds[wd] = path

    def add_tree(self, top: str) -> None:
        if not os.path.isdir(top):
            return
        for dirpath, dirnames, _ in os.walk(top):
            dirnames[:] = [n for n in dirnames if not n.startswith(".") and n != "__pycache__"]
            self._add(dirpath)

    def rewatch(self) -> int:
        for wd in list(self.wds):
            self.libc.inotify_rm_watch(self.fd, wd)
        self.wds.clear()
        for r in self.roots:
            self.add_tree(r)
        return len(self.wds)

    def missing_roots(self) -> list[str]:
        have = set(self.wds.values())
        return [r for r in self.roots if r not in have]

    def read(self) -> tuple[int, bool]:
        """Drain one read; returns (relevant events, overflow)."""
        buf = os.read(self.fd, 65536)
        n, overflow, i = 0, False, 0
        while i + EVENT.size <= len(buf):
            wd, mask, _cookie, ln = EVENT.unpack_from(buf, i)
            name = buf[i + EVENT.size:i + EVENT.size + ln].rstrip(b"\0").decode("utf-8", "replace")
            i += EVENT.size + ln
            if mask & IN_Q_OVERFLOW:
                overflow = True
                continue
            if mask & IN_IGNORED:
                self.wds.pop(wd, None)
                continue
            base = self.wds.get(wd)
            if mask & (IN_CREATE | IN_MOVED_TO) and mask & IN_ISDIR and base and not name.startswith("."):
                self.add_tree(os.path.join(base, name))
            if name.startswith(".") and not (mask & IN_ISDIR):
                continue      # editor swap / temp files
            n += 1
        return n, overflow


def build(w: dict) -> int:
    py = os.path.expanduser(w["index_python"])
    py = py if os.path.exists(py) else sys.executable
    env = dict(os.environ, KREW_INDEX_THREADS=str(int(w["threads"])))
    t0 = time.time()
    try:
        sys.path.insert(0, str(REPO / "lib"))
        import krew_places
        krew_places.write_map(krew_places.walk())
    except Exception as e:  # noqa: BLE001 - the build still runs on the last map
        log(f"places map not rewritten: {e}")
    try:
        r = subprocess.run([py, os.path.expanduser(w["index_script"]), "build", "--source", "places"],
                           env=env, capture_output=True, text=True, timeout=float(w["timeout_s"]))
    except subprocess.TimeoutExpired:
        log("build TIMEOUT")
        return 124
    tail = (r.stdout or "").strip().splitlines()[:1] or (r.stderr or "").strip().splitlines()[-1:]
    log(f"build rc={r.returncode} in {time.time() - t0:.1f}s {tail[0] if tail else ''}")
    return r.returncode


def run(w: dict) -> int:
    wt = Watcher(w["roots"])
    log(f"watching {wt.rewatch()} folders under {len(w['roots'])} roots; settle {w['settle_s']}s")
    if not off(w):
        build(w)                                  # catch up on whatever changed while it was down
    first = last = 0.0
    next_rescan = time.time() + float(w["rescan_s"])
    while True:
        now = time.time()
        if first:
            timeout = max(0.0, min(last + float(w["settle_s"]), first + float(w["max_wait_s"])) - now)
        else:
            timeout = max(0.0, next_rescan - now)
        ready, _, _ = select.select([wt.fd], [], [], timeout)
        now = time.time()
        if ready:
            n, overflow = wt.read()
            if overflow:
                log(f"queue overflow; rewatching {wt.rewatch()} folders")
                n = n or 1
            if n:
                first = first or now
                last = now
            continue
        if first:                                 # quiet for settle_s, or max_wait_s reached
            first = last = 0.0
            if off(w):
                log("changes seen; off switch present, no build")
            else:
                build(w)
        if now >= next_rescan:
            next_rescan = now + float(w["rescan_s"])
            for r in wt.missing_roots():
                if os.path.isdir(r):
                    wt.add_tree(r)
                    log(f"root appeared: {r}")
                    first = last = now


def main(argv: list[str]) -> int:
    w = settings()
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "once":
        return build(w)
    if cmd == "run":
        return run(w)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
