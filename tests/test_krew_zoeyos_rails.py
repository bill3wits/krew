"""The rest of krew's reaches into Crissa go through the player (2026-10-06): team-zoey's presence, camera and
registration through /v1/rail/<name>/, the keeper's ear through /v1/ear (observe only), morning-hello's voice through
/v1/speak, the needs sheet and the render window's presence through the rail, the M&S examples' ear through /v1/ear.
With no player each says "Crissa not installed"; a Crissa without the service says the player's why."""
from __future__ import annotations

import ast
import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT, load_plugin

TZ = (ROOT / "plugins" / "team-zoey" / "__init__.py").read_text()
KEEPER = (ROOT / "crew" / "scripts" / "krew-keeper.py").read_text()


def _dead_url() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return "http://127.0.0.1:%d" % port


@pytest.fixture()
def player():
    """A stand-in player: /v1/state, /v1/speak, and a rail with presence set up, camera not set up."""
    seen: list = []
    state = {"on_air": False, "say_pending": 0}

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
            seen.append(("GET", self.path, None))
            if self.path == "/v1/state":
                return self._send(200, dict(state))
            if self.path == "/v1/rail/presence/v1/presence":
                return self._send(200, {"headcount": 1, "count_trust": "high",
                                        "people": [{"name": "ann", "certain": True}]})
            if self.path.startswith("/v1/rail/camera/") or self.path.startswith("/v1/rail/enroll/"):
                return self._send(503, {"ok": False, "error": "not_configured",
                                        "why": "this Crissa has no camera (set ZOEYOS_CAMERA_URL to one)"})
            return self._send(404, {"ok": False, "error": "no such route"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            seen.append(("POST", self.path, body))
            if self.path == "/v1/speak":
                return self._send(202, {"ok": True, "queued": True})
            if self.path == "/v1/rail/enroll/start":
                return self._send(503, {"ok": False, "error": "not_configured",
                                        "why": "this Crissa has no registration panel (set ZOEYOS_ENROLL_URL to one)"})
            return self._send(404, {"ok": False, "error": "no such route"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], seen
    srv.shutdown()


class Ctx:
    def __init__(self):
        self.profile_name = "zoey"
        self.tools = {}
        self.schemas = {}

    def register_tool(self, name=None, toolset=None, schema=None, handler=None, **kw):
        self.tools[name] = handler
        self.schemas[name] = schema or {}

    def __getattr__(self, k):
        return lambda *a, **kw: None


def _tz(tmp_path, monkeypatch, url):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOEYOS_PLAYER", url)
    ctx = Ctx()
    ctx.mod = load_plugin("team-zoey")
    ctx.mod.register(ctx)
    return ctx


def _script(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_team_zoey_reaches_presence_camera_and_registration_only_through_the_player(tmp_path, monkeypatch, player):
    for port in ("8191", "8192", "8196", "8197"):
        assert port not in TZ
    assert "stills[:8]" not in TZ and "/v1/mjpeg" not in TZ and "INSPECTOR_CPU" not in TZ
    url, seen = player
    ctx = _tz(tmp_path, monkeypatch, url)
    assert ctx.tools["team_presence"]() == "headcount 1 (high): ann"
    out = json.loads(ctx.tools["team_observe_presence"]())
    assert out["frames"]["snapshot"] == url + "/v1/rail/camera/snapshot" and "not forwarded" in out["frames"]["stream"]
    said = ctx.tools["team_register_face"]({"name": "ann"})          # Hermes hands the arguments as one dict
    assert ctx.tools["team_register_voice"](name="ann").startswith("Could not start:")
    assert said.startswith("Could not start: the registration panel through the player said 503: this Crissa has no "
                           "registration panel")
    assert ("POST", "/v1/rail/enroll/start", {"display": "ann", "mode": "face", "create": True}) in seen


def test_team_zoey_without_zoeyos_says_not_installed(tmp_path, monkeypatch):
    dead = _dead_url()
    ctx = _tz(tmp_path, monkeypatch, dead)
    assert ctx.tools["team_presence"]().startswith(f"presence unavailable: Crissa not installed (no player at {dead})")
    assert "Crissa not installed" in ctx.tools["team_observe_presence"]()
    assert "Crissa not installed" in ctx.tools["team_register_voice"](name="ann")
    assert "Crissa not installed" in ctx.tools["team_look"](what="hear")


def test_morning_hello_speaks_through_the_player_and_waits_for_each_line(monkeypatch, player):
    url, seen = player
    monkeypatch.setenv("ZOEYOS_PLAYER", url)
    mh = _script("crew/scripts/morning-hello.py", "morning_hello_t")
    src = (ROOT / "crew/scripts/morning-hello.py").read_text()
    assert "krew-speak" not in src and "KREW_ZOEYOS_ROOT" not in src and "subprocess" not in src
    mh.speak("Good morning.", False)
    assert ("POST", "/v1/speak", {"text": "Good morning.", "by": "krew-morning", "queue": True}) in seen
    assert ("GET", "/v1/state", None) in seen
    monkeypatch.setattr(mh, "ZOEYOS_PLAYER", _dead_url())
    with pytest.raises(SystemExit) as e:
        mh.speak("Hello.", False)
    assert "Crissa not installed" in str(e.value)


def test_keeper_reads_her_ear_and_the_rail_through_the_player_observe_only(monkeypatch):
    assert 'EAR_HEALTH = ZOEYOS_PLAYER + "/v1/ear?part=stt"' in KEEPER
    assert 'EAR_OPEN = ZOEYOS_PLAYER + "/v1/ear/open"' in KEEPER
    # the lanes come from the services list (2026-10-08): the Wall's rows are reached through the player, observe only
    monkeypatch.setenv("KREW_SERVICES", str(ROOT / "config" / "services.example.json"))
    monkeypatch.setenv("ZOEYOS_PLAYER", "http://player.invalid")
    spec = importlib.util.spec_from_file_location("krew_keeper_rails_t", ROOT / "crew" / "scripts" / "krew-keeper.py")
    k = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(k)
    for lane, path in (("ear-rail", "/v1/ear?part=stt"), ("live-ear", "/v1/ear?part=asr"),
                       ("presence", "/v1/rail/presence/v1/presence"), ("camera", "/v1/rail/camera/health")):
        assert k.LANES[lane] == ("http://player.invalid" + path, None, None)
    for gone in ("_local(8190", "_local(8096", "_local(8191", "_local(8192", "rail-stt.service", "live-ear.service",
                 "_local(8000", "_local(8005", "_local(7789", "_local(8088", "_local(9222", "hermes-zoey-gateway",
                 "face-shell.service", "zoey-display.service", "voice.service"):
        assert gone not in KEEPER, gone
    tree = ast.parse(KEEPER)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_player_why")
    import urllib.error
    import urllib.request
    dead = _dead_url()
    ns = {"urllib": urllib, "json": json, "ZOEYOS_PLAYER": dead}
    exec(compile(ast.Module([fn], []), "keeper", "exec"), ns)
    assert ns["_player_why"](dead + "/v1/ear?part=stt") == f"Crissa not installed (no player at {dead})"


def test_keeper_why_reads_the_players_reason(player):
    url, _ = player
    tree = ast.parse(KEEPER)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_player_why")
    import urllib.error
    import urllib.request
    ns = {"urllib": urllib, "json": json, "ZOEYOS_PLAYER": url}
    exec(compile(ast.Module([fn], []), "keeper", "exec"), ns)
    assert "this Crissa has no camera" in ns["_player_why"](url + "/v1/rail/camera/health")


def test_needs_sheet_and_render_window_read_presence_through_the_rail(monkeypatch):
    dl = (ROOT / "plugins/team-zoey/zoey-decide-list.py").read_text()
    rw = (ROOT / "crew/scripts/render-window.py").read_text()
    assert 'PRESENCE = VO + "/v1/rail/presence/v1/presence"' in dl and "8191" not in dl
    assert 'ZOEYOS_PLAYER + "/v1/rail/presence/v1/presence"' in rw and "8191" not in rw
    dead = _dead_url()
    monkeypatch.setenv("ZOEYOS_PLAYER", dead)
    mod = _script("plugins/team-zoey/zoey-decide-list.py", "decide_list_t")
    may, why, _ = mod.room()
    assert may == "no" and why.startswith(f"Crissa not installed (no player at {dead}")


def test_ms_examples_read_the_ear_through_the_player():
    ms = (ROOT / "config/ms.example.yaml").read_text()
    rw = json.loads((ROOT / "config/restart-window.example.json").read_text())
    for port in ("8190", "8096", "8191", "8196"):
        assert f"127.0.0.1:{port}/health" not in ms
    assert "http://127.0.0.1:8100/v1/ear?part=stt" in ms and "http://127.0.0.1:8100/v1/ear?part=asr" in ms
    checks = rw["_reference_install"]["post_reboot_checks"]
    assert checks["ear (through the Crissa player)"] == "http://127.0.0.1:8100/v1/ear?part=stt"
    assert not any(":8190" in k or ":8190" in v for k, v in checks.items())


def test_no_zoeyos_checkout_paths_left():
    skill = (ROOT / "skills/render-avatars/SKILL.md").read_text()
    arch = (ROOT / "crew/scripts/architect_lib.py").read_text()
    assert "lila-host-intake" not in skill and "~/ZoeyOS" not in skill and "neither krew" in skill
    assert "~/ZoeyOS" not in arch and '"*start-beast.sh*",' in arch
    kit = (ROOT / "crew/scripts/krew-kit-reapply.sh").read_text()
    assert "krew-speak" not in kit and "/v1/state" in kit


@pytest.fixture()
def rail():
    """A stand-in player whose rail answers are set per test: {(method, path): (code, body)}."""
    answers: dict = {}
    seen: list = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else None
            seen.append((method, self.path, body))
            code, out = answers.get((method, self.path), (404, {"ok": False, "error": "no such route"}))
            raw = json.dumps(out).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._do("GET")

        def do_POST(self):
            self._do("POST")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], answers, seen
    srv.shutdown()


def test_registration_needs_confirmation_is_a_question_not_an_error(tmp_path, monkeypatch, rail):
    url, answers, seen = rail
    ctx = _tz(tmp_path, monkeypatch, url)
    answers[("POST", "/v1/rail/enroll/start")] = (200, {"ok": False, "needs_confirm": True, "display": "Ann",
                                                        "face": 2, "voice": 1, "error": "ALERT: ..."})
    said = ctx.tools["team_register_face"]({"name": "Ann"})
    assert said.startswith("NEEDS CONFIRMATION — nothing started yet: Ann already has records (face=2 voice=1)")
    assert "Control Panel" in said and "not by voice" in said and "Could not" not in said
    answers[("POST", "/v1/rail/enroll/start")] = (409, {"ok": False, "needs_confirm": True, "face": 1, "voice": 0})
    assert ctx.tools["team_register_voice"](name="Ann").startswith("NEEDS CONFIRMATION")


def test_registration_never_asks_the_panel_to_replace_by_voice(tmp_path, monkeypatch, rail):
    """Replacing face or voice records is panel-only: a spoken yes cannot prove who is speaking (Auth/Secure)."""
    url, answers, seen = rail
    ctx = _tz(tmp_path, monkeypatch, url)
    answers[("POST", "/v1/rail/enroll/start")] = (200, {"ok": True, "display": "Ann", "person_id": "ann"})
    for call in (lambda: ctx.tools["team_register_face"]({"name": "Ann", "confirm": True, "replace": True}),
                 lambda: ctx.tools["team_register_voice"](name="Ann", confirm=True, overwrite=True, force=True)):
        said = call()
        assert said.startswith("Started ") and "replac" not in said
        assert set(seen[-1][2]) == {"display", "mode", "create"}
    import inspect
    assert list(inspect.signature(ctx.mod._start_registration).parameters) == ["name", "mode"]
    for tool in ("team_register_face", "team_register_voice"):
        assert set(ctx.schemas[tool]["parameters"]["properties"]) == {"name"}


def test_team_look_says_the_camera_was_not_paused_on_success(tmp_path, monkeypatch, rail):
    url, answers, _ = rail
    ctx = _tz(tmp_path, monkeypatch, url)
    answers[("POST", "/v1/rail/presence/v1/inspector")] = (200, {"suggest": {"person_id": "ann"}, "peeked": {}})
    answers[("POST", "/v1/rail/camera/v1/pause")] = (503, {"ok": False, "why": "this Crissa has no camera"})
    said = ctx.tools["team_look"]({"what": "hear"})
    assert said.startswith("hear ann") and "(camera not paused: the camera through the player said 503: this Crissa has no camera)" in said
    answers[("POST", "/v1/rail/camera/v1/pause")] = (200, {"ok": True})
    assert "camera not paused" not in ctx.tools["team_look"]({"what": "hear"})


def test_morning_hello_carries_on_when_face_shell_is_down(monkeypatch, capsys):
    mh = _script("crew/scripts/morning-hello.py", "morning_hello_f")
    monkeypatch.setattr(mh, "FACE", _dead_url())
    mh.express({"kind": "scene"})
    mh.express({"kind": "card"})
    out = capsys.readouterr().out
    assert out.count("face-shell is not answering at") == 1 and "the brief carries on" in out
    assert mh.card_house() is None and "the home card is skipped" in capsys.readouterr().out


def test_ms_example_reads_the_aligner_through_the_player():
    ms = (ROOT / "config/ms.example.yaml").read_text()
    assert "127.0.0.1:8195/health" not in ms and "http://127.0.0.1:8100/v1/rail/align/health" in ms
