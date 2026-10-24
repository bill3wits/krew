"""Load M&S with the reference profile only - never a live ms.yaml (the owner 2026-10-03, lifted 2026-10-10).

krew ships no profile file: the reference install's whole settings file sits commented out in
config/ms.example.yaml. These helpers read that block, render its placeholders into a session temp folder
(a fake env root with one `<env>/bin/python` per listed env, a fake data disk, a fake Crissa checkout) and
point MS_PROFILE_FILE at the result, so the lifted tests see the same values the reference install runs with.
Importing this module sets MS_PROFILE_FILE / MS_SETTINGS for the session (a test module that loads the trail
at import must import this first). `reference_only()` and `load_ms()` are the reference helpers, renamed.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "ms.example.yaml"
HOME_DIR = Path(tempfile.mkdtemp(prefix="krew-ms-test-"))
NO_USER_LAYER = str(HOME_DIR / "ms-no-user-layer.yaml")   # never created
REF_PROFILE = HOME_DIR / "ms-reference-profile.yaml"
PLACEHOLDERS = {
    "${MS_ENVS_ROOT}": str(HOME_DIR / "envs"),
    "${KREW_HOME}": str(HOME_DIR / "krew"),
    "{data}": str(HOME_DIR / "data"),
    "{zoeyos_repo}": str(HOME_DIR / "zoeyos"),
    "{krew_repo}": str(ROOT),
    "{deliverables}": str(HOME_DIR / "deliverables"),
}


def reference_block() -> str:
    """The commented reference block of config/ms.example.yaml, uncommented (YAML text, placeholders in)."""
    text = EXAMPLE.read_text(encoding="utf-8")
    marker = "# upgrades: true\n"
    assert marker in text, "config/ms.example.yaml has no reference block"
    block = text[text.index(marker):]
    return "\n".join((l[2:] if l.startswith("# ") else "") for l in block.splitlines()) + "\n"


def reference_settings() -> dict:
    import yaml
    return yaml.safe_load(reference_block())


def render(text: str) -> str:
    for k, v in PLACEHOLDERS.items():
        text = text.replace(k, v)
    return text


def _lay_out() -> None:
    if REF_PROFILE.is_file():
        return
    import yaml
    ref = yaml.safe_load(render(reference_block()))
    envs = HOME_DIR / "envs"
    for name in ref.get("envs") or {}:
        if name in (ref.get("retired_envs") or []):
            continue
        (envs / name / "bin").mkdir(parents=True, exist_ok=True)
        py = envs / name / "bin" / "python"
        py.write_text("#!/bin/sh\nexit 0\n")
        py.chmod(0o755)
    for sub in ("krew/ms", "data", "zoeyos/scripts", "deliverables"):
        (HOME_DIR / sub).mkdir(parents=True, exist_ok=True)
    model = Path(str(((ref.get("canary") or {}).get("model_05b") or {}).get("model") or HOME_DIR / "data" / "model"))
    model.mkdir(parents=True, exist_ok=True)
    (model / "config.json").write_text("{}\n")   # the reference install has the 0.5B on disk; tests check the path
    REF_PROFILE.write_text(render(reference_block()), encoding="utf-8")


ENVS_ROOT = HOME_DIR / "envs"
DELIVERABLES = HOME_DIR / "deliverables"
ROOTS_FILE = HOME_DIR / "krew" / "ms-env-roots.json"
TEXT_FILE = HOME_DIR / "krew" / "ms-text.yaml"


def _lay_out_files() -> None:
    """The roots rows and the operator words the reference profile names (its ${KREW_HOME} files)."""
    import json
    ex = json.loads((ROOT / "config" / "ms-env-roots.example.json").read_text(encoding="utf-8"))
    ref = ex.get("_reference_install") or {}
    roots = {"_doc": "rendered for the tests from config/ms-env-roots.example.json", "defaults": ex.get("defaults") or {},
             "envs": json.loads(render(json.dumps(ref.get("envs") or {})))}
    ROOTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROOTS_FILE.write_text(json.dumps(roots, indent=1) + "\n", encoding="utf-8")
    TEXT_FILE.write_text(render((ROOT / "config" / "ms-text.example.yaml").read_text(encoding="utf-8")), encoding="utf-8")


_lay_out()
_lay_out_files()
for _p in (ROOT / "ms" / "lib", ROOT / "window" / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ["MS_PROFILE_FILE"] = str(REF_PROFILE)
os.environ.setdefault("MS_SETTINGS", NO_USER_LAYER)


@contextlib.contextmanager
def reference_only():
    """Settings = defaults + the reference profile, never a user layer."""
    old = os.environ.get("MS_SETTINGS")
    os.environ["MS_SETTINGS"] = NO_USER_LAYER
    os.environ["MS_PROFILE_FILE"] = str(REF_PROFILE)
    try:
        if str(ROOT / "ms" / "lib") not in sys.path:
            sys.path.insert(0, str(ROOT / "ms" / "lib"))
        import ms_settings
        importlib.reload(ms_settings)
        yield
    finally:
        if old is None:
            os.environ.pop("MS_SETTINGS", None)
        else:
            os.environ["MS_SETTINGS"] = old


def load_ms(name: str, script: Path | None = None):
    """ms/scripts/ms-trail-run.py as module `name`, settings = defaults + the reference profile."""
    path = script or ROOT / "ms" / "scripts" / "ms-trail-run.py"
    with reference_only():
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod
