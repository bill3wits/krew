#!/usr/bin/env python3
"""No-LLM collect from TEAM-RESEARCH-SOURCES.json into today's collect dir."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import BUNDLE_CAP, COLLECT_ROOT, DOCS, SEARXNG_URL, today  # noqa: E402
KREW_MEMORY_ROOT = os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory")  # krew: the house kept this on a data disk

UA = "krew-team-research-collect/1"
# Reddit 403-blocks house/bot UAs on this IP (measured 2026-09-06) but
# serves the subreddit's own Atom feed under a browser UA — the same
# measurement that fixed steward-architect-collect's localllama section
# (commits b7d784e5/5e014111). RSS-kind feeds carry the source's own
# measured UA; the rest keep the house UA.
REDDIT_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
# YouTube RSS 404s the house UA at 00:20 (measured 09-01/08/09) and
# serves the same channel_id at 06:50 / later GET. Same class as Reddit.
YT_UA = REDDIT_UA
SOURCES = DOCS / "TEAM-RESEARCH-SOURCES.json"
NS = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}

# Sources v2 (the User 2026-10-04), OFF until TEAM_SOURCES_V2=1:
#  - a source runs only when its `enabled` is true; a missing field is OFF, so a new install's entry
#    ships switched off and the house turns its own on in TEAM-RESEARCH-SOURCES.json;
#  - a youtube entry carries its `media:description` (whitespace-collapsed, capped) under the title,
#    so a channel whose titles do not name the repo still reaches the scout.
# Off: every source runs and youtube lines are titles only - today's behaviour byte for byte.
SOURCES_V2 = os.environ.get("TEAM_SOURCES_V2", "0").strip().lower() in ("1", "true", "yes", "on")
YT_DESC_CHARS = int(os.environ.get("TEAM_SOURCES_YT_DESC_CHARS", "300"))


def source_enabled(src: dict, v2: bool | None = None) -> bool:
    """Under v2 a source runs only when `enabled` is true (missing = off); off, every source runs."""
    if not (SOURCES_V2 if v2 is None else v2):
        return True
    return src.get("enabled") is True


def yt_description(entry, chars: int | None = None) -> str:
    """The entry's media:description, whitespace-collapsed and capped ('' when absent)."""
    d = entry.find("media:group/media:description", NS)
    text = " ".join(((d.text if d is not None else "") or "").split())
    n = YT_DESC_CHARS if chars is None else chars
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _hdr() -> dict[str, str]:
    return {"User-Agent": UA, "Accept": "*/*"}


# One blip must not cost a pinned source the whole night. 2026-09-01 00:20:
# nnt logged "rss fail HTTP Error 404" and was reported as a dead source; the
# same URL through the same helper returned the feed minutes later. YouTube
# rate-limits, and there was no second try.
RETRIES = int(os.environ.get("TEAM_COLLECT_RETRIES", "3"))
RETRY_SLEEP = float(os.environ.get("TEAM_COLLECT_RETRY_SLEEP", "2.0"))


def _fetch(url: str, timeout: int, headers: dict):
    last = None
    for attempt in range(max(1, RETRIES)):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace"), True
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            if attempt + 1 < max(1, RETRIES):
                time.sleep(RETRY_SLEEP * (attempt + 1))
    return last or "unknown", False


FEED_CACHE_DIR = Path(os.environ.get("TEAM_COLLECT_FEED_CACHE", str(COLLECT_ROOT / "feeds")))
FEED_CACHE_MAX_AGE_H = float(os.environ.get("TEAM_COLLECT_FEED_MAX_AGE_H", "36"))


def _feed_cache_write(src_id: str, text: str) -> None:
    try:
        FEED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (FEED_CACHE_DIR / f"{src_id}.xml").write_text(text, encoding="utf-8")
    except OSError:
        pass


def _feed_cache_read(src_id: str, *, max_age_h: float) -> tuple[str, float] | None:
    """(text, age_hours) of the last good fetch, or None if missing or too old."""
    p = FEED_CACHE_DIR / f"{src_id}.xml"
    try:
        age_h = (time.time() - p.stat().st_mtime) / 3600.0
        if age_h > max_age_h:
            return None
        return p.read_text(encoding="utf-8"), age_h
    except OSError:
        return None


def http_json(url: str, timeout: int = 20):
    body, ok = _fetch(url, timeout, {**_hdr(), "Accept": "application/json"})
    if not ok:
        return {"error": body}, False
    try:
        return json.loads(body), True
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}, False


