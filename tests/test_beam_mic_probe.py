"""The beam-mic probe (the User 2026-10-01): the Seeed was unplugged 09-30 10:02 -> 10-01 02:05 and nothing said so.
Report only: a missing source or a null beam for 5 min opens ONE card per episode; recovery closes it."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "crew" / "lib"))
import beam_mic_probe as b  # noqa: E402

SEEED = "alsa_input.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_000000000000000000-00.analog-stereo"   # serials made up
SHURE = "alsa_input.usb-Desk_Mic_USB_0-00000000000000000000000000000000-01.mono-fallback"
NOW = 1_790_000_000.0


def stt(az=176.0, ts=NOW - 1, mic="seeed"):
    return {"ear": {"mic": mic}, "doa_loop": {"last_az": az, "last_pub_ts": ts}}


def wall(az=176.0, ts=NOW - 1):
    return {"sound": {"az_deg": az, "ts": ts}}


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(b, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(b, "OFF", tmp_path / "off")
    monkeypatch.setattr(b, "LOG", tmp_path / "log.jsonl")
    monkeypatch.delenv("KREW_BEAM_MIC_PROBE", raising=False)
    monkeypatch.delenv("KREW_BEAM_MIC_PROBE_CARDS", raising=False)


def test_a_healthy_room_is_ok():
    r = b.probe(NOW, srcs=[SEEED, SHURE], stt=stt(), wall=wall())
    assert r["verdict"] == "ok" and r["faults"] == [] and r["ear_mic"] == "seeed"


def test_the_0930_outage_is_a_fault_on_both_counts():
    """Seeed gone from PipeWire, rail-stt on the Shure, no angle anywhere."""
    r = b.probe(NOW, srcs=[SHURE], stt=stt(az=None, mic="shure"), wall=wall(az=None))
    assert r["verdict"] == "fault" and r["faults"] == ["source missing", "beam null"] and r["ear_mic"] == "shure"


def test_a_stale_angle_is_null_and_one_fresh_source_is_enough():
    assert b.beam_fresh(stt(ts=NOW - 600), wall(ts=NOW - 600), NOW) is False
    assert b.beam_fresh(stt(ts=NOW - 600), wall(), NOW) is True
    assert b.beam_fresh(None, None, NOW) is None


def test_unreadable_is_unknown_never_a_fault():
    r = b.probe(NOW, srcs=None, stt=None, wall=None)
    assert r["verdict"] == "unknown" and r["faults"] == []


def test_five_minutes_then_one_card_then_recovery_closes_it(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(a)
        return (0, "Created t_abcdef12  (ready, assignee=ada)") if a[0] == "create" else (0, "")
    monkeypatch.setattr(b, "kanban", fake)
    bad = b.probe(NOW, srcs=[SHURE], stt=stt(az=None, mic="shure"), wall=wall(az=None))
    st, act = b.step({}, bad, NOW)
    assert act == ""                                   # not yet: the grace
    st, act = b.step(st, bad, NOW + 299)
    assert act == ""
    st, act = b.step(st, bad, NOW + 300)
    assert act == "file"
    tid = b.file_card(bad, st)
    assert tid == "t_abcdef12"
    assert [c[0] for c in calls] == ["create", "block"] and "needs_input" in calls[1]
    st["episode"] = {"since": st["fault_since"], "card": tid, "faults": st["faults_seen"]}
    st, act = b.step(st, bad, NOW + 360)
    assert act == "", "one card per episode"
    ok = b.probe(NOW + 400, srcs=[SEEED], stt=stt(ts=NOW + 399), wall=wall(ts=NOW + 399))
    st, act = b.step(st, ok, NOW + 400)
    assert act == "recovered" and "fault_since" not in st
    b.close_card(st.pop("episode"), NOW + 400)
    assert [c[0] for c in calls][-2:] == ["comment", "complete"]


def test_unknown_neither_files_nor_clears(monkeypatch):
    bad = b.probe(NOW, srcs=[SHURE], stt=stt(az=None), wall=wall(az=None))
    st, _ = b.step({}, bad, NOW)
    st, act = b.step(st, {"verdict": "unknown"}, NOW + 400)
    assert act == "" and "fault_since" in st


def test_the_kill_switches(monkeypatch, tmp_path):
    monkeypatch.setenv("KREW_BEAM_MIC_PROBE", "0")
    assert b.probe(NOW, srcs=[], stt=None, wall=None) == {"verdict": "off"}
    monkeypatch.delenv("KREW_BEAM_MIC_PROBE")
    (tmp_path / "off").write_text("")
    assert b.tick() == {"verdict": "off"}
    monkeypatch.setenv("KREW_BEAM_MIC_PROBE_CARDS", "0")
    assert not b.cards_enabled()


def test_it_never_touches_the_mic_or_rail_stt():
    src = (Path(__file__).resolve().parents[1] / "crew" / "lib" / "beam_mic_probe.py").read_text()
    for bad in ("systemctl", "set-default-source", "pactl set", "pactl load", "subscribe\"", "restart"):
        assert bad not in src.replace("NEVER RESTARTS", "").replace("restarts", ""), bad
