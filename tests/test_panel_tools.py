"""The krew-panel tools: an agent reads and presses the Crissa Control Panel through its own HTTP routes.

A stand-in Control Panel / camera service (stdlib HTTP on 127.0.0.1, a free port) answers the routes the
tools call and records every POST, so nothing here reaches a real panel, camera or presence service. Unset settings
must mean "not configured, nothing sent": those tests run with no stand-in at all.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import ROOT, load_plugin

TOOLS = {"panel_units", "panel_journal", "panel_script", "panel_log", "panel_security", "panel_health",
         "panel_camera_home", "panel_kiosk", "panel_announcer"}
ENV = ("KREW_PANEL_URL", "KREW_CAMERA_URL", "KREW_ANNOUNCE_MUTE")


class Stand:
    """What the stand-in serves, and what it was sent."""

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.state = {"svc": {"services": [
            {"unit": "a.service", "label": "A", "health": "ok", "active": "active", "restarts": 0},
            {"unit": "b.service", "label": "B", "health": "bad", "active": "failed", "restarts": 40}],
            "summary": [{"id": "g", "label": "G", "level": "bad", "parts": 2, "why": "b down"}],
            "announcer_muted": False}, "gpu": {"mode": "normal"}, "kiosk": "wall", "battery": {"worst": "ok"}}
        self.camera = {"ok": True, "at_door": True}
        self.presence: dict = {}
        self.doc: dict = {}


def serve(stand: Stand):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if u.path == "/v1/house":
                return self._send(200, stand.state)
            if u.path == "/v1/journal":
                return self._send(200, {"ok": True, "lines": [f"{q.get('unit')} line {i}" for i in range(150)]})
            if u.path == "/v1/script":
                return self._send(200, {"ok": True, "files": [{"path": "/x/run.sh", "text": "x" * 9000}]})
            if u.path == "/v1/logs":
                return self._send(200, {"ok": True, "lines": ["heard: hello"]})
            if u.path == "/v1/security/summary":
                return self._send(200, {"seen": 1, "refused": 0})
            if u.path == "/health":
                return self._send(200, stand.camera)
            if u.path == "/v1/presence":
                return self._send(200, stand.presence)
            if u.path == "/v1/doc":
                return self._send(200, stand.doc)
            return self._send(404, {"ok": False, "error": "no route"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            stand.posts.append((self.path, body))
            if self.path == "/v1/action" and body.get("action") in ("announcer_off", "announcer_on"):
                stand.state["svc"]["announcer_muted"] = body["action"] == "announcer_off"
            if self.path == "/v1/screen":
                stand.doc = {} if body.get("clear") else {"doc": body.get("doc"), "doc_title": body.get("doc_title")}
            return self._send(200, {"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for v in ENV:
        monkeypatch.delenv(v, raising=False)


@pytest.fixture
def stand(monkeypatch):
    s = Stand()
    srv, url = serve(s)
    s.url = url
    yield s
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def panel(stand, monkeypatch):
    monkeypatch.setenv("KREW_PANEL_URL", stand.url)
    monkeypatch.setenv("KREW_CAMERA_URL", stand.url)
    return load_plugin("krew-panel"), stand


def call(mod, name, **kw):
    return json.loads(getattr(mod, name)(kw))


# ---- the plugin ------------------------------------------------------------------------------------------------

def test_registers_the_nine_panel_tools_on_team():
    mod = load_plugin("krew-panel")
    got = []

    class Ctx:
        def register_tool(self, **kw):
            got.append(kw)

    mod.register(Ctx())
    assert {g["name"] for g in got} == TOOLS and {g["toolset"] for g in got} == {"team"}
    yml = (ROOT / "plugins" / "krew-panel" / "plugin.yaml").read_text(encoding="utf-8")
    assert set(re.findall(r"panel_\w+", yml)) == TOOLS


def test_unconfigured_every_tool_says_so_and_sends_nothing():
    mod = load_plugin("krew-panel")
    assert mod.PANEL == "" and mod.CAMERA == ""
    for name in sorted(TOOLS - {"panel_camera_home"}):
        out = call(mod, name, unit="a.service", lane="voice", mute=True, dest="panel", run=True)
        assert out["ok"] is False, (name, out)
        if name != "panel_log":
            assert "not configured" in out["error"] and "Nothing was read and nothing changed" in out["error"], (name, out)
    out = call(mod, "panel_camera_home")
    assert out["ok"] is False and "camera_url is not set" in out["error"] and "nothing moved" in out["error"]


def test_settings_come_from_krew_yaml_and_the_environment_wins(tmp_path, monkeypatch):
    (Path(os.environ["KREW_HOME"]) / "krew.yaml").write_text(
        "panel:\n  url: http://127.0.0.1:1/\n  camera_url: http://127.0.0.1:2\n  announce_mute: ~/mute-flag\n")
    mod = load_plugin("krew-panel")
    assert (mod.PANEL, mod.CAMERA) == ("http://127.0.0.1:1", "http://127.0.0.1:2")
    assert mod.ANNOUNCE_MUTE == os.path.expanduser("~/mute-flag")
    monkeypatch.setenv("KREW_PANEL_URL", "http://127.0.0.1:3")
    assert load_plugin("krew-panel").PANEL == "http://127.0.0.1:3"


def test_units_reads_the_lamps(panel):
    mod, stand = panel
    out = call(mod, "panel_units")
    assert out["ok"] and out["count"] == 2 and out["summary"][0]["why"] == "b down"
    lit = call(mod, "panel_units", trouble_only=True)
    assert [u["unit"] for u in lit["units"]] == ["b.service"] and lit["units"][0]["restarts"] == 40
    miss = call(mod, "panel_units", unit="nope.service")
    assert miss["ok"] is False and miss["units"] == ["a.service", "b.service"]
    assert stand.posts == []


def test_journal_is_capped_at_100_and_says_so(panel):
    mod, _ = panel
    out = call(mod, "panel_journal", unit="b.service", lines=400)
    assert out["shown"] == 100 and out["total"] == 150 and "at most 100" in out["note"]
    assert call(mod, "panel_journal")["ok"] is False


def test_script_bodies_are_cut_and_the_cut_is_named(panel):
    mod, _ = panel
    out = call(mod, "panel_script", unit="b.service", max_chars=1000)
    f = out["files"][0]
    assert f["truncated"] and f["full_chars"] == 9000 and len(f["text"]) == 1000 and "cut at 1000" in out["note"]


def test_log_refuses_an_unknown_lane_without_asking(panel):
    mod, _ = panel
    out = call(mod, "panel_log", lane="kitchen")
    assert out["ok"] is False and "camera, voice, security" in out["error"]
    assert call(mod, "panel_log", lane="voice")["lines"] == ["heard: hello"]


def test_health_reads_free_and_tier_3_needs_the_sentence(panel):
    mod, stand = panel
    assert call(mod, "panel_health")["ran"] is False
    out = call(mod, "panel_health", run=True, tier=3)
    assert out["ok"] is False and out["need"] == mod.T3_CONFIRM
    assert stand.posts == []


def test_camera_home_refuses_while_someone_holds_it(panel):
    mod, stand = panel
    stand.camera = {"ok": True, "lease": "inspector-7", "at_door": False}
    out = call(mod, "panel_camera_home")
    assert out["ok"] is False and "lease inspector-7" in out["error"] and stand.posts == []


def test_announcer_reports_what_landed(panel):
    mod, stand = panel
    out = call(mod, "panel_announcer", mute=True)   # no flag file configured: the panel's own answer, read back
    assert out["ok"] and out["muted"] is True and stand.posts == [("/v1/action", {"action": "announcer_off"})]


def test_kiosk_read_changes_nothing(panel):
    mod, stand = panel
    out = call(mod, "panel_kiosk")
    assert out == {"ok": True, "changed": False, "kiosk": "wall", "gpu_mode": "normal"} and stand.posts == []


# ---- the skill -------------------------------------------------------------------------------------------------

def test_the_panel_skill_is_named_for_its_folder():
    text = (ROOT / "skills" / "krew-panel" / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"^name: krew-panel$", text, re.M)


def test_the_panel_skill_names_only_tools_the_plugin_registers():
    text = (ROOT / "skills" / "krew-panel" / "SKILL.md").read_text(encoding="utf-8")
    named = set(re.findall(r"\bpanel_[a-z_]+", text))
    assert named and named <= TOOLS, named - TOOLS
    # every tool-shaped name in the skill and the plugin is a panel_ one (the lift renamed all nine)
    verbs = r"\b([a-z]+)_(units|journal|script|log|security|health|camera_home|kiosk|announcer)\b"
    for f in (ROOT / "skills" / "krew-panel" / "SKILL.md", ROOT / "plugins" / "krew-panel" / "__init__.py"):
        assert {m.group(1) for m in re.finditer(verbs, f.read_text(encoding="utf-8"))} <= {"panel", "read"}, f   # read_journal: the server function the cap note names


def test_the_decide_sheet_is_not_part_of_the_add_on():
    """Crissa owns the panel (the owner 2026-10-06): krew carries the panel_* tools, their skill and its tiles; the
    decide sheet stays with the reference install (port later). Crissa's own skills ship (tests/test_crissa_skills.py)."""
    assert not (ROOT / "panel" / "crissa-decide-list.py").exists()
    assert not (ROOT / "systemd" / "user" / "krew-crissa-decide-sheet-watch.service").exists()
