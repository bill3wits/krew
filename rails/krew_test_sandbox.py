#!/usr/bin/env python3
"""A crew test run cannot open the live board (krew security rails).

pre_tool_call hook on `terminal`. When a crew profile's command runs pytest, the command is
re-written to run inside a mount namespace where the live board directory (`~/.hermes/kanban`)
is an EMPTY scratch one:

    bwrap --dev-bind / / --bind <scratch> ~/.hermes/kanban -- /bin/bash -c '<the command>'

Everything else on the box is as it was (same files, same network, same cwd, same env). The
tests pass or fail as before; what they can no longer do is write the board the crew runs on.

Why: on the crew this was lifted from, bash and steward workers ran Hermes's own kanban tests
(`pytest tests/hermes_cli -k kanban`) in a Hermes clone. A test dispatcher with a fake spawn
(pid 12345) opened the LIVE board, created cards titled `t1`, claimed every ready card and marked
them blocked or crashed: 4 incidents in one afternoon, 40-odd false runs. How the test's own
isolation is bypassed from a worker is NOT known, so the guard does not depend on it: a path
that is not mounted cannot be opened.

It never blocks and never fails a call: anything unexpected answers `{}` (the command runs as
asked) and says why in the log. The T0 gate runs BEFORE it and judges the command as written.

Wire: hooks.pre_tool_call, matcher ^terminal$, on the profiles that run tests (krew-install --hooks; the
list is `hooks.test_sandbox_profiles` in $KREW_HOME/rails.yaml). Needs `bwrap` (bubblewrap); without it the
hook says so in its log and leaves the command alone.
Kill: KREW_TEST_SANDBOX=0. Report-only: KREW_TEST_SANDBOX=report (logs, rewrites nothing).
Log: $KREW_HOME/logs/test-sandbox.jsonl
"""
from __future__ import annotations

import json
import os
import pwd
import re
import shlex
import shutil
import sys
import time
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

REAL_HOME = Path(os.environ.get("KREW_REAL_HOME") or pwd.getpwuid(os.getuid()).pw_dir)
HERMES_ROOT = KH.hermes_home(passwd=True)
_KH = os.environ.get("KREW_HOME") or "~/.krew"
KREW_HOME = Path(str(REAL_HOME) + _KH[1:]) if _KH.startswith("~/") else Path(_KH)
BOARD_DIR = HERMES_ROOT / "kanban"
SCRATCH_ROOT = HERMES_ROOT / "cache" / "test-sandbox"
LOG = KREW_HOME / "logs" / "test-sandbox.jsonl"
# a test RUN, not the word: `pytest`, `py.test`, `python -m pytest|unittest`, `uv run … pytest`
RUNS_TESTS = re.compile(r"(?:(?<![\w./-])(?:pytest|py\.test)(?![\w.-])|-m\s+(?:pytest|unittest)\b)")
# …unless the word is only being searched for or read
ONLY_READS = re.compile(r"^\s*(?:grep|rg|cat|head|tail|ls|sed|awk|find|git\s+(?:diff|log|show|status))\b[^|;&]*$")


def mode() -> str:
    raw = os.environ.get("KREW_TEST_SANDBOX", "1").strip().lower()
    return "off" if raw in ("0", "off", "false") else ("report" if raw == "report" else "act")


def profile_of(payload: dict) -> str:
    """The served profile: the payload's, else the basename of a profile HERMES_HOME, else default."""
    p = str(payload.get("profile") or "").strip()
    if p:
        return p
    home = Path(os.environ.get("HERMES_HOME", "") or ".")
    if home.parent.name == "profiles":
        return home.name
    return os.environ.get("HERMES_PROFILE", "").strip() or "default"


def runs_tests(command: str) -> bool:
    if not RUNS_TESTS.search(command):
        return False
    # a reader (grep, cat, git diff…) whose ARGUMENT names pytest runs nothing; quotes are stripped
    # for this one question only, so `bash -c 'pytest'` is still a test run
    bare = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", command)
    return not ONLY_READS.match(bare)


def scratch_for(profile: str, *, board_dir: Path = BOARD_DIR, root: Path = SCRATCH_ROOT) -> Path:
    """An empty stand-in for the board directory: the `current` pointer and that board's empty
    folder, so code that only asks WHICH board is current still gets its answer."""
    d = root / re.sub(r"[^A-Za-z0-9_.-]", "_", profile) / "kanban"
    (d / "boards").mkdir(parents=True, exist_ok=True)
    try:
        cur = (board_dir / "current").read_text().strip()
    except OSError:
        cur = ""
    if cur and re.fullmatch(r"[A-Za-z0-9_.-]+", cur):
        (d / "current").write_text(cur + "\n")
        (d / "boards" / cur).mkdir(exist_ok=True)
    return d


def wrap(command: str, scratch: Path, *, board_dir: Path = BOARD_DIR, bwrap: str = "bwrap") -> str:
    return (f"{bwrap} --dev-bind / / --bind {shlex.quote(str(scratch))} {shlex.quote(str(board_dir))} "
            f"-- /bin/bash -c {shlex.quote(command)}")


def decide(payload: dict, *, which=shutil.which, board_dir: Path = BOARD_DIR, root: Path = SCRATCH_ROOT) -> tuple[dict, dict]:
    """(answer for hermes, row for the log). The answer is `{}` unless the command is re-written."""
    row: dict = {"ts": time.strftime("%FT%T"), "card": os.environ.get("HERMES_KANBAN_TASK") or None}
    if mode() == "off":
        return {}, {}
    if payload.get("tool_name") != "terminal":
        return {}, {}
    args = payload.get("args")
    command = args.get("command") if isinstance(args, dict) else None
    if not isinstance(command, str) or not runs_tests(command):
        return {}, {}
    prof = profile_of(payload)
    row.update(profile=prof, command=command[:300])
    if prof == "default":
        return {}, {**row, "did": "left", "why": "the default profile is the owner's"}
    if "bwrap " in command and str(board_dir) in command:
        return {}, {**row, "did": "left", "why": "already sandboxed"}
    exe = which("bwrap")
    if not exe:
        return {}, {**row, "did": "left", "why": "bwrap is not installed — the test run can reach the live board"}
    if not board_dir.is_dir():
        return {}, {**row, "did": "left", "why": f"{board_dir} is not a directory"}
    if mode() == "report":
        return {}, {**row, "did": "would sandbox"}
    scratch = scratch_for(prof, board_dir=board_dir, root=root)
    return ({"action": "modify", "args": {**args, "command": wrap(command, scratch, board_dir=board_dir, bwrap=exe)}},
            {**row, "did": "sandboxed"})


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        answer, row = decide(payload if isinstance(payload, dict) else {})
    except Exception as e:  # noqa: BLE001 - this hook never fails a call
        answer, row = {}, {"ts": time.strftime("%FT%T"), "did": "left", "why": f"{type(e).__name__}: {e}"[:200]}
    if row:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
