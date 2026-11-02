"""Where the user's ComfyUI is: ONE answer for krew and the Crissa Wall (the User 2026-10-10).

The truth is the Wall's render settings, <CRISSA_HOME or ~/.crissa>/render.json (CRISSA_RENDER_CONFIG names another
file): the file the Wall's render module (modules/render, crissa_render/settings.py) reads, written by
`crissa-render.py --configure` or, when the Wall has none yet, by krew's crew install from krew's own setting. A key
that file names wins. Without the file (krew without the Wall), or for a key it leaves out, krew's own setting
answers, then the default:

    name       render.json     krew's key              default
    root       comfy_dir       KREW_COMFY_ROOT         ~/ComfyUI
    python     comfy_python    KREW_COMFY_PYTHON       <root>/venv/bin/python
    url        comfy_url       KREW_COMFY_URL          http://127.0.0.1:8188
    workflows  workflows       KREW_COMFY_WORKFLOWS    <root>/workflows
    input      input           KREW_COMFY_INPUT        <root>/input
    output     output          KREW_COMFY_OUTPUT       <root>/output
    models     models          KREW_COMFY_MODELS       <root>/models

A folder the file leaves out while it names comfy_dir is <comfy_dir>/<folder>, the render module's own rule, so the
Wall and krew never read two places. The same for the address (2026-10-10): a file that names comfy_dir and no
comfy_url means the render module's own ComfyUI, on the Crissa port table's `comfy` port (8184, CRISSA_PORT_COMFY /
CRISSA_PORT_OFFSET move it; CRISSA_COMFY_URL names another) - never ComfyUI's stock 8188, which is the Wall's Pocket
voice. `python3 krew_comfy.py [name|--json]` prints the answer (start-comfyui.sh uses
it). Two identical copies ship (crew/lib/krew_comfy.py, plugins/krew-comfy/krew_comfy.py); a test holds them equal.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

KEYS = {
    "root": ("comfy_dir", "KREW_COMFY_ROOT"),
    "python": ("comfy_python", "KREW_COMFY_PYTHON"),
    "url": ("comfy_url", "KREW_COMFY_URL"),
    "workflows": ("workflows", "KREW_COMFY_WORKFLOWS"),
    "input": ("input", "KREW_COMFY_INPUT"),
    "output": ("output", "KREW_COMFY_OUTPUT"),
    "models": ("models", "KREW_COMFY_MODELS"),
}
FOLDERS = ("workflows", "input", "output", "models")
DEFAULT_ROOT = "~/ComfyUI"
DEFAULT_URL = "http://127.0.0.1:8188"
WALL_COMFY_PORT = 8184      # the Crissa port table's `comfy` (its lib/crissa_ports.py); 8188 is the Wall's Pocket voice


def render_json(env=None) -> Path:
    """The Wall's render settings file (it may not exist)."""
    e = os.environ if env is None else env
    got = str(e.get("CRISSA_RENDER_CONFIG") or "").strip()
    if got:
        return Path(os.path.expanduser(got))
    return Path(os.path.expanduser(str(e.get("CRISSA_HOME") or "").strip() or "~/.crissa")) / "render.json"


def wall_settings(env=None) -> dict:
    """render.json as a dict ({} without the file, or a file that does not parse)."""
    try:
        d = json.loads(render_json(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _norm(name: str, v: str) -> str:
    return v.rstrip("/") if name == "url" else os.path.expanduser(v)


def wall_comfy_url(env=None) -> str:
    """The address the Wall's render module gives its own ComfyUI when render.json names none (its settings.load)."""
    e = os.environ if env is None else env
    got = str(e.get("CRISSA_COMFY_URL") or "").strip()
    if got:
        return got.rstrip("/")
    v = str(e.get("CRISSA_PORT_COMFY") or "").strip()
    if v.isdigit() and 0 < int(v) < 65536:
        return "http://127.0.0.1:%d" % int(v)
    try:
        off = int(str(e.get("CRISSA_PORT_OFFSET") or "0").strip())
    except ValueError:
        off = 0
    return "http://127.0.0.1:%d" % (WALL_COMFY_PORT + off)


def where(name: str, env=None) -> tuple[str, str]:
    """(value, source): source is 'render.json', krew's key name, or 'default'."""
    if name not in KEYS:
        raise KeyError(f"krew_comfy: no setting {name!r} (have: {', '.join(KEYS)})")
    e = os.environ if env is None else env
    jkey, ekey = KEYS[name]
    wall = wall_settings(e)
    v = str(wall.get(jkey) or "").strip()
    if v:
        return _norm(name, v), "render.json"
    wall_root = str(wall.get("comfy_dir") or "").strip()
    if name in FOLDERS and wall_root:
        return os.path.join(os.path.expanduser(wall_root), name), "render.json"
    if name == "url" and wall_root:
        return wall_comfy_url(e), "render.json"
    v = str(e.get(ekey) or "").strip()
    if v:
        return _norm(name, v), ekey
    if name == "root":
        return os.path.expanduser(DEFAULT_ROOT), "default"
    if name == "url":
        return DEFAULT_URL, "default"
    root, src = where("root", e)
    sub = os.path.join("venv", "bin", "python") if name == "python" else name
    return os.path.join(root, sub), src if src == "render.json" else "default"


def get(name: str, env=None) -> str:
    return where(name, env)[0]


def snapshot(env=None) -> dict:
    """Every setting with its source, and the file it read: what `krew-mode status` shows."""
    out = {n: dict(zip(("value", "source"), where(n, env))) for n in KEYS}
    out["render_json"] = str(render_json(env))
    return out


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--json":
        print(json.dumps(snapshot(), indent=1))
        return 0
    name = argv[0] if argv else "root"
    try:
        print(get(name))
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
