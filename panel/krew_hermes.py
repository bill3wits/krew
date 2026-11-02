"""Where Hermes lives for this krew: one rule, so a krew beside another Hermes reads and writes its own home only.

The owner 2026-11-01: a krew installed under HERMES_HOME=~/.hermes-krew, on a machine whose ~/.hermes was still in
use, read the OTHER install's board, because thirty-odd krew files built `Path.home() / ".hermes"` themselves. Every
krew file asks here now. This file is copied byte for byte beside every krew folder that needs it (a plugin is
installed alone); tests/test_hermes_home.py holds the copies identical and refuses a built ~/.hermes path anywhere else.

    hermes_home()      the Hermes ROOT (the board, logs, profiles, plugins live there)
    kanban_dir()       <root>/kanban, or $HERMES_KANBAN_HOME/kanban (Hermes's own override)
    hermes_tree()      the Hermes Agent checkout: $HERMES_TREE, else <root>/hermes-agent
    runtime_python()   the python Hermes runs on, asked of its launcher; an error when it cannot be found, never a
                       guessed path (a fresh install's environment is installs/<id>/environments/<gen>/venv, and the
                       old <root>/hermes-agent/venv does not exist there)
"""
from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path


def hermes_home(*, passwd: bool = False, env: dict | None = None) -> Path:
    """The Hermes root, first match wins:
    KREW_HERMES_ROOT (krew's own override, named by an install or a test); HERMES_REAL_HOME or
    KREW_REAL_HOME + /.hermes (a test's login home); $HERMES_HOME (a profile's own `<root>/profiles/<name>` reads
    <root>: the board, logs and profiles are shared, as Hermes's get_default_hermes_root has it); else ~/.hermes (a
    Hermes profile's own HOME, `<root>/profiles/<name>/home`, reads <root>). passwd=True: that last step reads the
    account's passwd home, never $HOME (the files built that way on purpose: a cron or worker shell repoints HOME into
    a profile sandbox). env: read another environment than this process's (its HOME too). A file with an override
    of its own (KREW_HERMES: the tree krew-mode and the render scripts are pointed at) reads it before asking here."""
    own = env is None
    env = os.environ if own else env
    login = str(Path.home()) if own else (env.get("HOME") or str(Path.home()))

    def path(v: str) -> Path:
        return Path(login + v[1:] if v == "~" or v.startswith("~/") else v)

    v = (env.get("KREW_HERMES_ROOT") or "").strip()
    if v:
        return path(v)
    for name in ("HERMES_REAL_HOME", "KREW_REAL_HOME"):
        v = (env.get(name) or "").strip()
        if v:
            return path(v) / ".hermes"
    v = (env.get("HERMES_HOME") or "").strip()
    if v:
        p = path(v)
        return p.parent.parent if p.parent.name == "profiles" else p
    home = Path(pwd.getpwuid(os.getuid()).pw_dir) if passwd else Path(login)
    if home.name == "home" and home.parent.parent.name == "profiles":
        return home.parent.parent.parent
    return home / ".hermes"


def kanban_dir(*, passwd: bool = False) -> Path:
    """The board folder: $HERMES_KANBAN_HOME/kanban (Hermes's kanban_home override), else <root>/kanban."""
    v = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    return (Path(os.path.expanduser(v)) if v else hermes_home(passwd=passwd)) / "kanban"


def hermes_tree() -> Path:
    v = os.environ.get("HERMES_TREE", "").strip()
    return Path(os.path.expanduser(v)) if v else hermes_home() / "hermes-agent"


def hermes_launcher() -> str | None:
    """The `hermes` command: KREW_HERMES_BIN, else `hermes` on PATH, else the root's own published launcher."""
    v = os.environ.get("KREW_HERMES_BIN", "").strip()
    if v:
        return os.path.expanduser(v)
    found = shutil.which("hermes")
    if found:
        return found
    own = hermes_tree() / ".hermes" / "bin" / "hermes"
    return str(own) if own.is_file() else None


def _exec_python(launcher: str) -> str | None:
    """The interpreter a launcher runs: its shebang when that is a python, else the python its `exec` line names
    (Hermes's launcher is `#!/bin/sh` + `exec <python> -I -c ...`; a shim that only execs another launcher is
    followed, three deep)."""
    path = shutil.which(launcher) or launcher
    for _ in range(3):
        try:
            with open(path, "rb") as fh:
                lines = fh.read(8192).decode("utf-8", "replace").splitlines()
        except OSError:
            return None
        first = lines[0].strip() if lines else ""
        py = first[2:].split()[0] if first.startswith("#!") else ""
        if "python" in Path(py).name:
            return py
        nxt = None
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) > 1 and parts[0] == "exec":
                nxt = parts[1].strip("'\"")
                break
        if not nxt:
            return None
        if "python" in Path(nxt).name:
            return nxt
        path = nxt
    return None


def runtime_python(launcher: str | None = None, *, timeout: float = 30) -> str:
    """The python Hermes runs on. Asks the launcher (`hermes --print-runtime-command`, read only), else reads the
    interpreter its exec line names. RuntimeError, naming what was tried, when neither gives an executable python."""
    hermes = launcher or hermes_launcher()
    if not hermes:
        raise RuntimeError("no Hermes runtime python: no `hermes` command (KREW_HERMES_BIN, PATH, "
                           f"{hermes_tree() / '.hermes' / 'bin' / 'hermes'})")
    tried = []
    try:
        out = subprocess.run([hermes, "--print-runtime-command"], capture_output=True, text=True,
                             timeout=timeout).stdout.strip()
        argv = json.loads(out.splitlines()[-1]) if out else None
        py = argv[0] if isinstance(argv, list) and argv else None
        if py and os.access(py, os.X_OK):
            return py
        tried.append(f"{hermes} --print-runtime-command gave {py or 'nothing'}")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        tried.append(f"{hermes} --print-runtime-command: {type(e).__name__}")
    py = _exec_python(hermes)
    if py and os.access(py, os.X_OK):
        return py
    tried.append(f"its exec line names {py or 'no python'}")
    raise RuntimeError("no Hermes runtime python: " + "; ".join(tried))


# One line that runs a script the way Hermes's own launcher runs its entry point: the checkout first on sys.path,
# hermes_bootstrap (which brings the installed environment's packages), then the script as __main__ with its own
# folder on sys.path. PYTHONPATH is left as it is (krew's launcher puts the crew libraries there).
BOOT = ("import sys, runpy; sys.path.insert(0, {tree!r}); import hermes_bootstrap; sys.argv = [{script!r}] + sys.argv[1:]; "
        "sys.path.insert(0, {sdir!r}); runpy.run_path({script!r}, run_name='__main__')")


def runtime_argv(script: str, args: list | tuple = (), launcher: str | None = None) -> list[str]:
    """argv that runs `script` under Hermes's runtime (raises RuntimeError like runtime_python)."""
    py = runtime_python(launcher)
    script = str(Path(script).resolve())
    return [py, "-c", BOOT.format(tree=str(hermes_tree()), script=script, sdir=str(Path(script).parent)), *map(str, args)]
