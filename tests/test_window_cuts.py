"""No silent cuts in the restart window (the owner's rule: a cut an agent relies on says it was cut and offers a way to
read the rest). Each cut listed in truncation-flags-ms.md (window/ section) is either gone or carries
'[cut at N of M chars, full text: <how>]' pointing at a real copy. Nothing here calls systemctl or kanban."""
from __future__ import annotations

import datetime as dt
import importlib
import importlib.util
import os
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "window" / "lib"))

MARK = re.compile(r"\[(?:cut at|last) (\d+) of (\d+) chars, full text: ([^\]]+)\]")


def _tc():
    import text_cut
    return importlib.reload(text_cut)


def _full_from(marked: str) -> str:
    """Follow the marker the way an agent would: `cat <path>` -> the file's text."""
    m = MARK.search(marked)
    assert m, f"no cut marker in {marked[-200:]!r}"
    how = m.group(3)
    assert how.startswith("cat "), how
    return Path(os.path.expanduser(how[4:])).read_text(encoding="utf-8")


# --- the helper ------------------------------------------------------------------------------------------------

def test_text_that_fits_is_left_alone(krew_home):
    tc = _tc()
    assert tc.clip("short", 10, label="x") == "short"
    assert tc.clip("", 10, label="x") == "" and tc.clip(None, 10, label="x") == ""
    assert not (krew_home / "logs" / "cut-text").exists()


def test_a_hard_cut_fits_the_limit_says_so_and_saves_the_whole_text(krew_home):
    tc = _tc()
    text = "".join(f"line {i}\n" for i in range(2000))
    out = tc.clip(text, 5500, label="card-body")
    assert len(out) <= 5500
    m = MARK.search(out)
    kept, total = int(m.group(1)), int(m.group(2))
    assert total == len(text) and out.startswith(text[:kept])
    assert _full_from(out) == text
    assert str(krew_home / "logs" / "cut-text") in os.path.expanduser(m.group(3))


def test_a_soft_cut_keeps_the_limit_and_adds_the_marker(krew_home):
    tc = _tc()
    out = tc.clip("e" * 300, 200, label="note", hard=False)
    assert out.startswith("e" * 200) and "cut at 200 of 300 chars" in out
    assert _full_from(out) == "e" * 300


def test_a_tail_cut_keeps_the_end(krew_home):
    tc = _tc()
    text = "start " + "m" * 500 + " the end"
    out = tc.clip(text, 200, label="tail", tail=True, hard=False)
    assert out.endswith(text[-200:]) and "last 200 of" in out
    assert _full_from(out) == text


def test_where_points_at_an_existing_copy_and_saves_nothing(krew_home):
    tc = _tc()
    out = tc.clip("w" * 50, 10, label="x", where="card t_12345678", hard=False)
    assert out.endswith("[cut at 10 of 50 chars, full text: card t_12345678]")
    assert not (krew_home / "logs" / "cut-text").exists()


def test_same_text_is_one_file_and_old_files_are_dropped(krew_home):
    tc = _tc()
    a = tc.clip("a" * 100, 50, label="x")
    b = tc.clip("a" * 100, 50, label="x")
    assert a == b
    d = krew_home / "logs" / "cut-text"
    old = d / "old-000000000000.txt"
    old.write_text("old")
    t = time.time() - 40 * 86400
    os.utime(old, (t, t))
    tc.clip("b" * 100, 50, label="y")
    assert not old.exists() and len(list(d.glob("*.txt"))) == 2


def test_an_unwritable_store_is_said_not_silent(krew_home, monkeypatch):
    blocker = krew_home / "blocker"
    blocker.write_text("a file, not a dir")
    monkeypatch.setenv("KREW_CUT_TEXT_DIR", str(blocker / "sub"))
    tc = _tc()
    out = tc.clip("z" * 100, 60, label="x")
    assert len(out) <= 60 or out.startswith("[")
    assert "not saved" in out and "of 100 chars" in out


def test_flat_drops_nothing():
    assert _tc().flat("a\n  b\tc ") == "a b c"


# --- the restart window CLI ------------------------------------------------------------------------------------

def _rw():
    import krew_window_settings
    importlib.reload(krew_window_settings)
    _tc()
    import restart_window
    return importlib.reload(restart_window)


