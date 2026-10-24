""
















from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_settings as _S  # noqa: E402
PINS_FILE = Path(os.environ.get("MS_HERMES_ENV_PINS_FILE", str(_S.krew_home() / "hermes-env-pins.json")))
INSTALLS = Path(os.environ.get("MS_HERMES_INSTALLS", str(Path.home() / ".hermes" / "installs")))
GATEWAY_UNIT = "hermes-gateway.service"

_PROBE = r"""
import json, os, re, sys
sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))
import hermes_bootstrap  # noqa: F401
import importlib.metadata as m
site = next((p for p in sys.path if re.search(r"/installs/[^/]+/environments/[^/]+/venv/lib/python[0-9.]+/site-packages$", p)), "")
out = {"site": site, "versions": {}}
for d in json.loads(sys.argv[1]):
    try:
        out["versions"][d] = m.version(d)
    except Exception:
        out["versions"][d] = None
print(json.dumps(out))
"""


def enabled() -> bool:
    return os.environ.get("MS_HERMES_ENV_PINS", "1").strip().lower() not in ("0", "false", "no", "off")


def load_pins(path: Path | None = None) -> list[dict]:
    try:
        rows = json.loads((path or PINS_FILE).read_text(encoding="utf-8")).get("pins") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("dist") and r.get("min")]


def vtuple(version: str | None) -> tuple:
    ""
    nums = []
    for part in str(version or "").split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        nums.append(int(m.group(0)))
    return tuple(nums)


def meets(have: str | None, want: str) -> bool:
    return bool(have) and vtuple(have) >= vtuple(want)


def live(dists: list[str], runner=subprocess.run) -> dict:
    ""
    try:
        import hermes_runtime
        if not hermes_runtime.home_is_the_install():
            return {"error": "HERMES_HOME is not this install's: the bootstrap would provision a foreign home"}
        py = hermes_runtime.runtime_python()
    except Exception as e:
        return {"error": f"hermes_runtime: {type(e).__name__}: {e}"}
    if not py:
        return {"error": "the hermes launcher names no interpreter"}
    env = {k: v for k, v in os.environ.items() if k not in ("HERMES_KANBAN_TASK", "PYTHONPATH", "PYTHONHOME")}
    try:
        r = runner([py, "-I", "-c", _PROBE, json.dumps(list(dists))], capture_output=True, text=True, timeout=120, env=env)
        out = json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        return {"error": f"probe: {type(e).__name__}: {str(e)[:160]}"}
    return out if isinstance(out, dict) and out.get("site") else {"error": "the launcher's path holds no install environment"}


def below(pins: list[dict], versions: dict) -> list[dict]:
    return [{**p, "have": versions.get(p["dist"])} for p in pins if not meets(versions.get(p["dist"]), p["min"])]


def venv_python(site: str) -> Path:
    return Path(site).parents[2] / "bin" / "python"


def status(pins: list[dict] | None = None, runner=subprocess.run) -> dict:
    pins = load_pins() if pins is None else pins
    if not enabled():
        return {"off": True, "pins": pins, "below": []}
    if not pins:
        return {"pins": [], "below": []}
    now = live([p["dist"] for p in pins], runner=runner)
    if "error" in now:
        return {"pins": pins, "error": now["error"], "below": []}
    return {"pins": pins, "site": now["site"], "versions": now["versions"], "below": below(pins, now["versions"])}


def apply(pins: list[dict] | None = None, *, runner=subprocess.run, owe=None, by: str = "hermes_env_pins") -> dict:
    ""
    st = status(pins, runner=runner)
    st["applied"] = []
    if not _S.upgrades_on():
        st["refused"] = _S.UPGRADES_OFF_LINE.format(_S.settings_file())
        return st
    if st.get("off") or st.get("error") or not st["below"]:
        return st
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    py = venv_python(st["site"])
    for row in st["below"]:
        spec = str(row.get("install") or f"{row['dist']}>={row['min']}")
        try:
            r = runner([uv, "pip", "install", "--python", str(py), "--no-deps", spec], capture_output=True, text=True, timeout=300)
            rc, tail = r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
        except Exception as e:
            rc, tail = 1, f"{type(e).__name__}: {e}"[:200]
        st["applied"].append({"dist": row["dist"], "was": row["have"], "spec": spec, "rc": rc, "out": tail})
    after = live([p["dist"] for p in st["pins"]], runner=runner)
    if "error" not in after:
        st["versions"], st["below"] = after["versions"], below(st["pins"], after["versions"])
        for a in st["applied"]:
            a["now"] = after["versions"].get(a["dist"])
    if any(a["rc"] == 0 for a in st["applied"]):
        try:
            if owe is None:
                import restart_window
                owe = restart_window.owe_restart
            owe(GATEWAY_UNIT, "hermes env re-pinned after an update: " + ", ".join(
                f"{a['dist']} {a['was']} -> {a.get('now')}" for a in st["applied"] if a["rc"] == 0), by)
            st["restart_owed"] = GATEWAY_UNIT
        except Exception as e:
            st["restart_owed_error"] = str(e)[:120]
    return st


def facts_stamp(installs: Path | None = None) -> float:
    ""
    newest = 0.0
    try:
        for f in (installs or INSTALLS).glob("*/facts.json"):
            newest = max(newest, f.stat().st_mtime)
    except OSError:
        pass
    return newest
