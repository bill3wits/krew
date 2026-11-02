#!/usr/bin/env python3
"""team-eval-rank — add the roles' scores up into one ranked list.

Reads `<role>-improvement-ideas-<day>.md` files (skill team-eval, 01:00) and
the night's `team-research-<day>.md` picks. Per item: which roles scored it,
sum, min, breadth. Rank: items every role scored >= 2 first ("improving them
all goes to top" — the User 2026-09-12), then breadth, then sum, then min.

Writes:
  ~/Desktop/krew-deliverables/team-eval-<day>.md   (ranked table; `nnt-agreed:`
      lines under "## Proposed cards" for the top tier, which the filer (called by
      team-eval-run.sh right after this) turns into green-gated team-improve cards + the top improvements ledger)
  ~/.krew/memory/ms/improvements/rank-<day>.json (the numbers)

No LLM. Idempotent per day.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import INBOX, MS_DIR  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

ROLES = ("ada", "bash", "lila", "vera", "steward")
# [ \t]* not \s* after the separator: \s swallows the newline and the next
# line's score becomes this line's "why".
SCORE = re.compile(r"^[ \t]*[-*]?[ \t]*score:[ \t]*(.+?)[ \t]*=[ \t]*([0-3])[ \t]*(?:[—\-–:][ \t]*(.*))?$", re.I | re.M)
# [`]? — Scout wraps the line in backticks on most nights (2026-09-17: 0 of 4
# picks parsed, the night's one real idea never reached the table).
PICK = re.compile(r"^\s*[-*]?\s*`?\s*nnt-pick:\s*(.+)$", re.I | re.M)
IMPROVE = re.compile(r"^\s*[-*]?\s*`?\s*team-improve:\s*(.+)$", re.I | re.M)
AGREE_MIN = 2   # every role at >= this = top tier
# A 2 or 3 names the KPI it should move (skill team-eval, the User 2026-09-17: tie
# improvements to the team's KPIs). `kpi: none` is an honest answer.
KPI = re.compile(r"\bkpi\s*[:=]\s*([a-z][a-z0-9_]*)", re.I)
# Repo dedupe (t_68837b13): a repo-slug item the pick log already named inside the
# 8-day window is seen — it stays in the ranked table (the team still sees the
# scores) but is excluded from the top tier and the nnt-agreed re-filing.
# NNT_SEEN_REPOS=0 = the old ranker (the flag is off in the store module too,
# so all hooks go dark at once).
SEEN = os.environ.get("NNT_SEEN_REPOS", "1").strip().lower() not in ("0", "false", "no", "off")


def kpi_of(why: str) -> str:
    m = KPI.search(why or "")
    return m.group(1).lower() if m else ""


def item_kpi(per: dict[str, tuple[int, str]]) -> str:
    """The KPI most roles that scored the item >= 2 named; ties → first named.
    '' when nobody named one; 'none' when they said none."""
    named = [kpi_of(v[1]) for v in per.values() if v[0] >= AGREE_MIN and kpi_of(v[1])]
    if not named:
        return ""
    real = [k for k in named if k != "none"]
    pool = real or named
    return max(pool, key=lambda k: (pool.count(k), -pool.index(k)))
# TOP needs this many roles to have scored the item (2026-09-16: two voters of
# five made a "helps every role" card). TEAM_EVAL_QUORUM=0 = the old rule,
# every counted voter. TEAM_EVAL_FUZZY=0 = verbatim item match only.
QUORUM = int(os.environ.get("TEAM_EVAL_QUORUM", "3") or 0)
FUZZY = os.environ.get("TEAM_EVAL_FUZZY", "1").strip().lower() not in ("0", "false", "no", "off")
# A role whose cron writes "~/Desktop/..." under its hermes profile HOME. TEAM_EVAL_PROFILE_HOME=0 = inbox only.
PROFILE_HOME = os.environ.get("TEAM_EVAL_PROFILE_HOME", "1").strip().lower() not in ("0", "false", "no", "off")
PROFILES = Path(os.environ.get("HERMES_PROFILES_DIR", str(KH.hermes_home() / "profiles")))
_SLUG = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)+$")
_VOTE_PREFIX = re.compile(r"^(?:team-improve|nnt-pick|nnt)\s*:\s*", re.I)
_VOTE_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


def norm(item: str) -> str:
    return re.sub(r"\s+", " ", item.split(" — ")[0].strip().strip("`")).lower()


def items_from_research(text: str) -> dict[str, str]:
    """norm -> display (first field of each pick / improve line)."""
    out: dict[str, str] = {}
    for m in PICK.finditer(text or ""):
        first = m.group(1).split(" — ")[0].strip()
        out.setdefault(norm(first), first)
    for m in IMPROVE.finditer(text or ""):
        first = m.group(1).split(" — ")[0].strip()
        out.setdefault(norm(first), first)
    return out


def match_item(vote_key: str, items: dict[str, str]) -> str:
    """The item a role meant. Roles are told to echo the pick verbatim and do
    not: "team-improve: <slug>", "<slug> (team-improve, proposed card)",
    "NNT: <title>". A slug is matched as a whole token; a title by prefix."""
    if vote_key in items or not FUZZY:
        return vote_key
    v = vote_key
    for _ in range(3):
        v2 = _VOTE_PAREN.sub("", _VOTE_PREFIX.sub("", v)).strip().strip("\"'`")
        if v2 == v:
            break
        v = v2
    if v in items:
        return v
    tokens = set(re.split(r"[^a-z0-9.\-]+", v))
    for key in items:
        if _SLUG.match(key) and key in tokens:
            return key
    for key in items:
        if not _SLUG.match(key) and len(key) >= 8 and len(v) >= 8 and (v.startswith(key) or key.startswith(v)):
            return key
    return vote_key


def resolve_votes(vote: dict[str, tuple[int, str]], items: dict[str, str]) -> dict[str, tuple[int, str]]:
    """Re-key one role's scores onto the night's items. A verbatim line wins
    over a fuzzy one for the same item."""
    out: dict[str, tuple[int, str]] = {}
    for k, val in vote.items():
        if k in items:
            out[k] = val
    for k, val in vote.items():
        out.setdefault(match_item(k, items), val)
    return out


def scores_from_note(text: str) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for m in SCORE.finditer(text or ""):
        why = (m.group(3) or "").strip()
        short = why[:160]
        k = kpi_of(why)
        if k and not kpi_of(short):          # a long reason must not cut the KPI off
            short = short[:140].rstrip() + f" — kpi: {k}"
        out[norm(m.group(1))] = (int(m.group(2)), short)
    return out


def faked(vote: dict[str, tuple[int, str]], n_items: int) -> str:
    """Why a voice does not count. the User 2026-09-12: "i think some fake it".
    A real scorer spreads scores and explains a 3; a faker writes one number
    down the column or leaves the top scores bare."""
    if not vote:
        return "no scores"
    if n_items >= 3 and len(vote) < max(1, n_items // 2):
        return f"scored {len(vote)} of {n_items} items"
    vals = [v[0] for v in vote.values()]
    if len(vals) >= 3 and len(set(vals)) == 1:
        return f"all scores identical ({vals[0]})"
    bare3 = [k for k, v in vote.items() if v[0] == 3 and not v[1]]
    if bare3:
        return f"{len(bare3)} score(s) of 3 with no reason"
    return ""


def rank(items: dict[str, str], votes: dict[str, dict[str, tuple[int, str]]],
         roles: tuple[str, ...] = ROLES) -> list[dict]:
    votes = {r: resolve_votes(v, items) for r, v in votes.items() if not faked(v, len(items))}
    voters = [r for r in roles if r in votes]
    need = QUORUM if QUORUM > 0 else len(voters)
    rows: list[dict] = []
    for key, display in items.items():
        per = {r: votes[r][key] for r in voters if key in votes[r]}
        ss = [v[0] for v in per.values()]
        breadth = len(per)
        row = {
            "item": display,
            "kpi": item_kpi(per),
            "scores": {r: v[0] for r, v in per.items()},
            "why": {r: v[1] for r, v in per.items() if v[1]},
            "sum": sum(ss), "min": min(ss) if ss else 0, "breadth": breadth,
            "all_roles": bool(voters) and breadth == len(voters),
            "top": bool(voters) and breadth >= max(1, need) and min(ss) >= AGREE_MIN,
        }
        rows.append(row)
    rows.sort(key=lambda r: (-int(r["top"]), -r["breadth"], -r["sum"], -r["min"], r["item"].lower()))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def repo_slug_key(key: str) -> str:
    """The owner/repo slug an item is, or '' when the item is not a repo slug
    (team-improve slugs have no slash; titles are prose). The dedupe join key
    is this, lowercased — never card_key, which does not round-trip to a repo."""
    first = key.split(" — ")[0].strip().strip("`")
    return first if re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", first) else ""


_NNT_SEEN_SAID: list = []


def _nnt_seen():
    """The nnt-seen module (its filename keeps its hyphen: importlib, not import).
    Both hooks share this one loader — the same module object, once."""
    import importlib.util
    path = Path(__file__).resolve().parent / "nnt-seen.py"
    if not path.is_file():   # krew: the seen store is not shipped yet; say so once, never load a missing file
        if not _NNT_SEEN_SAID:
            _NNT_SEEN_SAID.append(path)
            print(f"eval-rank: seen store off: {path} is missing", file=sys.stderr)
        raise FileNotFoundError(str(path))
    spec = importlib.util.spec_from_file_location("nnt_seen", str(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seen_slugs(day: str = "") -> dict[str, str]:
    """{slug: newest day} the pick log named strictly before the eval day.
    Missing module or unreadable store: nothing seen (the old ranker), never
    a crash.

    The eval day is the self-suppression cut: the night's OWN note is the
    candidate list being scored, not "previously picked" evidence, and tonight's
    own re-agree must not count as seen tonight — so a repo picked for the
    first time tonight is not marked. Every source anchors to `day` (a
    historical re-rank sees that day's past, not this morning's):
      * `picked_before` — picks named on pre-`day` nights (even if tonight's
        note repeats them)
      * `ledger_rows(before=day)` — prior nnt-agreed rows, pre-`day`
      * `seen_map(before=day)` — the seen store's marks dated pre-`day`
    No `day` = the raw today window (the collect hook's view)."""
    if not SEEN:
        return {}
    try:
        nnt_seen = _nnt_seen()
        if day:
            seen = nnt_seen.picked_before(day)
            for row in nnt_seen.ledger_rows(before=day):
                slug = nnt_seen._slug_from_title(row.get("title") or "")
                if slug:
                    d = str(row.get("ts") or "")[:10]
                    if d and (slug not in seen or d > seen[slug]):
                        seen[slug] = d
            for slug, d in nnt_seen.seen_map(before=day).items():
                seen[slug] = max(seen.get(slug, ""), d)
            return seen
        seen = nnt_seen.seen_map()
        for slug, day_named in nnt_seen.pick_log_window().items():
            seen[slug] = max(seen.get(slug, ""), day_named)
        return seen
    except Exception:  # noqa: BLE001 — dedupe must never sink the rank
        return {}


def render(day: str, rows: list[dict], voters: list[str], absent: list[str] | None = None) -> str:
    rule = f"every role that scored it >= {AGREE_MIN}, at least {QUORUM} roles" if QUORUM > 0 else f"every role >= {AGREE_MIN}"
    seen_n = sum(1 for r in rows if r.get("seen"))
    lines = [f"# Team eval — ranked picks ({day})", "",
             f"lede: {len(rows)} item(s) scored by {', '.join(voters) or 'nobody'}; "
             f"{sum(1 for r in rows if r['top'])} at the top tier ({rule})."
             + (f" {seen_n} repo(s) seen in the pick log — ranked, not re-filed." if seen_n else "")
             + (f" No note from: {', '.join(absent)}." if absent else ""),
             "", "| rank | item | " + " | ".join(voters) + " | sum | tier |", "|---|---|" + "---|" * len(voters) + "---|---|"]
    for r in rows:
        cells = [str(r["scores"].get(v, "·")) for v in voters]
        seen_tag = " (seen)" if r.get("seen") else ""
        tier = (f"TOP — {r['breadth']}/{len(voters)} roles, all >= {AGREE_MIN}" if r["top"] and not r["all_roles"]
                else "TOP — helps every role") if r["top"] else ("all voted" if r["all_roles"] else f"{r['breadth']}/{len(voters)} voted")
        lines.append(f"| {r['rank']} | {r['item']}{seen_tag} | " + " | ".join(cells) + f" | {r['sum']} | {tier} |")
    top = [r for r in rows if r["top"] and not r.get("seen")]
    seen_top = [r for r in rows if r["top"] and r.get("seen")]
    lines += ["", "## Proposed cards"]
    if top:
        for r in top:
            whys = "; ".join(f"{k}: {v}" for k, v in r["why"].items())[:300]
            kpi = f" — kpi={r.get('kpi') or 'unnamed'}"
            lines.append(f"- nnt-agreed: {r['item']}{kpi} — {r['breadth']} of {len(voters)} roles, all >= {AGREE_MIN} (sum {r['sum']}){(' — ' + whys) if whys else ''}")
    elif not seen_top:
        lines.append("- none at the top tier tonight (a thin night is a pass)")
    if seen_top:
        lines += ["", "_Seen in the pick log (ranked, not re-filed):_"
                  + "".join(f"\n- {r['item']} (seen {r.get('seen_day', '')})" for r in seen_top)]
    lines += ["", "_Scores: 0 no effect · 1 nice · 2 real gain · 3 removes a pain. "
              "Ranked by: every role >= 2 first, then breadth, sum, min. scripts/team-eval-rank.py_"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=date.today().isoformat())
    ap.add_argument("--inbox", default=str(INBOX))
    ap.add_argument("--out-dir", default=str(MS_DIR / "improvements"))
    a = ap.parse_args(argv)
    inbox = Path(a.inbox)
    research = inbox / f"team-research-{a.day}.md"
    if not research.is_file():
        print(f"no {research.name}; nothing to rank")
        return 0
    # Record the night's picks in the seen store (t_68837b13, spec §5: the rank
    # marks, the collect prunes). Idempotent per day; a no-op when the kill
    # switch is off. The store's path comes from the nnt-seen copy (architect_lib
    # COLLECT_ROOT); the rank's own inbox/out args do not move the store — the
    # seen store is a house record, not a per-run artifact.
    try:
        nnt_seen = _nnt_seen()
        if nnt_seen.enabled():
            nnt_seen.mark_seen(a.day, nnt_seen.picks_in(research.read_text(encoding="utf-8", errors="replace")))
    except Exception:  # noqa: BLE001 — the seen store must never sink the rank
        pass
    items = items_from_research(research.read_text(encoding="utf-8", errors="replace"))
    votes: dict[str, dict[str, tuple[int, str]]] = {}
    discounted: dict[str, str] = {}
    for r in ROLES:
        # a role's note may land in the inbox or in its ideas/ drawer (2026-09-17:
        # vera wrote ideas/vera-improvement-ideas-<day>.md and was counted absent)
        p = inbox / f"{r}-improvement-ideas-{a.day}.md"
        if not p.is_file():
            p = inbox / "ideas" / f"{r}-improvement-ideas-{a.day}.md"
        if not p.is_file() and PROFILE_HOME:
            # ...or in the role's PROFILE home (2026-09-18: vera scored all 12 there, counted absent)
            p = PROFILES / r / "home" / "Desktop" / "krew-deliverables" / f"{r}-improvement-ideas-{a.day}.md"
        if p.is_file():
            v = scores_from_note(p.read_text(encoding="utf-8", errors="replace"))
            why = faked(v, len(items))
            if why:
                discounted[r] = why
            else:
                votes[r] = v
    rows = rank(items, votes)
    voters = [r for r in ROLES if r in votes]
    # Seen repos (t_68837b13): ranked with the rest, flagged, but excluded from
    # the top tier and the nnt-agreed re-filing. Team-improve slugs and prose
    # titles are not repo slugs and are never demoted here.
    seen = seen_slugs(a.day)
    for r in rows:
        slug = repo_slug_key(r["item"])
        if slug and slug in seen:
            r["seen"] = True
            r["seen_day"] = seen[slug]
    out_md = inbox / f"team-eval-{a.day}.md"
    absent = [r for r in ROLES if r not in votes and r not in discounted]
    md = render(a.day, rows, voters, absent)
    if discounted:
        md += "\n## Voices not counted\n" + "".join(f"- {r}: {why}\n" for r, why in discounted.items())
    out_md.write_text(md, encoding="utf-8")
    out_dir = Path(a.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"rank-{a.day}.json").write_text(json.dumps(
        {"day": a.day, "voters": voters, "discounted": discounted, "absent": absent,
         "quorum": QUORUM, "rows": rows}, indent=2) + "\n")
    print(f"ranked {len(rows)} item(s) from {len(voters)} voter(s) -> {out_md}"
          + (f"; no note from {', '.join(absent)}" if absent else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