def _script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "window" / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _win(monkeypatch, *, show=None):
    """krew-restart-window with kanban captured: [(args...)], `create` answers a card id."""
    _rw()
    win = _script("krew-restart-window")
    calls: list[tuple] = []

    def kanban(*args, timeout=60):
        calls.append(args)
        if args[0] == "create":
            return 0, "created t_0123abcd"
        if args[0] == "show" and show is not None:
            import json
            return 0, json.dumps(show)
        return 0, ""
    monkeypatch.setattr(win, "kanban", kanban)
    return win, calls


def _many_owed(n=200):
    return [{"unit": f"service-number-{i:03d}-with-a-long-name.service", "tier": 1,
             "why": "3 replaced native lib(s) still mapped; 12 env package(s) newer than the process"} for i in range(n)]


def test_card_body_over_5500_says_it_was_cut_and_where_the_rest_is(monkeypatch):
    win, calls = _win(monkeypatch)
    facts = {"owed": _many_owed(), "reboot": {}, "window_key": "2026-10-06", "hours": {"window": "01:00-06:00"},
             "kanban": {"running": 0, "ready": 0}}
    full = win.rw.card_body(facts)
    assert len(full) > 5500
    assert win.file_card(facts) == "t_0123abcd"
    create = calls[0]
    body = create[create.index("--body") + 1]
    assert len(body) <= 5500 and body.startswith(full[:4000])
    assert f"of {len(full)} chars" in body
    assert _full_from(body) == full


def test_a_card_body_that_fits_is_sent_whole(monkeypatch):
    win, calls = _win(monkeypatch)
    facts = {"owed": _many_owed(3), "reboot": {}, "window_key": "k", "hours": {}, "kanban": {}}
    win.file_card(facts)
    body = calls[0][calls[0].index("--body") + 1]
    assert body == win.rw.card_body(facts) and "cut at" not in body


def test_a_window_comment_over_4000_says_it_was_cut(monkeypatch):
    win, calls = _win(monkeypatch)
    text = "go (ruled by zoey): 300 restarted and checked\n" + "".join(f"- ok unit-{i}: restarted\n" for i in range(400))
    win.comment("t_0123abcd", text)
    sent = calls[0][2]
    assert calls[0][:2] == ("comment", "t_0123abcd") and len(sent) <= 4000
    assert _full_from(sent) == text
    win.comment("t_0123abcd", "short")
    assert calls[1][2] == "short"


class _Done:
    def __init__(self, out):
        self.stdout, self.stderr, self.returncode = out, "", 0


def _rac(krew_home, monkeypatch, row=None):
    import json
    (krew_home / "restart-window-after.json").write_text(json.dumps(
        {"units": {"a.service": row or {"name": "voice check", "cmd": ["true"]}}}))
    _rw()
    rac = _script("restart-after-check")
    sent: list[list] = []
    real = rac.subprocess.run

    def fake_run(argv, *a, **kw):
        if "kanban" in argv:
            sent.append(argv)
            return _Done("")
        return real(argv, *a, **kw)
    monkeypatch.setattr(rac.subprocess, "run", fake_run)
    return rac, sent


def test_an_after_check_comment_over_4000_says_it_was_cut(krew_home, monkeypatch):
    rac, sent = _rac(krew_home, monkeypatch)
    out = "RESULT: PASS " + "every clip matched; " * 300
    line = rac.run("a.service", "t_0123abcd", runner=lambda *a, **k: _Done(out))
    assert line == out
    text = sent[0][sent[0].index("comment") + 2]
    assert len(text) <= 4000 and "of " in text and "chars, full text:" in text
    assert _full_from(text) == f"after-check voice check (a.service): {line}"


def test_a_completion_result_over_1500_says_it_was_cut(monkeypatch):
    win, calls = _win(monkeypatch, show={"task": {"status": "ready", "created_at": 1}, "comments": []})
    result = "go (ruled by zoey): 40 restarted and checked, 0 failed.\n" + "".join(
        f"- ok unit-{i}.service: unit-{i} restarted · :80{i:02d} answering\n" for i in range(40))
    assert len(result) > 1500
    win.complete("t_0123abcd", result)
    done = [c for c in calls if c[0] == "complete"][0]
    sent = done[done.index("--result") + 1]
    assert len(sent) <= 1500 and sent.startswith(result[:1000])
    assert _full_from(sent) == result


# --- restart_window ---------------------------------------------------------------------------------------------

