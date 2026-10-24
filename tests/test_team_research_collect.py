"""00:20 team collector: YouTube RSS 404 is an honest fail (no backoff), not a 3x-2s blip."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "team_research_collect", ROOT / "crew" / "scripts" / "team-research-collect.py"
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>clip</title>
    <link href="https://www.youtube.com/watch?v=abc"/>
    <published>2099-01-01T00:00:00+00:00</published>
  </entry>
</feed>
"""


def test_youtube_404_is_an_honest_fail_no_backoff(monkeypatch):
    """t_243ceae8: YouTube 404 (persistent endpoint-down state) must NOT
    burn the old 20/40/60 backoff — honest fail immediately, like 5xx."""
    fetch_calls: list[int] = []
    sleeps: list[float] = []

    def fake_fetch(url, timeout, headers):
        fetch_calls.append(1)
        return "HTTPError: HTTP Error 404: Not Found", False

    monkeypatch.setattr(mod, "_fetch", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    text, ok = mod.pull_one(
        {
            "id": "nnt",
            "kind": "youtube",
            "name": "The Next New Thing",
            "channel_id": "UCNZEktrsM5oJZ-MK4jKPMOQ",
        }
    )
    assert not ok
    assert "rss fail" in text
    # No backoff sleeps (old test asserted sleeps == [20.0, 40.0]).
    assert sleeps == []
    # pull_one itself makes exactly one fetch attempt (no pull_one-level retry).
    assert fetch_calls == [1]


import pytest


@pytest.fixture(autouse=True)
def _own_feed_cache(tmp_path, monkeypatch):
    """Never read or write the live last-good-feed cache from a test."""
    monkeypatch.setattr(mod, "FEED_CACHE_DIR", tmp_path / "feed-cache")


def test_youtube_403_does_not_sleep(monkeypatch):
    sleeps: list[float] = []

    def fake_fetch(url, timeout, headers):
        return "HTTPError: HTTP Error 403: Forbidden", False

    monkeypatch.setattr(mod, "_fetch", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
    text, ok = mod.pull_one(
        {"id": "nnt", "kind": "youtube", "name": "NNT", "channel_id": "UCX"}
    )
    assert not ok
    assert "rss fail" in text
    assert sleeps == []


# --- 2026-09-12: a pinned feed that 404s at 00:20 reads its last good copy ----

_ATOM = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>NNT: repo review</title>'
         '<link href="https://youtu.be/x"/><published>2099-01-01T00:00:00+00:00</published></entry></feed>')


def test_youtube_falls_back_to_the_last_good_feed(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "FEED_CACHE_DIR", tmp_path / "feeds")
    src = {"id": "nnt", "kind": "youtube", "channel_id": "UCx", "name": "Next New Thing"}
    monkeypatch.setattr(mod, "_fetch", lambda *a, **k: (_ATOM, True))
    text, ok = mod.pull_one(src)
    assert ok and "repo review" in text and "cached feed" not in text
    assert (tmp_path / "feeds" / "nnt.xml").read_text() == _ATOM
    monkeypatch.setattr(mod, "_fetch", lambda *a, **k: ("HTTPError: HTTP Error 404: Not Found", False))
    text, ok = mod.pull_one(src)
    assert ok and "repo review" in text and "cached feed 0 h old" in text


def test_youtube_cache_too_old_is_a_real_failure(tmp_path, monkeypatch):
    import os, time
    monkeypatch.setattr(mod, "FEED_CACHE_DIR", tmp_path / "feeds")
    (tmp_path / "feeds").mkdir()
    old = tmp_path / "feeds" / "nnt.xml"
    old.write_text(_ATOM)
    os.utime(old, (time.time() - 48 * 3600, time.time() - 48 * 3600))
    monkeypatch.setattr(mod, "_fetch_backoff", lambda *a, **k: ("HTTPError: HTTP Error 404: Not Found", False))
    src = {"id": "nnt", "kind": "youtube", "channel_id": "UCx", "name": "Next New Thing"}
    text, ok = mod.pull_one(src)
    assert not ok and "rss fail" in text


# ── sources v2 (the User 2026-10-04): enabled field + youtube descriptions, OFF by default ──────────
FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:media="http://search.yahoo.com/mrss/"><title>AI Search</title>
<entry><title>Five new open agents</title><link href="https://www.youtube.com/watch?v=x"/>
<published>{ts}</published><media:group><media:description>Repos:
 github.com/a/b   and github.com/c/d  plus more words</media:description></media:group></entry></feed>"""


def _yt(monkeypatch, v2):
    from datetime import datetime, timezone
    monkeypatch.setattr(mod, "SOURCES_V2", v2)
    monkeypatch.setattr(mod, "_fetch", lambda url, timeout, headers: (FEED.format(ts=datetime.now(timezone.utc).isoformat()), True))
    monkeypatch.setattr(mod, "_feed_cache_write", lambda *a, **k: None)
    return mod.pull_one({"id": "ai-search", "kind": "youtube", "name": "AI Search", "channel_id": "UCx"})


def test_off_youtube_is_titles_only(monkeypatch):
    body, ok = _yt(monkeypatch, False)
    assert ok and "Five new open agents" in body and "github.com/a/b" not in body


def test_on_youtube_carries_the_description_collapsed(monkeypatch):
    body, ok = _yt(monkeypatch, True)
    assert ok and "  > Repos: github.com/a/b and github.com/c/d plus more words" in body


def test_the_description_is_capped(monkeypatch):
    import xml.etree.ElementTree as ET
    e = ET.fromstring(FEED.format(ts="2026-10-04T00:00:00+00:00")).find("atom:entry", mod.NS)
    assert mod.yt_description(e, chars=10).endswith("…") and len(mod.yt_description(e, chars=10)) == 11


def test_enabled_is_honoured_only_under_v2():
    assert mod.source_enabled({}, v2=False) and mod.source_enabled({"enabled": False}, v2=False)
    assert not mod.source_enabled({}, v2=True), "a missing enabled is OFF under v2"
    assert not mod.source_enabled({"enabled": False}, v2=True) and mod.source_enabled({"enabled": True}, v2=True)


def test_the_krew_turns_its_own_sources_on():
    import json
    data = json.loads((Path(__file__).resolve().parents[1] / "crew" / "config" / "team-research-sources.json").read_text())   # krew: the shipped sources file
    srcs = {s["id"]: s for s in data["sources"]}
    assert all(s.get("enabled") is True for s in srcs.values()), "every house source is on"
    ai = srcs["ai-search"]
    assert ai["kind"] == "youtube" and ai["channel_id"] == "UCIgnGlGkVRhd4qNFcEwLL4A"
