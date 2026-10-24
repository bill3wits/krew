"""The box owns /tmp checkouts (the User 2026-10-01): report, and only when armed reap, worktrees under the tmp
root older than 2 h with no live process - never a dirty one, never one whose HEAD is on no branch."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tmp_worktree_reap", ROOT / "crew" / "scripts" / "tmp-worktree-reap.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def _age(path: Path, repo: Path, hours: float):
    t = time.time() - hours * 3600
    adm = m.admin_dir(repo, path)
    for f in [path, adm, adm / "index", adm / "HEAD"]:
        if f.exists():
            os.utime(f, (t, t))


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "OFF", tmp_path / "off")
    repo, tmp = tmp_path / "repo", tmp_path / "tmproot"
    repo.mkdir(); tmp.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-q", "-m", "one")
    for name in ("old", "young", "dirty", "busy"):
        _git(repo, "worktree", "add", "-q", "--detach", str(tmp / name), "HEAD")
    (tmp / "dirty" / "a.txt").write_text("changed\n")
    for name in ("old", "dirty", "busy"):
        _age(tmp / name, repo, 3)
    return repo, tmp


def _run(repo, tmp, log, armed, monkeypatch):
    monkeypatch.setenv("KREW_TMP_WORKTREE_REAP", "1" if armed else "0")
    assert m.main(["--repo", str(repo), "--tmp-root", str(tmp), "--log", str(log)]) == 0
    return json.loads(log.read_text().splitlines()[-1])


def test_report_only_names_the_old_clean_idle_one_and_removes_nothing(box, tmp_path, monkeypatch):
    repo, tmp = box
    busy = subprocess.Popen(["sleep", "30"], cwd=tmp / "busy")
    try:
        rec = _run(repo, tmp, tmp_path / "log.jsonl", False, monkeypatch)
    finally:
        busy.kill()
    v = {Path(r["path"]).name: (r["verdict"], r["why"]) for r in rec["rows"]}
    assert v["old"][0] == "reap"
    assert v["young"][0] == "keep" and "younger" in v["young"][1]
    assert v["dirty"] == ("keep", "uncommitted changes")
    assert v["busy"][0] == "keep" and "in use by pid" in v["busy"][1]
    assert rec["mode"] == "report" and rec["candidates"] == 1 and rec["would_free"] > 0
    assert all((tmp / n).exists() for n in ("old", "young", "dirty", "busy")), "report mode removes nothing"


def test_armed_reaps_only_the_candidate_and_checks_before_after(box, tmp_path, monkeypatch):
    repo, tmp = box
    rec = _run(repo, tmp, tmp_path / "log.jsonl", True, monkeypatch)
    assert rec["mode"] == "reap" and "tmp_used_before" in rec and "tmp_used_after" in rec
    assert not (tmp / "old").exists()
    assert not (tmp / "busy").exists(), "old, clean and idle here (no process this time): reaped too"
    assert (tmp / "young").exists() and (tmp / "dirty").exists()
    assert str(tmp / "old") not in subprocess.run(["git", "-C", str(repo), "worktree", "list"],
                                                  capture_output=True, text=True).stdout


def test_a_head_on_no_branch_is_kept(box, tmp_path, monkeypatch):
    repo, tmp = box
    (tmp / "old" / "b.txt").write_text("b\n")
    _git(tmp / "old", "add", "b.txt"); _git(tmp / "old", "commit", "-q", "-m", "only here")
    _age(tmp / "old", repo, 3)
    rec = _run(repo, tmp, tmp_path / "log.jsonl", True, monkeypatch)
    row = [r for r in rec["rows"] if Path(r["path"]).name == "old"][0]
    assert row["verdict"] == "keep" and "no branch" in row["why"] and (tmp / "old").exists()


def test_the_off_file(box, tmp_path, monkeypatch):
    repo, tmp = box
    (tmp_path / "off").write_text("")
    monkeypatch.setenv("KREW_TMP_WORKTREE_REAP", "1")
    assert m.main(["--repo", str(repo), "--tmp-root", str(tmp), "--log", str(tmp_path / "l")]) == 0
    assert (tmp / "old").exists() and not (tmp_path / "l").exists()


def test_the_default_scan_covers_krew_rails_and_both_hermes_trees():
    names = [str(r) for r in m.DEFAULT_REPOS]
    assert any(n.endswith("/krew") for n in names)        # krew: the checkout (README's clone path); KREW_WORKTREE_REPOS moves it
    assert any(n.endswith("projects/hermes-agent") for n in names)
    assert any(n.endswith(".hermes/hermes-agent") for n in names)
