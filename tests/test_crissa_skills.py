"""Crissa's own skills: every crissa-* skill team-crissa names or crew/scripts/sync-packs.sh syncs ships, lifted by the
crew sync (scripts/krew-crew-sync.py, group team). A shipped reference to a skill that does not ship is a hidden
failure, so these tests read the references out of the shipped files themselves."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from conftest import ROOT

SKILLS = ROOT / "skills"
TEAM = ROOT / "plugins" / "team-crissa" / "__init__.py"
SYNC = ROOT / "crew" / "scripts" / "sync-packs.sh"
HERMES_SKILLS = {"ascii-art"}     # Hermes's own (optional-skills/creative/ascii-art at the pin)


def team_names() -> set:
    text = TEAM.read_text(encoding="utf-8")
    named = set()
    for m in re.finditer(r"Skills? ((?:crissa-[a-z-]+(?:, )?)+)", text):
        named |= set(re.findall(r"crissa-[a-z-]+", m.group(1)))
    named |= set(re.findall(r"(crissa-[a-z-]+)'s shape", text))
    named |= set(re.findall(r'"(crissa-[a-z-]+)/scripts/', text))
    return named


def sync_names() -> set:
    return set(re.findall(r"^\s*sync_skill (crissa-[a-z-]+) ", SYNC.read_text(encoding="utf-8"), re.M))


def test_every_crissa_skill_team_crissa_or_sync_packs_names_ships():
    want = team_names() | sync_names()
    assert {"crissa-look", "crissa-report", "crissa-her-wall", "crissa-list", "crissa-surface-art", "crissa-needs"} <= want
    missing = sorted(s for s in want if not (SKILLS / s / "SKILL.md").is_file())
    assert not missing, missing
    for s in want:
        text = (SKILLS / s / "SKILL.md").read_text(encoding="utf-8")
        assert re.search(rf"^name: {re.escape(s)}$", text, re.M), s


def test_crissa_skills_name_only_skills_that_ship():
    shipped = {p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()} | HERMES_SKILLS
    for d in sorted(SKILLS.glob("crissa-*")):
        for f in d.rglob("*.md"):
            text = f.read_text(encoding="utf-8")
            for m in re.finditer(r"related_skills: \[([^\]]*)\]", text):
                rel = {x.strip() for x in m.group(1).split(",") if x.strip()}
                assert rel <= shipped, (f, rel - shipped)
            for s in re.findall(r"\*\*(crissa-[a-z-]+)\*\*", text):
                assert s in shipped, (f, s)
    m = (SKILLS / "crissa-map" / "map.yaml").read_text(encoding="utf-8")
    assert set(re.findall(r"^\s*skill: ([a-z0-9-]+)", m, re.M)) <= shipped


def test_surface_art_ships_without_style_refs_or_the_roster():
    d = SKILLS / "crissa-surface-art"
    assert not (d / "examples" / "roster").exists()
    assert not [p for p in d.rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    cats = json.loads((d / "categories.json").read_text(encoding="utf-8"))
    seeds = [v.get("seed") for v in cats.get("density_languages", {}).values() if isinstance(v, dict)]
    seeds += [v.get("seed") for v in cats.get("categories", {}).values() if isinstance(v, dict)]
    seeds = [s for s in seeds if s]
    assert seeds
    for s in seeds:
        assert (d / s).is_file(), s
    for f in d.rglob("*.md"):
        assert "roster/" not in f.read_text(encoding="utf-8") or "your own `examples/roster/`" in f.read_text(encoding="utf-8"), f


def test_reference_addresses_go_through_the_player():
    # krew reaches Crissa only through the Wall's player: the wall is $CRISSA_PLAYER/v1/screen, presence is
    # $CRISSA_PLAYER/v1/rail/presence; the old direct addresses are neither in the skills nor settings
    crew_example = (ROOT / "config" / "crew.example.yaml").read_text(encoding="utf-8")
    assert "# CRISSA_PLAYER:" in crew_example
    for var in ("DISPLAY_SHELL_URL", "RAIL_PRESENCE_BASE"):
        assert var not in crew_example, var
    seen = set()
    for d in SKILLS.glob("crissa-*"):
        for f in d.rglob("*"):
            if f.is_file() and f.suffix in (".md", ".yaml", ".json"):
                text = f.read_text(encoding="utf-8")
                assert not re.search(r"(?<![\w/]):(8099|8191|8190)\b", text), f
                assert not re.search(r"\$(DISPLAY_SHELL_URL|RAIL_PRESENCE_BASE)\b", text), f
                seen.update(re.findall(r"\$CRISSA_PLAYER(/v1/screen|/v1/rail/presence)", text))
    assert seen == {"/v1/screen", "/v1/rail/presence"}, seen


def test_team_crissa_finds_the_banner_helper_beside_the_plugin(tmp_path):
    text = TEAM.read_text(encoding="utf-8")
    assert '_BANNER_TOOL = Path(__file__).resolve().parent / "render_banner.py"' in text
    assert "HERMES_HOME" not in text.split("_BANNER_TOOL =", 1)[1].split("\n", 1)[0]
    beside = TEAM.parent / "render_banner.py"
    assert beside.is_file()
    skill = SKILLS / "crissa-surface-art" / "scripts" / "render_banner.py"     # the skill keeps its own copy: one source
    if skill.is_file():
        assert skill.read_bytes() == beside.read_bytes()


def test_install_places_crissa_skills(tmp_path):
    hh, kh = tmp_path / "hermes-home", tmp_path / "krew-home"
    env = {**os.environ, "HOME": str(tmp_path)}
    env.pop("CRISSA_HOME", None)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-install.py"), "--hermes-home", str(hh),
                        "--krew-home", str(kh), "--no-rails"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    for s in sorted(team_names() | sync_names()):
        assert (hh / "skills" / s / "SKILL.md").is_file(), s
    assert (hh / "skills" / "crissa-surface-art" / "scripts" / "render_banner.py").is_file()
