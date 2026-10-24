#!/usr/bin/env python3
"""nnt-seen — repos the pick log has already seen (t_68837b13, 2026-09-27).

The pick log is two files, not one (spec nnt-picklog-dedupe-spec-2026-09-27.md):
  * `nnt-pick: <owner/repo>` lines in ~/Desktop/krew-deliverables/team-research-<day>.md
  * the agreed ledger ~/.krew/memory/ms/improvements/proposals.jsonl (nnt-agreed lines)

'Seen' is DERIVED, never stored per pick: a repo slug is seen when it appears in
the pick log inside the 8-day window the NNT repo list itself keeps
(NNT_REPOS_DAYS). The seen store is a day -> slugs map so a slug that left the
inbox (a note renamed or pruned) is not suddenly treated as new:

  ~/.krew/memory/ms/architect-collect/nnt-seen.json
  {"day": {"slug": day, ...}, ...}   # one map per day; pruned to the window

Join key = normalized lowercase owner/name (the nnt-repos.py `repos` dict key;
`/` kept as-is, never card_key, which does not round-trip to a repo).
NNT_SEEN_REPOS=0 turns every hook off at once.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from architect_lib import COLLECT_ROOT, INBOX, IMPROVE_DIR  # noqa: E402  (importers put scripts/ on the path)

DAYS = int(os.environ.get("NNT_REPOS_DAYS", "8") or 8)     # the repo list's own window
SEEN_PATH = COLLECT_ROOT / "nnt-seen.json"
# `nnt-pick: owner/name …` — the pick is the FIRST field, before any " — " / " - "
# (a hyphen inside owner/name like davila7/claude-code-templates is kept; a
# trailing hyphen is not). Case is preserved here and dropped at the join.
PICK_LINE = re.compile(r"nnt-pick:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")


def enabled() -> bool:
    return os.environ.get("NNT_SEEN_REPOS", "1").strip().lower() not in ("0", "false", "no", "off")


def repo_slug(name: str) -> str:
    """owner/name -> the join key: lowercase, case is the only variance seen
    (the nnt-repos.py `repos` dict is already normalized owner/name)."""
    return name.strip().strip("`").lower()


def pick_slugs(line: str) -> list[str]:
    m = PICK_LINE.search(line)
    return [repo_slug(m.group(1))] if m else []


def picks_in(text: str) -> list[str]:
    """nnt-pick repo slugs in one note (first field of the pick only)."""
    out: list[str] = []
    for line in (text or "").splitlines():
        if "nnt-pick:" in line.lower():
            out.extend(pick_slugs(line))
    return out


def pick_log_window(window: int = DAYS) -> dict[str, str]:
    """The pick log over `window` days: {slug: the newest day that named it}.
    Inbox team-research notes (the picks) PLUS the agreed ledger (the agree)."""
    today = date.today()
    newest: dict[str, str] = {}
    for i in range(window):
        day = (today - timedelta(days=i)).isoformat()
        note = INBOX / f"team-research-{day}.md"
        if note.is_file():
            for slug in picks_in(note.read_text(encoding="utf-8", errors="replace")):
                if slug not in newest or day > newest[slug]:
                    newest[slug] = day
    for row in ledger_rows(window):
        slug = _slug_from_title(row.get("title") or "")
        if not slug:
            continue
        day = str(row.get("ts") or "")[:10]
        if day and (slug not in newest or day > newest[slug]):
            newest[slug] = day
    return newest


def picked_before(day: str, window: int = DAYS) -> dict[str, str]:
    """{slug: newest day < day} the pick log named strictly BEFORE `day`.

    The rank hook's self-suppression view: the eval day's OWN note is the
    candidate list being scored, not "previously picked" evidence, so a repo
    picked for the first time tonight must not mark itself seen — but a repo
    the log named on an earlier night (even if tonight's note repeats it) IS
    seen. `pick_log_window` only keeps each slug's newest day, which cannot
    tell those apart; this scans the same notes for the pre-`day` days. The
    horizon is `day`, not today, so a historical re-rank of an older eval day
    sees that day's past, not this morning's."""
    newest: dict[str, str] = {}
    for i in range(1, window + 1):
        named = (date.fromisoformat(day) - timedelta(days=i)).isoformat()
        note = INBOX / f"team-research-{named}.md"
        if note.is_file():
            for slug in picks_in(note.read_text(encoding="utf-8", errors="replace")):
                newest[slug] = named
    return newest


def ledger_rows(window: int = DAYS, before: str = "") -> list[dict]:
    """nnt-agreed ledger lines from the last `window` days. `before` (an eval
    day) anchors the window to the days strictly before it — the rank hook's
    re-agree of tonight's pick must not count as "seen" tonight. A line that
    was later withdrawn is still a pick (withdrawn-then-resurfaced is the case
    the spec re-news; a resurface needs a NEW agree, and this row proves the
    repo was seen)."""
    if before:
        end = (date.fromisoformat(before) - timedelta(days=1)).isoformat()
    else:
        end = date.today().isoformat()
    start = (date.fromisoformat(end) - timedelta(days=window)).isoformat()
    rows: list[dict] = []
    p = IMPROVE_DIR / "proposals.jsonl"
    if not p.is_file():
        return rows
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") != "nnt-agreed":
            continue
        d = str(row.get("ts") or "")[:10]
        if d and start <= d <= end:
            rows.append(row)
    return rows


def _slug_from_title(title: str) -> str:
    """The owner/repo a ledger title names — the nnt-agreed line starts with the
    repo when the pick was a repo. Title text is not a slug, so require the
    first field to parse as one."""
    first = re.split(r"\s+[—–-]{1,2}\s+", (title or "").strip(), maxsplit=1)[0].strip().strip("`")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", first):
        return ""
    return repo_slug(first)


def seen_map(path: Path | None = None, window: int = DAYS, before: str = "") -> dict[str, str]:
    """The seen store: {slug: newest day it was seen}. Absent/corrupt = empty
    (a fresh house starts with nothing seen, never crashes the collect).
    `before` (an eval day) anchors the window to the days strictly before it —
    the night's own `mark_seen` of its picks dates to the eval day and must not
    count as "seen" tonight."""
    p = path or SEEN_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if before:
        end = (date.fromisoformat(before) - timedelta(days=1)).isoformat()
    else:
        end = date.today().isoformat()
    cutoff = (date.fromisoformat(end) - timedelta(days=window)).isoformat()
    out: dict[str, str] = {}
    for slug, days in data.items():
        if not isinstance(days, dict):
            continue
        newest = max((str(v) for v in days.values() if isinstance(v, (str, int))), default="")
        if newest and cutoff <= newest <= end:
            out[slug] = newest
    return out


def mark_seen(day: str, slugs: list[str], path: Path | None = None, window: int = DAYS) -> int:
    """Record the night's picks in the seen store. Idempotent: a slug the store
    already has for this day is not re-added; a re-rank of the same day is a
    no-op. Returns the number of slugs (re)recorded. NNT_SEEN_REPOS=0: no-op."""
    slugs = [s for s in dict.fromkeys(slugs) if s]
    if not slugs:
        return 0
    if not enabled():
        return 0
    p = path or SEEN_PATH
    data = {}
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    n = 0
    for slug in slugs:
        d = data.setdefault(slug, {})
        if d.get(day) != day:
            n += 1
        d[day] = day
    # prune the store to the window: a slug seen only outside it is new again.
    # The horizon is today, not the (possibly historical) `day` of this call —
    # pruning against `day` would keep yesterday's marks alive when an older
    # note is replayed and drop entries that the window still covers.
    cutoff = (date.today() - timedelta(days=window)).isoformat()
    for slug in [s for s, days in data.items() if not isinstance(days, dict)]:
        del data[slug]
    for slug in [s for s, days in data.items() if not any(str(v) >= cutoff for v in days.values())]:
        del data[slug]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)
    return n
