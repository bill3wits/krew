"""The idea ledger: one row per thing that happened to an idea, keyed on the line (self-loop step 2).

The User 09-28: the same repos were debated night after night (open-code-review x5,
agent-browser x5) because nothing remembered them, and the filer's own ledger stopped while
the Krew fence was up (cards_off() returned before record_proposal). This file is written
whether or not cards are filed: recording is not filing.

    ~/.krew/memory/ms/improvements/ideas.jsonl   (append-only events)
    {"ts", "day", "key", "event", "kind", "text", "source", "data"?}

Events: seen (a research note named it), debated (the night's rank scored it), agreed (an
`nnt-agreed` line), filed / seen-again (the filer), later ruled / shipped / measured.
The KEY is written on the line, never scraped from prose:

    idea: <key> | <sentence>            the new line (Scout, with t_68837b13's owner)
    nnt-pick: owner/repo — url — ...    repo:<owner/repo, lowercased>
    team-improve: <title> — why         team-improve:<slug of the first field>, the filer's
                                        idempotency key byte for byte (a test pins it)

A repo key is lowercased (`Tencent/WeKnora` and `tencent/weknora` are one idea). Two phrasings
of one house change are still two keys until Scout writes `idea:` lines; that is the point
of the line.

seen.jsonl and proposals.jsonl stay as today's writers wrote them; `views` writes
`*.view.jsonl` beside them from this ledger so the two can be compared before anyone
switches a reader. Rollback: a copy of this file (it lives under /data, not in git).
Kill: KREW_IDEA_LEDGER=0 (nothing recorded).

CLI:
    idea_ledger.py rebuild --since 2026-09-16 [--dry]   replay notes (inbox root AND krew-reports/<day>/),
                                                        rank files and seen.jsonl into the ledger
    idea_ledger.py state [--min-nights N]               folded state, one key per line
    idea_ledger.py views                                write seen.view.jsonl / proposals.view.jsonl
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
KREW_MEMORY_ROOT = os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory")  # krew: the house kept this on a data disk

HOME = Path.home()
MS_DIR = Path(os.environ.get("KREW_MS_DIR", (KREW_MEMORY_ROOT + "/ms")))
IMPROVE_DIR = MS_DIR / "improvements"
LEDGER = Path(os.environ.get("KREW_IDEA_LEDGER_PATH", str(IMPROVE_DIR / "ideas.jsonl")))
INBOX = Path(os.path.expanduser(os.environ.get("KREW_DELIVERABLES") or "~/Desktop/krew-deliverables"))
KREW_REPORTS = INBOX / "krew-reports"

KINDS = ("idea", "nnt-pick", "nnt-agreed", "team-improve", "architect-try", "architect-watch",
         "team-source", "team-source-drop")
LINE = re.compile(r"^\s*[-*]?\s*`?\s*(" + "|".join(re.escape(k) for k in KINDS) + r")\s*:\s*(.+)$", re.I)
REPO = re.compile(r"(?:github\.com/)?\b([A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*)")
NONE_ITEM = re.compile(r"^\W*(none|nothing|no (cards?|try|tries|candidates?|ideas?))\b", re.I)


def enabled() -> bool:
    return os.environ.get("KREW_IDEA_LEDGER", "1").strip().lower() not in ("0", "false", "no", "off")


def slug_key(kind: str, title: str) -> str:
    """The filer's idempotency key (steward-file-architect-cards.slug_key), byte for byte."""
    first = re.split(r"\s+[—–-]{1,2}\s+", title.strip(), maxsplit=1)[0]
    first = re.sub(r"[^a-z0-9./]+", "-", first.lower()).strip("-")[:80]
    return f"{kind}:{first}" if first else ""


def kpi_scope(kid: str, kpis_path: Path | None = None) -> str:
    """`board` or `unit` (self-loop step 3). A board-wide KPI (every `kanban_*` count) moves
    with anything on the board - the 09-26 hermes update took kanban_blocked 2 -> 34 - so a try
    filed on one is UNMEASURED and one open card per such KPI is the limit. A KPI may declare
    its own `scope` in kpis.json; otherwise kanban_* is board, the rest (ops_tok_s, crew_tok_s,
    per-unit samplers) is unit."""
    kid = (kid or "").strip().lower()
    try:
        k = (json.loads((kpis_path or IMPROVE_DIR / "kpis.json").read_text()).get("kpis") or {}).get(kid) or {}
    except (OSError, ValueError, AttributeError):
        k = {}
    if k.get("scope") in ("board", "unit"):
        return k["scope"]
    return "board" if kid.startswith("kanban_") else "unit"


