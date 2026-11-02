"""The GPU mode command every krew caller runs: one rule (the User 2026-10-10).

    KREW_MODE_BIN    the command as words (a test's stub, or `python3 /path/to/your-mode-script`)
    KREW_MODE_PATH   a mode script of your own
    $KREW_HOME/bin/krew-mode   the link crew/scripts/krew-crew-install.py makes (KREW_HOME default ~/.krew)
    crew/scripts/krew-mode     the switch krew ships, beside the crew scripts

render-submit, render-collect, the hold sweep and the Wall's Mode row (through the pointer the crew install writes)
all reach the same switch. `path()` never reads KREW_MODE_BIN: the restore after a render must run the real switch even
when a test stubs the command.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path

SHIPPED = Path(__file__).resolve().parents[1] / "scripts" / "krew-mode"


def krew_home(env=None) -> Path:
    e = os.environ if env is None else env
    return Path(os.path.expanduser(str(e.get("KREW_HOME") or "").strip() or "~/.krew"))


def path(env=None) -> str:
    """The mode script: KREW_MODE_PATH, else the install's link, else the shipped one."""
    e = os.environ if env is None else env
    got = str(e.get("KREW_MODE_PATH") or "").strip()
    if got:
        return os.path.expanduser(got)
    link = krew_home(e) / "bin" / "krew-mode"
    if link.is_file():
        return str(link)
    return str(SHIPPED)


def command(env=None) -> list[str]:
    """The mode command as argv: KREW_MODE_BIN's words, else `python3 <path()>`."""
    e = os.environ if env is None else env
    words = str(e.get("KREW_MODE_BIN") or "").strip()
    if words:
        return shlex.split(words)
    return ["python3", path(e)]
