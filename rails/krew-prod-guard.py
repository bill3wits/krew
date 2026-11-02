#!/usr/bin/env python3
"""krew-prod-guard: make your production files unwritable, by anyone (chattr +i).

    krew-prod-guard.py status          what is locked right now (no sudo needed)
    krew-prod-guard.py lock            protect the set            (sudo chattr +i)
    krew-prod-guard.py unlock          release the set (say why out loud)
    krew-prod-guard.py with <cmd...>   unlock, run cmd, re-lock even if cmd fails

The set is `prod_guard.files` in $KREW_HOME/rails.yaml (config/rails.example.yaml shows the shape): a list of
FILES, not folders - protect what production runs on, not a directory that also holds experiments and scratch.
A relative path is read against `prod_guard.root` (default: the current directory).

Why this and not a permission rule: every agent on one machine usually runs as the same unix user, so file
permissions cannot tell one agent from another, and a policy rule only binds the tools that read it. chattr +i
binds at the kernel: no process - not root, not an agent, not a runaway script - can write, rename or delete an
immutable file until the flag comes off. It protects THE FILE rather than restricting a person.

The intended path for a real change is the production flow:
    krew-prodgate stage <gate-id> <path>  ->  review  ->  approve  ->  krew-prod-guard.py with krew-prodgate apply <gate-id>
so the window stays open for exactly one command.

lock / unlock need `sudo chattr`. Without sudo they say so and change nothing (status still works).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import krew_settings  # noqa: E402


def settings() -> dict:
    return krew_settings.load("prod_guard", path=krew_settings.krew_home() / "rails.yaml")


def paths(cfg: dict | None = None) -> tuple[list[Path], list[str]]:
    """(files that exist, entries that do not - reported, never dropped silently)."""
    cfg = settings() if cfg is None else cfg
    root = Path(os.path.expanduser(str(cfg.get("root") or "."))).resolve()
    have, missing = [], []
    for f in cfg.get("files") or []:
        p = Path(os.path.expanduser(str(f)))
        p = p if p.is_absolute() else root / p
        (have if p.exists() else missing).append(p if p.exists() else str(p))
    return have, missing


def locked(p: Path) -> bool:
    r = subprocess.run(["lsattr", "-d", str(p)], capture_output=True, text=True)
    return r.returncode == 0 and "i" in (r.stdout.split() or [""])[0]


def _chattr(flag: str, files: list[Path]) -> int:
    rc = 0
    for p in files:
        r = subprocess.run(["sudo", "-n", "chattr", flag, str(p)], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  {'locked  ' if flag == '+i' else 'unlocked'} {p}")
        else:
            rc = 1
            print(f"  FAILED   {p}: {(r.stderr or r.stdout).strip()[:160]} (needs sudo chattr)")
    return rc


def cmd_status(files: list[Path], missing: list[str]) -> int:
    print(f"{'FILE':60} STATE")
    n = 0
    for p in files:
        st = locked(p)
        n += st
        print(f"{str(p):60} {'LOCKED' if st else 'writable'}")
    for m in missing:
        print(f"{m:60} MISSING (listed in prod_guard.files, not on disk)")
    if not files and not missing:
        print("(no files listed: set prod_guard.files in $KREW_HOME/rails.yaml)")
    print(f"\n{n} of {len(files)} protected")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    files, missing = paths()
    if cmd == "status":
        return cmd_status(files, missing)
    if cmd == "lock":
        return _chattr("+i", files)
    if cmd == "unlock":
        return _chattr("-i", files)
    if cmd == "with":
        if len(argv) < 2:
            print("usage: krew-prod-guard.py with <command...>", file=sys.stderr)
            return 2
        if _chattr("-i", files):
            return 1
        try:
            return subprocess.run(argv[1:]).returncode
        finally:
            _chattr("+i", files)          # re-lock no matter how the command exits
    print("usage: krew-prod-guard.py {status|lock|unlock|with <cmd...>}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