def test_a_long_ruling_is_kept_whole(monkeypatch):
    rw = _rw()
    why = "hands: " + "the camera on the left keeps dropping out after each restart and I want the owner to look. " * 8
    assert len(why) > 300
    got = rw.latest_ruling([{"author": "zoey", "created_at": 10, "body": why}])
    assert got["verdict"] == "hands" and got["text"] == why
    d = rw.decide({"window_key": "k", "card": "t_0123abcd"},
                  {"owed": [{"unit": "a.service", "tier": 1, "why": "x"}], "reboot": {}, "window_key": "k",
                   "in_window": True, "hours": {"source": "init"}, "kanban": {"running": 0}},
                  {"open": True, "created_at": 0, "comments": [{"author": "zoey", "created_at": 10, "body": why}]},
                  dt.datetime(2026, 10, 6, 2, 0))
    assert d["action"] == "escalate" and d["text"] == why


def test_a_long_list_item_says_it_was_cut_and_keeps_the_whole_title(krew_home):
    import json
    rw = _rw()
    title = "Restart these services tonight? " + ", ".join(f"unit-{i}.service" for i in range(30))
    body = "".join(f"- unit-{i}.service: 3 replaced native lib(s) still mapped; the owner asked to be told\n"
                   for i in range(120))
    root = krew_home / "approvals"
    item_id = rw.list_ask(title, body, card_id="t_0123abcd", root=root, now=1_000_000)
    item = json.loads((root / "pending" / f"{item_id}.json").read_text())
    assert len(item["title"]) <= 160 and "full text: the first line of this item's body" in item["title"]
    assert len(item["body"]) <= 5000
    assert item["body"].splitlines()[0] == f"Full title: {title}"
    whole = _full_from(item["body"])
    assert whole == f"Full title: {title}\n\n{body}"


def test_a_short_list_item_is_untouched(krew_home):
    import json
    rw = _rw()
    root = krew_home / "approvals"
    item_id = rw.list_ask("Reboot this machine tonight?", "why", root=root, now=1)
    item = json.loads((root / "pending" / f"{item_id}.json").read_text())
    assert item["title"] == "Reboot this machine tonight?" and item["body"] == "why"


# --- reboot intent from the owner's comment ---------------------------------------------------------------------

def _board(rows_tasks, rows_comments):
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at INTEGER)")
    conn.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY, task_id TEXT, author TEXT, body TEXT, "
                 "created_at INTEGER)")
    conn.executemany("INSERT INTO tasks VALUES (?,?,?,?)", rows_tasks)
    conn.executemany("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)", rows_comments)
    return conn


def _reboot_comment():
    import krew_window_settings
    importlib.reload(krew_window_settings)
    _tc()
    import reboot_comment
    return importlib.reload(reboot_comment)


def test_a_long_reboot_reason_says_it_was_cut_and_names_the_card():
    rc = _reboot_comment()
    said = "I rebooted the machine because " + "the GPU fan was screaming and the driver hung twice; " * 10
    now = 1_000_000
    conn = _board([("t_0123abcd", "JANITOR: reboot detected — INTENT: UNKNOWN", "blocked", now - 100)],
                  [("t_0123abcd", "user", said, now - 50)])
    before = rc.stated_before(conn, now)
    assert before["reason"].startswith(" ".join(said.split())[:300])
    assert f"cut at 300 of {len(' '.join(said.split()))} chars" in before["reason"]
    assert "hermes kanban show t_0123abcd" in before["reason"]
    after = rc.explained_cards(conn)
    assert after[0]["reason"] == before["reason"]


def test_a_short_reboot_reason_is_whole():
    rc = _reboot_comment()
    conn = _board([], [("t_1", "user", "reboot for the kernel", 999_990)])
    assert rc.stated_before(conn, 1_000_000)["reason"] == "reboot for the kernel"


def test_a_check_without_a_result_line_marks_its_tail_and_saves_the_output(krew_home, monkeypatch):
    rac, sent = _rac(krew_home, monkeypatch)
    out = "Traceback (most recent call last):\n" + "".join(f'  File "check.py", line {i}, in step\n' for i in range(50)) \
          + "RuntimeError: the voice server answered 503"
    line = rac.result_line(out)
    assert line.startswith("RESULT: FAIL - the check printed no RESULT line: ")
    assert line.endswith("RuntimeError: the voice server answered 503")
    assert f"last 200 of {len(out.strip())} chars" in line
    assert _full_from(line) == out.strip()
    assert rac.result_line("Error: short") == "RESULT: FAIL - the check printed no RESULT line: Error: short"


