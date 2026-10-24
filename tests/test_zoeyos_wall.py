"""zoeyos-wall, krew's plugin since 2026-10-06 (the owner's boundary: the Crissa Wall touches krew only through
the agent endpoint and the Control Panel tiles; Crissa ships no agent-framework plugins). The eight wall_* tools
reach the Wall only through the player's HTTP routes and say "Crissa not installed" with no player; kill-search
(the pre_tool_call hook + the tool_execution step) reads krew's settings, never a Crissa file. Every test that
turns the switch on also fails on any real lookup or outbound connect, so a pass cannot be a lucky network."""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT, load_plugin

PLUG = ROOT / "plugins" / "zoeyos-wall"
NAMES = ["wall_state", "wall_keys", "wall_cue", "wall_home", "wall_comms", "wall_speak", "wall_ask", "wall_stop"]


@pytest.fixture
def wall(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_HOME", str(tmp_path / "krew-home"))
    monkeypatch.delenv("KREW_SEARCH_KILL", raising=False)
    return load_plugin("zoeyos-wall")


def _home(tmp_path, **search):
    h = tmp_path / "krew-home"
    h.mkdir(parents=True, exist_ok=True)
    (h / "zoeyos-wall.json").write_text(json.dumps({"search": dict({"kill": True}, **search),
                                                     "lan_hosts": ["localhost", "searx.office"]}))
    return h


@pytest.fixture
def no_internet(monkeypatch):
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def lookup(host, *a, **k):
        if str(host) not in ("127.0.0.1", "localhost", "::1"):
            pytest.fail("a name was resolved under kill-search: %s" % host)
        return real_lookup(host, *a, **k)

    def connect(self, addr):
        if isinstance(addr, tuple) and not str(addr[0]).startswith("127."):
            pytest.fail("an outbound connect under kill-search: %s" % (addr,))
        return real_connect(self, addr)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket.socket, "connect", connect)


