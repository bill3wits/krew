"""krew_baseline keeps its readings and arm file under the Hermes home it runs under, never a hard-coded ~/.hermes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

JANITOR = Path(__file__).resolve().parents[1] / "janitor"


def _paths(tmp_path, **env) -> dict:
    e = {k: v for k, v in os.environ.items()
         if k not in ("KREW_STATE_HOME", "HERMES_HOME", "KREW_BASELINE_DIR", "KREW_BASELINE_ARM_FILE")}
    e.update(HOME=str(tmp_path / "home"), **env)
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import krew_baseline as b; "
            "print(json.dumps({'home': str(b.HOME), 'state': str(b.STATE), 'arm': str(b.ARM_FILE)}))")
    out = subprocess.run([sys.executable, "-c", code, str(JANITOR)], env=e, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_krew_state_home_wins(tmp_path):
    p = _paths(tmp_path, KREW_STATE_HOME=str(tmp_path / "state"), HERMES_HOME=str(tmp_path / "hermes"))
    assert p == {"home": str(tmp_path / "state"), "state": str(tmp_path / "state" / "krew-baseline"),
                 "arm": str(tmp_path / "state" / "krew-baseline.json")}


def test_the_hermes_home_it_runs_under(tmp_path):
    p = _paths(tmp_path, HERMES_HOME=str(tmp_path / "hermes"))
    assert p["home"] == str(tmp_path / "hermes")
    assert p["arm"] == str(tmp_path / "hermes" / "krew-baseline.json")


def test_a_profile_home_shares_the_root_baseline(tmp_path):
    p = _paths(tmp_path, HERMES_HOME=str(tmp_path / "hermes" / "profiles" / "bash"))
    assert p["home"] == str(tmp_path / "hermes"), "every gateway and crew loop reads one baseline"


def test_nothing_set_is_the_default_hermes_home(tmp_path):
    assert _paths(tmp_path)["home"] == str(tmp_path / "home" / ".hermes")
