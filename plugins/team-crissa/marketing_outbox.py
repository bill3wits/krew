"""krew: marketing_outbox.py (lifted from the reference install; see crew/README.md)."""















from __future__ import annotations

import csv
import datetime as _dt
import io
import os
import re
from pathlib import Path

OUTBOX = Path(os.environ.get("KREW_MARKETING_OUTBOX_DIR",
                             str(Path.home() / "Desktop" / "krew-deliverables" / "ready-to-post")))
X_MAX = 280
HEAD_MAX, HEAD_MIN, HEAD_CAP = 30, 3, 15
DESC_MAX, DESC_MIN, DESC_CAP = 90, 2, 4
REAL_TOOL = {
    "x": "X API v2 POST /2/tweets (hermes bundled skill `xurl`, needs the user's X developer keys and a shell); "
         "Spaces themselves are started in the X app - the API cannot open a Space.",
    "google_ads": "Google Ads Editor import (this CSV, by hand) now; later the Google Ads API through the "
                  "`google-ads` Python client (developer token + OAuth from the user's Ads account).",
}


def enabled() -> bool:
    return os.environ.get("KREW_MARKETING_OUTBOX", "1").strip().lower() not in ("0", "false", "no", "off")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "draft").lower()).strip("-")[:48] or "draft"


def _lines(v) -> list[str]:
    if isinstance(v, str):
        v = [x for x in re.split(r"\n|\s*\|\s*", v)]
    return [" ".join(str(x).split()) for x in (v or []) if str(x).strip()]


def _free(path: Path) -> Path:
    n, p = 2, path
    while p.exists():
        p = path.with_name(f"{path.stem}-{n}{path.suffix}"); n += 1
    return p


def fit_thread(text: str, limit: int = X_MAX) -> list[str]:
    """`text` as posts of <= limit characters, cut at sentence ends (else at words)."""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        while len(sent) > limit:                    # one sentence longer than a post: cut at a word
            cut = sent.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            pieces.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if sent:
            pieces.append(sent)
    out: list[str] = []
    for piece in pieces:
        if out and len(out[-1]) + 1 + len(piece) <= limit:
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


def x_post(text: str, title: str = "", *, root: Path | None = None, day: str | None = None) -> tuple[Path | None, str]:
    """One post, or a thread when `text` holds parts split by a line of three dashes. Returns (path, spoken line)."""
    parts = [" ".join(p.split()) for p in re.split(r"\n\s*-{3,}\s*\n", text or "") if p.strip()]
    if not parts:
        return None, "Nothing written: the post needs text."

    # post was refused three times and the helper would not cut "his copy"; a refusal cost the beat).
    parts = [q for p in parts for q in fit_thread(p)]
    day = day or _dt.date.today().isoformat()
    d = Path(root or OUTBOX) / "x" / day
    d.mkdir(parents=True, exist_ok=True)
    path = _free(d / f"{_slug(title or parts[0][:40])}.md")
    body = [f"# {title or 'X post'}", "", "status: draft - the user posts it", f"post with: {REAL_TOOL['x']}", ""]
    for i, p in enumerate(parts):
        body += [f"## {i + 1}/{len(parts)} ({len(p)} chars)" if len(parts) > 1 else f"## post ({len(p)} chars)", "", p, ""]
    path.write_text("\n".join(body), encoding="utf-8")
    what = f"a {len(parts)}-part thread" if len(parts) > 1 else "one post"
    return path, f"Saved {what} to the X outbox ({path.name}); it waits for you to post."


def google_ads(campaign: str, headlines, descriptions, *, final_url: str = "", ad_group: str = "",
               keywords=None, root: Path | None = None, day: str | None = None) -> tuple[Path | None, str]:
    """A responsive search ad + keywords as a Google Ads Editor CSV. Returns (path, spoken line)."""
    heads, descs = _lines(headlines), _lines(descriptions)
    kws = _lines(keywords)
    dropped = [f"headline {i + 1} ({len(h)} chars)" for i, h in enumerate(heads) if len(h) > HEAD_MAX]
    dropped += [f"description {i + 1} ({len(x)} chars)" for i, x in enumerate(descs) if len(x) > DESC_MAX]
    heads = list(dict.fromkeys(h for h in heads if len(h) <= HEAD_MAX))[:HEAD_CAP]
    descs = list(dict.fromkeys(x for x in descs if len(x) <= DESC_MAX))[:DESC_CAP]
    if len(heads) < HEAD_MIN or len(descs) < DESC_MIN:
        return None, (f"Nothing written: a search ad needs {HEAD_MIN}+ headlines of <= {HEAD_MAX} characters and "
                      f"{DESC_MIN}+ descriptions of <= {DESC_MAX}; usable {len(heads)} and {len(descs)}"
                      + (f" (too long: {', '.join(dropped)})" if dropped else "") + ".")
    campaign = " ".join((campaign or "Crissa").split())
    ad_group = " ".join((ad_group or campaign).split())
    url = final_url or "https://crissa.ai"
    cols = (["Campaign", "Ad group", "Ad type"] + [f"Headline {i}" for i in range(1, HEAD_CAP + 1)]
            + [f"Description {i}" for i in range(1, DESC_CAP + 1)] + ["Final URL", "Keyword", "Criterion Type"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerow([campaign, ad_group, "Responsive search ad"] + heads + [""] * (HEAD_CAP - len(heads))
               + descs + [""] * (DESC_CAP - len(descs)) + [url, "", ""])
    for k in kws:
        w.writerow([campaign, ad_group, ""] + [""] * (HEAD_CAP + DESC_CAP) + ["", k, "Phrase"])
    day = day or _dt.date.today().isoformat()
    d = Path(root or OUTBOX) / "google-ads" / day
    d.mkdir(parents=True, exist_ok=True)
    path = _free(d / f"{_slug(campaign)}.csv")
    path.write_text(buf.getvalue(), encoding="utf-8")
    path.with_suffix(".md").write_text(
        f"# {campaign} - Google Ads draft\n\nstatus: draft, not uploaded - the user imports it\n"
        f"import with: {REAL_TOOL['google_ads']}\n\nfile: {path.name}\nheadlines {len(heads)}, descriptions "
        f"{len(descs)}, keywords {len(kws)}\n" + (f"dropped (too long): {', '.join(dropped)}\n" if dropped else ""),
        encoding="utf-8")
    return path, (f"Saved a Google Ads draft ({path.name}): {len(heads)} headlines, {len(descs)} descriptions, "
                  f"{len(kws)} keywords; you import it with Ads Editor."
                  + (f" Dropped as too long: {', '.join(dropped)}." if dropped else ""))
