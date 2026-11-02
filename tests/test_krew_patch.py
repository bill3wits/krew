"""krew-patch.py: the pin is consistent, and the installer refuses / applies / rolls back on a tiny git repo.

The real proof (the whole series on a clean Hermes checkout at the pin, then each patch's own tests) needs a
Hermes clone: set KREW_HERMES_PIN_TREE to a checkout at the pinned commit and test_real_series_checks runs
`krew-patch.py check` against it (check works in a throwaway worktree and changes nothing).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIN = json.loads((ROOT / "config" / "hermes-pin.json").read_text(encoding="utf-8"))


def kp():
    spec = importlib.util.spec_from_file_location("krew_patch", ROOT / "scripts" / "krew-patch.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_pin_lists_every_patch_in_series_order_with_matching_sha256():
    series = (ROOT / "patches" / "series").read_text(encoding="utf-8").split()
    assert [e["file"] for e in PIN["patches"]] == series
    on_disk = sorted(p.name for p in (ROOT / "patches").glob("*.patch"))
    assert sorted(series) == on_disk
    for e in PIN["patches"]:
        assert hashlib.sha256((ROOT / "patches" / e["file"]).read_bytes()).hexdigest() == e["sha256"], e["file"]
    assert len(PIN["commit"]) == 40


def test_every_patch_says_why_and_its_base():
    for e in PIN["patches"]:
        head = (ROOT / "patches" / e["file"]).read_text(encoding="utf-8").split("\ndiff --git", 1)[0]
        assert "# why: " in head and f"# base: hermes-agent {PIN['commit'][:12]}" in head, e["file"]


def _git(d: Path, *a: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "krew-test@localhost",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "krew-test@localhost", "GIT_CONFIG_GLOBAL": os.devnull}
    return subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True, env=env).stdout.strip()


@pytest.fixture
def mini(tmp_path, monkeypatch):
    """A tiny 'Hermes' (hermes_cli/ + one file), a pin at its commit, and two stacked patches."""
    repo = tmp_path / "hermes"
    (repo / "hermes_cli").mkdir(parents=True)
    (repo / "hermes_cli" / "a.py").write_text("one\ntwo\nthree\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    pdir = tmp_path / "patches"
    pdir.mkdir()
    p1 = ("# why: first\n\ndiff --git a/hermes_cli/a.py b/hermes_cli/a.py\n--- a/hermes_cli/a.py\n+++ b/hermes_cli/a.py\n"
          "@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n")
    p2 = ("# why: second, stacks on first\n\ndiff --git a/hermes_cli/a.py b/hermes_cli/a.py\n--- a/hermes_cli/a.py\n"
          "+++ b/hermes_cli/a.py\n@@ -1,3 +1,3 @@\n one\n-TWO\n+2\n three\n")
    (pdir / "10-a.patch").write_text(p1)
    (pdir / "20-b.patch").write_text(p2)
    pin = {"repo": "https://example.invalid/hermes.git", "commit": base, "describe": "v0-test", "version": "0",
           "patches": [{"file": n, "sha256": hashlib.sha256((pdir / n).read_bytes()).hexdigest(), "tests": []}
                       for n in ("10-a.patch", "20-b.patch")]}
    pf = tmp_path / "pin.json"
    pf.write_text(json.dumps(pin))
    m = kp()
    monkeypatch.setattr(m, "PIN_FILE", pf)
    monkeypatch.setattr(m, "PATCHES", pdir)
    return m, repo, pdir, pf


def run(m, *argv):
    try:
        return m.main(list(argv)), ""
    except SystemExit as e:
        return 1, str(e.code if e.code is not None else "")


def test_apply_then_revert_round_trip(mini):
    m, repo, _, _ = mini
    assert run(m, "check", "--hermes", str(repo))[0] == 0
    assert run(m, "apply", "--hermes", str(repo))[0] == 0
    assert (repo / "hermes_cli" / "a.py").read_text() == "one\n2\nthree\n"
    assert run(m, "apply", "--hermes", str(repo))[0] == 0          # second apply: nothing to do
    assert run(m, "revert", "--hermes", str(repo))[0] == 0
    assert (repo / "hermes_cli" / "a.py").read_text() == "one\ntwo\nthree\n"
    assert _git(repo, "status", "--porcelain") == ""


def test_refuses_another_hermes_version_and_changes_nothing(mini):
    m, repo, _, _ = mini
    (repo / "hermes_cli" / "b.py").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "newer")
    rc, msg = run(m, "apply", "--hermes", str(repo))
    assert rc == 1 and "version mismatch" in msg and "Nothing was changed" in msg and "v0-test" in msg
    assert (repo / "hermes_cli" / "a.py").read_text() == "one\ntwo\nthree\n"


def test_refuses_a_dirty_checkout(mini):
    m, repo, _, _ = mini
    (repo / "hermes_cli" / "a.py").write_text("local edit\n")
    rc, msg = run(m, "apply", "--hermes", str(repo))
    assert rc == 1 and "locally changed" in msg
    assert (repo / "hermes_cli" / "a.py").read_text() == "local edit\n"


def test_refuses_an_edited_patch(mini):
    m, repo, pdir, _ = mini
    (pdir / "20-b.patch").write_text((pdir / "20-b.patch").read_text().replace("+2", "+3"))
    rc, msg = run(m, "apply", "--hermes", str(repo))
    assert rc == 1 and "does not match its sha256" in msg


def test_refuses_a_folder_that_is_not_git(mini, tmp_path):
    m = mini[0]
    rc, msg = run(m, "apply", "--hermes", str(tmp_path / "nowhere"))
    assert rc == 1 and "not a git checkout" in msg


def test_a_failing_patch_rolls_back_the_ones_before_it(mini):
    m, repo, pdir, pf = mini
    bad = ("# why: does not apply\n\ndiff --git a/hermes_cli/a.py b/hermes_cli/a.py\n--- a/hermes_cli/a.py\n"
           "+++ b/hermes_cli/a.py\n@@ -1,3 +1,3 @@\n one\n-nothing-like-this\n+x\n three\n")
    (pdir / "20-b.patch").write_text(bad)
    pin = json.loads(pf.read_text())
    pin["patches"][1]["sha256"] = hashlib.sha256(bad.encode()).hexdigest()
    pf.write_text(json.dumps(pin))
    rc, _ = run(m, "apply", "--hermes", str(repo))
    assert rc == 1
    assert (repo / "hermes_cli" / "a.py").read_text() == "one\ntwo\nthree\n"
    assert _git(repo, "status", "--porcelain") == ""


@pytest.mark.skipif(not os.environ.get("KREW_HERMES_PIN_TREE")
                    or not (Path(os.environ["KREW_HERMES_PIN_TREE"]) / ".git").exists(),
                    reason="set KREW_HERMES_PIN_TREE to a Hermes git checkout at the pin (an archive copy, as the "
                           "install-parity doctor run takes, is not one)")
def test_real_series_checks():
    m = kp()
    assert m.main(["check", "--hermes", os.environ["KREW_HERMES_PIN_TREE"]]) == 0


# --- the tree is the truth (the 1.0.2 buyer test, 2026-10-30: Hermes's installer stashed the series out of a checkout
#     whose state file still said "applied 17 of 17") ---

def test_status_reads_the_tree_not_the_state_file(mini, capsys):
    m, repo, _, _ = mini
    assert run(m, "apply", "--hermes", str(repo))[0] == 0
    capsys.readouterr()
    assert run(m, "status", "--hermes", str(repo))[0] == 0
    assert "applied  2 of 2 in the tree" in capsys.readouterr().out
    _git(repo, "stash", "push", "-m", "hermes-install-autostash-test")      # what the installer did
    rc, _ = run(m, "status", "--hermes", str(repo))
    out = capsys.readouterr().out
    assert rc == 1 and "applied  0 of 2 in the tree" in out and "STATE" in out and "stash" in out


def test_apply_after_an_autostash_applies_again(mini, capsys):
    m, repo, _, _ = mini
    assert run(m, "apply", "--hermes", str(repo))[0] == 0
    _git(repo, "stash", "push", "-m", "hermes-install-autostash-test")
    assert run(m, "apply", "--hermes", str(repo))[0] == 0
    assert "carries none of them" in capsys.readouterr().out
    assert (repo / "hermes_cli" / "a.py").read_text() == "one\n2\nthree\n"
    assert run(m, "status", "--hermes", str(repo))[0] == 0


def test_a_hand_edit_over_a_patched_file_is_not_applied(mini, capsys):
    m, repo, _, _ = mini
    assert run(m, "apply", "--hermes", str(repo))[0] == 0
    (repo / "hermes_cli" / "a.py").write_text("one\nsomething else\nthree\n")
    capsys.readouterr()
    rc, _ = run(m, "status", "--hermes", str(repo))
    assert rc == 1 and "applied  0 of 2 in the tree" in capsys.readouterr().out
    rc, msg = run(m, "apply", "--hermes", str(repo))
    assert rc == 1 and "locally changed" in msg                     # never applied over someone's edit


def test_status_kill_switch_is_the_state_file(mini, monkeypatch, capsys):
    m, repo, _, _ = mini
    assert run(m, "apply", "--hermes", str(repo))[0] == 0
    _git(repo, "stash", "push", "-m", "x")
    monkeypatch.setenv("KREW_PATCH_TREE_TRUTH", "0")
    capsys.readouterr()
    assert run(m, "status", "--hermes", str(repo))[0] == 0
    assert "applied  2 of 2" in capsys.readouterr().out


def test_new_files_a_stash_leaves_behind_are_cleared_before_reapply(tmp_path, monkeypatch, capsys):
    """A stash keeps untracked files out: the series' NEW files stay behind, so `git apply` of the patch that creates
    them would fail ("already exists"). They are byte for byte the series' own and go before the series re-applies."""
    repo = tmp_path / "hermes"
    (repo / "hermes_cli").mkdir(parents=True)
    (repo / "hermes_cli" / "a.py").write_text("one\ntwo\nthree\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    pdir = tmp_path / "patches"
    pdir.mkdir()
    (pdir / "10-new.patch").write_text(
        "# why: edits a.py and creates n.py\n\ndiff --git a/hermes_cli/a.py b/hermes_cli/a.py\n--- a/hermes_cli/a.py\n"
        "+++ b/hermes_cli/a.py\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
        "diff --git a/hermes_cli/n.py b/hermes_cli/n.py\nnew file mode 100644\n--- /dev/null\n+++ b/hermes_cli/n.py\n"
        "@@ -0,0 +1 @@\n+new\n")
    pin = {"repo": "x", "commit": base, "describe": "v0", "version": "0",
           "patches": [{"file": "10-new.patch", "sha256": hashlib.sha256((pdir / "10-new.patch").read_bytes()).hexdigest()}]}
    (tmp_path / "pin.json").write_text(json.dumps(pin))
    m = kp()
    monkeypatch.setattr(m, "PIN_FILE", tmp_path / "pin.json")
    monkeypatch.setattr(m, "PATCHES", pdir)
    assert run(m, "apply", "--hermes", str(repo))[0] == 0
    _git(repo, "stash", "push", "-m", "autostash")
    assert (repo / "hermes_cli" / "n.py").exists()          # left behind
    assert run(m, "apply", "--hermes", str(repo))[0] == 0
    assert "removed  1 file(s)" in capsys.readouterr().out
    assert (repo / "hermes_cli" / "a.py").read_text() == "one\nTWO\nthree\n"
    assert run(m, "status", "--hermes", str(repo))[0] == 0
