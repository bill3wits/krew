"""Settings for the window jobs (restart window, after-checks, reboot intent): $KREW_HOME/<name>.json.

No file = empty lists and the restart window OFF. The shapes, with the reference install's values, are in
config/restart-window.example.json and config/restart-window-after.example.json. Also puts ms/lib on the import
path (os_update, hermes_runtime, ms_settings are shared with M&S).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MS_LIB = HERE.parent.parent / "ms" / "lib"
if MS_LIB.is_dir() and str(MS_LIB) not in sys.path:
    sys.path.append(str(MS_LIB))


def krew_home() -> Path:
    return Path(os.environ.get("KREW_HOME") or Path.home() / ".krew").expanduser()


def settings_file(name: str) -> Path:
    env = "KREW_" + name.upper().replace("-", "_") + "_SETTINGS"
    return Path(os.environ.get(env) or krew_home() / f"{name}.json").expanduser()


def load(name: str) -> dict:
    """The settings dict ({} when the file is absent). A file that does not parse is an error, never {}."""
    p = settings_file(name)
    if not p.is_file():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError(f"{p}: not a JSON object")
    return {k: v for k, v in d.items() if not k.startswith("_")}


def path(value: str, *, unset: str) -> Path:
    """A configured path, or (unset) a path that never exists, named after the setting, so is_file() is False."""
    if value:
        return Path(os.path.expandvars(os.path.expanduser(str(value))))
    return krew_home() / ".unset" / unset


def hermes_bin() -> str:
    return os.environ.get("KREW_HERMES_BIN") or os.environ.get("HERMES_BIN") or str(Path.home() / ".local" / "bin" / "hermes")


# --- krew.yaml's top-level keys (off_hours, quiet_hours): window/lib/krew_hours.py reads and writes them here ---
def krew_yaml_path() -> Path:
    """$KREW_HOME/krew.yaml, or KREW_CONFIG."""
    if os.environ.get("KREW_CONFIG"):
        return Path(os.environ["KREW_CONFIG"]).expanduser()
    return krew_home() / "krew.yaml"


def krew_yaml_top(text: str, keys: tuple[str, ...]) -> dict:
    """The top-level keys of krew.yaml (all of them with PyYAML; else a plain line match of `keys`)."""
    try:
        import yaml
        d = yaml.safe_load(text) or {}
        return d if isinstance(d, dict) else {}
    except ImportError:
        pass
    import re
    out: dict = {}
    for key in keys:
        m = re.search(rf"^{key}:\s*['\"]?([^'\"#\n]*)", text, re.M)
        if m and m.group(1).strip():
            out[key] = m.group(1).strip()
    return out


def krew_yaml_set(mut, keys: tuple[str, ...]) -> dict:
    """Change top-level `keys` of krew.yaml through `mut(settings)`: each `key: value` line is replaced in place or
    appended, every other line is left as it is; the file is swapped atomically."""
    import re
    p = krew_yaml_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        text = ""
    site = krew_yaml_top(text, keys)
    mut(site)
    for key in keys:
        if key not in site:
            continue
        line = f'{key}: "{site[key]}"'
        text, n = re.subn(rf"^{key}:.*$", line, text, count=1, flags=re.M)
        if not n:
            text = text.rstrip("\n") + ("\n" if text.strip() else "") + line + "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return site
