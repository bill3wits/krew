"""The engine tiles only when an engine is the brain (crissatest 2026-10-31: on the claude brain the Control Panel
listed krew-engine@ops / @crew, never installed, as down - the User: "krew came up not the models though")."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("krew_install_panel_tiles", ROOT / "scripts" / "krew_install_panel_tiles.py")
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

CLAUDE = "https://api.anthropic.com/v1 https://api.anthropic.com/v1"
ENGINES = "http://127.0.0.1:8000/v1 http://127.0.0.1:8005/v1"


def test_a_remote_brain_drops_the_engine_tiles():
    assert P.engines_unused({}, {"KREW_BRAIN_URLS": CLAUDE})


def test_local_engines_keep_them():
    assert P.engines_unused({}, {"KREW_BRAIN_URLS": ENGINES}) == ""
    assert P.engines_unused({}, {"KREW_BRAIN_URLS": "https://api.x.ai/v1 http://localhost:8005/v1"}) == ""


def test_no_brains_named_keeps_them():
    assert P.engines_unused({}, {}) == ""


def test_a_users_own_engines_setting_wins():
    assert P.engines_unused({"engines": {"ops": 8000}}, {"KREW_BRAIN_URLS": CLAUDE}) == ""


def test_the_kill_switch():
    assert P.engines_unused({}, {"KREW_BRAIN_URLS": CLAUDE, "KREW_TILES_ENGINES_BY_BRAIN": "0"}) == ""


def test_the_manifest_carries_no_engine_tile_when_dropped():
    spec = importlib.util.spec_from_file_location("krew_panel_tiles", ROOT / "panel" / "krew-panel-tiles.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)
    ids = [x.get("id") for x in T.manifest({"engines": {}})["tiles"]]
    assert not any(str(i).startswith("krew-engine-") for i in ids)
    ids = [x.get("id") for x in T.manifest({})["tiles"]]
    assert "krew-engine-ops" in ids


def test_init_passes_the_brains():
    src = (ROOT / "scripts" / "krew_install_init.py").read_text(encoding="utf-8")
    assert 'sub_env["KREW_BRAIN_URLS"]' in src