# --- panel_units (the executor's restart + post-check helpers) --------------------------------------------------

def _pu(monkeypatch, run):
    import krew_window_settings
    importlib.reload(krew_window_settings)
    _tc()
    import panel_units
    pu = importlib.reload(panel_units)
    monkeypatch.setattr(pu, "_run", run)
    monkeypatch.setattr(pu, "_shared_rows", lambda: None)
    return pu


LONG_ERR = "Job for a.service failed because the control process exited with error code.\n" + \
           "See 'systemctl --user status a.service' and 'journalctl --user -xeu a.service' for details. " * 4


def test_a_failed_restart_note_says_it_was_cut(monkeypatch):
    pu = _pu(monkeypatch, lambda cmd, timeout=8.0: (1, "", LONG_ERR.strip()))
    monkeypatch.setattr(pu, "POSTCHECK", False)
    res = pu.start_restart("a", "user", None)
    assert res["ok"] is False and res["note"].startswith(LONG_ERR.strip()[:200])
    assert _full_from(res["note"]) == LONG_ERR.strip()
    monkeypatch.setattr(pu, "POSTCHECK", True)
    res = pu.start_restart("a", "user", None)
    assert _full_from(res["error"]) == LONG_ERR.strip()


def test_a_short_restart_note_is_whole(monkeypatch):
    pu = _pu(monkeypatch, lambda cmd, timeout=8.0: (1, "", "Unit a.service not found."))
    monkeypatch.setattr(pu, "POSTCHECK", False)
    assert pu.start_restart("a", "user", None)["note"] == "Unit a.service not found."


def test_a_journal_error_says_it_was_cut(monkeypatch):
    err = "Failed to get journal access: " + "permission denied on /var/log/journal/abc; " * 10
    pu = _pu(monkeypatch, lambda cmd, timeout=8.0: (1, "", err))
    got = pu.read_journal([("a", "user")], files=False)
    assert got["error"].startswith("user: " + err[:120]) and _full_from(got["error"]) == err


def test_log_file_lines_say_when_they_are_cut(tmp_path):
    import krew_window_settings
    importlib.reload(krew_window_settings)
    _tc()
    import panel_units
    pu = importlib.reload(panel_units)
    log = tmp_path / "app.log"
    log.write_text("short line\n" + "L" * 9000 + "\nlast line\n")
    rows = pu._tail(log, 10)
    assert rows[0] == "short line" and rows[-1] == "last line"
    assert rows[1].startswith("L" * 8192) and f"[line cut at 8192 of 9000 chars, full line: {log}]" in rows[1]
    big = tmp_path / "big.log"
    big.write_text("X" * 70_000 + "\nend\n")
    rows = pu._tail(big, 10)
    assert rows[0].startswith(f"[line start cut, full line: {big}] ") and rows[-1] == "end"
    exact = tmp_path / "exact.log"
    exact.write_text("a" * 99 + "\n" + ("b" * 99 + "\n") * 700)
    rows = pu._tail(exact, 1000)
    assert not any("line start cut" in r for r in rows)


# --- error strings that become a deferral reason -----------------------------------------------------------------

LONG_WHY = "error: patch failed: hermes_cli/kanban_db.py:922 " + "error: hermes_cli/kanban_db.py: patch does not apply " * 4


def test_tree_reasons_keep_the_whole_why():
    rw = _rw()
    got = rw.tree_reason({"ok": False, "broke_at": "20-kanban", "why": LONG_WHY}, None)
    assert LONG_WHY in got and "20-kanban" in got
    got = rw.tree_reason(None, {"rc": 1, "why": LONG_WHY})
    assert LONG_WHY in got


def test_an_api_error_keeps_its_whole_message(krew_home, monkeypatch):
    import json
    import urllib.request
    (krew_home / "restart-window.json").write_text(json.dumps({"api": {"health_detailed_url": "http://127.0.0.1:9/h"}}))
    rw = _rw()

    def boom(*a, **k):
        raise ValueError(LONG_WHY)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert rw.api_runs() == {"error": f"ValueError: {LONG_WHY}"}


