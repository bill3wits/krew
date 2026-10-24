"""Load the mode switch and its siblings (krew-mode, turbo-krew.py, heavy-render.py, render-still.py) with every
import-time path inside a sandbox (2026-10-10).

krew-mode reads its tree from the passwd home (`KREW_HERMES`, else the account's ~/.hermes from passwd, never $HOME: a
kanban worker's HOME is its profile's), so a scrubbed HOME does not isolate a test, and a test that called a writer
on the module as loaded wrote into the live tree (a lane test rewrote a profile's config on the first run of this
lift). So no test loads it bare: `load()` sets KREW_HERMES, KREW_HOME, KREW_CONFIG, KREW_SERVICES and
KREW_TRAIN_HOLD to a fresh temporary tree for the import, and `env()` hands the same to a subprocess. A test that
still patches a path (the reference install's careful ones do) keeps working; one that forgets writes into the sandbox.
"""
from __future__ import annotations

import atexit
import importlib.machinery
import importlib.util
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = Path(tempfile.mkdtemp(prefix="krew-mode-test-"))
atexit.register(shutil.rmtree, SANDBOX, True)
(SANDBOX / "hermes").mkdir()
(SANDBOX / "krew").mkdir()
# krew's own example services list (the reference install's rows: ops.service, crew.service, voice.service), so the
# lifted tests' unit names are the rows', read through crew/lib/krew_services like a real install's
shutil.copyfile(ROOT / "config" / "services.example.json", SANDBOX / "krew" / "services.json")
ENV = {
    "KREW_HERMES": str(SANDBOX / "hermes"),
    "KREW_HOME": str(SANDBOX / "krew"),
    "KREW_CONFIG": str(SANDBOX / "krew" / "krew.yaml"),
    "KREW_SERVICES": str(SANDBOX / "krew" / "services.json"),
    "KREW_TRAIN_HOLD": str(SANDBOX / "train-hold"),
}


def load(name: str, rel: str, **over: str):
    """Import ROOT/rel as module `name` with the sandbox's environment (plus `over`; a value of "" unsets that
    variable for the import), then put the caller's back."""
    want = {**ENV, **over}
    saved = {k: os.environ.get(k) for k in want}
    for k, v in want.items():
        if v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        loader = importlib.machinery.SourceFileLoader(name, str(ROOT / rel))
        spec = importlib.util.spec_from_loader(name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return mod


def env(**extra) -> dict:
    """os.environ plus the sandbox (and `extra`), for a subprocess that runs one of these scripts."""
    return {**os.environ, **ENV, **extra}
