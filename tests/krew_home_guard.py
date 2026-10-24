"""Test support: keep a loaded script's import-time paths out of the real home.

A crew script fixes its paths when it is imported (LOG, STATE, SCHEDULE, the memory root ...), from HOME and
KREW_HOME as they were then: before the conftest's per-test KREW_HOME exists. A test that loads the script at module
level and then calls a function that logs or reads a default path wrote ~/.hermes/logs/kanban-loop.log (the LIVE
loop's log on the reference install: "archive t_1: ok"), ~/.krew/logs/krew-window-tick.log and ~/.krew/memory/...
(found 2026-10-10 inside the safe-test sandbox). `keep_in_tmp` points every such Path at the test's tmp folder.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def keep_in_tmp(mod, tmp_path: Path, monkeypatch) -> None:
    """Every module-level Path under the home (never one inside this checkout) moves under tmp_path/home."""
    home = Path.home()
    for name, val in list(vars(mod).items()):
        if not isinstance(val, Path):
            continue
        try:
            rel = val.relative_to(home)
        except ValueError:
            continue
        if val == REPO or REPO in val.parents:
            continue
        monkeypatch.setattr(mod, name, tmp_path / "home" / rel)
