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
    for prof in ("ada", "crissa"):
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


# --- a buyer's install (the 1.0.2 buyer test, 2026-10-30, finding 4): it crashed on ~/.krew/packs/distributions ---

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

SCRIPT = ROOT / "crew" / "scripts" / "krew-config-restore.py"


def _run(*a, env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *a], capture_output=True, text=True,
                          env=dict(os.environ, **(env or {})))


def test_no_pack_says_so_and_changes_nothing(tmp_path):
    r = _run("--apply", "--hermes-home", str(tmp_path / "h"), "--packs", str(tmp_path / "nopacks"))
    assert r.returncode == 0, r.stderr
    assert "nothing to restore" in r.stdout and not (tmp_path / "h").exists()


def test_default_pack_root_follows_krew_home(tmp_path):
    r = _run("--hermes-home", str(tmp_path / "h"), env={"KREW_HOME": str(tmp_path / "k")})
    assert r.returncode == 0 and str(tmp_path / "k" / "packs" / "distributions") in r.stdout


def _hermes_stub(tmp_path):
    """A hermes that 'migrates' (stamps _config_version: 47 and renames a key the way an upstream migration might),
    and logs each HERMES_HOME it ran under."""
    bin_ = tmp_path / "hermes-stub"
    log = tmp_path / "hermes-calls.log"
    bin_.write_text("#!/bin/sh\necho \"$HERMES_HOME $@\" >> %s\n"
                    "f=\"$HERMES_HOME/config.yaml\"\n"
                    "sed -i 's/^  base_url: .*/  base_url: http:\\/\\/upstream-default\\/v1/' \"$f\"\n"
                    "grep -q '^_config_version' \"$f\" || echo '_config_version: 47' >> \"$f\"\n" % log)
    bin_.chmod(0o755)
    return bin_, log


def test_migrate_first_then_krew_keys_on_top(tmp_path):
    """init's order: Hermes's migration on krew's freshly written configs FIRST, then the pack (krew's pinned
    settings) merged over, so what Hermes changed of krew's keys is put back and upstream's own additions stay."""
    hh, packs = tmp_path / "h", tmp_path / "k" / "packs"
    cfg = "model:\n  provider: custom\n  base_url: http://127.0.0.1:8005/v1\n  default: crew\n"
    for prof in ("crissa", "ada"):
        (hh / "profiles" / prof).mkdir(parents=True)
        (hh / "profiles" / prof / "config.yaml").write_text(cfg)
        (packs / "distributions" / prof).mkdir(parents=True)
        (packs / "distributions" / prof / "config.yaml").write_text(cfg)
    (hh / "config.yaml").write_text(cfg)
    (packs / "distributions" / "BRIDGE-config.yaml").write_text(cfg)
    stub, log = _hermes_stub(tmp_path)
    r = _run("--apply", "--hermes-home", str(hh), "--packs", str(packs), "--hermes-bin", str(stub),
             "--migrate-first", "--migrate-unstamped", "--no-repin")
    assert r.returncode == 0, r.stdout + r.stderr
    homes = {ln.split()[0] for ln in log.read_text().splitlines()}
    assert homes == {str(hh), str(hh / "profiles" / "crissa"), str(hh / "profiles" / "ada")}
    import yaml
    for f in (hh / "config.yaml", hh / "profiles" / "crissa" / "config.yaml", hh / "profiles" / "ada" / "config.yaml"):
        data = yaml.safe_load(f.read_text())
        assert data["model"]["base_url"] == "http://127.0.0.1:8005/v1", f     # krew's key wins, after Hermes
        assert data["_config_version"] == 47, f                                # Hermes's stamp kept
    r2 = _run("--apply", "--hermes-home", str(hh), "--packs", str(packs), "--hermes-bin", str(stub),
              "--migrate-first", "--migrate-unstamped", "--no-repin")
    assert "Nothing to restore" in r2.stdout                                    # idempotent


def test_a_scratch_home_never_runs_the_path_hermes(tmp_path):
    hh, packs = tmp_path / "h", tmp_path / "k" / "packs"
    (hh / "profiles" / "ada").mkdir(parents=True)
    (hh / "profiles" / "ada" / "config.yaml").write_text("x: 1\n")
    (packs / "distributions" / "ada").mkdir(parents=True)
    (packs / "distributions" / "ada" / "config.yaml").write_text("x: 2\n")
    r = _run("--apply", "--hermes-home", str(hh), "--packs", str(packs), "--migrate-unstamped", "--no-repin")
    assert r.returncode == 0, r.stderr
    assert "no hermes command serves" in r.stdout
