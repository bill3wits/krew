"""krew-keeper's PA through the Wall's player (2026-10-06): /v1/announce, no Audio8 lane, unit or RAM probe."""
from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conftest import ROOT

KEEPER = ROOT / "crew" / "scripts" / "krew-keeper.py"


def _load(monkeypatch, tmp_path, url):
    monkeypatch.setenv("ZOEYOS_PLAYER", url)
    monkeypatch.setenv("KREW_MEMORY_ROOT", str(tmp_path / "mem"))
    spec = importlib.util.spec_from_file_location("krew_keeper_wall", KEEPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    logged = []
    monkeypatch.setattr(mod, "log", lambda ev, **kw: logged.append((ev, kw)))
    return mod, logged


def test_the_keeper_announces_through_the_player(monkeypatch, tmp_path):
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            raw = b'{"ok": true, "queued": true, "depth": 1}'
            self.send_response(202)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        mod, logged = _load(monkeypatch, tmp_path, "http://127.0.0.1:%d" % srv.server_address[1])
        mod.announce("ops is down and its restart budget is spent. Needs a human.", category="stuck")
    finally:
        srv.shutdown()
    assert seen[0][0] == "/v1/announce"
    assert seen[0][1]["kind"] == "stuck" and seen[0][1]["by"] == "krew-keeper" and "Needs a human" in seen[0][1]["text"]
    assert logged[-1][0] == "announced" and logged[-1][1]["queued"] is True


def test_no_player_is_logged_as_zoeyos_not_installed(monkeypatch, tmp_path):
    mod, logged = _load(monkeypatch, tmp_path, "http://127.0.0.1:9")
    mod.announce("Stuck.", category="stuck")
    assert logged[-1][0] == "announce_failed" and "Crissa not installed" in logged[-1][1]["error"]


def test_the_audio8_lane_unit_and_probe_are_gone(monkeypatch, tmp_path):
    mod, _ = _load(monkeypatch, tmp_path, "http://127.0.0.1:9")
    assert "announcer" not in mod.LANES
    src = KEEPER.read_text()
    assert "8024" not in src and "audio8-tts.service" not in src and "krew_announce.py" not in src
