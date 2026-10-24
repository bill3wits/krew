"""One env root for every M&S module: MS_ENVS_ROOT (09-29).

Discovery hardcoded the reference env root while env_rebuild honoured MS_ENVS_ROOT, and
env_roots.lend, os_update (restart debt, code prefixes, which-env) and the
live-user scan kept their own copies - set one and the rest kept reading the
house path. Each check runs in a fresh interpreter, because the root is read
at import.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_SRC = Path(os.environ.get("MS_TRAIL_UNDER_TEST") or ROOT / "ms" / "scripts" / "ms-trail-run.py")

PROBE = r"""
import importlib.util, json, sys
sys.path.insert(0, sys.argv[2])
spec = importlib.util.spec_from_file_location("ms_probe", sys.argv[1])
ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
import os_update, env_roots, env_rebuild
root = sys.argv[3]
results = {"appenv": {"borrowed": {root + "/lender": ["numpy"]}, "orphans": [{"dist": "numpy"}]},
           "lender": {"orphans": []}}
env_roots.lend(results)
print(json.dumps({
    "venvs": sorted(ms.VENVS),
    "ms_root": str(ms.ENVS_ROOT),
    "which": ms.which_env(root + "/appenv/bin/python"),
    "which_house": ms.which_env(sys.argv[4] + "/appenv/bin/python"),
    "osu_which": os_update.default_which_env(root + "/appenv/lib/x.py"),
    "osu_root": str(os_update.ENVS_ROOT),
    "osu_code_prefix": any(p == root.rstrip("/") + "/" for p in os_update._CODE_PREFIXES),
    "rebuild_root": str(env_rebuild.ENVS_ROOT),
    "roots_root": str(env_roots.ENVS_ROOT),
    "lent": results["lender"].get("lent_to"),
}))
"""


def _probe(root: Path) -> dict:
    env = dict(os.environ, MS_ENVS_ROOT=str(root))
    env.setdefault("MS_SETTINGS", ms_defaults_only.NO_USER_LAYER)   # not the live ms.yaml
    out = subprocess.run([sys.executable, "-c", PROBE, str(_SRC), str(ROOT / "ms" / "lib"), str(root), str(ms_defaults_only.ENVS_ROOT)],
                         capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_every_module_reads_the_one_root(tmp_path):
    for name in ("appenv", "lender"):
        (tmp_path / name / "bin").mkdir(parents=True)
        (tmp_path / name / "bin" / "python").write_text("")
    got = _probe(tmp_path)
    root = str(tmp_path)
    assert got["venvs"] == ["appenv", "lender"]            # discovery found the user's envs
    assert got["ms_root"] == got["osu_root"] == got["rebuild_root"] == got["roots_root"] == root
    assert got["which"] == "appenv" and got["which_house"] is None
    assert got["osu_which"] == "appenv" and got["osu_code_prefix"]
    assert got["lent"] == {"appenv": ["numpy"]}, "lend() did not credit the lender under the configured root"
