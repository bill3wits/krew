"""A hermes update's bundled-skill sync cannot silently add a skill an allowlist excludes (the User 2026-10-02).

Replays 09-26 18:09: the bundled sync copied autonomous-ai-agents/hermes-agent into every profile. judge's
allowlist keeps nothing bundled; ada's keeps hermes-agent. The live profiles are never touched: a scratch
profiles root, the repo's real krew-kitting plugin and allowlist.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "crew" / "scripts" / "krew-kit-disable-unlisted.py"
spec = importlib.util.spec_from_file_location("kit_unlisted", SCRIPT)
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)
# krew: the plugin is read from the live hermes home by default; the test reads the repo's own copy
KITTING = ROOT / "plugins" / "krew-kitting" / "__init__.py"
U.KITTING = KITTING

SKILL = "---\nname: {n}\ndescription: test\n---\nbody\n"


def profile(root: Path, name: str, disabled: list[str]) -> Path:
    p = root / name
    (p / "skills" / "house" / f"krew-{name}").mkdir(parents=True)
    (p / "skills" / "house" / f"krew-{name}" / "SKILL.md").write_text(SKILL.format(n=f"krew-{name}"))
    (p / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "x"},
                                                   "skills": {"disabled": disabled, "write_approval": True}}))
    return p


def bundled_sync(root: Path, names=("judge", "ada")) -> None:
    """What `hermes update` did at 09-26 18:09: the always-bundled skill into every profile, plus its manifest."""
    for n in names:
        d = root / n / "skills" / "autonomous-ai-agents" / "hermes-agent"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(SKILL.format(n="hermes-agent"))
        (root / n / "skills" / ".bundled_manifest").write_text("hermes-agent:70b84ca19f722816fd70582c47a1d481\n")


def disabled(root: Path, name: str) -> list[str]:
    return (yaml.safe_load((root / name / "config.yaml").read_text())["skills"] or {}).get("disabled") or []


def test_the_0926_copy_is_caught_and_disabled_only_where_the_allowlist_excludes_it(tmp_path):
    """09-26's copy was hermes-agent, which hermes treats as ESSENTIAL (seeded everywhere, never disableable), so
    judge's allowlist now keeps it (2026-10-02). The same sync copying a skill judge does NOT keep is caught."""
    allow = U.kitting().load_allowlist()
    assert allow.get("judge", set()) == {"hermes-agent"} and "github" not in allow.get("judge", set())
    profile(tmp_path, "judge", [])
    profile(tmp_path, "ada", ["zz-old"])
    assert U.run(tmp_path, write=False) == {}, "before the update: nothing to do"
    bundled_sync(tmp_path)                                   # the exact 09-26 copy
    assert U.run(tmp_path, write=False) == {}, "hermes-agent is kept by judge's allowlist now"
    for n in ("judge", "ada"):                               # the same sync, with a skill judge excludes
        d = tmp_path / n / "skills" / "github" / "github"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(SKILL.format(n="github"))
    want = {"judge": ["github"]} if "github" in allow.get("ada", set()) else {"judge": ["github"], "ada": ["github"]}
    ada_before = (tmp_path / "ada" / "config.yaml").read_text()
    assert U.run(tmp_path, write=True) == want
    assert disabled(tmp_path, "judge") == ["github"]
    if "ada" not in want:
        assert (tmp_path / "ada" / "config.yaml").read_text() == ada_before, "kept by its allowlist: untouched"
    assert yaml.safe_load((tmp_path / "judge" / "config.yaml").read_text())["skills"]["write_approval"] is True
    assert U.run(tmp_path, write=True) == {}, "a second pass writes nothing"


def test_it_only_adds_never_removes_or_enables(tmp_path):
    profile(tmp_path, "judge", ["zz-old-name", "airtable"])
    d = tmp_path / "judge" / "skills" / "github" / "github"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL.format(n="github"))
    U.run(tmp_path, write=True)
    assert disabled(tmp_path, "judge") == ["airtable", "github", "zz-old-name"]


def test_the_cli_reports_and_the_kill_switch_stops_it(tmp_path):
    profile(tmp_path, "judge", [])
    d = tmp_path / "judge" / "skills" / "github" / "github"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL.format(n="github"))
    r = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--profiles-root", str(tmp_path)],
                       capture_output=True, text=True, env=dict(os.environ, KREW_KITTING_PLUGIN=str(KITTING)))
    assert "judge would disable 1: github" in r.stdout and disabled(tmp_path, "judge") == []
    r = subprocess.run([sys.executable, str(SCRIPT), "--profiles-root", str(tmp_path)], capture_output=True,
                       text=True, env={"PATH": "/usr/bin:/bin", "KREW_KIT_DISABLE_UNLISTED": "0",
                                       "HOME": str(tmp_path)})
    assert "off" in r.stdout and disabled(tmp_path, "judge") == []


def test_the_rule_is_the_pin_checks_own():
    pin = (ROOT / "crew" / "scripts" / "krew-pin-check.py").read_text()
    assert "on_disk = kit.discovered" in pin and "extra = on_disk - kit.allowed - live_dis" in pin
    src = SCRIPT.read_text()
    assert "set(kit.discovered) - kit.allowed - live_dis" in src


def test_every_update_trigger_runs_it_before_the_pin_check():
    sh = (ROOT / "crew" / "scripts" / "krew-kit-reapply.sh").read_text()
    restore = sh.index('scripts/krew-config-restore.py" --apply')
    step = sh.index('scripts/krew-kit-disable-unlisted.py"')
    pin = sh.index('scripts/krew-pin-check.py"')
    assert restore < step < pin, "after the YAML restore, before the pin check"
    assert 'krew-kit-disable-unlisted.py" --dry-run' in sh, "a dry run of the reapply reports it too"
    # the triggers that run krew-kit-reapply: the hermes tree's HEAD moving (an update) and hermes-tree update
    assert "hermes-agent/.git/logs/HEAD" in (ROOT / "crew" / "units" / "krew-kit-reapply.path").read_text()


def test_hermes_tree_update_runs_it_even_with_the_reapply_switched_off():
    """krew-kit-reapply has an off switch (on since 09-27); the update command's own post-update steps run
    regardless, so the disable runs there too - after the gate check, before the (possibly off) reapply."""
    src = (ROOT / "crew" / "scripts" / "hermes-tree.py").read_text()
    gate = src.index('receipt["gate_check"] = post_update_gate_check(post)')
    step = src.index('receipt["skills_unlisted"] = disable_unlisted_after_update()')
    kit = src.index("k = subprocess.run([\"bash\", str(kit)")
    assert gate < step < kit
    fn = src.split("def disable_unlisted_after_update() -> dict:")[1].split("\ndef ")[0]
    assert "krew-kit-disable-unlisted.py" in src and "never fails" in fn.lower()
