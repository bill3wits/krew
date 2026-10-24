"""Keeper GPU revive: parked observe-only; crash on a live lane may start."""
from __future__ import annotations

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import sys
import time

ROOT = Path(__file__).resolve().parents[1]

# krew: the keeper's lanes come from the services list ($KREW_SERVICES); these rows are the house's GPU lanes and
# modes as the house keeper hard-coded them (ops parked in beast and render, crew in heavy-render, the voice server
# never), plus the one host gateway it wakes after a boot.
import json as _json
import os as _os
import tempfile as _tempfile

_SVC = Path(_tempfile.mkdtemp(prefix="krew-keeper-test-")) / "services.json"
_SVC.write_text(_json.dumps({"schema": "krew-services/v1", "services": [
    {"name": "ops", "unit": "ops.service", "port": 8000, "health": "/v1/models", "role": "watch", "gpu": True,
     "kind": "engine", "parked_in": ["beast", "render"], "approved": True},
    {"name": "crew", "unit": "crew.service", "port": 8005, "health": "/v1/models", "role": "watch", "gpu": True,
     "kind": "engine", "parked_in": ["heavy-render"], "approved": True},
    {"name": "voice", "unit": "turbo-voice.service", "port": 7789, "health": "/health", "role": "watch", "gpu": True,
     "kind": "voice", "approved": True},
    {"name": "zoey-gw", "unit": "hermes-gateway.service", "port": 8642, "health": "/health", "role": "watch",
     "kind": "gateway", "profile": "zoey", "wake_at_boot": True, "no_heal_in": ["beast", "render"], "approved": True},
]}))


def _keeper():
    saved = {k: _os.environ.get(k) for k in ("KREW_SERVICES", "KREW_MEMORY_ROOT")}
    _os.environ["KREW_SERVICES"] = str(_SVC)          # read once, at import; put back after (never leaks to others)
    _os.environ.setdefault("KREW_MEMORY_ROOT", str(_SVC.parent / "mem"))
    try:
        loader = SourceFileLoader("krew_keeper", str(ROOT / "crew" / "scripts" / "krew-keeper.py"))
        spec = spec_from_loader(loader.name, loader)
        assert spec is not None
        mod = module_from_spec(spec)
        loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
    return mod


def test_occupancy_parks_gpu_lane_beast_ops_not_crew(tmp_path, monkeypatch):
    k = _keeper()
    monkeypatch.setattr(k, "TRAIN_HOLD_PATHS", (str(tmp_path / "no-hold"),))
    assert k.occupancy_parks_gpu_lane("ops.service", "beast") is True
    assert k.occupancy_parks_gpu_lane("crew.service", "beast") is False
    assert k.occupancy_parks_gpu_lane("turbo-voice.service", "beast") is False


def test_occupancy_parks_gpu_lane_sleeping_crew(tmp_path, monkeypatch):
    k = _keeper()
    monkeypatch.setattr(k, "TRAIN_HOLD_PATHS", (str(tmp_path / "no-hold"),))
    monkeypatch.setattr(k, "_crew_is_sleeping", lambda: True)
    assert k.occupancy_parks_gpu_lane("crew.service", "normal") is True
    assert k.occupancy_parks_gpu_lane("ops.service", "normal") is False


def test_occupancy_parks_gpu_lane_render_ops_not_crew(tmp_path, monkeypatch):
    k = _keeper()
    monkeypatch.setattr(k, "TRAIN_HOLD_PATHS", (str(tmp_path / "no-hold"),))
    assert k.occupancy_parks_gpu_lane("ops.service", "render") is True
    assert k.occupancy_parks_gpu_lane("crew.service", "render") is False
    assert k.occupancy_parks_gpu_lane("turbo-voice.service", "render") is False


def test_occupancy_parks_gpu_lane_normal_heavy_none(tmp_path, monkeypatch):
    k = _keeper()
    monkeypatch.setattr(k, "TRAIN_HOLD_PATHS", (str(tmp_path / "no-hold"),))
    for mode in ("normal", "heavy"):
        for unit in ("ops.service", "crew.service", "turbo-voice.service"):
            assert k.occupancy_parks_gpu_lane(unit, mode) is False