def repo_key(text: str, *, strict: bool = True) -> str:
    """repo:<owner/repo> lowercased, from the FIRST field of a line, else ''.

    strict (the default): the first field must BE a repo (`owner/repo`, optionally a github
    URL) - a house change that mentions "cron/kanban" is not a repo. An nnt-pick line is
    always a repo and reads non-strict."""
    first = re.split(r"\s+[—–-]{1,2}\s+", text.strip().strip("`"), maxsplit=1)[0].strip("` ")
    if strict:
        first = re.sub(r"^(?:https?://)?(?:www\.)?github\.com/", "", first).rstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", first):
            return ""
    m = REPO.search(first)
    if not m:
        return ""
    slug = m.group(1).rstrip(".`").lower()
    if slug.count("/") != 1 or slug.startswith(("http", "www.")):
        return ""
    return f"repo:{slug}"


def _body(rest: str) -> str:
    """The line's own text: inside the backticks when the line opened one, else the rest."""
    rest = rest.strip()
    if "`" in rest:
        rest = rest.split("`", 1)[0]
    return rest.strip()


def key_of(kind: str, text: str) -> str:
    kind = kind.lower()
    if kind == "idea":
        k = text.split("|", 1)[0].strip().strip("`").lower()
        rk = repo_key(k)
        return rk or (f"idea:{re.sub(r'[^a-z0-9./:-]+', '-', k).strip('-')[:80]}" if k else "")
    if kind == "nnt-pick":
        return repo_key(text, strict=False)
    if kind == "nnt-agreed":
        return repo_key(text) or slug_key("team-improve", text)
    return slug_key(kind, text)


def parse_note(text: str) -> list[dict]:
    """Every idea line of a note, once per key (the first phrasing wins). `none` lines are not ideas."""
    out: list[dict] = []
    keys: set[str] = set()
    for line in (text or "").splitlines():
        m = LINE.match(line)
        if not m:
            continue
        kind, body = m.group(1).lower(), _body(m.group(2))
        if not body or NONE_ITEM.match(body):
            continue
        k = key_of(kind, body)
        if not k or (k, kind == "nnt-agreed") in keys:
            continue
        keys.add((k, kind == "nnt-agreed"))
        text_ = body.split("|", 1)[1].strip() if kind == "idea" and "|" in body else body
        out.append({"key": k, "kind": kind, "text": text_,
                    "event": "agreed" if kind == "nnt-agreed" else "seen"})
    return out


def rank_items(rank: dict) -> list[dict]:
    """The night's scored items as `debated` events (the vote, with its sum and whether it was top)."""
    out = []
    for r in (rank or {}).get("rows") or []:
        if not isinstance(r, dict):
            continue
        item = str(r.get("item") or "").replace("`", "").strip()
        k = repo_key(item) or slug_key("team-improve", item)
        if k:
            out.append({"key": k, "kind": "rank", "text": item, "event": "debated",
                        "data": {"sum": r.get("sum"), "top": bool(r.get("top")), "rank": r.get("rank"),
                                 "voters": len(r.get("scores") or {})}})
    return out


# --- the file ------------------------------------------------------------------------------

def load(path: Path | None = None) -> list[dict]:
    p = path or LEDGER
    rows = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                rows.append(r)
    except OSError:
        pass
    return rows


def _ident(r: dict) -> tuple:
    return (r.get("day"), r.get("key"), r.get("event"), Path(str(r.get("source") or "")).name)


def record(events: list[dict], day: str, source: str, *, path: Path | None = None, dry: bool = False) -> int:
    """Append events not already in the ledger for (day, key, event, source file name). Returns the count.

    The source is compared by FILE NAME: the inbox archive moves a note into krew-reports/<day>/
    the next day, and a replay from there must not record it twice."""
    if not enabled():
        return 0
    p = path or LEDGER
    have = {_ident(r) for r in load(p)}
    new = []
    for e in events:
        row = {"ts": round(time.time(), 3), "day": day, "key": e["key"], "event": e["event"],
               "kind": e.get("kind", ""), "text": e.get("text", ""), "source": source}
        if e.get("data"):
            row["data"] = e["data"]
        if _ident(row) in have:
            continue
        have.add(_ident(row))
        new.append(row)
    if new and not dry:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            for row in new:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(new)


