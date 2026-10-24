"""Every krew plugin loads in the pinned Hermes and registers its tools (needs a Hermes checkout + its venv).

Set KREW_HERMES_PIN_TREE (a Hermes checkout at the pin, patches applied) and KREW_HERMES_PYTHON (that checkout's
venv python). The plugins are installed into a throwaway HERMES_HOME; nothing touches your own.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT

TREE, PY = os.environ.get("KREW_HERMES_PIN_TREE"), os.environ.get("KREW_HERMES_PYTHON")
pytestmark = pytest.mark.skipif(not (TREE and PY), reason="set KREW_HERMES_PIN_TREE and KREW_HERMES_PYTHON")

EXPECT = {"krew-board": {"team_board", "team_time", "team_config_audit"}, "krew-docs": {"krew_docs"},
          "krew-hands": {"hands"}, "krew-render": {"render_order"},
          "zoeyos-wall": {"wall_state", "wall_keys", "wall_cue", "wall_home", "wall_comms", "wall_speak", "wall_ask",
                          "wall_stop"}}

PROBE = r"""
import json, sys
import hermes_cli.plugins as P
P.discover_plugins(force=True)
m = P.get_plugin_manager()
out = {}
for k, lp in m._plugins.items():
    out[lp.manifest.name] = {"enabled": lp.enabled, "error": lp.error, "tools": lp.tools_registered,
                             "hooks": lp.hooks_registered}
from toolsets import TOOLSETS
from tools.registry import registry
lean = list((TOOLSETS.get("kanban_lean") or {}).get("tools") or [])
out["__lean__"] = lean
out["__lean_desc__"] = {n: (registry.get_entry(n).schema.get("description") or "") for n in lean if registry.get_entry(n)}
print("JSON" + json.dumps(out))
"""


def test_all_plugins_load_and_register(tmp_path):
    hh = tmp_path / "hermes-home"
    env = {**os.environ, "HERMES_HOME": str(hh), "HOME": str(tmp_path), "KREW_HOME": str(tmp_path / "krew")}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-install.py"), "--hermes-home", str(hh)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in (ROOT / "plugins").iterdir() if (p / "plugin.yaml").exists())
    (hh / "config.yaml").write_text("plugins:\n  enabled: " + json.dumps(names) + "\n")
    p = subprocess.run([PY, "-c", PROBE], cwd=TREE, capture_output=True, text=True, env=env, timeout=300)
    assert p.returncode == 0, p.stderr[-4000:]
    got = json.loads(p.stdout.split("JSON", 1)[1])
    for n in names:
        assert n in got, f"{n} not discovered: {sorted(got)}"
        assert got[n]["enabled"] and not got[n]["error"], (n, got[n])
        assert EXPECT.get(n, set()) <= set(got[n]["tools"]), (n, got[n]["tools"])
    assert len(got["__lean__"]) == 10
    assert all(len(d) <= 160 for d in got["__lean_desc__"].values()), "kanban_lean schemas were not thinned"