def test_hold_parks_named_units_and_render_ops_hold_frees_crew(tmp_path, monkeypatch):
    """Dual-park hold is exclusive. Ops-only hold makes crew the lane brain."""
    k = _keeper()
    hold = tmp_path / "krew-train-hold"
    monkeypatch.setattr(k, "TRAIN_HOLD_PATHS", (str(hold),))
    hold.write_text("ops.service\ncrew.service\n")
    assert k.occupancy_parks_gpu_lane("ops.service", "render") is True
    assert k.occupancy_parks_gpu_lane("crew.service", "render") is True
    hold.write_text("crew.service\n")
    assert k.occupancy_parks_gpu_lane("crew.service", "render") is True
    assert k.occupancy_parks_gpu_lane("ops.service", "render") is False


def test_voice_warm_skips_when_mouth_busy(tmp_path, monkeypatch):
    k = _keeper()
    gate = tmp_path / "gate.json"
    gate.write_text('{"speaking": true, "deaf_until": 0}')
    monkeypatch.setattr(k, "TTS_GATE", gate)
    monkeypatch.setattr(k, "log", lambda *a, **kw: None)
    called = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("must not POST /warm while speaking")

    monkeypatch.setattr(k.urllib.request, "urlopen", boom)
    k.keep_voice_warm()
    assert called == []


def test_prod_lock_holds_gpu_lanes(tmp_path, monkeypatch):
    k = _keeper()
    lock = tmp_path / "krew-prod-lock"
    lock.write_text("on\n")
    monkeypatch.setattr(k, "PROD_LOCK_FILE", str(lock))
    monkeypatch.setattr(k, "TRAIN_HOLD_PATHS", (str(tmp_path / "no-hold"),))
    assert k.prod_locked() is True
    assert k._lane_held("ops.service") is True
    assert k._lane_held("crew.service") is True
    assert k._lane_held("turbo-voice.service") is True
    lock.write_text("off\n")
    assert k.prod_locked() is False
    assert k._lane_held("ops.service") is False


def test_in_start_grace_covers_simple_load(monkeypatch):
    k = _keeper()
    monkeypatch.setattr(k, "unit_start_timeout_s", lambda *_a, **_k: 600.0)
    assert k.in_start_grace("user", "ops.service", age_s=65) is True
    assert k.in_start_grace("user", "ops.service", age_s=599) is True
    assert k.in_start_grace("user", "ops.service", age_s=600) is False
    monkeypatch.setattr(k, "unit_age_s", lambda *_a, **_k: None)
    assert k.in_start_grace("user", "ops.service") is False


def test_proc_age_s_none_for_dead_pid():
    k = _keeper()
    assert k.proc_age_s(0) is None
    assert k.proc_age_s(-1) is None


def test_start_grace_before_restart_action():
    src = (ROOT / "crew" / "scripts" / "krew-keeper.py").read_text(encoding="utf-8")
    assert "in_start_grace" in src
    assert "START_GRACE_SEC" in src
    assert "reprobe before budget PA" in src
    grace = src.find("in_start_grace(scope, unit)")
    action = src.find('action = "restart"')
    assert 0 <= grace < action


def test_gpu_lanes_continue_only_when_parked():
    src = (ROOT / "crew" / "scripts" / "krew-keeper.py").read_text(encoding="utf-8")
    assert "occupancy_parks_gpu_lane" in src
    assert "lane_down_gpu_occupancy" in src
    # Blanket continue on every GPU_LANES unit is the 08-21 silent death.
    assert "if unit in GPU_LANES:" not in src
    parked = src.find("if occupancy_parks_gpu_lane(unit):")
    held = src.find("if _lane_held(unit):")
    action = src.find('action = "restart"')
    assert 0 <= parked < held < action