def record_note(note: Path, day: str, *, path: Path | None = None, dry: bool = False) -> int:
    try:
        text = note.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return record(parse_note(text), day, str(note), path=path, dry=dry)


def record_rank(rank_path: Path, day: str, *, path: Path | None = None, dry: bool = False) -> int:
    try:
        rank = json.loads(rank_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return record(rank_items(rank), day, str(rank_path), path=path, dry=dry)


# --- folded state --------------------------------------------------------------------------

def fold(rows: list[dict]) -> dict[str, dict]:
    st: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: (str(r.get("day") or ""), float(r.get("ts") or 0))):
        k = r.get("key")
        if not k:
            continue
        s = st.setdefault(k, {"key": k, "kind": r.get("kind"), "text": r.get("text"), "first_day": r.get("day"),
                              "last_day": r.get("day"), "seen_days": [], "debated_days": [], "agreed_days": [],
                              "top_days": [], "best_sum": None, "task": None, "state": "seen"})
        day, ev = str(r.get("day") or ""), r.get("event")
        s["last_day"] = max(s["last_day"] or day, day)
        if r.get("kind") not in ("rank", "filer") and r.get("text"):
            s["text"] = r["text"]
        if ev == "seen" and day not in s["seen_days"]:
            s["seen_days"].append(day)
        elif ev == "debated":
            if day not in s["debated_days"]:
                s["debated_days"].append(day)
            d = r.get("data") or {}
            if d.get("top") and day not in s["top_days"]:
                s["top_days"].append(day)
            if isinstance(d.get("sum"), (int, float)):
                s["best_sum"] = max(s["best_sum"] if s["best_sum"] is not None else d["sum"], d["sum"])
        elif ev == "agreed" and day not in s["agreed_days"]:
            s["agreed_days"].append(day)
        elif ev in ("filed", "seen-again") and (r.get("data") or {}).get("task"):
            s["task"] = s["task"] or r["data"]["task"]
        elif ev in ("ruled", "shipped", "measured"):
            s[ev] = r.get("data") or True
    for s in st.values():
        s["nights"] = len(set(s["seen_days"]) | set(s["debated_days"]))
        s["state"] = ("measured" if s.get("measured") else "shipped" if s.get("shipped") else
                      "ruled" if s.get("ruled") else "filed" if s["task"] else
                      "agreed" if s["agreed_days"] or s["top_days"] else
                      f"debated({len(s['debated_days'])})" if s["debated_days"] else "seen")
    return st


# --- views (checked beside today's writers, never instead of them yet) ---------------------

def views(rows: list[dict], out_dir: Path | None = None) -> tuple[Path, Path]:
    out_dir = out_dir or IMPROVE_DIR
    seen = [{"day": r["day"], "key": r["key"], "task": (r.get("data") or {}).get("task", ""),
             "status": (r.get("data") or {}).get("status", r["event"]), "note": r.get("source", "")}
            for r in rows if r.get("event") in ("filed", "seen-again")]
    props = [dict((r.get("data") or {}).get("proposal") or {}) for r in rows
             if r.get("event") == "agreed" and (r.get("data") or {}).get("proposal")]
    a, b = out_dir / "seen.view.jsonl", out_dir / "proposals.view.jsonl"
    for p, rs in ((a, seen), (b, props)):
        tmp = p.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rs), encoding="utf-8")
        tmp.replace(p)
    return a, b


# --- rebuild -------------------------------------------------------------------------------

def _note_for(name: str, day: str) -> Path | None:
    for p in (INBOX / name, KREW_REPORTS / day / name):
        if p.is_file():
            return p
    return None


