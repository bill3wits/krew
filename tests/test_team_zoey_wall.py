"""team-zoey through the Wall's player (2026-10-06): team_speak -> /v1/speak, team_announce and the board hooks ->
/v1/announce, nothing reaches into the Wall's files, and with no player each says Crissa is not installed. The
lock, the hook dedup (under HERMES_HOME) and the KREW_ANNOUNCE_* knobs keep working."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT, load_plugin

SRC = (ROOT / "plugins" / "team-zoey" / "__init__.py").read_text()


class Ctx:
    def __init__(self, profile="zoey"):
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
    seen: list[tuple[str, dict]] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            seen.append((self.path, body))
            out = {"ok": True, "queued": True, "depth": 1} if self.path == "/v1/announce" else {"ok": True}
            raw = json.dumps(out).encode()
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], seen
    srv.shutdown()


def _tz(tmp_path, monkeypatch, url, **env):
    hh = tmp_path / "hermes-home"
    hh.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hh))
    monkeypatch.setenv("KREW_ANNOUNCE_LOCK", str(tmp_path / "krew-announce.lock"))
    monkeypatch.delenv("KREW_ANNOUNCE_HOOK_DEDUP", raising=False)
    monkeypatch.delenv("KREW_ANNOUNCE_MIC_WAIT_S", raising=False)
    monkeypatch.setenv("ZOEYOS_PLAYER", url)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    ctx = Ctx("zoey")
    load_plugin("team-zoey").register(ctx)
    return ctx, hh


def test_team_speak_goes_to_the_players_speak(tmp_path, monkeypatch, player):
    url, seen = player
    ctx, _ = _tz(tmp_path, monkeypatch, url)
    assert ctx.tools["team_speak"]({"text": "Hello  there."}) == "spoke on the Wall"
    assert ctx.tools["team_speak"](text="After that.", queue=True) == "spoke on the Wall (queued)"
    assert seen == [("/v1/speak", {"text": "Hello there.", "by": "krew"}),
                    ("/v1/speak", {"text": "After that.", "by": "krew", "queue": True})]


def test_team_announce_queues_on_the_wall_with_the_krew_knobs(tmp_path, monkeypatch, player):
    url, seen = player
    ctx, _ = _tz(tmp_path, monkeypatch, url, KREW_ANNOUNCE_SPEECH_PAUSE_SEC="7", KREW_ANNOUNCE_MIC_WAIT_S="30")
    out = ctx.tools["team_announce"]({"text": "The build is done.", "category": "completion", "dedup_key": "c:1"})
    assert out.startswith("announce queued on the Wall")
    assert seen == [("/v1/announce", {"text": "The build is done.", "kind": "completion", "by": "krew",
                                      "speech_pause_s": 7.0, "max_wait_s": 30.0, "dedup_key": "c:1"})]
    assert (tmp_path / "krew-announce.lock").exists(), "the cross-process lock still wraps the hand-off"


def test_no_mic_wait_set_means_nothing_is_dropped_for_waiting(tmp_path, monkeypatch, player):
    url, seen = player
    ctx, _ = _tz(tmp_path, monkeypatch, url)
    ctx.tools["team_announce"](text="Stuck on the deploy.", category="stuck")
    assert "max_wait_s" not in seen[0][1] and seen[0][1]["speech_pause_s"] == 12.0


def test_the_board_hooks_announce_through_the_player_once(tmp_path, monkeypatch, player):
    url, seen = player
    ctx, hh = _tz(tmp_path, monkeypatch, url)
    done = ctx.hooks["kanban_task_completed"]
    done({"task_id": "t_42", "title": "Ship the docs", "summary": "All green."})
    done({"task_id": "t_42", "title": "Ship the docs", "summary": "All green."})        # double hook: coalesced
    assert seen == [("/v1/announce", {"text": "Done. Ship the docs. All green.", "kind": "completion", "by": "krew",
                                      "speech_pause_s": 12.0, "dedup_key": "completion:t_42"})]
    assert (hh / "krew-announce-hook-dedup.json").is_file()


def test_completions_off_still_silences_the_hook(tmp_path, monkeypatch, player):
    url, seen = player
    ctx, _ = _tz(tmp_path, monkeypatch, url, KREW_ANNOUNCE_COMPLETIONS="0")
    assert ctx.hooks["kanban_task_completed"]({"task_id": "t_1", "title": "x"}) == "announce skipped — completions off"
    assert seen == []


def test_no_player_says_zoeyos_is_not_installed(tmp_path, monkeypatch):
    ctx, _ = _tz(tmp_path, monkeypatch, "http://127.0.0.1:9")
    assert ctx.tools["team_speak"](text="Hello.").startswith("Crissa not installed")
    assert ctx.tools["team_announce"](text="Done.").startswith("Crissa not installed")


def test_krew_never_reaches_into_the_walls_files_or_voice_services():
    for gone in ('lib/krew_announce.py"', "from announce_queue", "krew-speak.py", "_local(8024", "_local(7789",
                 "_speak_announce_audio8"):
        assert gone not in SRC, gone
    for_speech = SRC.split("def _for_speech", 1)[1].split("\ndef ", 1)[0]
    assert "_ZOEYOS" not in for_speech, "her words go to the player as they are; it makes them speakable"
    assert 'ZOEYOS_PLAYER = os.environ.get("ZOEYOS_PLAYER", "http://127.0.0.1:8100")' in SRC
