""















from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REAL_HOME = Path(os.environ.get("HERMES_REAL_HOME", str(Path.home())))
TREE = Path(os.environ.get("HERMES_TREE", str(_REAL_HOME / ".hermes" / "hermes-agent")))
HERMES_BIN = os.environ.get("MS_HERMES_BIN", str(_REAL_HOME / ".local" / "bin" / "hermes"))
_MARK = "MS_HERMES_REEXECED"

_BOOT = ("import sys, runpy; sys.path.insert(0, {tree!r}); import hermes_bootstrap; "
         "sys.argv = [{script!r}] + sys.argv[1:]; "
         "sys.path.insert(0, {sdir!r}); "
         "runpy.run_path({script!r}, run_name='__main__')")


def imports_here() -> bool:
    ""
    if str(TREE) not in sys.path:
        sys.path.insert(0, str(TREE))
    try:
        import hermes_cli.kanban_db_connect
        return True
    except Exception:
        return False


def runtime_python() -> str | None:
    ""
    try:
        out = subprocess.run([HERMES_BIN, "--print-runtime-command"], capture_output=True,
                             text=True, timeout=30).stdout.strip()
        argv = json.loads(out.splitlines()[-1])
        py = argv[0] if isinstance(argv, list) and argv else None
        return py if py and os.access(py, os.X_OK) else None
    except Exception:
        return None


def home_is_the_install() -> bool:
    ""






    raw = os.environ.get("HERMES_HOME", "").strip()
    if not raw:
        return True
    try:
        home = Path(raw).resolve()
        root = (_REAL_HOME / ".hermes").resolve()
    except OSError:
        return False
    return home == root or (root / "profiles") in home.parents and (home / "config.yaml").is_file()


def reexec_argv(script: str, args: list[str], py: str) -> list[str]:
    script = str(Path(script).resolve())
    boot = _BOOT.format(tree=str(TREE), script=script, sdir=str(Path(script).parent))
    return [py, "-I", "-c", boot, *args]


def ensure(script: str) -> None:
    ""
    if imports_here():
        return
    if os.environ.get("MS_HERMES_REEXEC", "1") == "0" or os.environ.get(_MARK) == "1":
        return
    if not home_is_the_install():
        return
    py = runtime_python()
    if not py:
        return
    os.environ[_MARK] = "1"
    os.execv(py, reexec_argv(script, sys.argv[1:], py))