def http_text(url: str, timeout: int = 20):
    return _fetch(url, timeout, _hdr())


def _fetch_backoff(
    url: str,
    headers: dict,
    timeout: int = 20,
    max_backoff: int = 2,
    backoff_on_429: bool = False,
    backoff_tokens: tuple[str, ...] | None = None,
    base_sleep: float = 20.0,
):
    """_fetch plus escalating sleep when a transient code comes back.

    Reddit 429s and YouTube 00:20 404s clear in seconds-to-minutes. The
    flat 3x-2s retry in _fetch hits the same limiter again. 403 (IP
    block) is not worth the wait — only tokens in backoff_tokens sleep.
    """
    tokens = list(backoff_tokens or ())
    if backoff_on_429 and "429" not in tokens:
        tokens.append("429")
    last_err, ok = "", False
    for attempt in range(1 + max_backoff):
        last_err, ok = _fetch(url, timeout, headers)
        hit = (not ok) and tokens and any(tok in last_err for tok in tokens)
        if ok or not hit:
            break
        sleep_s = base_sleep * (attempt + 1)
        print(
            f"  backoff {tokens} on {url}, sleeping {sleep_s:.0f}s "
            f"({attempt + 1}/{max_backoff})",
            file=sys.stderr,
        )
        time.sleep(sleep_s)
    return last_err, ok


def load_sources() -> dict:
    return json.loads(SOURCES.read_text(encoding="utf-8"))


# self-loop step 5 (the User 09-28): the trusted list stays in git; the nightly hit counters do
# not. They were written into docs/TEAM-RESEARCH-SOURCES.json every night, so the tracked file was
# always dirty and a catch-up commit carried counters. Counters (hits, misses, stale) live in
# /data now, seeded once from the doc's own numbers; the doc is read, never written, by the night.
# TEAM_SOURCES_COUNTERS_IN_DOCS=1 = the old write-back.
HITS = Path(os.environ.get("TEAM_SOURCES_HITS", (KREW_MEMORY_ROOT + "/ms/improvements/research-source-hits.json")))
COUNTERS_IN_DOCS = os.environ.get("TEAM_SOURCES_COUNTERS_IN_DOCS", "0").strip().lower() in ("1", "true", "yes", "on")


