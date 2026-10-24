"""Shared test helpers: load a plugin folder (its name has a hyphen) as a package, with a private KREW_HOME."""
from __future__ import annotations

import importlib.util
import os
import pwd
import sys
import tempfile
import time
from pathlib import Path

import pytest

# The tests' recorded times are the reference machine's (US Eastern), so on another zone (GitHub's runners are UTC) a
# night or a journal line read an hour or a day off. Pinned here so a clone tests the same anywhere; KREW_TEST_TZ moves it.
os.environ["TZ"] = os.environ.get("KREW_TEST_TZ", "America/New_York")
time.tzset()

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"

# The user's REAL hermes launchers, found from the passwd home (a test may change HOME and HERMES_HOME, never
# this). hermes's bootstrap, imported under a scratch HERMES_HOME, rewrites them to exec the scratch home's
# Python, and every later `hermes` dies when that scratch folder goes (2026-10-10: a full krew run did this).
_REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
_LAUNCHERS = [_REAL_HOME / ".hermes" / "hermes-agent" / ".hermes" / "bin" / n for n in ("hermes", "hermes-acp")]


def _launcher_bytes(paths=None) -> dict:
    out = {}
    for f in (_LAUNCHERS if paths is None else paths):
        try:
            out[f] = f.read_bytes()
        except OSError:
            pass
    return out


def _healthy(data: bytes | None) -> bool:
    """A launcher is healthy when its `exec` runs an interpreter inside the user's own ~/.hermes."""
    if not data:
        return False
    own = str(_REAL_HOME / ".hermes") + "/"
    for line in data.decode("utf-8", "replace").splitlines():
        if line.startswith("exec "):
            parts = line.split()
            return len(parts) > 1 and parts[1].startswith(own)
    return False


@pytest.fixture(autouse=True)
def _real_hermes_launcher_guard(request):
    """Fail the test that pointed the user's hermes launcher away from their own install, and put the old bytes
    back - but only bytes that were themselves healthy. A launcher that was already broken when the test started
    and got repaired while it ran (hermes heals it on its next real run) is never "restored" to the broken copy,
    and a change TO a healthy launcher (a repair, a hermes update) is not this test's doing."""
    before = _launcher_bytes()
    yield
    now = _launcher_bytes(list(before))
    broke = [f for f, b in before.items() if now.get(f) != b and not _healthy(now.get(f))]
    restored = []
    for f in broke:
        if not _healthy(before[f]):
            continue
        try:
            tmp = f.with_name(f.name + ".pytest-restore")
            tmp.write_bytes(before[f])
            os.chmod(tmp, 0o755)
            os.replace(tmp, f)
            restored.append(f.name)
        except OSError:
            pass
    if broke:
        pytest.fail(f"{request.node.nodeid} pointed the user's hermes launcher away from their own install "
                    f"({', '.join(f.name for f in broke)}; restored: {', '.join(restored) or 'none'}). "
                    "A test must never import hermes_bootstrap or run the real `hermes` under a scratch "
                    "HERMES_HOME: stub it on PATH.")


def load_plugin(name: str):
    """Import plugins/<name>/ as package `krewtest_<name>` and return it (fresh each call)."""
    pkg = "krewtest_" + name.replace("-", "_")
    for k in [k for k in sys.modules if k == pkg or k.startswith(pkg + ".")]:
        del sys.modules[k]
    d = PLUGINS / name
    spec = importlib.util.spec_from_file_location(pkg, d / "__init__.py", submodule_search_locations=[str(d)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[pkg] = mod
    spec.loader.exec_module(mod)
    return mod


def submodule(name: str, sub: str):
    import importlib
    load_plugin(name)
    return importlib.import_module("krewtest_" + name.replace("-", "_") + "." + sub)


# NO TEST REACHES THE REAL `hermes` (the User 2026-10-10). A stand-in that only logs its argv sits first on PATH for
# the whole session, so code that resolves the command by PATH (krew_settings.hermes_bin, the janitor's card filer,
# the crew install's crons) finds it, never the user's own launcher (the whole-suite run of 10-10 caught the janitor's
# watcher filing `hermes -p zoey kanban create` through the real one). A test that builds its own PATH or names
# KREW_HERMES_BIN keeps its own stand-in.
_STAND_IN_DIR = Path(tempfile.mkdtemp(prefix="krew-test-hermes-"))
STAND_IN_LOG = _STAND_IN_DIR / "calls.log"
(_STAND_IN_DIR / "hermes").write_text("#!/bin/sh\necho \"$@\" >> " + str(STAND_IN_LOG) + "\n", encoding="utf-8")
(_STAND_IN_DIR / "hermes").chmod(0o755)
os.environ["PATH"] = str(_STAND_IN_DIR) + os.pathsep + os.environ.get("PATH", "")


@pytest.fixture(autouse=True)
def krew_home(tmp_path, monkeypatch):
    """Every test gets its own KREW_HOME and no settings file unless it writes one."""
    home = tmp_path / "krew-home"
    home.mkdir()
    monkeypatch.setenv("KREW_HOME", str(home))
    # never the Wall (Crissa) on this machine: krew-install's marry step and the team-zoey player calls (2026-10-06)
    monkeypatch.setenv("ZOEYOS_HOME", str(tmp_path / "no-zoeyos-home"))
    monkeypatch.setenv("ZOEYOS_PLAYER", "http://127.0.0.1:9")
    monkeypatch.delenv("ZOEYOS_IDENTITY", raising=False)   # Crissa's site.json: only a test that writes one
    monkeypatch.delenv("KREW_CONFIG", raising=False)
    for v in ("KREW_DOCS", "KREW_DOCS_INDEX", "KREW_KANBAN_SHOW_SLIM", "KREW_RENDER_ORDER_TOOL", "KREW_ORDERS_REQUEUE",
              "KREW_RENDER_CARD", "KREW_HANDS_NESTED", "KREW_HERMES_BIN", "HERMES_KANBAN_TASK", "HERMES_KANBAN_DB"):
        monkeypatch.delenv(v, raising=False)
    # no test reaches a real Crissa: its player and ear point at a closed port unless a test starts a fake one
    monkeypatch.setenv("ZOEYOS_PLAYER", "http://127.0.0.1:9")
    monkeypatch.setenv("ZOEYOS_EAR", "off")
    return home
