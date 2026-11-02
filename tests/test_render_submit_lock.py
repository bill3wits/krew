"""One submit per live card+name."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("render_submit", ROOT / "crew" / "scripts" / "render-submit.py")
rs = importlib.util.module_from_spec(SPEC)
sys.modules["render_submit"] = rs
SPEC.loader.exec_module(rs)


def test_live_collision_preparing_and_pending():
    jobs = [
        {"card": "t_a", "name": "sit", "status": "preparing", "take": 1, "submitted": "x"},
        {"card": "t_a", "name": "sit", "status": "pending", "take": 2, "prompt_id": "abc"},
        {"card": "t_a", "name": "sit", "status": "done", "take": 3, "prompt_id": "old"},
        {"card": "t_b", "name": "sit", "status": "preparing"},
    ]
    hit = rs.live_collision(jobs, "t_a", "sit")
    assert hit and hit["status"] == "preparing" and hit["take"] == 1
    jobs[0]["status"] = "done"
    hit = rs.live_collision(jobs, "t_a", "sit")
    assert hit and hit["status"] == "pending" and hit.get("prompt_id") == "abc"
    jobs[1]["status"] = "done"
    assert rs.live_collision(jobs, "t_a", "sit") is None  # take n+1 may append


def test_live_collision_other_name_ok():
    jobs = [{"card": "t_a", "name": "listen", "status": "pending", "prompt_id": "x"}]
    assert rs.live_collision(jobs, "t_a", "sit") is None