def test_a_stack_check_crash_keeps_its_whole_message(krew_home, tmp_path):
    import json
    script = tmp_path / "hermes-tree.py"
    script.write_text(f"def stack_check(*a, **k):\n    raise RuntimeError({LONG_WHY!r})\n")
    (krew_home / "restart-window.json").write_text(json.dumps({"hermes_tree_script": str(script)}))
    rw = _rw()
    assert rw.hermes_stack() == {"ok": False, "error": f"RuntimeError: {LONG_WHY}"}


# --- the CLI's log lines and a failed restart's note ----------------------------------------------------------------

def test_failed_filing_logs_kanban_s_whole_answer(monkeypatch, krew_home):
    win, calls = _win(monkeypatch)
    answer = "Error: refused\n" + "the board is read-only while the roll runs; " * 10

    def kanban(*args, timeout=60):
        calls.append(args)
        return (1, answer) if args[0] == "create" else (0, "")
    monkeypatch.setattr(win, "kanban", kanban)
    assert win.file_card({"owed": [], "reboot": {}, "window_key": "k", "hours": {}, "kanban": {}}) == ""
    log = (krew_home / "logs" / "restart-window.log").read_text()
    assert win.text_cut.flat(answer) in log and log.count("\n") == 1


def test_failed_block_logs_the_whole_answer(monkeypatch, krew_home):
    win, calls = _win(monkeypatch)
    answer = "Error: " + "cannot block a card that a worker holds; " * 10

    def kanban(*args, timeout=60):
        calls.append(args)
        if args[0] == "create":
            return 0, "created t_0123abcd"
        return (1, answer) if args[0] == "block" else (0, "")
    monkeypatch.setattr(win, "kanban", kanban)
    win.file_card({"owed": [], "reboot": {}, "window_key": "k", "hours": {}, "kanban": {}})
    assert win.text_cut.flat(answer) in (krew_home / "logs" / "restart-window.log").read_text()


def test_a_failed_restart_s_note_reaches_the_card_whole(monkeypatch, krew_home, tmp_path):
    import json
    monkeypatch.setenv("KREW_RESTART_RECHECK", "0")
    win, calls = _win(monkeypatch)
    import panel_units
    monkeypatch.setattr(panel_units, "unit_props", lambda name, scope: {"LoadState": "loaded", "InvocationID": "x"})
    monkeypatch.setattr(panel_units, "_run", lambda cmd, timeout=8.0: (1, "", LONG_ERR.strip()))
    monkeypatch.setattr(win.rw, "switched_off", lambda unit: "")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"card": "t_0123abcd", "units": [{"unit": "a.service", "tier": 1, "why": "x"}],
                                "ruling": {"who": "zoey"}}))
    assert win.execute(plan) == 1
    said = [c[2] for c in calls if c[0] == "comment"][-1]
    assert f"- FAILED a.service: {LONG_ERR.strip()}" in said


# --- the guard: no bare cut left in window/ ---------------------------------------------------------------------

# Slices that are not cuts of text anyone reads: ids, a sort key, a prefix search, a suffix strip, a job-table trim.
NOT_TEXT = (
    "hexdigest()[:12]",              # text_cut's file name
    "uuid.uuid4().hex[:12]",          # a job id
    "list(_JOBS)[:-12]",              # keeps the last 12 restart jobs in memory
    "argv[:4]",                       # which argv words name the process
    '.strip("-")[:40]',               # the list item's id slug (the title itself is kept)
    "ln[:25]",                        # sort key: the timestamp
    "body[:400]", "body[:200]",       # find_carried looks for its marks near the top
    "unit[:-8]",                      # drops ".service"
)
SLICE = re.compile(r"\[-?\d*:-?\d+\]|\[-\d+:\]")


def test_no_bare_cut_is_left_in_the_window():
    left = []
    for p in sorted((ROOT / "window").rglob("*")):
        if p.is_dir() or "__pycache__" in p.parts or p.suffix not in (".py", ""):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            rest = line
            for ok in NOT_TEXT:
                rest = rest.replace(ok, "")
            if SLICE.search(rest):
                left.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not left, "cut with no marker (use text_cut.clip, or keep it whole):\n" + "\n".join(left)


