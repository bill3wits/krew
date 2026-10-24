#!/usr/bin/env python3
"""nnt-repos — the GitHub repos The Next New Thing actually features, from the feed.

the User 2026-09-17: "Let's ignite that loop now. Ensure the source is working."
The source worked (the feed answers) but was read half: the scout skill said
"the title carries the repo", and NNT titles do not ("Amazing: FREE AI Agent from
Meta"). The repos are in each video's DESCRIPTION (`media:description` in the same
Atom feed): 09-16 "Why is everyone using these?" lists six. So the scout picked
videos, never repos, and "repo lookup pending" never happened on a 3-a-night meter.

This reads the feed the collector already cached (architect-collect/feeds/nnt.xml,
no second fetch), takes the last 8 days of videos, pulls github.com/<owner>/<repo>
links out of the descriptions, and asks the GitHub API for each repo's one-line
description, stars, last push, licence and language — free, not the Firecrawl
meter. Writes architect-collect/<day>/nnt-repos.{md,json} and replaces the
"## NNT repos" section of that day's team.md, which the scout reads.

No LLM. Honest when the feed is missing or stale: the section says so.
Day = architect_lib.today() (KREW_DAY from the window ticker, else the calendar).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import COLLECT_ROOT, today  # noqa: E402

FEED = Path(os.environ.get("NNT_FEED_XML", str(COLLECT_ROOT / "feeds" / "nnt.xml")))
DAYS = int(os.environ.get("NNT_REPOS_DAYS", "8"))
# Repo dedupe against the pick log (t_68837b13): the pick log = `nnt-pick:` lines in
# the inbox team-research notes + the agreed ledger (proposals.jsonl). A repo the
# pick log named inside the window is marked seen in this list and skipped by the
# ranker's re-filing (team-eval-rank.py). NNT_SEEN_REPOS=0 = the old list.
SEEN = os.environ.get("NNT_SEEN_REPOS", "1").strip().lower() not in ("0", "false", "no", "off")
MAX_REPOS = int(os.environ.get("NNT_REPOS_MAX", "30"))   # unauthenticated GitHub API: 60 an hour
STALE_H = 36
NS = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/"}
REPO_RE = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)")
NOT_REPOS = {"sponsors", "orgs", "topics", "features", "marketplace", "collections", "settings", "about"}
SECTION = "## NNT repos (from the video descriptions)"


def videos(xml_text: str, *, now: dt.datetime | None = None, days: int = DAYS) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", NS):
        pub_raw = (e.findtext("a:published", default="", namespaces=NS) or "").strip()
        try:
            pub = dt.datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - pub).days > days:
            continue
        link = e.find("a:link", NS)
        desc = e.findtext("m:group/m:description", default="", namespaces=NS) or ""
        out.append({"title": (e.findtext("a:title", default="", namespaces=NS) or "").strip(),
                    "url": link.get("href") if link is not None else "",
                    "published": pub.date().isoformat(), "repos": repos_in(desc)})
    return out


def repos_in(text: str) -> list[str]:
    seen: list[str] = []
    for owner, name in REPO_RE.findall(text or ""):
        name = re.sub(r"(\.git)?[.,;:)]*$", "", name)
        if owner.lower() in NOT_REPOS or not name:
            continue
        full = f"{owner}/{name}"
        if full.lower() not in {s.lower() for s in seen}:
            seen.append(full)
    return seen


def github_meta(full: str, *, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{full}",
                                 headers={"Accept": "application/vnd.github+json",
                                          "User-Agent": "<repo-worktree>-repos"})
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"repo": full, "error": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"repo": full, "error": type(e).__name__}
    return {"repo": d.get("full_name") or full, "about": (d.get("description") or "")[:200],
            "stars": d.get("stargazers_count"), "pushed": (d.get("pushed_at") or "")[:10],
            "license": ((d.get("license") or {}).get("spdx_id") or ""), "language": d.get("language") or "",
            "archived": bool(d.get("archived"))}


def render(day: str, vids: list[dict], metas: dict[str, dict], feed_note: str,
           seen: dict[str, str] | None = None) -> str:
    seen = seen or {}
    lines = [SECTION, "", f"_{feed_note}. Repos linked in each video's description; stars / last push / licence "
             "from the GitHub API (free — not the extraction meter). Pick from these: `nnt-pick: <owner/repo> — …`. "
             "`(seen …)` = the pick log named it inside the window — a repeat, not new._", ""]
    if not vids:
        lines.append("- no NNT videos in the last %d days of the feed" % DAYS)
    for v in vids:
        lines.append(f"- **{v['published']} · {v['title']}** — {v['url']}")
        if not v["repos"]:
            lines.append("  - (no GitHub link in the description)")
        for full in v["repos"]:
            m = metas.get(full) or {"repo": full}
            if m.get("error"):
                lines.append(f"  - `{full}` — https://github.com/{full} — metadata {m['error']}")
                continue
            key = m.get("repo") or full
            sday = seen.get(key.lower()) or seen.get(full.lower())
            tag = f" (seen {sday})" if sday else ""
            bits = [f"★{m.get('stars')}" if m.get("stars") is not None else "",
                    f"pushed {m['pushed']}" if m.get("pushed") else "", m.get("license") or "",
                    m.get("language") or "", "ARCHIVED" if m.get("archived") else ""]
            lines.append(f"  - `{key}` — https://github.com/{key} — "
                         f"{m.get('about') or '(no description)'} ({', '.join(b for b in bits if b)}){tag}")
    return "\n".join(lines) + "\n"


def _nnt_seen():
    """The nnt-seen module (its filename keeps its hyphen: importlib, not import)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("nnt_seen", str(Path(__file__).resolve().parent / "nnt-seen.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seen_slugs() -> dict[str, str]:
    """{slug: newest day} the pick log named inside the window. Missing module
    or unreadable store: nothing seen (the old list), never a crash."""
    if not SEEN:
        return {}
    try:
        nnt_seen = _nnt_seen()
        seen = nnt_seen.seen_map(window=DAYS)
        for slug, day_named in nnt_seen.pick_log_window(DAYS).items():
            seen[slug] = max(seen.get(slug, ""), day_named)
        return seen
    except Exception:  # noqa: BLE001 — dedupe must never sink the list
        return {}


def replace_section(team_md: str, section: str) -> str:
    """Put the section in team.md once: replace a previous copy, else append."""
    i = team_md.find(SECTION)
    if i < 0:
        return team_md.rstrip("\n") + "\n\n" + section
    j = team_md.find("\n## ", i + len(SECTION))
    return team_md[:i] + section + (team_md[j + 1:] if j >= 0 else "")


def main(argv: list[str] | None = None) -> int:
    day = today().isoformat()
    dest = COLLECT_ROOT / day
    dest.mkdir(parents=True, exist_ok=True)
    if not FEED.is_file():
        vids, note = [], f"NNT feed cache missing ({FEED}) — the collector's fetch failed"
    else:
        age_h = (time.time() - FEED.stat().st_mtime) / 3600
        note = f"NNT feed cached {age_h:.0f} h ago" + (" — STALE, treat as last known" if age_h > STALE_H else "")
        try:
            vids = videos(FEED.read_text(encoding="utf-8", errors="replace"))
        except ET.ParseError as e:
            vids, note = [], f"NNT feed cache unreadable ({e})"
    metas: dict[str, dict] = {}
    for v in vids:
        for full in v["repos"]:
            if full not in metas and len(metas) < MAX_REPOS:
                metas[full] = github_meta(full)
    # Dedupe against the pick log (t_68837b13): every repo the pick log named inside
    # the window is tagged seen here, and the pick log is re-read (not just the
    # store) so a slug whose note outlived the store is not treated as new again.
    seen = seen_slugs()
    for m in metas.values():
        if not m.get("error"):
            slug = (m.get("repo") or "").lower()
            if slug in seen:
                m["seen"] = seen[slug]
    section = render(day, vids, metas, note, seen)
    (dest / "nnt-repos.md").write_text(section, encoding="utf-8")
    (dest / "nnt-repos.json").write_text(json.dumps({"day": day, "note": note, "videos": vids,
                                                     "repos": metas}, indent=2) + "\n", encoding="utf-8")
    team = dest / "team.md"
    if team.is_file():
        team.write_text(replace_section(team.read_text(encoding="utf-8", errors="replace"), section), encoding="utf-8")
    # the night's PICKS are what feed the store (the pick log's inbox half);
    # without the scout's note there is nothing new to record and a no-op
    # run appends nothing. The ranker's nnt-agreed re-file goes to the ledger,
    # and the seen set always re-reads the inbox + ledger, so the store is
    # the belt-and-braces copy for notes that get pruned out of the inbox.
    if SEEN:
        team = dest / "team.md"
        if team.is_file():
            _nnt_seen().mark_seen(day, _nnt_seen().picks_in(team.read_text(encoding="utf-8", errors="replace")))
    n = sum(len(v["repos"]) for v in vids)
    print(f"nnt-repos {day}: {len(vids)} video(s), {n} repo link(s), {len(metas)} looked up — {note}")
    return 0 if FEED.is_file() else 3


if __name__ == "__main__":
    raise SystemExit(main())
