"""team-crissa puts things on the Wall only through the player (2026-10-06): a card beside her at /v1/screen (read
back off GET /v1/screen), her ear at /v1/ear. Nothing reaches into a Crissa checkout or drives a browser; the tools
that are not the Wall's ship beside the plugin; with no player each tool says "Crissa not installed"."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT, load_plugin

PLUG = ROOT / "plugins" / "team-crissa"
SRC = (PLUG / "__init__.py").read_text()
LIFTED = ("crissa-decide-list.py", "photo-ascii.py", "glyph_density.py", "render_banner.py", "kanban_show_slim.py",
          "board_parallel.py", "marketing_outbox.py")
FIELDS = ("title", "notes", "notes_title", "doc", "doc_title", "art", "art_label", "url", "html")
DEAD = "http://127.0.0.1:9"


class Ctx:
    def __init__(self, profile="crissa"):
        self.profile_name = profile
        self.tools, self.hooks = {}, {}

    def register_tool(self, name=None, toolset=None, schema=None, handler=None, **kw):
        self.tools[name] = handler

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def __getattr__(self, k):
        return lambda *a, **kw: None


@pytest.fixture()
def player():
    """A stand-in for the Wall's player: /v1/screen as Crissa keeps it, /v1/ear as it answers."""
    st = {"card": {}, "at": 0.0, "by": "", "ear": (200, {"ok": True, "stt": {"ok": True, "engine": "parakeet"}}),
          "posts": []}

    def view():
        return dict(st["card"], at=st["at"], by=st["by"], up=bool(st["card"]))

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, out):
            raw = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/v1/screen":
                return self._send(200, {"ok": True, "screen": view()})
            if self.path == "/v1/ear":
                return self._send(*st["ear"])
            self._send(404, {"error": "not found"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            if self.path != "/v1/screen":
                return self._send(404, {"error": "not found"})
            st["posts"].append(body)
            clear = body.get("clear")
            clear = list(FIELDS) if clear in (True, "all", "*") else [clear] if isinstance(clear, str) else clear or []
            for k in clear:
                st["card"].pop(k, None)
            for k in FIELDS:
                if body.get(k) is not None:
                    if str(body[k]).strip():
                        st["card"][k] = str(body[k])
                    else:
                        st["card"].pop(k, None)
            st["at"], st["by"] = time.time(), str(body.get("by") or "")
            self._send(200, {"ok": True, "screen": view()})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], st
    srv.shutdown()


def _tz(tmp_path, monkeypatch, url, **env):
    hh = tmp_path / "hermes-home"
    hh.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hh))
    monkeypatch.setenv("KREW_ANNOUNCE_LOCK", str(tmp_path / "krew-announce.lock"))
    monkeypatch.setenv("CRISSA_PLAYER", url)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    ctx = Ctx("crissa")
    load_plugin("team-crissa").register(ctx)
    return ctx


def test_nothing_reaches_into_crissa_or_its_browser():
    for gone in ("KREW_CRISSA_ROOT", "9222", "_local(8099", "zos_brave", "krew-crissa-display", "doc-window",
                 "crissa-show-window", "speakable.py", "WALL_CDP", "WALL_HOME", "/json/new", "/json/list",
                 "Page.navigate", "_local(8190"):
        assert gone not in SRC, gone
    for name in LIFTED:
        text = (PLUG / name).read_text()
        compile(text, name, "exec")
        assert ":8099" not in text, name


def test_chalk_view_and_home_go_through_the_players_card(tmp_path, monkeypatch, player):
    url, st = player
    ctx = _tz(tmp_path, monkeypatch, url)
    show = ctx.tools["team_display"]
    out = show({"action": "chalk", "title": "Backup", "text": "12 files · done"})
    assert "a note 'Backup' (1 lines)" in out and st["card"]["notes"] == "12 files · done"
    assert st["posts"][-1]["by"] == "krew"
    assert "a note 'Backup'" in show({"action": "status"})
    out = show({"action": "view", "url": "https://example.com/page"})
    assert "the page https://example.com/page in a frame" in out
    assert st["card"] == {"title": "https://example.com/page", "url": "https://example.com/page"}
    out = show({"action": "view", "name": "kanban"})
    assert st["card"]["url"].endswith("/kanban?profile=crissa") and "in a frame" in out
    out = show({"action": "home"})
    assert out.startswith("Home: Her card is down") and st["card"] == {}
    show({"action": "chalk", "text": "x"})
    assert show({"action": "view", "name": "home"}).startswith("Home:") and st["card"] == {}


def test_old_browser_actions_answer_plainly_and_change_nothing(tmp_path, monkeypatch, player):
    url, st = player
    ctx = _tz(tmp_path, monkeypatch, url)
    for action in ("shot", "activate", "ensure", "front", "hybrid", "voice-only"):
        out = ctx.tools["team_display"]({"action": action, "target": "abc"})
        assert "is not something the Wall does on Crissa" in out and "Nothing changed on the Wall" in out, action
    assert "no tabs to close" in ctx.tools["team_display"]({"action": "close", "target": "kanban"})
    assert "I don't know a screen called 'nowhere'" in ctx.tools["team_display"]({"action": "view", "name": "nowhere"})
    assert st["posts"] == []