def rebuild(since: str, until: str | None = None, *, path: Path | None = None, dry: bool = False) -> dict:
    """Replay history into the ledger: the research and eval notes (inbox root AND
    krew-reports/<day>/ - the archive moves yesterday's notes there), the rank files, and the
    filer's seen.jsonl. Idempotent: a second replay records nothing."""
    d0 = dt.date.fromisoformat(since)
    d1 = dt.date.fromisoformat(until) if until else dt.date.today()
    n = {"notes": 0, "ranks": 0, "events": 0, "days": 0}
    # agreed proposals FIRST, so the `agreed` event carries its record (the proposals view);
    # the same line read later from the note is the same (day, key, event, source) and is skipped
    try:
        for line in (IMPROVE_DIR / "proposals.jsonl").read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ds = str(r.get("ts") or "")[:10]
            if not isinstance(r, dict) or not ds or ds < since or not r.get("title"):
                continue
            k = key_of("nnt-agreed", str(r["title"]))
            if k:
                n["events"] += record([{"key": k, "event": "agreed", "kind": "nnt-agreed",
                                        "text": str(r["title"]), "data": {"proposal": r}}],
                                      ds, str(r.get("source") or "proposals.jsonl"), path=path, dry=dry)
    except OSError:
        pass
    day = d0
    while day <= d1:
        ds = day.isoformat()
        n["days"] += 1
        for name in (f"team-research-{ds}.md", f"team-eval-{ds}.md"):
            p = _note_for(name, ds)
            if p:
                n["notes"] += 1
                n["events"] += record_note(p, ds, path=path, dry=dry)
        rp = IMPROVE_DIR / f"rank-{ds}.json"
        if rp.is_file():
            n["ranks"] += 1
            n["events"] += record_rank(rp, ds, path=path, dry=dry)
        day += dt.timedelta(days=1)
    seen = []
    try:
        for line in (IMPROVE_DIR / "seen.jsonl").read_text().splitlines():
            try:
                seen.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    by_day: dict[str, list[dict]] = {}
    for r in seen:
        if isinstance(r, dict) and r.get("key") and str(r.get("day") or "") >= since:
            ev = "filed" if r.get("status") == "filed" else "seen-again"
            by_day.setdefault(str(r["day"]), []).append(
                {"key": r["key"], "kind": "filer", "event": ev, "text": "",
                 "data": {"task": r.get("task", ""), "status": r.get("status", "")}})
    for ds, evs in by_day.items():
        n["events"] += record(evs, ds, "seen.jsonl", path=path, dry=dry)
    return n


LESSONS_HEAD = "## House lessons"


def attach_lessons(lessons_md: Path, target: Path, who: str) -> bool:
    """Put the night's lessons LAST in `target` under their own heading, replacing an earlier
    copy (self-loop step 4): team.md before Scout runs, the research note after, for the voters.
    The block is always the file's last section, so a re-attach cuts from its heading to the end.
    Its lines carry no kind word (improvement-outcomes' _plain), so no ranker, ledger or filer
    reads them as an item. False when either file is missing."""
    try:
        body = lessons_md.read_text(encoding="utf-8").splitlines()
        text = target.read_text(encoding="utf-8")
    except OSError:
        return False
    i = text.find("\n" + LESSONS_HEAD)
    if i >= 0:
        text = text[:i]
    block = [f"{LESSONS_HEAD} (from {lessons_md.name}; {who}, not a source)",
             *(l.replace("## ", "### ", 1) if l.startswith("## ") else l for l in body if not l.startswith("# "))]
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text.rstrip("\n") + "\n\n" + "\n".join(block).rstrip("\n") + "\n", encoding="utf-8")
    tmp.replace(target)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rb = sub.add_parser("rebuild")
    rb.add_argument("--since", required=True)
    rb.add_argument("--until")
    rb.add_argument("--dry", action="store_true")
    s = sub.add_parser("state")
    s.add_argument("--min-nights", type=int, default=1)
    sub.add_parser("views")
    at = sub.add_parser("attach-lessons")
    at.add_argument("lessons")
    at.add_argument("target")
    at.add_argument("--who", default="appended by the runner")
    a = ap.parse_args(argv)
    if a.cmd == "attach-lessons":
        ok = attach_lessons(Path(a.lessons), Path(a.target), a.who)
        print(("lessons attached to " if ok else "no lessons attached (a file is missing): ") + a.target)
        return 0
    if a.cmd == "rebuild":
        print(json.dumps(rebuild(a.since, a.until, dry=a.dry)))
        return 0
    if a.cmd == "state":
        st = fold(load())
        for s_ in sorted(st.values(), key=lambda x: (-x["nights"], x["key"])):
            if s_["nights"] >= a.min_nights:
                print(f"{s_['nights']:>2} nights  {s_['state']:<12} {s_['key'][:70]:<70} "
                      f"sum<= {s_['best_sum']} last {s_['last_day']}")
        return 0
    if a.cmd == "views":
        for p in views(load()):
            print(p)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
