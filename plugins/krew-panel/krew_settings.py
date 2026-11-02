"""krew settings: one YAML file, $KREW_HOME/krew.yaml (default ~/.krew/krew.yaml).

Every machine- or user-specific value (paths, profile names, endpoints) lives there or in the
environment, never in code. `config/krew.example.yaml` in the krew repository lists every key.
This file is copied byte for byte into each plugin that reads settings (a plugin is installed alone);
tests/test_krew_packages.py holds the copies identical.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("krew")


def krew_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


def settings_path() -> Path:
    return Path(os.path.expanduser(os.environ.get("KREW_CONFIG") or str(krew_home() / "krew.yaml")))


def _safe_load(text: str):
    """Hermes's own YAML reader inside Hermes; PyYAML or ruamel.yaml when a script runs outside it."""
    try:
        from hermes_yaml import safe_load
    except ImportError:
        try:
            from yaml import safe_load
        except ImportError:
            try:
                from ruamel.yaml import YAML
            except ImportError:
                raise ImportError("no YAML reader: run with the Hermes venv's python, or install PyYAML") from None
            return YAML(typ="safe").load(text)
    return safe_load(text)


def load(section: str | None = None, path: Path | str | None = None) -> dict:
    """The whole settings file, or one top-level section of it ({} when absent).

    A missing file is normal (every key has a default). A file that does not parse is NOT silent:
    it is logged, and the defaults apply."""
    p = Path(path) if path else settings_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = _safe_load(text) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("krew: %s does not parse (%s: %s); using defaults", p, type(e).__name__, e)
        return {}
    if not isinstance(data, dict):
        logger.warning("krew: %s is not a mapping; using defaults", p)
        return {}
    if section is None:
        return data
    sec = data.get(section) or {}
    return sec if isinstance(sec, dict) else {}


def switch_on(name: str, default: bool = True) -> bool:
    """An environment switch: unset = `default`; 0/false/no/off = off; anything else = on."""
    v = os.environ.get(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def hermes_bin() -> str:
    """The `hermes` command: KREW_HERMES_BIN, else `hermes_bin:` in krew.yaml, else `hermes` on PATH,
    else ~/.local/bin/hermes (where the Hermes installer puts it)."""
    import shutil
    v = os.environ.get("KREW_HERMES_BIN") or str(load().get("hermes_bin") or "")
    if v.strip():
        return os.path.expanduser(v.strip())
    return shutil.which("hermes") or os.path.expanduser("~/.local/bin/hermes")
