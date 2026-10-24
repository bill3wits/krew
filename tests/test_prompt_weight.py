"""The prompt-weight instrument reads a session's own records and never writes to them."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import prompt_weight as pw

ROOT = Path(__file__).resolve().parents[1]
RECALL = "<memory-context>\n# Hindsight Memory\n- one\n- two\n- three\n</memory-context>"


def _store(home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(exist_ok=True)
    db = home / "state.db"
    con = sqlite3.connect(db)
    con.executescript(
        "create table sessions (id text, source text, system_prompt text, system_prompt_hash text, started_at real,"
        " input_tokens int, api_call_count int, end_reason text);"
        "create table system_prompts (hash text, prompt text);"
        "create table messages (id integer primary key, session_id text, role text, content text, api_content text,"
        " tool_calls text, timestamp real);")
    con.execute("insert into system_prompts values ('h1', ?)", ("S" * 1000,))
    con.execute("insert into sessions values ('s1','api_server',NULL,'h1',100.0,300,3,NULL)")
    rows = [
        ("user", "hello", None, None, 100.0),
        ("assistant", "", None, '[{"function": {"name": "hands"}}]', 101.0),
        ("tool", "T" * 50, None, None, 102.0),
        ("assistant", "done", None, None, 103.0),
        ("user", "again", "again\n\n" + RECALL, None, 110.0),
        ("assistant", "ok", None, None, 111.0),
        ("user", "third", "third\n\n" + RECALL, None, 120.0),
        ("assistant", "ok", None, None, 121.0),
    ]
    con.executemany("insert into messages (session_id, role, content, api_content, tool_calls, timestamp)"
                    " values ('s1',?,?,?,?,?)", rows)
    con.commit()
    con.close()
    (home / "logs" / "agent.log").write_text(
        "2026-09-30 07:52:19,948 INFO [s1] agent.conversation_loop: API call #1: model=ops provider=custom in=500 out=7 total=507 latency=1.5s id=a\n"
        "2026-09-30 07:52:20,948 INFO [s1] agent.conversation_loop: API call #2: model=ops provider=custom in=560 out=7 total=567 latency=0.5s id=b\n"
        "2026-09-30 07:52:30,948 INFO [s1] agent.conversation_loop: API call #1: model=ops provider=custom in=900 out=7 total=907 latency=1.0s id=c\n"
        "2026-09-30 07:52:31,000 INFO [other] agent.conversation_loop: API call #1: model=ops provider=custom in=1 out=1 total=2 latency=1.0s id=x\n"
        "2026-09-30 07:52:40,948 INFO [s1] agent.conversation_loop: API call #1: model=crew provider=custom in=1300 out=7 total=1307 latency=2.0s id=d\n",
        encoding="utf-8")
    return db


def test_recall_is_counted_on_its_turn_and_carried_into_the_next(tmp_path):
    db = _store(tmp_path / "home")
    con = pw.open_ro(db)
    s = pw.weigh_session(con, "s1", pw.log_calls(tmp_path / "home", {"s1"}).get("s1"), tools_chars=200)
    con.close()
    t1, t2, t3 = s["turns"]
    assert s["system"] == 1000 and s["tools"] == 200
    assert (t1["recall"], t2["recall"], t3["recall"]) == (0, len(RECALL), len(RECALL))
    assert (t1["carried"], t2["carried"], t3["carried"]) == (0, 0, len(RECALL))
    assert t2["bullets"] == 3
    assert t1["results"] == 50 and t1["tool_calls"] == 1
    # the request is system + tools + everything sent before + this turn as sent
    assert t1["request"] == 1000 + 200 + 0 + len("hello")
    assert t2["history"] == t1["sent"] + len('[{"function": {"name": "hands"}}]') + 50 + len("done")
    assert t3["request"] == 1000 + 200 + t3["history"] + t3["sent"]


def test_a_call_counter_restart_is_a_turn_boundary(tmp_path):
    db = _store(tmp_path / "home")
    con = pw.open_ro(db)
    s = pw.weigh_session(con, "s1", pw.log_calls(tmp_path / "home", {"s1"}).get("s1"))
    con.close()
    assert [(t["first_tokens"], t["peak_tokens"], t["calls"]) for t in s["turns"]] == [(500, 560, 2), (900, 900, 1), (1300, 1300, 1)]
    assert s["turns"][2]["model"] == "crew"
    assert s["turns"][0]["seconds"] == 2.0


def test_the_store_is_opened_read_only(tmp_path):
    db = _store(tmp_path / "home")
    before = db.read_bytes()
    con = pw.open_ro(db)
    try:
        con.execute("insert into sessions (id) values ('x')")
        raised = False
    except sqlite3.OperationalError:
        raised = True
    con.close()
    assert raised and db.read_bytes() == before
    src = (ROOT / "crew" / "lib" / "prompt_weight.py").read_text()
    assert "mode=ro" in src and "immutable=1\"" not in src and "?immutable" not in src


def test_summary_medians_per_source(tmp_path):
    db = _store(tmp_path / "home")
    con = pw.open_ro(db)
    s = pw.weigh_session(con, "s1", pw.log_calls(tmp_path / "home", {"s1"}).get("s1"))
    ids = pw.sessions_between(con, since=50.0, source="api_server")
    con.close()
    assert ids == ["s1"] and pw.sessions_between(pw.open_ro(db), since=500.0) == []
    d = pw.summarize([s])["api_server"]
    assert d["sessions"] == 1 and d["turns"] == 3 and d["turns_with_recall"] == 2
    assert d["first_tokens"] == 500 and d["peak_tokens"] == 1300 and d["recall_per_later_turn"] == len(RECALL)


def test_default_is_the_passwd_home_and_a_missing_profile_is_never_bootstrapped(tmp_path):
    assert pw.profile_home("default", tmp_path) == tmp_path
    assert pw.profile_home("zoey", tmp_path) == tmp_path / "profiles" / "zoey"
    spec = importlib.util.spec_from_file_location("krew_tool_tax", ROOT / "crew" / "scripts" / "krew-tool-tax.py")
    tax = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tax)
    tax.HERMES = tmp_path  # no config.yaml under it: measure must refuse before it runs hermes at all
    assert "no such profile home" in tax.measure("nobody")["error"]
