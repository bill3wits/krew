"""mediapipe and mfa carry real smoke tests (09-29), so their greens may land.

Bug 1 made an env with no declared smoke test report-only; these two (the
licence-swap envs, both live) had none. Each smoke runs its consumer's real
path: the pinned FaceLandmarker on a blank frame, rail-align's MFA engine warm.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_SRC = Path(os.environ.get("MS_TRAIL_UNDER_TEST") or ROOT / "ms" / "scripts" / "ms-trail-run.py")
_spec = importlib.util.spec_from_file_location("ms_smoke_licence", _SRC)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

MP_PY = Path(str(ms_defaults_only.ENVS_ROOT) + "/mediapipe/bin/python")
MFA_PY = Path(str(ms_defaults_only.ENVS_ROOT) + "/mfa/bin/python")


def test_both_envs_declare_a_smoke_that_runs_their_consumer():
    for env in ("mediapipe", "mfa"):
        assert ms.has_declared_smoke(env)
    assert "FaceLandmarker.create_from_options" in ms.SMOKE_IMPORT["mediapipe"]["code"]
    assert "REF_FACE_MP_MODEL" in ms.SMOKE_IMPORT["mediapipe"]["code"]
    assert "align_mfa.Engine()" in ms.SMOKE_IMPORT["mfa"]["code"] and "e.start()" in ms.SMOKE_IMPORT["mfa"]["code"]


@pytest.mark.skipif(not MP_PY.is_file(), reason="no mediapipe env on this box")
@pytest.mark.skip(reason="runs the smoke in the reference install's real env; krew's test env root holds stubs")
def test_mediapipe_smoke_is_green_and_turns_red_without_its_model(monkeypatch):
    assert ms.smoke("mediapipe", str(MP_PY)) is True
    monkeypatch.setenv("REF_FACE_MP_MODEL", "/nonexistent/face_landmarker.task")
    assert ms.smoke("mediapipe", str(MP_PY)) is False


@pytest.mark.skipif(not MFA_PY.is_file(), reason="no mfa env on this box")
@pytest.mark.skip(reason="runs the smoke in the reference install's real env; krew's test env root holds stubs")
def test_mfa_smoke_is_green_and_turns_red_without_its_bundle(monkeypatch):
    assert ms.smoke("mfa", str(MFA_PY)) is True
    monkeypatch.setenv("REF_ALIGN_MFA_BUNDLE", "/nonexistent/english_us_arpa")
    assert ms.smoke("mfa", str(MFA_PY)) is False
