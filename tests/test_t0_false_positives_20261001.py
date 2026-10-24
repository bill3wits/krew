"""Two T0 false-positive classes fixed in the board sweep (the owner 2026-10-01).

t_3d1ee1b2: the hermes-tree git rule read `git merge-base --is-ancestor` (a READ) as `merge` (2 enforce hits 09-30).
t_8a84e193: the pack rules fired on a prodgate WORK copy (~/.hermes/gate/<id>/work/<abs path>, staging, not the pack)
and on `2>&1` / `2>/dev/null` in reads (68 enforce hits 09-28..09-30). Real pack writes still fire.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import t0_policy  # noqa: E402
from t0_policy import yaml  # noqa: E402

POLICY = t0_policy.POLICY
RULES = t0_policy.filled_rules()
PACK = [re.compile(r["pattern"], re.I) for r in RULES if "crew packs" in r.get("reason", "") or "deployment truth" in r.get("reason", "")]
GIT = [re.compile(r["pattern"], re.I) for r in RULES if "merge\\b(?!-)" in r.get("pattern", "")]
WORK = "/home/crew/.hermes/gate/t_1/work/home/crew/projects/crew-rails/hermes-packs"


def _hit(rules, text: str) -> bool:
    return any(r.search(text) for r in rules)


def test_the_rules_exist():
    assert len(PACK) == 2 and GIT


@pytest.mark.parametrize("cmd,blocked", [
    ("cd ~/.hermes/hermes-agent && git merge-base --is-ancestor 9eccf02f HEAD", False),
    ("cd ~/.hermes/hermes-agent && git merge origin/main", True),
    ("git -C /home/crew/.hermes/hermes-agent merge x", True),
])
def test_merge_base_is_a_read(cmd, blocked):
    assert _hit(GIT, cmd) is blocked


@pytest.mark.parametrize("text,blocked", [
    (f"cp x {WORK}/plugins/a.py", False),
    (f"{WORK}/x.yaml", False),
    ("cd ~/.hermes/gate/t_1 && ls work/home/crew/projects/crew-rails/hermes-packs/tests/ 2>/dev/null", False),
    ("ls /home/crew/projects/crew-rails/hermes-packs/ 2>/dev/null", False),
    ("grep -c x hermes-packs/skills/a.md 2>&1 | head", False),
    ("cp x /home/crew/projects/crew-rails/hermes-packs/plugins/a.py", True),
    ("/home/crew/projects/crew-rails/hermes-packs/x.yaml", True),
    ("echo x > hermes-packs/a", True),
    ("cat a >> hermes-packs/b", True),
    ("sed -i s/a/b/ hermes-packs/x", True),
    ("rm -rf hermes-packs/plugins", True),
])
def test_pack_rules_skip_staging_and_reads(text, blocked):
    assert _hit(PACK, text) is blocked
