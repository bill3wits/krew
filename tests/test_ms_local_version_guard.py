"""apply_green never touches a local-version build (+cu130 etc.) (fixed 09-29).

The guard tested the package NAME for a '+', which a name never carries, so it
filtered nothing; +cu builds were only protected by NEVER_AUTO and --no-deps.
The label is on the version. Kill: MS_LOCAL_VERSION_GUARD=0.
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
_spec = importlib.util.spec_from_file_location("ms_local_ver", _SRC)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

ROWS = [
    {"package": "torchaudio", "current": "2.9.1+cu130", "latest": "2.10.0"},
    {"package": "xformers-ish", "current": "0.1.0", "latest": "0.2.0+cu132"},
    {"package": "timm", "current": "1.0.1", "latest": "1.0.2"},
]


def _rig(monkeypatch):
    """Everything up to the install; the pre-freeze returns None, so apply_green
    reports what it WOULD install and never runs an installer."""
    monkeypatch.setattr(ms, "MS_LOCAL_VERSION_GUARD", True)
    monkeypatch.setattr(ms, "filter_apply_blocked", lambda env, rows: (rows, []))
    monkeypatch.setattr(ms, "own_prefix_packages", lambda py: {ms.canon(r["package"]) for r in ROWS})
    monkeypatch.setattr(ms, "_pip_check_set", lambda py: set())
    monkeypatch.setattr(ms, "freeze_env", lambda *a, **k: None)
    monkeypatch.setattr(ms, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("installer ran")))


def test_the_label_is_read_from_the_version():
    assert ms.is_local_version_row({"package": "torch", "current": "2.9.1+cu130", "latest": "2.10.0"})
    assert ms.is_local_version_row({"package": "x", "current": "1.0", "target": "1.1+local"})
    assert not ms.is_local_version_row({"package": "timm", "current": "1.0.1", "latest": "1.0.2"})


def test_local_builds_are_left_out_of_the_install(monkeypatch):
    _rig(monkeypatch)
    out = ms.apply_green("vllm-x", "/nonexistent/python", [dict(r) for r in ROWS])
    assert out["failed"] == ["timm"]          # the would-install list: only the PyPI build


def test_only_local_builds_means_nothing_to_do(monkeypatch):
    _rig(monkeypatch)
    out = ms.apply_green("vllm-x", "/nonexistent/python", [dict(r) for r in ROWS[:2]])
    assert out["ok"] is True and out["upgraded"] == []
    assert out["skipped_local"] == ["torchaudio", "xformers-ish"]


def test_kill_switch(monkeypatch):
    _rig(monkeypatch)
    monkeypatch.setattr(ms, "MS_LOCAL_VERSION_GUARD", False)
    out = ms.apply_green("vllm-x", "/nonexistent/python", [dict(r) for r in ROWS])
    assert sorted(out["failed"]) == ["timm", "torchaudio", "xformers-ish"]
