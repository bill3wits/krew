"""hermes-tree: the house's hermes changes as a patch queue that survives
`hermes update` (the User 2026-09-12: "any way to clean the tree?")."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hermes_tree", ROOT / "crew" / "scripts" / "hermes-tree.py")
ht = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ht)


def _git(*a, cwd):
    return subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """A bare 'origin' with main, and a clone that tracks it."""
    origin = tmp_path / "origin.git"; _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    work = tmp_path / "seed"; work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work); _git("config", "user.email", "t@t", cwd=work); _git("config", "user.name", "t", cwd=work)
    (work / "core.py").write_text("LIMIT = 3\n\ndef f():\n    return 1\n")
    (work / "tool.py").write_text("def gate():\n    return True\n")
    _git("add", ".", cwd=work); _git("commit", "-qm", "upstream base", cwd=work)
    _git("remote", "add", "origin", str(origin), cwd=work); _git("push", "-q", "origin", "main", cwd=work)
    tree = tmp_path / "tree"; _git("clone", "-q", str(origin), str(tree), cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=tree); _git("config", "user.name", "t", cwd=tree)
    return origin, tree


def test_capture_status_verify_round_trip(tmp_path, monkeypatch):
    origin, tree = _repo(tmp_path)
    queue = tmp_path / "queue"
    monkeypatch.setattr(ht, "TREE", tree); monkeypatch.setattr(ht, "QUEUE", queue)
    # a committed house change + a dirty one on the same file
    (tree / "core.py").write_text("LIMIT = 3\n\ndef f():\n    return 1\n\ndef refuse_unknown():\n    return False\n")
    _git("commit", "-qam", "house: refuse unknown", cwd=tree)
    (tree / "core.py").write_text((tree / "core.py").read_text().replace("LIMIT = 3", "LIMIT = 1"))
    (tree / "tool.py").write_text("def gate():\n    return 'resolved'\n")
    s = ht.status(tree, queue)
    assert s["uncovered"] == ["core.py", "tool.py"] and s["tree"]["ahead"] == 1
    ht.capture("10-refuse-unknown", ["core.py"], base="origin/main..HEAD", why="w", card="t_1", tests="", tree=tree, queue=queue)
    ht.capture("20-limit", ["core.py"], base="HEAD", why="w", tree=tree, queue=queue)
    ht.capture("30-gate", ["tool.py"], base="HEAD", why="w", tree=tree, queue=queue)
    hdr = ht.parse_header((queue / "10-refuse-unknown.patch").read_text())
    assert hdr["card"] == "t_1" and "captured" in hdr
    s = ht.status(tree, queue)
    assert [r["state"] for r in s["patches"]] == ["applied", "applied", "applied"] and s["uncovered"] == []
    assert ht.verify(tree, queue) == 0
    # the committed patch must not contain upstream history: only the house hunk
    assert "refuse_unknown" in (queue / "10-refuse-unknown.patch").read_text()
    assert "LIMIT = 1" not in (queue / "10-refuse-unknown.patch").read_text()


def test_missing_and_stale_are_told_apart(tmp_path, monkeypatch):
    origin, tree = _repo(tmp_path)
    queue = tmp_path / "queue"
    (tree / "tool.py").write_text("def gate():\n    return 'resolved'\n")
    ht.capture("30-gate", ["tool.py"], base="HEAD", why="w", tree=tree, queue=queue)
    _git("checkout", "-q", "--", "tool.py", cwd=tree)          # edit dropped -> patch missing
    assert ht.patch_state(queue / "30-gate.patch", tree) == "missing"
    (tree / "tool.py").write_text("def gate():\n    return 0\n")  # upstream moved under it -> stale
    assert ht.patch_state(queue / "30-gate.patch", tree) == "stale"
    assert ht.verify(tree, queue) == 1


def test_update_refuses_uncovered_and_stale(tmp_path, monkeypatch):
    origin, tree = _repo(tmp_path)
    queue = tmp_path / "queue"; queue.mkdir()
    (tree / "tool.py").write_text("def gate():\n    return 'x'\n")
    assert ht.update(dry_run=True, tree=tree, queue=queue) == 2          # uncovered
    ht.capture("30-gate", ["tool.py"], base="HEAD", why="w", tree=tree, queue=queue)
    assert ht.update(dry_run=True, tree=tree, queue=queue) == 0          # plan only


def test_an_edit_under_an_applied_patch_is_seen_as_beyond_the_queue(tmp_path, monkeypatch):
    """2026-09-17 22:36: a worker edited kanban_db.py in place, under patch 70; the
    file-level `uncovered` check read all green."""
    origin, tree = _repo(tmp_path)
    queue = tmp_path / "queue"
    monkeypatch.setattr(ht, "TREE", tree); monkeypatch.setattr(ht, "QUEUE", queue)
    (tree / "core.py").write_text((tree / "core.py").read_text().replace("LIMIT = 3", "LIMIT = 1"))
    ht.capture("20-limit", ["core.py"], base="HEAD", why="w", tree=tree, queue=queue)
    assert ht.status(tree, queue)["beyond"] == {} and ht.verify(tree, queue) == 0
    # a worker adds a line under the patched file
    (tree / "core.py").write_text((tree / "core.py").read_text() + "\ndef spawn_env():\n    return 'wrapper'\n")
    s = ht.status(tree, queue)
    assert [r["state"] for r in s["patches"]] == ["applied"] and s["uncovered"] == []
    assert s["beyond"] == {"core.py": "3 line(s) differ from HEAD + patches"}
    assert ht.verify(tree, queue) == 1


def _overlapping_queue(tmp_path):
    """Base A/B/C; patch 10 rewrites B, patch 20 rewrites A and C around it —
    so 10 cannot reverse alone on the live tree (its context moved), while the
    stack, reversed newest-first, lands on the base (2026-09-19: 10 and 80 read
    stale on the live hermes tree for exactly this reason)."""
    origin, tree = _repo(tmp_path)
    queue = tmp_path / "queue"; queue.mkdir()
    (tree / "core.py").write_text("A\nB\nC\n"); _git("commit", "-qam", "base", cwd=tree)
    _git("push", "-q", "origin", "main", cwd=tree); _git("fetch", "-q", cwd=tree)
    (tree / "core.py").write_text("A\nB1\nC\n")
    (queue / "10-b.patch").write_text("# why: b\n\n" + _git("diff", cwd=tree))
    _git("add", "core.py", cwd=tree)                     # stage 10 only to diff 20 against it
    (tree / "core.py").write_text("A2\nB1\nC2\n")
    (queue / "20-ac.patch").write_text("# why: ac\n\n" + _git("diff", cwd=tree))
    _git("reset", "-q", cwd=tree)                          # both live as dirty edits, like the hermes tree
    return tree, queue


def test_overlapping_patches_are_applied_in_the_stack(tmp_path):
    tree, queue = _overlapping_queue(tmp_path)
    assert ht.patch_state(queue / "10-b.patch", tree) == "stale"          # alone, it cannot tell
    s = ht.status(tree, queue)
    assert s["stack"]["ok"] and s["beyond"] == {}
    assert [r["state"] for r in s["patches"]] == ["applied", "applied"]
    assert s["patches"][0].get("stacked") is True
    assert ht.verify(tree, queue) == 0
    assert _git("status", "--porcelain", cwd=tree).strip() == "M core.py"  # tree untouched


def test_an_edit_beyond_the_stack_breaks_it(tmp_path, monkeypatch):
    tree, queue = _overlapping_queue(tmp_path)
    (tree / "core.py").write_text("A2\nB1\nC2\nD-by-a-worker\n")
    s = ht.status(tree, queue)
    assert not s["stack"]["ok"] and ht.verify(tree, queue) == 1
    monkeypatch.setenv("HERMES_TREE_STACK", "0")
    assert ht.stack_check(tree, queue) == {"ok": False, "why": "off"}
