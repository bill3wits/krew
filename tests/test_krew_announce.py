"""krew_announce: the announce drain's one seam. The reference install played each PA line on its own audio stack;
krew hands it to the Wall's player (POST /v1/announce) and reads the player's air and the ear's session to hold the
queue. No player answering: the line is logged failed with "Crissa not installed" (2026-10-10, lane E)."""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "crew" / "lib"))
sys.path.insert(0, str(ROOT / "window" / "lib"))


@pytest.fixture
def player(monkeypatch, tmp_path):
    seen: list = []
    answers = {"/v1/state": {"on_air": False}, "/v1/ear?part=stt": {"ok": True}}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body):
            b = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            self._send(answers.get(self.path, {}))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            seen.append((self.path, body))
            self._send({"ok": True, "queued": True, "depth": 1})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CRISSA_PLAYER", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("KREW_ANNOUNCE_MUTE", str(tmp_path / "mute"))
    monkeypatch.setenv("KREW_ANNOUNCE_LOG_DIR", str(tmp_path / "announcer"))
    import announce_log
    import krew_announce
    importlib.reload(announce_log)
    ka = importlib.reload(krew_announce)
    yield ka, seen, answers, tmp_path
    srv.shutdown()


def test_a_line_goes_to_the_players_announce_route(player):
    ka, seen, _a, _t = player
    r = ka.announce("Done. The board is quiet.", category="completion", dedup_key="k1")
    assert r["ok"] and r["queued"] and seen[0][0] == "/v1/announce"
    assert seen[0][1]["kind"] == "completion" and seen[0][1]["dedup_key"] == "k1" and seen[0][1]["text"]
    import announce_log
    rows = announce_log.read(dt.date.today())
    assert rows[-1]["event"] == "spoken" and rows[-1]["reason"] == "crissa-player"


def test_mute_and_a_bad_category_never_reach_the_player(player):
    ka, seen, _a, tmp = player
    assert ka.announce("x", category="chatter")["reason"] == "category_not_allowed:chatter"
    (tmp / "mute").write_text("")
    assert ka.announce("Stuck. a card.", category="stuck")["reason"] == "muted"
    assert seen == []


def test_the_players_air_and_an_open_session_hold_the_queue(player):
    ka, _s, answers, _t = player
    assert ka.mic_busy() is None and ka.pa_hold() is None
    answers["/v1/state"] = {"on_air": True}
    answers["/v1/ear?part=stt"] = {"session_open": True}
    assert ka.mic_busy() == "player on air" and ka.pa_hold() == "session_open"
    answers["/v1/ear?part=stt"] = {"session_open": True, "comms_hold": True, "speaking": False}
    assert ka.pa_hold() is None                       # an all-day comms hold with a quiet ear lets PA through


def test_no_player_is_said_plainly(monkeypatch, tmp_path):
    monkeypatch.setenv("CRISSA_PLAYER", "http://127.0.0.1:9")
    monkeypatch.setenv("KREW_ANNOUNCE_MUTE", str(tmp_path / "mute"))
    import krew_announce
    ka = importlib.reload(krew_announce)
    r = ka.announce("Stuck. a card.", category="stuck")
    assert not r["ok"] and "Crissa not installed" in r["error"]
    assert ka.mic_busy() is None and ka.pa_hold() is None


def test_the_drain_imports_only_the_seam():
    src = (ROOT / "crew" / "scripts" / "krew-announce-drain.py").read_text()
    assert "from krew_announce import MUTE, announce, mic_busy, pa_hold" in src
    unit = (ROOT / "crew" / "units" / "krew-announce-drain.service").read_text()
    assert "krew-crew-run krew-announce-drain.py" in unit and "Restart=always" in unit