def load_hits(path: Path | None = None) -> dict:
    try:
        d = json.loads((path or HITS).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_hits(hits: dict, path: Path | None = None) -> None:
    p = path or HITS
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(hits, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def count(src: dict, ok: bool, hits: dict) -> dict:
    """Update the source's counters in `hits` (seeded from the doc's own numbers the first time)
    and return the source as the night should treat it (trust `stale` after 3 misses, unless pinned)."""
    h = hits.setdefault(str(src.get("id")), {"hits": int(src.get("hits") or 0), "misses": int(src.get("misses") or 0)})
    if ok:
        h["hits"] = int(h.get("hits") or 0) + 1
        h["misses"] = 0
    else:
        h["misses"] = int(h.get("misses") or 0) + 1
    h["stale"] = src.get("trust") != "pinned" and int(h["misses"]) >= 3
    h["updated"] = today().isoformat()
    return h


def effective(src: dict, hits: dict) -> dict:
    h = hits.get(str(src.get("id"))) or {}
    return dict(src, trust="stale") if h.get("stale") and src.get("trust") != "pinned" else src


def save_sources(data: dict) -> None:
    data["updated"] = today().isoformat()
    tmp = SOURCES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SOURCES)


def pull_one(src: dict) -> tuple[str, bool]:
    kind = src.get("kind")
    if src.get("trust") == "stale":
        return f"# {src['id']} stale — skipped\n", False
    if kind == "youtube":
        cid = src.get("channel_id")
        if not cid:
            return f"# {src['id']} missing channel_id\n", False
        rss = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
        hdr = {"User-Agent": YT_UA, "Accept": "application/atom+xml"}
        # 00:20 house UA blips; _fetch's flat 3x-2s RETRIES ride inside the
        # flock tick and the <=36h last-good-feed cache below absorbs the rest.
        # 2026-09-16 (t_fa1ef213/t_243ceae8): the persistent-404 state (YouTube
        # downed the endpoint for this channel) made the old 20/40/60 backoff
        # 120s of guaranteed-wasted wait on EVERY 00:20 tick, and the 09-15
        # ask's no-retry-on-5xx path never fired for 404 (backoff_tokens
        # carried 404). YouTube 404 now behaves like 5xx: honest fail, no
        # backoff.
        text, ok = _fetch(
            rss,
            timeout=20,
            headers=hdr,
        )
        cached_note = ""
        if ok:
            _feed_cache_write(src["id"], text)
        else:
            # 00:20 404s while 06:50 and the rest of the day are 200 (measured
            # 09-01 → 09-12, six nights). A pinned source must not go dark for a
            # rate-limit window: read the last good feed and say so in the header.
            cached = _feed_cache_read(src["id"], max_age_h=FEED_CACHE_MAX_AGE_H)
            if cached is None:
                return f"# {src['id']} rss fail {text}\n", False
            text, age_h = cached
            cached_note = f" (cached feed {age_h:.0f} h old — live fetch failed)"
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            return f"# {src['id']} xml {e}\n", False
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        lines = [f"# {src['id']} {src.get('name')}{cached_note}", ""]
        n = 0
        for entry in root.findall("atom:entry", NS):
            title = (entry.findtext("atom:title", default="", namespaces=NS) or "").strip()
            link = entry.find("atom:link", NS)
            href = link.get("href") if link is not None else ""
            published = entry.findtext("atom:published", default="", namespaces=NS) or ""
            try:
                pub = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                pub = datetime.now(timezone.utc)
            if pub < cutoff:
                continue
            n += 1
            lines.append(f"- {published[:16]} {title} {href}")
            if SOURCES_V2:
                desc = yt_description(entry)
                if desc:
                    lines.append(f"  > {desc}")
        if n == 0:
            lines.append("_none in 48h_")
        return "\n".join(lines) + "\n", True
    if kind == "hn":
        # Algolia has NO boolean OR: "a OR b OR c" is searched as a literal
        # phrase and matches nothing. The pinned query asked for four topics
        # in one string, so hn returned 0 hits every night while each term on
        # its own returns 8. Split the intent and merge, rather than dumbing
        # the source list down to a single keyword.
        q = src.get("query") or "local llm"
        terms = [t.strip() for t in re.split(r"\s+OR\s+", q) if t.strip()] or [q]
        lines = [f"# {src['id']} hn {' | '.join(terms)}", ""]
        seen: set[str] = set()
        per = max(2, 8 // len(terms))
        any_ok = False
        for term in terms:
            qs = urllib.parse.urlencode(
                {"query": term, "tags": "story", "hitsPerPage": per}
            )
            data, ok = http_json(f"https://hn.algolia.com/api/v1/search?{qs}")
            if not ok or not isinstance(data, dict):
                continue
            any_ok = True
            for hit in data.get("hits") or []:
                oid = str(hit.get("objectID"))
                if oid in seen:
                    continue
                seen.add(oid)
                lines.append(
                    f"- {hit.get('title')} "
                    f"https://news.ycombinator.com/item?id={oid}"
                )
        if not any_ok:
            return "\n".join(lines) + "_fail_\n", False
        if not seen:
            lines.append("_no stories matched_")
        return "\n".join(lines) + "\n", True

    if kind == "rss":
        # Generic feed. syntax.fm is unreachable through searx — every query
        # returns 0 results on the domain — but it publishes a normal feed.
        url = src.get("url") or ""
        if not url:
            return f"# {src['id']} missing url\n", False
        # Per-source measured UA wins (reddit rss needs a browser UA;
        # the house UA 403s on this IP — measured 2026-09-06).
        hdr = {"User-Agent": src.get("ua") or REDDIT_UA, "Accept": "*/*"}
        # Reddit rate-limits hot (HTTP 429 clears in seconds) — back the
        # generic fetch with backoff, not a flat 3x-2s retry. One backoff
        # round (20s) keeps the flock-held cron tick bounded.
        text, ok = _fetch_backoff(url, hdr, backoff_on_429=True, max_backoff=1)
        if not ok:
            return f"# {src['id']} rss fail {text}\n", False
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            return f"# {src['id']} xml {e}\n", False
        lines = [f"# {src['id']} rss {src.get('name') or url}", ""]
        items = root.findall(".//item") or root.findall("atom:entry", NS)
        # 72h staleness filter (same as the architect pipeline): feeds are
        # not guaranteed to be fresh — a stale item with a dead-looking
        # date reads as junk to the scout. Visible drop count, never silent.
        cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
        kept, dropped = [], 0
        for item in items:
            title = (item.findtext("title") or "").strip()
            if not title:
                title = (item.findtext("atom:title", default="", namespaces=NS) or "").strip()
            link = (item.findtext("link") or "").strip()
            if not link:
                le = item.find("atom:link", NS)
                link = le.get("href") if le is not None else ""
            ts = (item.findtext("published")
                  or item.findtext("atom:published", namespaces=NS)
                  or item.findtext("pubDate")
                  or "")
            pub = None
            try:
                pub = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                try:
                    from email.utils import parsedate_to_datetime
                    pub = parsedate_to_datetime(ts)
                except (TypeError, ValueError):
                    pub = None
            if pub is not None:
                if pub.tzinfo is None:  # naive feed date — assume UTC
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < cutoff:
                    dropped += 1
                    continue
            kept.append(f"- {title} {link}".rstrip())
        lines.extend(kept[:8])
        if not items:
            lines.append("_no items_")
        elif not kept:
            lines.append("_no posts in last 72h (honest empty — pass)_")
        if dropped:
            lines.append(f"_{dropped} older posts dropped by 72h filter_")
        return "\n".join(lines) + "\n", True
    if kind in {"reddit", "web", "podcast"}:
        q = src.get("query") or src.get("name") or src["id"]
        qs = urllib.parse.urlencode({"q": q, "format": "json"})
        data, ok = http_json(f"{SEARXNG_URL.rstrip('/')}/search?{qs}")
        lines = [f"# {src['id']} searx {q}", ""]
        if not ok or not isinstance(data, dict):
            return "\n".join(lines) + f"_fail_\n", False
        for r in (data.get("results") or [])[:8]:
            lines.append(f"- {r.get('title')} {r.get('url')}")
        return "\n".join(lines) + "\n", True
    if kind == "github":
        url = (src.get("url") or "").rstrip("/")
        name = url.split("github.com/")[-1]
        data, ok = http_json(f"https://api.github.com/repos/{name}")
        if not ok or not isinstance(data, dict):
            return f"# {src['id']} github fail\n", False
        rel, rok = http_json(f"https://api.github.com/repos/{name}/releases?per_page=3")
        lines = [
            f"# {src['id']} github.com/{name}",
            f"stars={data.get('stargazers_count')} pushed={data.get('pushed_at')}",
            "",
        ]
        if rok and isinstance(rel, list):
            for r in rel[:3]:
                lines.append(f"- release {r.get('tag_name')} {r.get('html_url')}")
        return "\n".join(lines) + "\n", True
    if kind == "local":
        p = Path(os.path.expanduser(src.get("path") or ""))
        if not p.exists():
            return f"# {src['id']} missing path {p}\n", False
        files = []
        if p.is_dir():
            files = sorted(str(x.relative_to(p)) for x in p.rglob("*.md"))[:40]
        lines = [f"# {src['id']} local {p}", ""] + [f"- {f}" for f in files]
        return "\n".join(lines) + "\n", True
    return f"# {src['id']} unknown kind {kind}\n", False


def main() -> int:
    dest = COLLECT_ROOT / today().isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    data = load_sources()
    chunks = [f"# team-research collect {today().isoformat()}\n"]
    budget = BUNDLE_CAP
    changed = False
    hits = load_hits()
    for src in data.get("sources") or []:
        if not source_enabled(src):
            chunks.append(f"# {src.get('id')} disabled (enabled is not true) — skipped\n")
            continue
        body, ok = pull_one(src if COUNTERS_IN_DOCS else effective(src, hits))
        if len(body) > 3500:
            body = cut(body, 3500, "the source itself has the rest: " + str(src.get("url") or src.get("id"))) + "\n"
        if budget - len(body) < 0:
            chunks.append("_cap reached_\n")
            break
        chunks.append(body)
        budget -= len(body)
        if not COUNTERS_IN_DOCS:
            count(src, ok, hits)
            continue
        if ok:
            src["hits"] = int(src.get("hits") or 0) + 1
            src["misses"] = 0
        else:
            src["misses"] = int(src.get("misses") or 0) + 1
            if src.get("trust") != "pinned" and src["misses"] >= 3:
                src["trust"] = "stale"
        changed = True
    text = "\n".join(chunks)
    if len(text.encode()) > BUNDLE_CAP:
        text = text[:BUNDLE_CAP] + "\n_truncated_\n"
    (dest / "team.md").write_text(text, encoding="utf-8")
    if changed:
        save_sources(data)
    if not COUNTERS_IN_DOCS:
        save_hits(hits)
    print(dest / "team.md", len(text.encode()), "B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