def test_a_changed_hermes_env_is_named_whole(tmp_path):
    import json
    rw = _rw()
    inst = tmp_path / "installs" / "abc"
    run_env = inst / "environments" / ("1" * 40)
    cur_env = inst / "environments" / ("2" * 40)
    for d in (run_env, cur_env):
        d.mkdir(parents=True)
    (inst / "facts.json").write_text(json.dumps({"packages": {"venv": {"environment": str(cur_env)}}}))
    proc = tmp_path / "proc" / "42"
    proc.mkdir(parents=True)
    (proc / "maps").write_text(f"7f00 r-xp 0 0 0 {run_env}/lib/python3.12/site-packages/x.so\n")
    rows = rw.hermes_env_owed(lambda unit: 42, proc=tmp_path / "proc")
    assert "1" * 40 in rows[0]["why"] and "2" * 40 in rows[0]["why"]


# --- the Hermes stack check: wired to krew's hermes-tree.py (crew/scripts) and its patch queue ---------------------

def _git(cwd, *args):
    import subprocess
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _hermes_fixture(tmp_path):
    """A Hermes-like checkout at origin/main with one queued patch applied to the working tree (as krew-patch does)."""
    tree, queue = tmp_path / "hermes-agent", tmp_path / "patches"
    (tree / "hermes_cli").mkdir(parents=True)
    queue.mkdir()
    f = tree / "hermes_cli" / "kanban_db.py"
    f.write_text("LIMIT = 3\n")
    _git(tree, "init", "-q")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "base")
    _git(tree, "update-ref", "refs/remotes/origin/main", "HEAD")
    f.write_text("LIMIT = 1\n")
    (queue / "20-limit.patch").write_text("# why: test\n\n" + _git(tree, "diff"))
    return tree, queue, f


def test_the_stack_check_runs_krew_s_hermes_tree_against_the_queue(monkeypatch, tmp_path):
    tree, queue, f = _hermes_fixture(tmp_path)
    monkeypatch.setenv("KREW_HERMES_TREE", str(tree))
    monkeypatch.delenv("HERMES_TREE_STACK", raising=False)
    rw = _rw()
    assert rw.HERMES_TREE_SCRIPT == ROOT / "crew" / "scripts" / "hermes-tree.py"
    monkeypatch.setattr(rw, "HERMES_PATCH_QUEUE", queue)
    got = rw.hermes_stack()
    assert got["ok"] is True and got["differs"] == [] and "note" not in got
    assert rw.tree_reason(got, None) == ""
    f.write_text("LIMIT = 1\nEXTRA = True\n")                    # an edit no patch covers
    got = rw.hermes_stack()
    assert got["ok"] is False
    assert "the hermes patch stack is broken" in rw.tree_reason(got, None)


def test_a_tree_it_cannot_read_is_cannot_tell_not_broken(monkeypatch, tmp_path):
    (tmp_path / "not-git").mkdir()
    monkeypatch.setenv("KREW_HERMES_TREE", str(tmp_path / "not-git"))
    monkeypatch.delenv("HERMES_TREE_STACK", raising=False)
    rw = _rw()
    got = rw.hermes_stack()
    assert "no merge-base" in got["error"] and str(tmp_path / "not-git") in got["error"]
    assert rw.tree_reason(got, None).startswith("cannot tell:")


def test_a_hermes_unit_waits_on_a_broken_stack(krew_home, monkeypatch, tmp_path):
    import json
    tree, queue, f = _hermes_fixture(tmp_path)
    f.write_text("LIMIT = 2\n")                                  # not what the queue says
    (krew_home / "restart-window.json").write_text(json.dumps(
        {"hermes_units": ["hermes-gateway.service"], "hermes_patch_queue": str(queue)}))
    monkeypatch.setenv("KREW_HERMES_TREE", str(tree))
    monkeypatch.delenv("HERMES_TREE_STACK", raising=False)
    monkeypatch.delenv("KREW_RESTART_TREE_CHECK", raising=False)
    rw = _rw()
    assert rw.HERMES_PATCH_QUEUE == queue
    monkeypatch.setattr(rw, "hermes_node_deps", lambda *a, **k: {"rc": 0, "why": ""})
    assert "the hermes patch stack is broken" in rw.tree_now("hermes-gateway.service")
    assert rw.tree_now("other.service") == ""


def test_a_missing_script_is_still_said_not_checked(krew_home, tmp_path):
    import json
    (krew_home / "restart-window.json").write_text(json.dumps({"hermes_tree_script": str(tmp_path / "nope.py")}))
    rw = _rw()
    got = rw.hermes_stack()
    assert got["ok"] is True and got["note"].startswith("not checked: no hermes-tree.py at")