def test_clear_takes_down_only_what_it_names(tmp_path, monkeypatch, player):
    url, st = player
    ctx = _tz(tmp_path, monkeypatch, url)
    ctx.tools["team_display"]({"action": "chalk", "text": "note", "art": "/\\\n\\/", "title": "t"})
    out = ctx.tools["team_display"]({"action": "clear", "target": "notes"})
    assert out.startswith("Cleared.") and "notes" not in st["card"] and st["card"]["art"] == "/\\\n\\/"
    ctx.tools["team_display"]({"action": "clear"})
    assert st["card"] == {}


def test_show_me_a_local_page_goes_up_whole(tmp_path, monkeypatch, player):
    url, st = player
    ctx = _tz(tmp_path, monkeypatch, url)
    page = tmp_path / "findings.html"
    big = "<html><body>" + "<p>row</p>" * 3000 + "<script>alert(1)</script></body></html>"
    page.write_text(big)
    out = ctx.tools["show_me"]({"what": str(page), "title": "Findings"})
    assert st["card"]["html"] == big, "the whole page, not a cut of it (the Wall frames it with no scripts)"
    assert "a page 'Findings'" in out
    note = ctx.tools["show_me"]({"what": "note", "text": "Back in five."})
    assert st["card"].get("notes") == "Back in five." and "a note" in note


def test_glyph_art_lands_on_the_card_and_is_read_back(tmp_path, monkeypatch, player):
    url, st = player
    ctx = _tz(tmp_path, monkeypatch, url)
    out = ctx.tools["team_glyph_art"]({"subject": "spiral", "width": 30, "height": 10, "seed": 1, "label": "s"})
    assert "is on the wall — on the Wall: " in out and st["card"]["art_label"] == "s" and st["card"]["art"]


def test_ear_health_asks_the_player(tmp_path, monkeypatch, player):
    url, st = player
    ctx = _tz(tmp_path, monkeypatch, url)
    assert ctx.tools["team_ear_health"]().startswith("ear ok=True engine=parakeet")
    st["ear"] = (200, {"ok": False, "stt": None, "why": "the voice module is not answering at x"})
    assert ctx.tools["team_ear_health"]() == "her ear is not answering: the voice module is not answering at x"
    st["ear"] = (404, {"error": "not found"})
    assert "older than /v1/ear" in ctx.tools["team_ear_health"]()


def test_no_player_says_crissa_not_installed(tmp_path, monkeypatch):
    ctx = _tz(tmp_path, monkeypatch, DEAD)
    for call in (lambda: ctx.tools["team_display"]({"action": "chalk", "text": "hi"}),
                 lambda: ctx.tools["team_display"]({"action": "home"}),
                 lambda: ctx.tools["team_display"]({"action": "status"}),
                 lambda: ctx.tools["team_display"]({"action": "view", "name": "kanban"}),
                 lambda: ctx.tools["team_ear_health"](),
                 lambda: ctx.tools["team_glyph_art"]({"subject": "spiral", "width": 20, "height": 8}),
                 lambda: ctx.tools["team_needs_sheet"]("status"),
                 lambda: ctx.tools["team_express"]({"text": "hello"})):
        assert "Crissa not installed" in call()


def test_session_end_takes_down_only_an_old_krew_card(tmp_path, monkeypatch, player):
    url, st = player
    ctx = _tz(tmp_path, monkeypatch, url)
    ctx.tools["team_display"]({"action": "chalk", "text": "fresh"})
    ctx.hooks["on_session_finalize"]()
    assert st["card"].get("notes") == "fresh", "a hands run's exit must not take down what it just put up"
    st["by"] = "wall"
    monkeypatch.setenv("KREW_CARD_HOLD_S", "0")
    ctx.hooks["on_session_finalize"]()
    assert st["card"].get("notes") == "fresh", "a card someone else put up is not krew's to take down"
    st["by"] = "krew"
    ctx.hooks["on_session_finalize"]()
    assert st["card"] == {}


def _photo_ascii(monkeypatch):
    np_ = types.ModuleType("numpy")
    pil, img = types.ModuleType("PIL"), types.ModuleType("PIL.Image")
    pil.Image = img
    for k, m in (("numpy", np_), ("PIL", pil), ("PIL.Image", img)):
        monkeypatch.setitem(sys.modules, k, sys.modules.get(k) or m)
    spec = importlib.util.spec_from_file_location("krewtest_photo_ascii", PLUG / "photo-ascii.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_photo_ascii_posts_to_the_player_and_reads_the_card_back(monkeypatch, player):
    url, st = player
    monkeypatch.setenv("CRISSA_PLAYER", url)
    mod = _photo_ascii(monkeypatch)
    assert mod.post_to_wall("##\n..", "ascii", "me", None).startswith("on the glass: 2 lines, ramp ascii")
    assert st["card"]["art"] == "##\n.." and st["card"]["art_label"] == "me"
    monkeypatch.setenv("CRISSA_PLAYER", DEAD)
    out = _photo_ascii(monkeypatch).post_to_wall("##", "ascii", None, None)
    assert out.startswith("Crissa not installed") and "did NOT land" in out


def test_decide_list_status_says_crissa_not_installed(tmp_path):
    env = {"HOME": str(tmp_path), "CRISSA_PLAYER": DEAD, "KREW_PORT_8191": "9", "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(PLUG / "crissa-decide-list.py"), "status"], capture_output=True,
                       text=True, timeout=60, env=env)
    assert "Crissa not installed" in r.stdout, r.stdout + r.stderr
