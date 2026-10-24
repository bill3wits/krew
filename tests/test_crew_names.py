"""One set of crew names everywhere: every profile name a SOUL, skill, default or example keys on has a SOUL template.

SOUL cross-references, board assignees, render allow-lists and the hands owner all key on profile names; a name that
drifts in one place (a SOUL says **coder**, the board says bash) breaks routing silently. This keeps them in step."""
from __future__ import annotations

import re

from conftest import ROOT

CREW = {"zoey", "lila", "ada", "bash", "steward", "vera", "scout", "judge"}
ROLES = {"coder", "reviewer", "orchestrator", "researcher", "designer", "worker"}  # a profile is named, not a role


def templates() -> set[str]:
    return {p.parent.name for p in (ROOT / "souls").glob("*/SOUL.md")}


CONSOLE = {"main"}   # the Bridge: Hermes's default profile, the owner's console; not crew (team-zoey and krew-board run there)


def test_templates_are_the_crew():
    assert templates() == CREW | CONSOLE


def test_soul_cross_references_name_crew_profiles():
    bad = []
    for p in sorted((ROOT / "souls").glob("*/SOUL.md")):
        for m in re.finditer(r"\*\*([a-z]+)s?\*\*", p.read_text(encoding="utf-8")):
            if m.group(1) in ROLES:
                bad.append(f"{p.relative_to(ROOT)}: **{m.group(1)}**")
    assert not bad, "\n".join(bad)


def test_no_role_placeholders_left():
    pat = re.compile(r"\b(coder-1|orchestrator profile|reviewer profile)\b|--souls [^\n]*\b(coder|orchestrator|reviewer|researcher)\b")
    hits = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and p.suffix in (".md", ".py", ".yaml", ".json")
            and "test_crew_names" not in p.name and pat.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert not hits, hits


def test_defaults_name_crew_profiles():
    text = (ROOT / "plugins" / "krew-render" / "render_order_tool.py").read_text(encoding="utf-8")
    m = re.search(r"DEFAULT_PROFILES = \(([^)]*)\)", text)
    assert m and set(re.findall(r'"([a-z]+)"', m.group(1))) == {"lila", "bash"}
    hands = (ROOT / "plugins" / "krew-hands" / "hands.py").read_text(encoding="utf-8")
    assert '"profile": "zoey"' in hands
    ex = (ROOT / "config" / "krew.example.yaml").read_text(encoding="utf-8")
    assert re.search(r"^\s+default_assignee: ada\b", ex, re.M)
    assert re.search(r"^\s+profile: zoey\b", ex, re.M)
    assert re.search(r"^\s+profiles: \[lila, bash\]", ex, re.M)
    for name in re.findall(r"^#\s{3}([a-z]+)[ ,(]", (ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"), re.M):
        if name not in ("team", "docs", "hands", "skill_read", "kanban_lean", "image_gen", "zoeyos", "steward"):
            assert name in CREW, name
