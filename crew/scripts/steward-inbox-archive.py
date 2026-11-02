#!/usr/bin/env python3
"""Move yesterday-and-older inbox files by *filename date*.


"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import (  # noqa: E402
    KREW_REPORTS,
    KREW_RENDERS,
    INBOX,
    KANBAN_ARCHIVE,
    plan_archive_moves,
)


def apply_moves(moves: list[tuple[Path, Path]], *, dry: bool) -> list[str]:
    lines: list[str] = []
    for src, dest in moves:
        lines.append(f"{src.name} -> {dest}")
        if dry:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(dest.name + ".dup")
        shutil.move(str(src), str(dest))
    return lines


def ensure_archive_dirs() -> None:
    KREW_REPORTS.mkdir(parents=True, exist_ok=True)
    KANBAN_ARCHIVE.mkdir(parents=True, exist_ok=True)
    # Rename empty placeholders from before this land (zero repo refs).
    old_r = INBOX / "krew-reports-archive"
    old_k = INBOX / "daily-kanban-archive"
    if old_r.is_dir() and KREW_REPORTS.is_dir():
        try:
            if not any(old_r.iterdir()):
                old_r.rmdir()
        except OSError:
            pass
    if old_k.is_dir() and KANBAN_ARCHIVE.is_dir():
        try:
            if not any(old_k.iterdir()):
                old_k.rmdir()
        except OSError:
            pass
    KREW_RENDERS.mkdir(parents=True, exist_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inbox", type=Path, default=INBOX)
    args = ap.parse_args()
    if not args.dry_run:
        ensure_archive_dirs()
    moves = plan_archive_moves(args.inbox)
    lines = apply_moves(moves, dry=args.dry_run)
    for line in lines:
        print(line)
    print(f"# {len(moves)} file(s) {'would move' if args.dry_run else 'moved'}")
    if not args.dry_run:
        org = Path(__file__).resolve().parent / "krew-inbox-organize.py"
        # Full shelve, not --index-only: undated probes and crystals must
        # leave the root or the 06:10 pass just rebuilds the junkyard.
        subprocess.run(
            [sys.executable, str(org), "--inbox", str(args.inbox)],
            check=False,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
