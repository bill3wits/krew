"""The trail-scan confirmation reads the scan file and says one true line."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)


def _mod():
    spec = importlib.util.spec_from_file_location("ms_security_confirm", ROOT / "ms" / "scripts" / "ms-security-confirm.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_listed_clean_stale_and_unmeasured(tmp_path):
    m = _mod()
    f = tmp_path / "security-LATEST.json"
    f.write_text(json.dumps({"measured": True, "finding_count": 2, "findings": [
        {"package": "PyJWT", "version": "2.13.0", "vuln_id": "GHSA-w2cx-738m-mc7w"}, {"package": "other", "vuln_id": "X"}]}))
    rc, line = m.check("pyjwt", f)
    assert rc == 1 and "STILL lists" in line and "GHSA-w2cx-738m-mc7w" in line
    rc, line = m.check("pyjwt", f, newer_than=f.stat().st_mtime + 10)
    assert rc == 2 and "no scan since the change" in line, "an old scan proves nothing about a new install"
    f.write_text(json.dumps({"measured": True, "finding_count": 1, "findings": [{"package": "other", "vuln_id": "X"}]}))
    assert m.check("PyJWT", f)[0] == 0
    f.write_text(json.dumps({"measured": False, "findings": []}))
    assert m.check("PyJWT", f)[0] == 2, "a scan that did not complete is not clean"
    assert m.check("PyJWT", tmp_path / "missing.json")[0] == 2
