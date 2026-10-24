"""krew-hands: the child command, its environment, one job at a time, never nested, the reply whole."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

from conftest import submodule


def H():
    return submodule("krew-hands", "hands")


def test_argv_has_no_yolo_by_default_and_carries_the_profile(monkeypatch):
    h = H()
    monkeypatch.setenv("KREW_HERMES_BIN", "/opt/x/hermes")
    argv = h.argv_for("list the board", "zoey", h.settings())
    assert argv[:3] == ["/opt/x/hermes", "-p", "zoey"]
    assert "--yolo" not in argv and "--oneshot" in argv
    assert "--yolo" in h.argv_for("x", "zoey", h.settings({"yolo": True}))


def test_child_env_marks_nesting_and_switches_off_subdirectory_hints():
    h = H()
    env = h.child_env(h.settings(), base={})
    assert env["KREW_HANDS_NESTED"] == "1" and env["HERMES_SUBDIRECTORY_HINTS"] == "0"
    assert "HERMES_SUBDIRECTORY_HINTS" not in h.child_env(h.settings({"subdirectory_hints": True}), base={})


def test_the_reply_comes_back_whole(tmp_path):
    h = H()
    long = "line\n" * 5000
    out = h.run("do it", profile="zoey", runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=long, stderr=""),
                lock_path=tmp_path / "l")
    assert out == long.strip()


def test_a_failed_child_says_it_did_not_finish(tmp_path):
    h = H()
    out = h.run("do it", profile="zoey", lock_path=tmp_path / "l",
                runner=lambda *a, **k: SimpleNamespace(returncode=3, stdout="partial", stderr=""))
    assert "did not finish" in out and "partial" in out


def test_a_timeout_says_it_did_not_finish(tmp_path):
    h = H()

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("hermes", 1)
    assert "did not finish" in h.run("x", profile="p", runner=slow, lock_path=tmp_path / "l")


def test_nested_hands_refuses(monkeypatch, tmp_path):
    h = H()
    monkeypatch.setenv("KREW_HANDS_NESTED", "1")
    assert "already inside" in h.run("x", profile="p", lock_path=tmp_path / "l", runner=lambda *a, **k: None)


# ---- the plugin's handler is the reference install's hands plugin code (2026-10-10) --------------------------------
from types import SimpleNamespace as _NS  # noqa: E402

from conftest import load_plugin  # noqa: E402


def P():
    return load_plugin("krew-hands")


def test_run_hands_calls_the_shipped_child_under_krew_home(krew_home):
    p = P()
    script = krew_home / "crew" / "scripts" / "zoey-hands.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n")
    assert p.SCRIPT == script
    seen = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return _NS(returncode=0, stdout="filed. confirmed: card t_1", stderr="")
    out = p.run_hands("file a card", runner=runner)
    assert seen["argv"][:2] == ["python3", str(script)] and seen["argv"][2] == "file a card"
    assert out.endswith("confirmed: card t_1")


def test_run_hands_falls_back_to_the_in_plugin_child_when_the_script_is_absent(krew_home, tmp_path):
    p = P()
    assert not p.SCRIPT.is_file()
    seen = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        return _NS(returncode=0, stdout="done", stderr="")
    out = p.run_hands("list the board", runner=runner, cfg=p.H.settings({"profile": "zoey"}))
    assert "-p" in seen["argv"] and "zoey" in seen["argv"] and "--oneshot" in seen["argv"]
    assert out == "done"


def test_run_hands_empty_ask_is_the_empty_message():
    p = P()
    assert p.run_hands("") == p.EMPTY_ASK


def test_ask_from_folds_the_action_shape_into_words():
    p = P()
    assert p.ask_from({"action": "team_board", "action_args": '{"action": "list", "status": "blocked"}'}) == "team_board list status blocked"
    assert p.ask_from({"ask": "put the board up", "session_id": "s"}) == "put the board up"
    assert p.ask_from({"tool": "team_board", "args": ["show", "t_1"]}) == "team_board show t_1"


def test_runtime_kwargs_are_never_the_job():
    p = P()
    assert p._args((), {"session_id": "x", "task_id": "y", "ask": "hi"}) == {"ask": "hi"}


def test_the_who_gate_refuses_another_profile(monkeypatch):
    p = P()
    monkeypatch.delenv("KREW_HANDS_NESTED", raising=False)
    tools = {}

    class Ctx:
        profile_name = "bash"

        def register_tool(self, name, **kw):
            tools[name] = kw["handler"]
    p.register(Ctx())
    assert "hands" in tools
    assert tools["hands"]({"ask": "x"}).startswith("hands: the zoey profile's CLI kit")


def test_inside_a_hands_child_the_tool_is_not_registered(monkeypatch):
    p = P()
    monkeypatch.setenv("KREW_HANDS_NESTED", "1")
    tools = {}

    class Ctx:
        def register_tool(self, name, **kw):
            tools[name] = kw
    p.register(Ctx())
    assert "hands" not in tools


def test_canonical_schema_carries_the_description_to_the_model():
    p = P()
    s = p.canonical_schema("hands", "do it", {"type": "object", "properties": {}})
    assert s["name"] == "hands" and s["description"] == "do it" and s["parameters"]["type"] == "object"


def test_krew_docs_registers_only_with_the_switch_and_from_the_sibling_plugin(monkeypatch):
    p = P()
    tools = {}

    class Ctx:
        def register_tool(self, name, **kw):
            tools[name] = kw
    monkeypatch.delenv("KREW_HANDS_DOCS", raising=False)
    assert p.register_docs(Ctx()) is False and not tools
    monkeypatch.setenv("KREW_HANDS_DOCS", "1")
    assert p.register_docs(Ctx()) is True and "krew_docs" in tools
    assert tools["krew_docs"]["schema"]["name"] == "krew_docs"
