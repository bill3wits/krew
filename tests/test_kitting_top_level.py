"""krew-kitting rewrites `skills.disabled` under the TOP-LEVEL `skills:` only (the 1.0.2 buyer test, 2026-10-30).

The front door's config has no top-level `skills:` block but ends with `krew_preload:\n  skills: none`. The old search
took that nested key as the parent and wrote `disabled:` under a scalar: the gateway logged "Failed to load plugin
'krew-kitting'" at line 87 (`skills: none ...` then `disabled:`, expected <block end>)."""
from __future__ import annotations

import yaml

from conftest import load_plugin

FRONT_DOOR = """model:
  provider: custom
plugins:
  enabled:
    - krew-kitting
    - krew-skill-preload
krew_preload:
  skills: none      # the room voice loads no skill bodies (krew-skill-preload)
_config_version: 47
"""


def test_nested_skills_key_is_never_the_parent():
    K = load_plugin("krew-kitting")
    out = K._replace_list_under(FRONT_DOOR, "skills", "disabled", ["a", "b"])
    data = yaml.safe_load(out)                       # parses: the bug wrote `disabled:` under a scalar
    assert data["krew_preload"] == {"skills": "none"}
    assert data["skills"] == {"disabled": ["a", "b"]}


def test_top_level_parent_still_rewritten_in_place():
    K = load_plugin("krew-kitting")
    text = "skills:\n  disabled:\n    - old\n  write_approval: true\nkrew_preload:\n  skills: none\n"
    data = yaml.safe_load(K._replace_list_under(text, "skills", "disabled", ["new"]))
    assert data["skills"] == {"disabled": ["new"], "write_approval": True}
    assert data["krew_preload"] == {"skills": "none"}


def test_apply_config_text_on_the_front_door_parses():
    K = load_plugin("krew-kitting")
    kit = K.Kit(profile="crissa", allowed=[], disabled=["x"])
    data = yaml.safe_load(K.apply_config_text(FRONT_DOOR, kit, allowlist={}))
    assert data["skills"]["disabled"] == ["x"] and "krew-kitting" in data["plugins"]["enabled"]


def test_kill_switch_keeps_the_old_search(monkeypatch):
    monkeypatch.setenv("KREW_KITTING_TOP_LEVEL", "0")
    K = load_plugin("krew-kitting")
    out = K._replace_list_under(FRONT_DOOR, "skills", "disabled", ["a"])
    assert "(krew-skill-preload)\n    disabled:\n      - a\n" in out    # the old (broken) shape
