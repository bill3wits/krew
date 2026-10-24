"""M&S crash evidence reads the unit's journal with journalctl itself (09-29).

It ran `systemctl --no-ask-password journalctl -u <unit>`: systemctl has no
`journalctl` verb, so the journal half of every "did not come back" note was
empty, and the test that covered it faked the systemctl wrapper and accepted
the verb. This pins the real argv.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_SRC = Path(os.environ.get("MS_TRAIL_UNDER_TEST") or ROOT / "ms" / "scripts" / "ms-trail-run.py")
_spec = importlib.util.spec_from_file_location("ms_crash_journal", _SRC)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


def test_journal_is_read_with_journalctl_not_a_systemctl_verb():
    argv = ms._journal_argv("hindsight.service")
    assert argv[0] == "journalctl" and "systemctl" not in argv
    assert argv[argv.index("-u") + 1] == "hindsight.service" and "--user" not in argv
    assert ms._journal_argv("audio8-tts.service", "user")[:2] == ["journalctl", "--user"]


def test_crash_evidence_runs_that_argv_and_never_systemctl(monkeypatch):
    seen = []
    monkeypatch.setattr(ms, "run", lambda argv, *a, **k: seen.append(argv) or (0, "x: Traceback boom\n", ""))
    monkeypatch.setattr(ms, "_svc_systemctl", lambda *a, **k: (_ for _ in ()).throw(AssertionError("systemctl used")))
    ev = ms._crash_evidence("rail-stt.service", "user")
    assert seen == [ms._journal_argv("rail-stt.service", "user")]
    assert "Traceback boom" in ev


def test_the_restart_check_passes_the_unit_scope():
    src = _SRC.read_text()
    assert '_crash_evidence(spec["unit"], spec.get("scope") or "system")' in src


def test_crash_evidence_returns_real_journal_lines():
    """No fakes: a transient user unit dies with a known error line, and
    _crash_evidence reads that line back out of the real journal. Skipped where
    there is no user systemd/journal (containers, CI)."""
    import shutil
    import subprocess
    import time
    import uuid
    if not (shutil.which("systemd-run") and shutil.which("journalctl")):
        import pytest
        pytest.skip("no systemd-run/journalctl")
    tag = uuid.uuid4().hex[:12]
    unit = f"ms-crash-evidence-probe-{tag}"
    r = subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
                        "sh", "-c", f"echo 'Traceback (most recent call last): probe-{tag}' >&2; exit 3"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        import pytest
        pytest.skip(f"no user systemd: {r.stderr.strip()[:120]}")
    ev = ""
    for _ in range(40):                       # the journal flushes within a second or two
        ev = ms._crash_evidence(f"{unit}.service", "user")
        if f"probe-{tag}" in ev:
            break
        time.sleep(0.25)
    assert f"probe-{tag}" in ev, f"journal line not found; got {ev!r}"
    assert ev.startswith("journal: ")
    # the system-scope read of a user unit finds nothing, which is why scope is passed
    assert f"probe-{tag}" not in ms._crash_evidence(f"{unit}.service", "system")
