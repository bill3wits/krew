#!/usr/bin/env python3
"""One-off Scout research for Crissa. Not a kanban spawn. Not the morning cron.

Writes ~/Desktop/krew-deliverables/scratch/scout-oneoff-YYYYmmdd-HHMMSS.md
and prints NOTE <path> plus Scout's short reply.

Usage: python3 scripts/scout-one-off.py "why does glass-probe time out"
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HERMES_DIR = Path(os.environ.get("KREW_HERMES") or KH.hermes_home())
INBOX = Path(os.path.expanduser(os.environ.get("KREW_DELIVERABLES") or "~/Desktop/krew-deliverables")) / "scratch"
HERMES = Path(os.environ.get("HERMES_BIN", str(Path.home() / ".local/bin/hermes")))
TIMEOUT = int(os.environ.get("SCOUT_ONEOFF_TIMEOUT", "180"))
MAX_TURNS = int(os.environ.get("SCOUT_ONEOFF_TURNS", "8"))

# its tools are free to choose before launch. It reads, searches, looks things up and writes one note;
# `memory` is left to the nightly jobs (6 calls in 28 one-offs). Kill: SCOUT_ONEOFF_KIT=0.
ONEOFF_TOOLSETS = os.environ.get("SCOUT_ONEOFF_TOOLSETS", "file,web,search,skills,session_search")


def kit() -> list[str]:
    if os.environ.get("SCOUT_ONEOFF_KIT", "1").strip().lower() in ("0", "false", "no", "off"):
        return []
    return ["-t", ONEOFF_TOOLSETS]


def gpu_mode(path: Path | None = None) -> str:
    p = path or (HERMES_DIR / "krew-gpu-mode")
    try:
        first = p.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except OSError:
        return "normal"
    return first if first in {"render", "normal", "beast", "off"} else "normal"


def note_path(now: float | None = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    return INBOX / f"scout-oneoff-{stamp}.md"


def prompt_for(ask: str, dest: Path) -> str:
    return (
        "One-off research from Crissa (spoken User ask). Not the morning "
        "team-research note.\n\n"
        f"Ask: {ask.strip()}\n\n"
        f"Write {dest} with the ask as title, named sources, measured or "
        "ASSUMED numbers. None worth naming is a pass. Do not implement. "
        "Do not touch kanban. Do not sudo.\n"
        "You have no shell and no board tools. If the ask needs a live probe of a service or the "
        "contents of a kanban card, write that one line in the note and stop; do not hunt for another way.\n"
        "When the file is written, reply with:\n"
        f"NOTE {dest}\n"
        "then two sentences."
    )


def stub_if_missing(dest: Path, ask: str, why: str) -> bool:
    """A note always exists for the path Crissa is handed - written only when Scout left none."""
    try:
        if dest.exists():
            return False
        dest.write_text(f"# {ask}\n\n_{why} Nothing was found; ask again or file a card._\n", encoding="utf-8")
        return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ask", nargs="+", help="the question")
    args = ap.parse_args(argv)
    ask = " ".join(args.ask).strip()
    if not ask:
        print("scout-one-off: empty ask", file=sys.stderr)
        return 2
    mode = gpu_mode()
    if mode == "beast":
        print("Scout sits out Beast — crew is leftover-fit. Ask again in Standard.")
        return 3
    dest = note_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # task has not seen its full current content"), so 6 of 28 one-offs spent two of their eight steps
    # reading the stub back before they could write the note. The stub is written AFTER a run that left
    # no note (stub_if_missing), which is the only case anyone ever read it.
    hermes = str(HERMES) if HERMES.is_file() else "hermes"
    cmd = [
        hermes,
        "-p",
        "scout",
        "--cli",
        "--accept-hooks",
        "--skills",
        "scout-one-off",
        "chat",
        *kit(),
        "--oneshot",
        "--max-turns",
        str(MAX_TURNS),
        "--run-budget",
        str(max(30, TIMEOUT - 20)),
        "-q",
        prompt_for(ask, dest),
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=str(INBOX.parent),
        )
    except subprocess.TimeoutExpired:
        stub_if_missing(dest, ask, "Scout hit the time cap before the note was written.")
        print(f"NOTE {dest}")
        print("Scout hit the time cap. The stub note is on disk — I can put it on the wall.")
        return 4
    out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    stub_if_missing(dest, ask, "Scout finished without writing the note.")
    if "NOTE " not in out:
        print(f"NOTE {dest}")
    print((f"[first {len(out) - 1200} of {len(out)} chars cut; the whole answer is the note {dest}]\n" + out[-1200:])
          if len(out) > 1200 else (out or f"scout rc={r.returncode}"))
    return 0 if r.returncode == 0 else min(r.returncode, 125)


if __name__ == "__main__":
    raise SystemExit(main())