class _Server:
    """A loopback HTTP server: the Wall's player (/v1/*) and a LAN relay (SearXNG /search, firecrawl /v1/scrape)."""

    def __init__(self):
        seen = self.seen = []

        class H(BaseHTTPRequestHandler):
            def _send(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                seen.append(("GET", self.path))
                if self.path == "/v1/state":
                    return self._send({"now": "idle", "clip": "rest", "on_air": False, "secret": "x"})
                if self.path == "/v1/kit":
                    return self._send({"keys": ["wave"]})
                self._send({"results": [{"title": "low", "url": "https://a.example/", "content": "a", "score": 1},
                                        {"title": "high", "url": "https://b.example/", "content": "b", "score": 9}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                seen.append(("POST", self.path, body))
                if self.path == "/v1/scrape":
                    return self._send({"success": True, "data": {"markdown": "# page", "metadata": {"title": "T"}}})
                if self.path == "/v1/cue" and body.get("key") != "wave":
                    return self._send({"error": "not a key: %s" % body.get("key")}, 400)
                self._send({"ok": True})

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.srv.server_port
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.srv.shutdown()


class Ctx:
    def __init__(self, middleware=True):
        self.tools, self.hooks, self.mw = {}, {}, {}
        if middleware:
            self.register_middleware = lambda kind, fn: self.mw.__setitem__(kind, fn)

    def register_tool(self, name=None, toolset=None, schema=None, handler=None, **kw):
        self.tools[name] = (toolset, schema, handler)

    def register_hook(self, name, fn):
        self.hooks[name] = fn


# ---- the tools: the player's HTTP routes, nothing else ---------------------------------------------------------

def test_the_eight_tools_register_with_the_hook_and_the_execution_step(wall):
    ctx = Ctx()
    wall.register(ctx)
    assert list(ctx.tools) == NAMES and {t for t, _, _ in ctx.tools.values()} == {"zoeyos"}
    assert all(s["name"] == n and s["description"] and s["parameters"]["type"] == "object"
               for n, (_, s, _) in ctx.tools.items())
    assert list(ctx.hooks) == ["pre_tool_call"] and list(ctx.mw) == ["tool_execution"]
    assert "provides_tools: [" + ", ".join(NAMES) + "]" in (PLUG / "plugin.yaml").read_text()
    assert "name: zoeyos-wall\n" in (PLUG / "plugin.yaml").read_text()


def test_each_tool_is_one_call_to_the_player(wall, monkeypatch, server):
    ctx = Ctx()
    wall.register(ctx)
    monkeypatch.setattr(wall.V, "PLAYER", server.url)
    st = json.loads(ctx.tools["wall_state"][2]({}))
    assert st["ok"] and st["result"] == {"now": "idle", "clip": "rest", "on_air": False}
    assert json.loads(ctx.tools["wall_home"][2]({}))["ok"] is True and server.seen[-1][1] == "/v1/home"
    assert json.loads(ctx.tools["wall_speak"][2]({"text": "hi"}))["ok"]
    assert server.seen[-1] == ("POST", "/v1/speak", {"text": "hi", "by": "agent"})
    bad = json.loads(ctx.tools["wall_cue"][2]({"key": "nope"}))
    assert bad == {"ok": False, "error": "not a key: nope"}


def test_no_player_means_zoeyos_not_installed(wall, monkeypatch):
    ctx = Ctx()
    wall.register(ctx)
    monkeypatch.setattr(wall.V, "PLAYER", "http://127.0.0.1:9")
    for name in NAMES:
        args = {"text": "x", "key": "wave", "on": True}
        out = json.loads(ctx.tools[name][2](args))
        assert out["ok"] is False and out["error"].startswith("Crissa not installed"), (name, out)


def test_it_reads_no_zoeyos_file_and_loads_only_its_own_modules(wall, tmp_path, monkeypatch):
    for f in PLUG.glob("*.py"):
        text = f.read_text()
        for word in ("ZOEYOS_HOME", ".zoeyos", "zoeyos-network.json", "zoeyos_home", "sys.path.insert",
                     "import zoeyos_", "state.db"):
            assert word not in text, (f.name, word)
    other = tmp_path / "other-lib"
    other.mkdir()
    for name in ("wall_egress", "wall_verbs", "zoeyos_egress", "zoeyos_wall_verbs"):
        (other / f"{name}.py").write_text("raise SystemExit('a module from another tree was loaded')\n")
    monkeypatch.syspath_prepend(str(other))
    m = load_plugin("zoeyos-wall")
    assert m.E.__file__.startswith(str(PLUG)) and m.V.__file__.startswith(str(PLUG))
    assert m.V._egress is m.E


# ---- kill-search: krew's settings ---------------------------------------------------------------------------------

def test_off_and_no_relay_the_agent_tools_run_untouched(wall, tmp_path):
    E = wall.E
    for name, args in (("web_search", {"query": "x"}), ("web_extract", {"urls": ["https://a.example"]}),
                       ("browser_navigate", {"url": "https://a.example"}), ("terminal", {"command": "ls"})):
        assert E.agent_tool_verdict(name, args) is None and E.relay_answer(name, args) is None


def test_the_switch_is_krews_file_or_krews_env(wall, tmp_path, monkeypatch):
    E = wall.E
    assert E.killed() is False
    _home(tmp_path)
    assert E.killed() is True, "$KREW_HOME/zoeyos-wall.json search.kill"
    monkeypatch.setenv("KREW_SEARCH_KILL", "0")
    assert E.killed() is False, "the environment outranks the file"
    monkeypatch.setenv("ZOEYOS_SEARCH_KILL", "1")
    assert E.killed() is False, "Crissa's own switch is Crissa's"


def test_on_with_no_relay_search_and_page_reads_are_refused(wall, tmp_path, no_internet):
    E = wall.E
    _home(tmp_path)
    v = E.agent_tool_verdict("web_search", {"query": "weather"})
    assert v["action"] == "block" and "search is off" in v["message"]
    v = E.agent_tool_verdict("web_extract", {"urls": ["https://a.example/x"]})
    assert v["action"] == "block" and "page reads are off" in v["message"]


def test_on_with_a_relay_search_and_page_reads_are_answered_from_it(wall, tmp_path, no_internet, server):
    E = wall.E
    _home(tmp_path, relay_url=server.url, relay_extract_url=server.url)
    assert E.agent_tool_verdict("web_search", {"query": "q"}) == {"action": "relay", "kind": "search"}
    got = json.loads(E.relay_answer("web_search", {"query": "zoey wall", "limit": 1}))
    assert got["success"] and got["via"] == "relay"
    assert got["data"]["web"] == [{"title": "high", "url": "https://b.example/", "description": "b", "position": 1}]
    got = json.loads(E.relay_answer("web_extract", {"urls": ["https://b.example/"]}))
    assert got["results"] == [{"url": "https://b.example/", "title": "T", "content": "# page", "error": None}]


def test_a_relay_off_the_lan_is_never_asked(wall, tmp_path, no_internet):
    _home(tmp_path, relay_url="https://searx.example.com")
    assert "search is off" in wall.E.agent_tool_verdict("web_search", {"query": "q"})["message"]


def test_on_the_browser_may_not_leave_the_lan(wall, tmp_path, no_internet):
    E = wall.E
    _home(tmp_path)
    v = E.agent_tool_verdict("browser_navigate", {"url": "https://news.example.com/"})
    assert v["action"] == "block" and "news.example.com" in v["message"]
    assert E.agent_tool_verdict("browser_navigate", {"url": "http://127.0.0.1:8100/connect"}) is None
    assert E.agent_tool_verdict("terminal", {"command": "curl https://example.com"}) is None


def test_the_hook_refuses_before_and_the_execution_step_answers_from_the_relay(wall, tmp_path, monkeypatch, server):
    _home(tmp_path, relay_url=server.url)
    ctx = Ctx()
    wall.register(ctx)
    pre, mw = ctx.hooks["pre_tool_call"], ctx.mw["tool_execution"]
    assert pre(tool_name="browser_navigate", args={"url": "https://x.example"})["action"] == "block"
    assert mw(tool_name="terminal", args={"c": 1}, next_call=lambda a: "ran") == "ran"
    assert pre(tool_name="web_search", args={"query": "q"}) is None            # the execution step answers it
    out = mw(tool_name="web_search", args={"query": "q"}, next_call=lambda a: pytest.fail("the backend ran"))
    assert json.loads(out)["via"] == "relay"


def test_without_an_execution_step_the_veto_carries_the_answer(wall, tmp_path, server):
    _home(tmp_path, relay_url=server.url)
    wall.register(Ctx(middleware=False))
    v = wall._on_pre_tool_call(tool_name="web_search", args={"query": "q"})
    assert v["action"] == "block" and json.loads(v["message"])["via"] == "relay"


def test_a_broken_switch_never_breaks_a_tool_call(wall, monkeypatch):
    monkeypatch.setattr(wall.E, "agent_tool_verdict", lambda *a, **k: 1 / 0)
    assert wall._on_pre_tool_call(tool_name="web_search", args={"query": "q"}) is None


def test_the_player_off_the_lan_is_refused_under_kill(wall, tmp_path, no_internet):
    _home(tmp_path)
    out = wall.V.call("wall_state", {}, base="https://wall.example.com")
    assert out["ok"] is False and "kill-search is on" in out["error"] and "wall.example.com" in out["error"]


def test_the_port_record_is_current():
    tools = ROOT.parent / "krew-house" / "tools" / "port_zoeyos_wall.py"
    if not tools.is_file():
        pytest.skip("krew-house (the reference install's port tools) is not beside this checkout")
    import subprocess
    r = subprocess.run([sys.executable, str(tools), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