def test_start_grace_follows_unit_timeout_never_below_floor(monkeypatch):
    k = _keeper()
    timeouts = {"crew.service": 900.0, "turbo-voice.service": 90.0, "odd.service": None}
    monkeypatch.setattr(k, "unit_start_timeout_s", lambda _s, u: timeouts[u])
    # crew: the unit says 900 s; 2026-09-09 a flat 600 killed three boots
    assert k.start_grace_s("user", "crew.service") == 900.0
    assert k.in_start_grace("user", "crew.service", age_s=750) is True
    assert k.in_start_grace("user", "crew.service", age_s=900) is False
    # short-timeout and unknown units keep the 600 floor
    assert k.start_grace_s("user", "turbo-voice.service") == 600.0
    assert k.start_grace_s("user", "odd.service") == 600.0


def test_retry_zoey_glass_homes_only_while_lila_is_camped(monkeypatch):
    import io
    import json
    k = _keeper()
    posted = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=0):
        url = req if isinstance(req, str) else getattr(req, "full_url", "")
        if "state" in str(url):
            return _Resp(json.dumps({"now": "lila-work"}).encode())
        posted.append(json.loads(req.data.decode()))
        return _Resp(json.dumps({"taken": True}).encode())

    monkeypatch.setattr(k.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(k, "log", lambda *a, **kw: None)
    k._retry_zoey_glass()
    assert posted == [{"by": "render"}]


def test_parse_systemd_span_s():
    k = _keeper()
    assert k.parse_systemd_span_s("15min") == 900.0
    assert k.parse_systemd_span_s("1min 30s") == 90.0
    assert k.parse_systemd_span_s("10min") == 600.0
    assert k.parse_systemd_span_s("2h") == 7200.0
    assert k.parse_systemd_span_s("infinity") is None
    assert k.parse_systemd_span_s("0") is None
    assert k.parse_systemd_span_s("") is None


# ── boot bring-up of the crew's gateways (2026-09-17: Ada and Lila stayed down after a reboot) ──
def _ok(**kw):
    base = dict(flag="normal", hold=False, brain_ok_ticks=3, state="inactive",
                ran_this_boot=False, tried=False)
    base.update(kw)
    return base


def test_gateway_wake_starts_only_a_never_run_unit_in_normal_with_crew_serving():
    k = _keeper()
    assert k.gateway_wake_refusal(**_ok()) == ""
    assert k.gateway_wake_refusal(**_ok(brain_ok_ticks=2)).startswith("crew served 2/")
    assert k.gateway_wake_refusal(**_ok(ran_this_boot=True)).startswith("ran since boot")
    assert k.gateway_wake_refusal(**_ok(state="failed")) == "state failed"
    assert k.gateway_wake_refusal(**_ok(state="deactivating")) == "state deactivating"
    assert k.gateway_wake_refusal(**_ok(hold=True)) == "a GPU hold is up"
    assert k.gateway_wake_refusal(**_ok(tried=True)) == "one try per keeper life"
    for flag in ("render", "beast", "off", ""):       # `off` must not read as normal here
        assert k.gateway_wake_refusal(**_ok(flag=flag)).startswith("flag ")


def _wired(k, monkeypatch, tmp_path, *, states, ran, brain, flag="normal"):
    calls = []
    monkeypatch.setattr(k, "GATEWAY_WAKE", True)
    monkeypatch.setattr(k, "GATEWAY_WAKE_OFF", tmp_path / "no-off")
    monkeypatch.setattr(k, "GATEWAY_WAKE_UNITS", ("a.service", "l.service"))
    monkeypatch.setattr(k, "TRAIN_HOLD_PATHS", (str(tmp_path / "no-hold"),))
    monkeypatch.setattr(k, "BEAST_HOLD", str(tmp_path / "no-beast"))
    monkeypatch.setattr(k, "_gpu_flag_raw", lambda: flag)
    monkeypatch.setattr(k, "http_ok", lambda url, timeout=4.0: brain())
    monkeypatch.setattr(k, "unit_state", lambda scope, unit: states[unit])
    monkeypatch.setattr(k, "unit_ran_this_boot", lambda scope, unit: ran[unit])
    monkeypatch.setattr(k, "unit_action", lambda s, u, a: calls.append((u, a)) or True)
    monkeypatch.setattr(k, "log", lambda *a, **kw: None)
    return calls


def test_gateway_wake_waits_three_probes_then_starts_each_unit_once(monkeypatch, tmp_path):
    k = _keeper()
    calls = _wired(k, monkeypatch, tmp_path, states={"a.service": "inactive", "l.service": "inactive"},
                   ran={"a.service": False, "l.service": False}, brain=lambda: True)
    mem: dict = {}
    k.wake_gateways(mem); k.wake_gateways(mem)
    assert calls == []
    k.wake_gateways(mem)
    assert calls == [("a.service", "start"), ("l.service", "start")]
    k.wake_gateways(mem); k.wake_gateways(mem)
    assert len(calls) == 2                             # never again in this keeper life


def test_gateway_wake_resets_on_a_failed_probe_and_leaves_a_hand_stopped_unit(monkeypatch, tmp_path):
    k = _keeper()
    seq = iter([True, True, False, True, True, True])
    calls = _wired(k, monkeypatch, tmp_path, states={"a.service": "inactive", "l.service": "inactive"},
                   ran={"a.service": False, "l.service": True}, brain=lambda: next(seq))
    mem: dict = {}
    for _ in range(5):
        k.wake_gateways(mem)
    assert calls == []                                 # OK, OK, miss, OK, OK: never three in a row
    k.wake_gateways(mem)
    assert calls == [("a.service", "start")]           # l ran since boot: someone stopped it


def test_gateway_wake_is_off_in_render_and_under_the_off_file(monkeypatch, tmp_path):
    k = _keeper()
    calls = _wired(k, monkeypatch, tmp_path, states={"a.service": "inactive", "l.service": "inactive"},
                   ran={"a.service": False, "l.service": False}, brain=lambda: True, flag="render")
    mem: dict = {}
    for _ in range(5):
        k.wake_gateways(mem)
    assert calls == []
    monkeypatch.setattr(k, "_gpu_flag_raw", lambda: "normal")
    off = tmp_path / "off"; off.write_text("")
    monkeypatch.setattr(k, "GATEWAY_WAKE_OFF", off)
    k.wake_gateways(mem)
    assert calls == []


# ── the "stopped on purpose" marker (2026-09-18: the re-pull after a clean stop cycles forever) ──
def _marker_at(k, monkeypatch, tmp_path, epoch: float, agent: str = "keeper-drain"):
    """Write the stop marker with a fixed timestamp (epoch seconds)."""
    p = tmp_path / "stop-marker"
    p.write_text(f"{int(epoch)}  {agent}\n")
    monkeypatch.setattr(k, "STOP_MARKER", p)
    return p


def test_stop_marker_fresh_only_within_ttl(monkeypatch, tmp_path):
    k = _keeper()
    now = time.time()
    p = _marker_at(k, monkeypatch, tmp_path, now - 10)
    assert k._stop_marker_fresh("a.service") is True
    _marker_at(k, monkeypatch, tmp_path, now - (k.STOP_MARKER_TTL_S + 10))
    assert k._stop_marker_fresh("a.service") is False
    # no marker / garbage / unreadable -> not a deliberate stop (wake proceeds)
    p.unlink()
    assert k._stop_marker_fresh("a.service") is False
    p.write_text("not-a-timestamp  keeper-drain\n")
    assert k._stop_marker_fresh("a.service") is False
    p.write_text("")
    assert k._stop_marker_fresh("a.service") is False


def test_stop_marker_sticks_a_deliberate_stop(monkeypatch, tmp_path):
    # the exact bug: unit_ran_this_boot reads False after a clean stop
    # (ActiveEnterMonotonic resets to 0), so without the marker the keeper
    # re-pulls a deliberately stopped unit.
    k = _keeper()
    calls = _wired(k, monkeypatch, tmp_path,
                   states={"a.service": "inactive", "l.service": "inactive"},
                   ran={"a.service": False, "l.service": False}, brain=lambda: True)
    now = time.time()
    _marker_at(k, monkeypatch, tmp_path, now)
    mem: dict = {}
    for _ in range(5):
        k.wake_gateways(mem)
    assert calls == []                                 # fresh marker: a stays down (and l too)
    # l has NO marker: a clean stop of l that nobody marked still wakes as designed
    # (the marker is only written by stop callers; an unmarked stop is the boot case)


def test_stop_marker_is_unit_specific_and_per_unit_tried(monkeypatch, tmp_path):
    # a marker on the file is unit-agnostic by design (one marker file per
    # mitigation spec §4.1); both wake units see it. Verify a fresh marker
    # marks BOTH tried, and that a stale marker lets the normal wake proceed.
    k = _keeper()
    now = time.time()
    _marker_at(k, monkeypatch, tmp_path, now)
    calls = _wired(k, monkeypatch, tmp_path,
                   states={"a.service": "inactive", "l.service": "inactive"},
                   ran={"a.service": False, "l.service": False}, brain=lambda: True)
    mem: dict = {}
    k.wake_gateways(mem)
    assert calls == []
    assert mem["why"] == {"a.service": "stop-marker", "l.service": "stop-marker"}
    # a second tick in the same keeper life must not re-log (one wait per unit)
    # (the why dict already records stop-marker; the unit is in tried so the
    # marker branch is skipped entirely on subsequent ticks)


def test_stop_marker_stale_lets_the_wake_proceed(monkeypatch, tmp_path):
    # a marker older than the TTL is an escape-hatch case ("stopped it 6h ago,
    # want it back") -> the normal bring-up proceeds.
    k = _keeper()
    now = time.time()
    _marker_at(k, monkeypatch, tmp_path, now - (k.STOP_MARKER_TTL_S + 60))
    calls = _wired(k, monkeypatch, tmp_path,
                   states={"a.service": "inactive", "l.service": "inactive"},
                   ran={"a.service": False, "l.service": False}, brain=lambda: True)
    mem: dict = {}
    k.wake_gateways(mem); k.wake_gateways(mem); k.wake_gateways(mem)
    assert calls == [("a.service", "start"), ("l.service", "start")]


def test_no_wake_or_fallback_list_names_a_retired_per_profile_gateway(monkeypatch):
    """The per-profile gateways are masked since the 09-27 cutover: the keeper's fallback wake list is the one
    host gateway, the same unit the host-topology override uses, and no live script's wake or fallback list
    spells a hermes-<profile>-gateway unit (the User 2026-10-08)."""
    import re
    monkeypatch.delenv("KEEPER_GATEWAY_WAKE_UNITS", raising=False)
    k = _keeper()
    assert k.GATEWAY_WAKE_UNITS == ("hermes-gateway.service",)
    pat = re.compile(r"hermes-(zoey|ada|lila|steward|vera|bash|scout|judge)-gateway")
    bad = []
    # the live scripts the cutover named: the keeper, the restart window, krew-mode, the janitor (the panel's
    # own render-wake row is the panel's, read with the host-topology rule, and stays as it is)
    live = ["crew/scripts/krew-keeper.py", "window/scripts/krew-restart-window.py", "window/lib/restart_window.py",
            "janitor/krew-janitor.py"]
    for p in [ROOT / rel for rel in live if (ROOT / rel).is_file()]:
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if ("WAKE" in line or re.search(r"start_unit\(", line)) and pat.search(line):
                bad.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    # the window's sets name the one host gateway, never a retired unit
    import importlib.util
    sys.path.insert(0, str(ROOT / "window" / "lib"))
    spec = importlib.util.spec_from_file_location("restart_window_t", ROOT / "window" / "lib" / "restart_window.py")
    rw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rw)
    for name in ("TIER_OF", "ZOEY_UNITS", "API_UNITS", "HERMES_UNITS"):
        assert not [u for u in getattr(rw, name) if pat.search(u)], name
    assert not bad, "\n".join(bad)
