"""Scout and other utility profiles climb the Hermes config floor on restore."""
from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = runpy.run_path(str(ROOT / "crew" / "scripts" / "krew-config-restore.py"))


def test_lift_below_floor_rewrites_ancient_stamp(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("toolsets:\n- file\n_config_version: 1\n")
    assert MOD["_lift_below_floor"](p) == 12
    assert "_config_version: 12" in p.read_text()
    assert MOD["_explicit_config_version"](p) == 12


def test_lift_is_noop_at_or_above_floor(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("_config_version: 39\n")
    assert MOD["_lift_below_floor"](p) == 39
    assert p.read_text() == "_config_version: 39\n"


def test_crew_includes_scout_as_equal_citizen():
    assert "scout" in MOD["CREW"]
    assert MOD["CONFIG_SUPPORT_FLOOR"] == 12


def test_restore_targets_are_yaml_only(tmp_path, monkeypatch):
    """SOUL.md cannot land via restore. Measured 2026-09-02: ada never appeared."""
    # krew: the packs checkout is KREW_PACKS_ROOT (the reference read its repo's hermes-packs); a scratch one here
    for prof in ("ada", "zoey"):
        d = tmp_path / "distributions" / prof
        d.mkdir(parents=True)
        for name in ("config.yaml", "profile.yaml", "SOUL.md"):
            (d / name).write_text("x: 1\n")
    monkeypatch.setitem(MOD["targets"].__globals__, "PACKS", tmp_path / "distributions")
    names = [label for label, pack, live in MOD["targets"](None)]
    assert names, "restore has YAML targets"
    assert all(
        label.endswith("config.yaml") or label.endswith("profile.yaml")
        for label in names
    ), names
    assert not any("SOUL" in label for label in names)
    src = (ROOT / "crew" / "scripts" / "krew-config-restore.py").read_text()
    assert "THIS TOOL DOES NOT TOUCH SOUL.md" in src
    assert "docs/PACK-SYNC.md" in src


def test_profile_description_is_the_profiles_page_and_never_restored_from_pack():
    """2026-09-18 (the User): Ada routes by the Profiles-page description; a hand edit there must
    survive kit re-apply. Only profile.yaml's description / display_name are live-wins."""
    src = (ROOT / "crew" / "scripts" / "krew-config-restore.py").read_text()
    body = src[src.index("def main("):]
    guard = body[body.index('if label.endswith("profile.yaml")'):body.index("merged = merge(pack, live)")]
    assert '"description"' in guard and '"display_name"' in guard and "pack.pop" in guard
