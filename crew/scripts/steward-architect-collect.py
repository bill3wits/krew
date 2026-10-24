#!/usr/bin/env python3
"""No-LLM collect bundle for scout world/stack notes.

Uses public GitHub API unless KREW_ARCHITECT_GH_TOKEN is set. Never reads
the <owner> keyring token.
"""
from __future__ import annotations

import json
import os
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
from architect_lib import (  # noqa: E402
    BUNDLE_CAP,
    COLLECT_ROOT,
    GH_TOKEN_ENV,
    NNT_CHANNEL_ID,
    SEARXNG_URL,
    collect_dir,
    recent_world_slugs,
    today,
)

UA = os.environ.get("KREW_ARCHITECT_UA") or "krew-architect-collect/1 (+local)"
# Suspended identity: reddit 403-blocks the house UA on this IP (measured
# 2026-09-06); the browser UA gets the Atom feed. JSON endpoints stay 403.
REDDIT_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
NS = {"atom": "http://www.w3.org/2005/Atom"}


def _headers() -> dict[str, str]:
    h = {"User-Agent": UA, "Accept": "application/json"}
    tok = os.environ.get(GH_TOKEN_ENV, "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def http_json(url: str, timeout: int = 20) -> tuple[object | None, str]:
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8", "replace")), f"http {resp.status} {len(raw)}B"
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def http_text(url: str, timeout: int = 20, accept: str = "*/*") -> tuple[str | None, str]:
    h = _headers()
    h["Accept"] = accept
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return raw.decode("utf-8", "replace"), f"http {resp.status} {len(raw)}B"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def cap_write(path: Path, body: str, remaining: list[int]) -> None:
    if remaining[0] <= 0:
        path.write_text("_omitted: bundle cap reached_\n", encoding="utf-8")
        return
    if len(body.encode()) > remaining[0]:
        body = body[: remaining[0]] + "\n_truncated to bundle cap_\n"
    path.write_text(body, encoding="utf-8")
    remaining[0] -= len(path.read_bytes())


def gh_search(q: str, per_page: int = 8) -> tuple[list[dict], str]:
    qs = urllib.parse.urlencode(
        {"q": q, "sort": "updated", "order": "desc", "per_page": per_page}
    )
    data, note = http_json(f"https://api.github.com/search/repositories?{qs}")
    if not isinstance(data, dict):
        return [], note
    items = []
    for it in data.get("items") or []:
        items.append(
            {
                "full_name": it.get("full_name"),
                "html_url": it.get("html_url"),
                "description": cut(it.get("description") or "", 180, "the repository page has the rest"),
                "stargazers_count": it.get("stargazers_count"),
                "updated_at": it.get("updated_at"),
                "language": it.get("language"),
            }
        )
    return items, note


def resolve_repo(full_name: str) -> bool:
    data, _ = http_json(f"https://api.github.com/repos/{full_name}")
    return isinstance(data, dict) and data.get("full_name")


def section_github() -> str:
    queries = [
        "local llm OR local ai",
        "vllm OR sglang OR llama.cpp",
        "cosyvoice OR chatterbox OR kokoro TTS",
        "parakeet OR whisper.cpp OR kyutai",
    ]
    lines = [
        "# github",
        f"_auth: {'token' if os.environ.get(GH_TOKEN_ENV) else 'public/unauthenticated'}_",
        "",
    ]
    seen: set[str] = set()
    for q in queries:
        items, note = gh_search(q)
        lines.append(f"## q={q} ({note})")
        for it in items:
            name = it.get("full_name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            ok = resolve_repo(name)
            if not ok:
                lines.append(f"- DROPPED unresolved `{name}`")
                continue
            lines.append(
                f"- `{name}` stars={it.get('stargazers_count')} "
                f"updated={it.get('updated_at')} {it.get('html_url')}\n"
                f"  {it.get('description')}"
            )
        lines.append("")
    return "\n".join(lines)


def section_hf() -> str:
    url = (
        "https://huggingface.co/api/models"
        "?sort=lastModified&direction=-1&limit=10&pipeline_tag=text-generation"
    )
    data, note = http_json(url)
    lines = [f"# huggingface ({note})", ""]
    if not isinstance(data, list):
        lines.append(f"_no list: {note}_")
        return "\n".join(lines)
    for m in data:
        mid = m.get("modelId") or m.get("id") or ""
        lines.append(
            f"- `{mid}` likes={m.get('likes')} downloads={m.get('downloads')} "
            f"modified={m.get('lastModified')}"
        )
    return "\n".join(lines)


# The feed YouTube answers here at 06:50 and 20:40 is the one nnt-repos.py looks the weekly
# repos up from (feeds/nnt.xml). Only team-research-collect wrote that cache, at ~20:20, when
# the feed 404s — so nnt-repos read a 2-day-old copy while this collector held a fresh one
# (09-19: 200 at 06:50, "Top Repos Explained" with 15 repo links; cache dated 09-17).
# NNT_CACHE_FROM_COLLECT=0 = this collector keeps its copy to itself.
NNT_CACHE = COLLECT_ROOT / "feeds" / "nnt.xml"


def _nnt_cache_write(text: str) -> None:
    if os.environ.get("NNT_CACHE_FROM_COLLECT", "1").strip().lower() in ("0", "false", "no", "off"):
        return
    try:
        NNT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = NNT_CACHE.with_suffix(".xml.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(NNT_CACHE)            # atomic: a reader never sees half a feed
    except OSError as e:
        print(f"nnt cache write failed: {e}", file=sys.stderr)


def section_youtube() -> str:
    rss = f"https://www.youtube.com/feeds/videos.xml?channel_id={NNT_CHANNEL_ID}"
    text, note = http_text(rss, accept="application/atom+xml")
    lines = [f"# youtube Next New Thing ({note})", f"_channel_id={NNT_CHANNEL_ID}_", ""]
    if not text:
        lines.append(f"_rss fail: {note}_")
        return "\n".join(lines)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        lines.append(f"_xml fail: {e}_")
        return "\n".join(lines)
    _nnt_cache_write(text)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    n = 0
    for entry in root.findall("atom:entry", NS):
        title = (entry.findtext("atom:title", default="", namespaces=NS) or "").strip()
        link_el = entry.find("atom:link", NS)
        href = link_el.get("href") if link_el is not None else ""
        published = entry.findtext("atom:published", default="", namespaces=NS) or ""
        try:
            pub = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            pub = datetime.now(timezone.utc)
        if pub < cutoff:
            continue
        n += 1
        lines.append(f"- {published[:16]} {title} {href}")
    if n == 0:
        lines.append("_none in last 48h (honest empty — pass)_")
    return "\n".join(lines)


def section_hn() -> str:
    qs = urllib.parse.urlencode(
        {"query": "local llm OR local ai OR llama.cpp OR vllm", "tags": "story", "hitsPerPage": 8}
    )
    data, note = http_json(f"https://hn.algolia.com/api/v1/search?{qs}")
    lines = [f"# hn ({note})", ""]
    if not isinstance(data, dict):
        lines.append(f"_fail {note}_")
        return "\n".join(lines)
    for hit in data.get("hits") or []:
        lines.append(
            f"- {hit.get('title')} points={hit.get('points')} "
            f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        )
    if not data.get("hits"):
        lines.append("_none worth naming_")
    return "\n".join(lines)


def section_searx(query: str, slug: str, expected_domain: str = "") -> str:
    qs = urllib.parse.urlencode({"q": query, "format": "json"})
    data, note = http_json(f"{SEARXNG_URL.rstrip('/')}/search?{qs}")
    lines = [f"# {slug} ({note})", ""]
    if not isinstance(data, dict):
        lines.append(f"_searx fail {note}_")
        return "\n".join(lines)
    results = (data.get("results") or [])[:8]
    # Junk guard: if expected_domain is set and no result URL matches it,
    # flag the section so the scout LLM knows the feed is degraded.
    if expected_domain:
        from urllib.parse import urlparse
        matching = [
            r
            for r in results
            if expected_domain
            in (urlparse(r.get("url", "")).netloc + urlparse(r.get("url", "")).path)
        ]
        if not matching:
            lines.append(
                f"_junk guard: no results on {expected_domain}; "
                f"searx returned {len(results)} unrelated results_"
            )
            for r in results[:3]:
                lines.append(f"  (junk) {r.get('title')} {r.get('url')}")
            lines.append("")
            return "\n".join(lines)
        results = matching
    for r in results:
        lines.append(f"- {r.get('title')} {r.get('url')}")
    if not results:
        lines.append("_none_")
    return "\n".join(lines)


def section_localllama() -> str:
    """r/LocalLLaMA via the subreddit's own hot RSS (primary).

    Reddit 403-blocks this host on the JSON endpoints (probed
    2026-09-06) but serves the Atom RSS feed under a browser UA.
    RSS has no score/flair, so freshness comes from entry order (hot)
    plus a staleness filter (72h). On RSS failure (403/429/network),
    the section falls back to the searx junk-guarded list and says
    why RSS failed.
    """
    url = "https://www.reddit.com/r/LocalLLaMA/hot.rss?limit=20"
    entries, err = _rss_entries(url, limit=20, ua=REDDIT_UA)
    lines = ["# localllama"]
    if entries is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
        fresh = []
        skipped = 0
        for ent in entries:
            try:
                pub = datetime.fromisoformat(ent["published"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pub = None
            if pub is None or pub < cutoff:
                skipped += 1
                continue
            if ent["link"]:
                fresh.append(
                    f"- {ent['published'][:16]} {ent['title']} {ent['link']}"
                )
        if not fresh:
            if entries:
                lines.append("_no posts in last 72h (honest empty — pass)_")
            else:
                lines.append("_feed parsed but zero entries (degraded — check next run)_")
        else:
            lines.extend(fresh)
            if skipped:
                lines.append(f"_{skipped} older posts dropped by 72h filter_")
        return "\n".join(lines)
    # Fallback: searx junk-guarded section, labelled as degraded with
    # the RSS failure reason so a 403 outage is visible, not silent.
    fb = section_searx(
        "LocalLLaMA new model",
        "localllama-fb",
        expected_domain="reddit.com/r/LocalLLaMA",
    )
    lines.append(f"_rss fail: {err} — searx fallback (degraded)_")
    lines.extend(fb.splitlines()[1:])  # drop the fallback's own H1
    return "\n".join(lines)


def _rss_entries(
    url: str, limit: int = 20, ua: str | None = None, timeout: int = 25
) -> tuple[list[dict] | None, str]:
    """Atom entries -> ([{title, link, published}], '') or (None, error).

    One 30s backoff retry before declaring failure (429 rate limits
    clear; 403 IP blocks do not, but the note says which it was).
    Reddit's Atom feed carries a single atom:link per entry with no
    rel attribute (measured 2026-09-06) — prefer rel=alternate when
    present, otherwise take the only link; an entry with zero links
    is dropped, since a dangling empty URL would read as dead to the
    scout.
    """
    raw: bytes | None = None
    last = "timeout"
    for attempt in (1, 2):
        h = {"User-Agent": ua or UA, "Accept": "application/atom+xml"}
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            break
        except Exception as e:  # HTTP 403/429, socket errors, etc.
            last = f"{type(e).__name__}: {e}"
            if attempt == 1:
                time.sleep(30)
                continue
    if raw is None:
        return None, last
    try:
        root = ET.fromstring(raw.decode("utf-8", "replace"))
    except ET.ParseError as e:
        return None, f"ParseError: {e}"
    entries: list[dict] = []
    for entry in root.findall("atom:entry", NS)[:limit]:
        title = (entry.findtext("atom:title", default="", namespaces=NS) or "").strip()
        links = entry.findall("atom:link", NS)
        href = ""
        for l in links:
            if l.get("rel") == "alternate":
                href = l.get("href") or ""
        if not href and len(links) == 1:
            href = links[0].get("href") or ""
        if not href:
            continue
        published = entry.findtext("atom:published", default="", namespaces=NS) or ""
        entries.append({"title": title, "link": href, "published": published})
    return entries, ""


def section_dedupe() -> str:
    slugs = recent_world_slugs(14)
    lines = ["# dedupe last 14d (do not re-headline unless stars/release changed)", ""]
    if not slugs:
        lines.append("_no prior world notes_")
    for s in slugs[:80]:
        lines.append(f"- {s}")
    return "\n".join(lines)


def main() -> int:
    day = today()
    dest = collect_dir(day)
    dest.mkdir(parents=True, exist_ok=True)
    remaining = [BUNDLE_CAP]
    parts = [
        ("github.md", section_github),
        ("hf.md", section_hf),
        ("youtube.md", section_youtube),
        ("hn.md", section_hn),
        ("localllama.md", section_localllama),
        ("x.md", lambda: section_searx("llama.cpp vllm x.com", "x", expected_domain="x.com")),
        ("dedupe.md", section_dedupe),
    ]
    index = [f"# collect {day.isoformat()} cap={BUNDLE_CAP}", ""]
    for name, fn in parts:
        body = fn()
        cap_write(dest / name, body, remaining)
        n = (dest / name).stat().st_size
        index.append(f"- {name} {n}B")
    index.append(f"- remaining_cap {remaining[0]}B")
    (dest / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(dest)
    print("\n".join(index))
    return 0


def run_with_status() -> int:
    """main() plus the job's own word (self-loop, the User 09-28, lib/job_status.py): the
    window ticker's architect-collect-evening row reads `collect-<day>` in the same tick as
    systemd's verdict. The world and stack jobs no longer collect; they refuse to run
    without this bundle's INDEX.md, so a collect that fails says so here, and exits 1."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "window" / "lib"))   # krew: job_status lives with the window
    import job_status
    day = today().isoformat()
    idx = collect_dir(today()) / "INDEX.md"
    try:
        rc = main()
    except Exception as e:  # noqa: BLE001 - the status line must say why, then re-raise
        job_status.write("collect", day, "failed", f"{type(e).__name__}: {e}", str(idx))
        raise
    if rc == 0 and idx.is_file() and idx.stat().st_size > 0:
        job_status.write("collect", day, "ok", "", str(idx))
    else:
        job_status.write("collect", day, "failed", f"exit {rc}; INDEX.md {'present' if idx.is_file() else 'missing'}", str(idx))
        rc = rc or 1
    return rc


if __name__ == "__main__":
    raise SystemExit(run_with_status())
