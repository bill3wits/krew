"""Render work-in-progress (lib/render_wip.py) — no Comfy, no GPU, no :8099.

the owner 2026-09-18: the studio stage shows work in progress only; blank + the
wait note until something draws or lands; every version this Render.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _mod(tmp_path, monkeypatch):
    lib = str(ROOT / "crew" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import render_wip as rw

    monkeypatch.setattr(rw, "WIP_DIR", tmp_path / "wip")
    monkeypatch.setattr(rw, "STATE", tmp_path / "wip" / "state.json")
    monkeypatch.setattr(rw, "PREVIEW", tmp_path / "wip" / "preview.img")
    monkeypatch.setattr(rw, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(rw, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "out").mkdir()
    return rw


def test_prompt_body_carries_krew_client_and_preview_override(tmp_path, monkeypatch):
    rw = _mod(tmp_path, monkeypatch)
    b = rw.prompt_body({"1": {}})
    assert b["client_id"] == "krew-wip"
    assert b["extra_data"] == {"preview_method": "auto"}
    assert "extra_data" not in rw.prompt_body({}, preview="none")


def test_every_krew_submitter_posts_the_same_client_id():
    """Comfy streams progress + previews only to the socket holding the prompt's
    client_id — a submitter with its own id draws blind on the stage."""
    plugin = (ROOT / "plugins" / "krew-comfy" / "__init__.py").read_text()
    submit = (ROOT / "crew" / "scripts" / "render-submit.py").read_text()
    for src in (plugin, submit):
        assert 'os.environ.get("KREW_COMFY_CLIENT_ID", "krew-wip")' in src
        assert '"preview_method": os.environ.get("KREW_COMFY_PREVIEW", "auto")' in src
    assert 'f"hermes-{prefix}"' not in plugin
    assert 'f"render-submit-{a.card}"' not in submit


def test_tracker_follows_a_draw_and_records_what_landed(tmp_path, monkeypatch):
    rw = _mod(tmp_path, monkeypatch)
    tr = rw.Tracker()
    tr.on_text({"type": "status", "data": {"status": {"exec_info": {"queue_remaining": 1}}}})
    assert tr.st["phase"] == "queued" and tr.st["queue"] == 1
    tr.on_text({"type": "execution_start", "data": {"prompt_id": "p1"}})
    assert tr.st["phase"] == "drawing" and tr.st["prompt_id"] == "p1"
    tr.on_text({"type": "executing", "data": {"node": "9", "prompt_id": "p1"}})
    tr.on_text({"type": "progress", "data": {"value": 3, "max": 8, "node": "9", "prompt_id": "p1"}})
    assert (tr.st["value"], tr.st["max"], tr.st["node"]) == (3, 8, "9")
    tr.on_text({"type": "executed", "data": {"node": "12", "prompt_id": "p1", "output": {
        "images": [{"filename": "a_00001_.png", "subfolder": "crew/x", "type": "output"},
                   {"filename": "tmp.png", "subfolder": "", "type": "temp"}]}}})
    assert tr.st["landed"] == ["crew/x/a_00001_.png"]
    tr.on_text({"type": "executing", "data": {"node": None, "prompt_id": "p1"}})
    assert tr.st["phase"] == "done" and tr.st["ended"] > 0
    st = json.loads((tmp_path / "wip" / "state.json").read_text())
    assert st["phase"] == "done" and st["landed"] == ["crew/x/a_00001_.png"]
    tr.on_text({"type": "execution_error", "data": {"prompt_id": "p2", "exception_message": "OOM"}})
    assert tr.st["phase"] == "error" and tr.st["error"] == "OOM"


def test_preview_frames_both_shapes_land_in_the_preview_file(tmp_path, monkeypatch):
    rw = _mod(tmp_path, monkeypatch)
    tr = rw.Tracker()
    tr.on_text({"type": "execution_start", "data": {"prompt_id": "p1"}})
    # PREVIEW_IMAGE: [event=1][format 1=jpeg,2=png][bytes]
    assert tr.on_binary(struct.pack(">II", 1, 2) + b"PNGDATA")
    assert (tmp_path / "wip" / "preview.img").read_bytes() == b"PNGDATA"
    assert tr.st["preview_mime"] == "image/png" and tr.st["preview_seq"] == 1
    # PREVIEW_IMAGE_WITH_METADATA: [event=4][meta len][meta json][bytes]
    meta = json.dumps({"image_type": "image/jpeg", "node_id": "9"}).encode()
    assert tr.on_binary(struct.pack(">II", 4, len(meta)) + meta + b"JPG")
    assert (tmp_path / "wip" / "preview.img").read_bytes() == b"JPG"
    assert tr.st["preview_mime"] == "image/jpeg" and tr.st["preview_seq"] == 2
    assert tr.st["preview_prompt"] == "p1"
    # TEXT (3) and junk are not previews
    assert not tr.on_binary(struct.pack(">I", 3) + b"hello")
    assert not tr.on_binary(b"\x00\x01")
    st = rw.read_state()
    assert st["preview_url"] == "/render-wip.img?s=2"
    blob, mime = rw.preview_bytes()
    assert blob == b"JPG" and mime == "image/jpeg"


def test_read_state_flags_a_dead_watcher_as_stale(tmp_path, monkeypatch):
    rw = _mod(tmp_path, monkeypatch)
    (tmp_path / "wip").mkdir()
    st = rw._blank("drawing")
    st["updated"] = time.time() - rw.STALE_S - 5
    (tmp_path / "wip" / "state.json").write_text(json.dumps(st))
    got = rw.read_state()
    assert got["stale"] is True and got["preview_url"] is None
    assert rw.read_state()["phase"] == "drawing"
    (tmp_path / "wip" / "state.json").write_text("not json")
    assert rw.read_state()["phase"] == "idle"


def test_versions_are_every_output_since_engage_newest_first(tmp_path, monkeypatch):
    rw = _mod(tmp_path, monkeypatch)
    out = tmp_path / "out"
    (out / "crew" / "loop").mkdir(parents=True)
    now = time.time()
    old = out / "wallpaper_00008_.png"
    old.write_bytes(b"x"); os.utime(old, (now - 900, now - 900))
    a = out / "flux_00010_.png"; a.write_bytes(b"x"); os.utime(a, (now - 100, now - 100))
    b = out / "crew" / "loop" / "take_00001_.mp4"; b.write_bytes(b"x"); os.utime(b, (now - 20, now - 20))
    j = out / "crew" / "loop" / "take-1.job.json"; j.write_text("{}")
    since = now - 300
    rows = rw.versions(since)
    assert [r["name"] for r in rows] == ["crew/loop/take_00001_.mp4", "flux_00010_.png"]
    assert rows[0]["kind"] == "video" and rows[0]["url"] == "/render-out/crew/loop/take_00001_.mp4"
    assert rows[1]["kind"] == "still"
    assert rw.versions(0) == []                       # off Render: nothing
    (tmp_path / "mode").write_text("render\n")
    assert rw.render_since() == (tmp_path / "mode").stat().st_mtime
    (tmp_path / "mode").write_text("normal\n")
    assert rw.render_since() == 0.0


def test_version_bytes_is_allowlisted_to_the_output_tree(tmp_path, monkeypatch):
    rw = _mod(tmp_path, monkeypatch)
    out = tmp_path / "out"
    (out / "crew").mkdir()
    (out / "crew" / "v.mp4").write_bytes(b"MP4")
    (out / "s.png").write_bytes(b"PNG")
    (out / "notes.txt").write_text("no")
    (tmp_path / "secret.png").write_bytes(b"S")
    assert rw.version_bytes("crew/v.mp4") == (b"MP4", "video/mp4")
    assert rw.version_bytes("s.png") == (b"PNG", "image/png")
    assert rw.version_bytes("notes.txt") == (None, "")
    assert rw.version_bytes("../secret.png") == (None, "")
    assert rw.version_bytes("/etc/passwd") == (None, "")
    assert rw.version_bytes("") == (None, "")


def test_studio_snapshot_carries_wip_and_versions(tmp_path, monkeypatch):
    rw = _mod(tmp_path, monkeypatch)
    import render_studio as rs

    monkeypatch.setattr(rs, "STATE", tmp_path / "studio.json")
    monkeypatch.setattr(rs, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(rs.rc, "MODE_FILE", tmp_path / "mode")
    (tmp_path / "mode").write_text("render\n")
    monkeypatch.setattr(rs, "crew_feed", lambda **kw: [])
    monkeypatch.setattr(rs, "lila_card_busy", lambda: "")
    monkeypatch.setattr(rs, "studio_engine_list", lambda: [])
    (tmp_path / "out" / "new_00001_.png").write_bytes(b"x")
    d = rs.snapshot()
    assert d["render_since"] > 0
    assert d["wip"]["phase"] == "idle"
    assert [v["name"] for v in d["versions"]] == ["new_00001_.png"]
    assert "wip_error" not in d


def test_journal_since_reads_after_engage_only(monkeypatch):
    lib = str(ROOT / "window" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import panel_units as pu

    seen: list[list[str]] = []

    def fake_run(cmd, timeout=8.0):
        seen.append(cmd)
        return 0, "2026-09-18T05:00:00-0400 line", ""

    monkeypatch.setattr(pu, "_run", fake_run)
    monkeypatch.setattr(pu, "output_files", lambda u, s: [Path("/nope")])
    out = pu.read_journal([("hermes-lila-gateway", "user")], n=50, since=1789720000.0)
    assert "--since=@1789720000" in seen[0]
    assert "--user" in seen[0]
    assert out["lines"] == ["2026-09-18T05:00:00-0400 line"]   # no old file tails
    seen.clear()
    pu.read_journal([("hermes-lila-gateway", "user")], n=50)
    assert not any(a.startswith("--since") for a in seen[0])


# The reference install's Control Panel page (display/voiceonly-panel.html) is the Crissa pack's; its test is
# not here: test_panel_stage_is_wip_only
