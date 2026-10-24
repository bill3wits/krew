"""krew's keeper and Janitor: Crissa installed and live, or not (the owner 2026-10-06).

Live means Crissa's Wall player answers GET /v1/state. Then the Crissa side is watched too, only over HTTP: the
player only (its /v1/state and /v1/ear), never Crissa's files. Not live means krew's own services only.
Their status says "Crissa not installed — watching krew only", and a missing Crissa service raises no alarm.
Every pass asks again. The "Crissa" here is a local fake player that each test starts and stops; nothing reaches
a real one.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent))
import janitor_env  # noqa: E402,F401

import builtins
import http.server
import importlib.util
import json
import socket
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOT_INSTALLED = "Crissa not installed — watching krew only"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeZoeyOS:
    """A Wall player on one local port: /v1/state, and /v1/ear (?part=stt) the way krew-join's player answers it
    ({ok, why, stt: {ok, engine, ear: {open, closed_reason}}}). start() / stop() at will."""

    def __init__(self):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.ear = {"open": True}
        self.ear_answers = True     # False: the player answers {"ok": false, "why": ...}
        self.ear_code = 200         # 404: a Crissa older than /v1/ear; 503: the player's own error
        self.asked: list[str] = []
        self.srv = None

    def start(self):
        fake = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                fake.asked.append(self.path)
                code = 200
                if self.path == "/v1/state":
                    body = {"ok": True, "on_air": False}
                elif self.path == "/v1/ear?part=stt" and fake.ear_code == 404:
                    body = None
                elif self.path == "/v1/ear?part=stt" and fake.ear_code != 200:
                    code, body = fake.ear_code, {"ok": False, "error": "the player could not ask her ear"}
                elif self.path == "/v1/ear?part=stt" and fake.ear_answers:
                    body = {"ok": True, "stt": {"ok": True, "engine": "parakeet", "ear": dict(fake.ear)}}
                elif self.path == "/v1/ear?part=stt":
                    body = {"ok": False, "why": "rail-stt is down"}
                else:
                    body = None
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        http.server.HTTPServer.allow_reuse_address = True
        self.srv = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None


@pytest.fixture
def zoeyos(monkeypatch):
    z = FakeZoeyOS()
    monkeypatch.setenv("ZOEYOS_PLAYER", z.url)
    monkeypatch.delenv("ZOEYOS_EAR", raising=False)     # on: her ear is read through the player
    yield z
    z.stop()


@pytest.fixture
def W():
    return _load(ROOT / "crew" / "lib" / "zoeyos_watch.py", "krewtest_zoeyos_watch")


# ---- the look itself -------------------------------------------------------------------------------------------
def test_not_installed_is_said_and_is_no_alarm(W, zoeyos):
    z = W.probe()
    assert z["live"] is False and z["headline"] == NOT_INSTALLED and z["level"] == "ok"
    assert "/v1/state" in z["why"] and zoeyos.url in z["why"] and z["problems"] == []


def test_live_watches_the_player_and_the_ear_over_http(W, zoeyos):
    zoeyos.start()
    z = W.probe()
    assert z["live"] is True and z["level"] == "ok" and z["headline"].startswith("Crissa live")
    assert z["ear"]["open"] is True and z["state"]["on_air"] is False
    assert zoeyos.asked == ["/v1/state", "/v1/ear?part=stt"]
    assert z["ear_url"] == zoeyos.url + "/v1/ear?part=stt" and z["ear"]["engine"] == "parakeet"


def test_live_names_a_closed_or_silent_ear(W, zoeyos):
    zoeyos.start()
    zoeyos.ear = {"open": False, "closed_reason": "idle timeout"}
    z = W.probe()
    assert z["live"] and z["level"] == "bad" and "ear is closed (idle timeout)" in z["headline"]
    zoeyos.ear_answers = False
    z = W.probe()
    assert z["level"] == "bad" and z["problems"][0].startswith("her ear is not answering: rail-stt is down")
    assert "/v1/ear?part=stt" in z["problems"][0]
    zoeyos.ear_code = 503
    z = W.probe()
    assert z["level"] == "bad" and "HTTP 503: the player could not ask her ear" in z["problems"][0]
    n = len(zoeyos.asked)
    assert W.probe(ear="off")["level"] == "ok" and zoeyos.asked[n:] == ["/v1/state"]


def test_a_zoeyos_older_than_v1_ear_is_a_note_not_an_alarm(W, zoeyos):
    zoeyos.start()
    zoeyos.ear_code = 404
    z = W.probe()
    assert z["live"] and z["level"] == "ok" and z["problems"] == [] and z["ear"]["older"] is True
    assert "older than /v1/ear" in z["notes"][0] and "older than /v1/ear" in z["headline"]


def test_the_ear_is_asked_of_the_player_only(W, zoeyos):
    # an old zoeyos.ear address (the ear's own :8190) is just "on" now: krew never calls the ear itself
    zoeyos.start()
    z = W.probe(ear="http://127.0.0.1:8190")
    assert z["ear"]["open"] is True and z["ear_url"].startswith(zoeyos.url)
    assert zoeyos.asked == ["/v1/state", "/v1/ear?part=stt"] and "8190" not in json.dumps(z)


def test_it_never_opens_a_file(W, zoeyos, monkeypatch):
    zoeyos.start()

    def no_files(*a, **k):
        raise AssertionError(f"opened a file: {a[:1]}")
    monkeypatch.setattr(builtins, "open", no_files)
    monkeypatch.setattr(Path, "read_text", no_files)
    monkeypatch.setattr(Path, "read_bytes", no_files)
    assert W.probe()["live"] is True


def test_every_look_asks_again(W, zoeyos):
    assert W.probe()["live"] is False
    zoeyos.start()
    first = W.probe()
    assert first["live"] is True and "live" in W.flip({"live": False}, first)
    zoeyos.stop()
    gone = W.probe()
    assert gone["live"] is False and W.flip(first, gone).startswith(NOT_INSTALLED)
    assert W.flip(gone, W.probe()) == ""


# ---- the keeper ------------------------------------------------------------------------------------------------
@pytest.fixture
def K(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_MEMORY_ROOT", str(tmp_path / "mem"))
    monkeypatch.setenv("KREW_SERVICES", str(ROOT / "config" / "services.example.json"))   # the lanes (2026-10-08)
    monkeypatch.delenv("KEEPER_STATUS", raising=False)
    monkeypatch.delenv("KEEPER_ZOEYOS_LANES", raising=False)
    return _load(ROOT / "crew" / "scripts" / "krew-keeper.py", "krewtest_keeper_zoeyos")


def _status(K):
    return json.loads(Path(K.KEEPER_STATUS).read_text())


def _log(K):
    return [json.loads(x) for x in Path(K.KEEPER_LOG).read_text().splitlines()]


def test_keeper_without_zoeyos_watches_krew_only_and_says_so(K, zoeyos, capsys):
    z = K.zoeyos_pass(None)
    st = _status(K)
    assert z["live"] is False and st["headline"] == NOT_INSTALLED and st["level"] == "ok"
    assert {"ear-rail", "zoey-display", "face-shell"} <= set(st["skipped_zoeyos_lanes"])
    assert {"crew", "ops", "zoey-gw", "dashboard"} <= set(st["lanes"])
    assert K.zoeyos_skips("ear-rail", z) and not K.zoeyos_skips("crew", z)
    assert NOT_INSTALLED in capsys.readouterr().out
    assert [r for r in _log(K) if r["event"] == "zoeyos_watch"][0]["live"] is False


def test_keeper_picks_up_zoeyos_coming_and_going_without_a_restart(K, zoeyos):
    z = K.zoeyos_pass(None)
    zoeyos.start()
    z = K.zoeyos_pass(z)
    st = _status(K)
    assert z["live"] and st["headline"].startswith("watching krew + Crissa") and st["skipped_zoeyos_lanes"] == []
    assert not K.zoeyos_skips("ear-rail", z)
    zoeyos.stop()
    z = K.zoeyos_pass(z)
    assert not z["live"] and _status(K)["headline"] == NOT_INSTALLED
    assert [r["live"] for r in _log(K) if r["event"] == "zoeyos_watch"] == [False, True, False]


class _Stop(Exception):
    pass


def _one_keeper_pass(K, monkeypatch):
    """Run main() for exactly one pass with every system touch stubbed; return what it probed."""
    seen = {"http": [], "ear": 0, "units": []}
    monkeypatch.setattr(K, "LANES", {"crew": ("http://krew.invalid/crew", "user", "crew.service"),
                                     "ear-rail": ("http://zoeyos.invalid/health", "user", "rail-stt.service")})
    monkeypatch.setattr(K, "load_state", lambda: {})
    monkeypatch.setattr(K, "gate_d_assert", lambda: None)
    monkeypatch.setattr(K, "keep_ear_open", lambda: seen.__setitem__("ear", seen["ear"] + 1))
    monkeypatch.setattr(K, "http_ok", lambda url, *a, **k: seen["http"].append(url) or False)
    monkeypatch.setattr(K, "unit_state", lambda scope, unit: seen["units"].append(unit) or "inactive")
    monkeypatch.setattr(K, "unit_action", lambda *a, **k: pytest.fail(f"keeper acted: {a}"))
    monkeypatch.setattr(K, "announce", lambda *a, **k: pytest.fail(f"keeper announced: {a}"))
    for name in ("wake_gateways", "tts_scratch_janitor", "tts_unit_pressure", "keep_voice_warm"):
        monkeypatch.setattr(K, name, lambda *a, **k: None)
    monkeypatch.setattr(K, "http_json", lambda *a, **k: None)
    monkeypatch.setattr(K.time, "sleep", lambda s: (_ for _ in ()).throw(_Stop()))
    with pytest.raises(_Stop):
        K.main()
    return seen


def test_keeper_pass_without_zoeyos_never_probes_a_zoeyos_lane(K, zoeyos, monkeypatch):
    seen = _one_keeper_pass(K, monkeypatch)
    assert seen["http"] == ["http://krew.invalid/crew"] and seen["ear"] == 0
    assert _status(K)["headline"] == NOT_INSTALLED


def test_keeper_pass_with_zoeyos_live_watches_its_side_too(K, zoeyos, monkeypatch):
    zoeyos.start()
    seen = _one_keeper_pass(K, monkeypatch)
    assert seen["http"] == ["http://krew.invalid/crew", "http://zoeyos.invalid/health"] and seen["ear"] == 1
    assert "/v1/state" in zoeyos.asked and "/v1/ear?part=stt" in zoeyos.asked


# ---- the Janitor -----------------------------------------------------------------------------------------------
@pytest.fixture
def J(tmp_path, monkeypatch):
    j = _load(ROOT / "janitor" / "krew-janitor.py", "krewtest_janitor_zoeyos")
    monkeypatch.setattr(j, "ZOEYOS_PLAYER", "")
    monkeypatch.setattr(j, "ZOEYOS_EAR", "")
    return j


class FakeHW:
    def __init__(self, rows):
        self.rows = rows
        self.snap = None

    def on(self):
        return True

    def _registry(self):
        return SimpleNamespace(cheap_check=lambda: {})

    def check_all(self, **kw):
        return [dict(r) for r in self.rows]

    def read_snapshot(self):
        return {}

    def gaps(self, prev=None):
        return []

    def write_snapshot(self, snap):
        self.snap = snap
        return "/tmp/written"


def _tick(J, monkeypatch, tmp_path):
    """One Janitor tick with every system touch stubbed: krew's crew.service is up, Crissa's units are not there."""
    cards = []
    monkeypatch.setattr(J, "UNITS", [("crew.service", "user"), ("rail-stt.service", "user"),
                                     ("zoeyos-player.service", "user")])
    monkeypatch.setattr(J, "ZOEYOS_UNITS", {"rail-stt.service"})
    monkeypatch.setattr(J, "PROBES", {"crew": (8005, "/v1/models"), "ear": (8190, "/health")})
    monkeypatch.setattr(J, "PROBE_UNITS", {"crew": "crew.service", "ear": "rail-stt.service"})
    for name in ("RETIRED_UNITS", "RETIRED_PROBES", "GPU_OCCUPANCY_PROBES", "TRAIN_PARKED_PROBES",
                 "GPU_TENANT_REPORT_ONLY"):
        monkeypatch.setattr(J, name, set())
    monkeypatch.setattr(J, "GLASS_UNIT", "")
    monkeypatch.setattr(J, "METRICS_DIR", tmp_path / "metrics")
    monkeypatch.setattr(J, "STATE_PATH", tmp_path / "metrics" / "janitor-state.json")
    monkeypatch.setattr(J, "host_sample", lambda: {"boot_id": "b1"})
    monkeypatch.setattr(J, "gpu_sample", lambda: {})
    monkeypatch.setattr(J, "reboot_comment_tick", lambda: 0)
    monkeypatch.setattr(J, "train_window_active", lambda: False)
    monkeypatch.setattr(J, "read_gpu_mode", lambda: "normal")
    monkeypatch.setattr(J, "unit_state", lambda u, m: {"active": "active" if u == "crew.service" else "inactive",
                                                       "sub": "running" if u == "crew.service" else "dead",
                                                       "nrestarts": 0})
    monkeypatch.setattr(J, "detect_crash_loop", lambda *a, **k: None)
    monkeypatch.setattr(J, "row_unit_parked", lambda *a, **k: False)
    monkeypatch.setattr(J, "occupancy_parks_probe", lambda *a, **k: False)
    monkeypatch.setattr(J, "restart_refusal", lambda *a, **k: "dry")
    monkeypatch.setattr(J, "journal_tail", lambda *a, **k: "")
    monkeypatch.setattr(J, "file_card", lambda title, body: cards.append(title) or True)
    monkeypatch.setattr(J, "probe", lambda port, path: {"ok": port == 8005})
    for name in ("pin_tick", "render_leftover_tick"):
        monkeypatch.setattr(J, name, lambda *a, **k: {})
    for name in ("audio_orphan_tick", "code_canary_tick", "hermes_venv_gen_tick"):
        monkeypatch.setattr(J, name, lambda: {})
    monkeypatch.setattr(J, "watch_tick", lambda *a, **k: {})
    J.tick()
    rows = (tmp_path / "metrics" / f"janitor-{date.today():%Y%m%d}.jsonl").read_text().splitlines()
    return json.loads(rows[-1]), cards


def test_janitor_without_zoeyos_parks_its_units_and_probes_and_cards_nothing(J, zoeyos, monkeypatch, tmp_path,
                                                                             capsys):
    row, cards = _tick(J, monkeypatch, tmp_path)
    assert row["zoeyos"]["live"] is False and cards == []
    for u in ("rail-stt.service", "zoeyos-player.service"):     # named in zoeyos.units / matching zoeyos-*
        assert row["units"][u]["red"] is False and row["units"][u]["parked_reason"] == NOT_INSTALLED
    assert row["units"]["crew.service"]["red"] is False
    assert row["probes"]["ear"]["parked"] is True and row["probes"]["ear"]["parked_reason"] == NOT_INSTALLED
    assert NOT_INSTALLED in capsys.readouterr().out


def test_janitor_with_zoeyos_live_watches_its_units_again_next_tick(J, zoeyos, monkeypatch, tmp_path):
    row, cards = _tick(J, monkeypatch, tmp_path)
    assert cards == []
    zoeyos.start()                                                # installed / started: the next tick sees it
    row, cards = _tick(J, monkeypatch, tmp_path)
    assert row["zoeyos"]["live"] is True
    assert row["units"]["rail-stt.service"]["red"] is True and not row["units"]["rail-stt.service"].get("parked")
    assert sorted(cards) == ["JANITOR RED: rail-stt.service inactive", "JANITOR RED: zoeyos-player.service inactive"]


def test_janitor_tile_says_krew_only_or_watches_zoeyos(J, zoeyos):
    hw = FakeHW([])
    units = {"crew.service": {"active": "active", "sub": "running", "red": False}}
    z = J.zoeyos_tick({})
    snap = J.janitor_snapshot(0.0, units, {"probes": {}, "zoeyos": z}, [], hw)
    assert snap["level"] == "ok" and snap["headline"].endswith("· " + NOT_INSTALLED) and snap["zoeyos"]["live"] is False
    zoeyos.start()
    z = J.zoeyos_tick({"zoeyos": {"live": False}})
    snap = J.janitor_snapshot(0.0, units, {"probes": {}, "zoeyos": z}, [], hw)
    assert snap["level"] == "ok" and "Crissa live, watched through its player" in snap["headline"]
    zoeyos.ear_answers = False
    z = J.zoeyos_tick({"zoeyos": {"live": True}})
    snap = J.janitor_snapshot(0.0, units, {"probes": {}, "zoeyos": z}, [], hw)
    assert snap["level"] == "bad" and snap["reds"][0]["name"] == "Crissa" and "her ear is not answering" in snap["headline"]


def test_janitor_tile_warns_when_zoeyos_is_older_than_v1_ear(J, zoeyos):
    hw = FakeHW([])
    units = {"crew.service": {"active": "active", "sub": "running", "red": False}}
    zoeyos.start()
    zoeyos.ear_code = 404
    z = J.zoeyos_tick({"zoeyos": {"live": False}})
    snap = J.janitor_snapshot(0.0, units, {"probes": {}, "zoeyos": z}, [], hw)
    assert snap["level"] == "warn" and not snap["reds"] and "older than /v1/ear" in snap["headline"]


def test_janitor_watch_list_parks_zoeyos_registry_units_only_while_it_is_not_live(J, zoeyos):
    rows = [{"unit": "zoeyos-wall.service", "scope": "user", "red": True, "level": "bad", "why": "not loaded"},
            {"unit": "krew-x.service", "scope": "user", "red": True, "level": "bad", "why": "failed"}]
    cards = []
    hw = FakeHW(rows)
    out = J.watch_tick({}, 1e9, "normal", False, {}, {"zoeyos": {"live": False}}, hw=hw,
                       card=lambda t, b: cards.append(t) or True)
    assert out["red"] == ["krew-x.service"] and cards == ["JANITOR RED: krew-x.service failed"]
    assert [r for r in hw.snap["watch"] if r["unit"] == "zoeyos-wall.service"][0]["why"] == NOT_INSTALLED
    cards.clear()
    out = J.watch_tick({}, 1e9, "normal", False, {}, {"zoeyos": {"live": True}}, hw=FakeHW(rows),
                       card=lambda t, b: cards.append(t) or True)
    assert sorted(out["red"]) == ["krew-x.service", "zoeyos-wall.service"]


def test_janitor_without_the_helper_reads_krew_only(J, tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_zoeyos_watch", lambda: (_ for _ in ()).throw(ImportError("no zoeyos_watch")))
    z = J.zoeyos_tick({})
    assert z["live"] is False and z["headline"] == NOT_INSTALLED and "did not load" in z["why"]
