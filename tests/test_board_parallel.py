"""Parallel team_board reads (house patch 200; the User 2026-10-03)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import board_parallel as bp  # noqa: E402


def test_only_show_with_an_id_is_parallel_safe():
    assert bp.team_board_read_only({"action": "show", "id": "t_0123abcd"})
    assert bp.team_board_read_only({"action": "thread", "card": "t_0123abcd"})        # a read word + id = show
    assert not bp.team_board_read_only({"action": "show"})                              # no id: team_board refuses
    assert not bp.team_board_read_only({"action": "list"})                              # list WRITES (recompute_ready)
    assert not bp.team_board_read_only({})                                              # default action is list
    for w in ("create", "comment", "unblock", "complete", "answer"):
        assert not bp.team_board_read_only({"action": w, "id": "t_0123abcd", "text": "x"}), w


def test_the_plugin_hook_is_off_by_default():
    text = (ROOT / "plugins/team-crissa/__init__.py").read_text()
    assert 'os.environ.get("KREW_PARALLEL_BOARD_READS", "0")' in text


def test_patch_200_is_on_the_live_tree():
    """200 went live 10-03 (15587694): it is ON the tree when it reverse-applies cleanly - what
    hermes-tree status calls [applied]. A forward check fails on an applied patch by design."""
    tree = Path.home() / ".hermes" / "hermes-agent"
    if not (tree / "agent" / "tool_dispatch_helpers.py").is_file():
        import pytest
        pytest.skip("no hermes tree")
    r = subprocess.run(["git", "apply", "--check", "-R", str(ROOT / "config/hermes/patches/200-plugin-parallel-safe.patch")],
                       cwd=tree, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
