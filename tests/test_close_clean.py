"""close-clean (the User 10-02): a crew card may not close while its worker leaves uncommitted changes in a live
tree. Own git repo + own state.db in tmp; the live board, repos and profiles are never read."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
import close_clean as cc  # noqa: E402

HOOK = ROOT / "crew" / "scripts" / "krew-close-clean.py"


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "krew"
    (r / "lib").mkdir(parents=True)
    (r / "lib" / "a.py").write_text("x = 1\n")
    (r / ".gitignore").write_text("ignored.txt\n")
    _git(r, "init", "-q")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "base")
    return r


def _db(tmp_path, sid, started, calls):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("create table if not exists sessions (id text, started_at real)")
    con.execute("create table if not exists messages (session_id text, role text, content text, tool_calls text)")
    con.execute("insert into sessions values (?, ?)", (sid, started))
    con.execute("insert into messages values (?, 'user', ?, null)", (sid, f"work kanban task t_card{sid[-1]}"))
    for name, args in calls:
        con.execute("insert into messages values (?, 'assistant', '', ?)",
                    (sid, json.dumps([{"function": {"name": name, "arguments": json.dumps(args)}}])))
    con.commit()
    con.close()
    return db


def test_off_is_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv("KREW_CLOSE_CLEAN", raising=False)
    monkeypatch.setattr(cc, "MODE_FILE", tmp_path / "missing.mode")
    assert cc.mode() == "off"
    (tmp_path / "m").write_text("enforce\n")
    monkeypatch.setattr(cc, "MODE_FILE", tmp_path / "m")
    assert cc.mode() == "enforce"
    monkeypatch.setenv("KREW_CLOSE_CLEAN", "bogus")
    assert cc.mode() == "off"


def test_the_hook_answers_nothing_when_off():
    env = {k: v for k, v in os.environ.items() if k != "KREW_CLOSE_CLEAN"}
    env.update(KREW_CLOSE_CLEAN="off", HERMES_PROFILE="bash")
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": "kanban_complete", "session_id": "s1"}),
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == "{}"


def test_a_worker_write_left_uncommitted_is_caught(repo, tmp_path):
    t0 = time.time() - 30
    (repo / "lib" / "a.py").write_text("x = 2\n")
    db = _db(tmp_path, "s1", t0, [("write_file", {"path": str(repo / "lib" / "a.py")})])
    assert cc.worker_dirty(db, "s1", repos=[repo]) == ["krew/lib/a.py"]


def test_a_tilde_path_and_a_shell_write_count(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "REAL_HOME", tmp_path)
    t0 = time.time() - 30
    (repo / "lib" / "b.py").write_text("new\n")
    (repo / "lib" / "a.py").write_text("x = 3\n")
    db = _db(tmp_path, "s2", t0, [("patch", {"path": "~/krew/lib/b.py"}),
                                  ("terminal", {"command": f"sed -i s/2/3/ {repo}/lib/a.py"})])
    assert cc.worker_dirty(db, "s2", repos=[repo]) == ["krew/lib/a.py", "krew/lib/b.py"]


def test_running_or_reading_a_file_is_not_writing_it(repo, tmp_path):
    t0 = time.time() - 30
    (repo / "lib" / "a.py").write_text("someone else's edit\n")
    db = _db(tmp_path, "s3", t0, [("terminal", {"command": f"python3 -m pytest {repo}/lib/a.py; cat {repo}/lib/a.py"})])
    assert cc.worker_dirty(db, "s3", repos=[repo]) == []


def test_another_agents_dirty_file_is_never_blamed(repo, tmp_path):
    t0 = time.time() - 30
    (repo / "lib" / "a.py").write_text("other agent\n")
    (repo / "lib" / "mine.py").write_text("mine\n")
    _git(repo, "add", "lib/mine.py")
    _git(repo, "commit", "-qm", "mine committed")
    db = _db(tmp_path, "s4", t0, [("write_file", {"path": str(repo / "lib" / "mine.py")})])
    assert cc.worker_dirty(db, "s4", repos=[repo]) == []


def test_a_file_dirty_before_the_session_started_is_not_this_worker(repo, tmp_path):
    (repo / "lib" / "a.py").write_text("old dirt\n")
    old = time.time() - 7200
    os.utime(repo / "lib" / "a.py", (old, old))
    db = _db(tmp_path, "s5", time.time() - 60, [("write_file", {"path": str(repo / "lib" / "a.py")})])
    assert cc.worker_dirty(db, "s5", repos=[repo]) == []


def test_ignored_files_never_count(repo, tmp_path):
    (repo / "ignored.txt").write_text("x\n")
    db = _db(tmp_path, "s6", time.time() - 30, [("write_file", {"path": str(repo / "ignored.txt")})])
    assert cc.worker_dirty(db, "s6", repos=[repo]) == []


def test_prodgate_stage_is_a_write(repo, tmp_path):
    (repo / "lib" / "a.py").write_text("staged\n")
    db = _db(tmp_path, "s7", time.time() - 30, [("terminal", {"command": f"prodgate stage t_x {repo}/lib/a.py 2>&1"})])
    assert cc.worker_dirty(db, "s7", repos=[repo]) == ["krew/lib/a.py"]


def test_enforce_refuses_and_names_the_files():
    msg = cc.refusal(["krew/lib/a.py"])
    assert "krew/lib/a.py" in msg and "never bare" in msg


# --- the house's values live in config, never in the code (the User 10-02) ------------------------
def test_the_code_holds_no_repo_or_profile_of_the_house():
    import re
    cfg = json.loads((ROOT / "crew" / "config" / "close-clean.json").read_text())
    names = [Path(r).name for r in cfg["live_repos"]] + list(cfg["crew_profiles"])
    assert names, "the house config lists its repos and crew"
    for code in (ROOT / "crew" / "lib" / "close_clean.py", HOOK):
        text = code.read_text()
        for n in names:
            assert not re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", text), f"{n!r} is in {code.name}"


def test_the_config_resolves_against_the_passwd_home(tmp_path, monkeypatch):
    (tmp_path / "c.json").write_text(json.dumps({"live_repos": ["~/projects/a", "/abs/b"], "crew_profiles": ["X", " y "]}))
    monkeypatch.setattr(cc, "CONFIG", tmp_path / "c.json")
    monkeypatch.setattr(cc, "REAL_HOME", Path("/home/someone"))
    assert cc.live_repos() == [Path("/home/someone/projects/a"), Path("/abs/b")]
    assert cc.crew_profiles() == ("x", "y")


def test_a_missing_config_checks_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CONFIG", tmp_path / "missing.json")
    assert cc.live_repos() == [] and cc.crew_profiles() == ()
    db = _db(tmp_path, "s9", time.time() - 30, [("write_file", {"path": "/x/y.py"})])
    assert cc.worker_dirty(db, "s9") == []


def test_the_krew_config_is_todays_lists():
    cfg = json.loads((ROOT / "crew" / "config" / "close-clean.json").read_text())
    assert isinstance(cfg["live_repos"], list)   # krew ships no live repos: they are the install's (crew.example.yaml ref: line)
    assert cfg["crew_profiles"] == ["ada", "bash", "judge", "lila", "scout", "steward", "vera"]
