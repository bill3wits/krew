#!/usr/bin/env python3
"""Crissa's morning brief — one page from the previous calendar day's reports.

Runs at 02:00 (krew-morning-brief.timer). Default --day is yesterday so
early-morning pop-up is a complete day, not a half-written today.

  python3 scripts/crissa-team-daily.py

"""
from __future__ import annotations

import argparse
import os
import importlib.util
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import INBOX, MS_DIR, filename_date, today  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

_HTML = Path(__file__).resolve().parent / "krew-report-html.py"
_spec = importlib.util.spec_from_file_location("krew_report_html", _HTML)
assert _spec and _spec.loader
_html_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_html_mod)

DAILY_FILES = (
    ("team-research-{day}.md", "team research"),
    ("team-eval-{day}.md", "team eval"),
    ("architect-world-{day}.md", "architect world"),
    ("architect-stack-{day}.md", "architect stack"),
    ("logs-{day}.md", "logs"),
    ("ms-{day}.md", "M&S"),
    ("ops-efficiency-{day}.html", "ops efficiency"),
    ("team-sources-{day}.md", "team sources"),
)

_TEXT_SUFFIX = {".md", ".html", ".txt", ".json"}
_SKIP_OTHER_PREFIX = ("team-daily-",)
_SKIP_OTHER_SUFFIX = {".bak", ".dup", ".pyc"}


def _roles_from_research(md: str) -> dict[str, str]:
    """## ada (board) … until the next ## of the same depth."""
    out: dict[str, str] = {}
    if not md:
        return out
    parts = re.split(r"(?m)^(## .+)$", md)
    # parts[0] is preamble; then heading, body, heading, body...
    for i in range(1, len(parts) - 1, 2):
        heading = parts[i][3:].strip()
        body = parts[i + 1].strip()
        key = heading.split("(")[0].strip().lower()
        if key in ("proposed cards", "pointers for the stack job", "expansion / unlocks"):
            continue
        bullets: list[str] = []
        cur = ""
        for ln in body.splitlines():
            if ln.startswith("- "):
                if cur:
                    bullets.append(cur)
                cur = ln[2:].strip()
            elif cur and ln.strip() and not ln.startswith("#"):
                cur += " " + ln.strip()
        if cur:
            bullets.append(cur)
        if bullets:
            first = bullets[0]
            if len(first) > 220:
                first = first[:217].rstrip() + "..."
            extra = f" (+{len(bullets) - 1} more)" if len(bullets) > 1 else ""
            out[heading] = first + extra
        elif body:
            out[heading] = " ".join(body.split())[:220]
    return out


def _unlocks_today(decisions_path: Path, day: str) -> list[dict]:
    if not decisions_path.is_file():
        return []
    try:
        data = json.loads(decisions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = []
    for r in data.get("decisions") or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("ts") or "") != day:
            continue
        if str(r.get("decision") or "") != "applied":
            continue
        u = r.get("unlock") if isinstance(r.get("unlock"), dict) else {}
        rows.append(
            {
                "env": r.get("env") or "",
                "package": r.get("package") or "",
                "disposition": u.get("disposition") or "pre-unlock",
                "claim": u.get("claim") or (r.get("reason") or "")[:120],
                "kpi_id": u.get("kpi_id") or "",
                "path_on": u.get("path_on"),
            }
        )
    return rows


def yesterday_iso() -> str:
    return (today() - timedelta(days=1)).isoformat()


def _find(inbox: Path, name: str, day: str) -> Path | None:
    """Root first, then the dated krew-reports / kanban-archive shelves."""
    for p in (
        inbox / name,
        inbox / "krew-reports" / day / name,
        inbox / "kanban-archive" / day / name,
    ):
        if p.is_file():
            return p
    return None


def _other_day_files(
    inbox: Path, day: str, already_names: set[str]
) -> list[tuple[str, str]]:
    """Every other text file dated that day (sweeps, reviews, land notes)."""
    dirs = (
        inbox,
        inbox / "krew-reports" / day,
        inbox / "kanban-archive" / day,
    )
    hits: list[tuple[str, str]] = []
    seen: set[str] = set(already_names)
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file():
                continue
            suf = p.suffix.lower()
            if suf in _SKIP_OTHER_SUFFIX or suf not in _TEXT_SUFFIX:
                continue
            if p.name.startswith(_SKIP_OTHER_PREFIX):
                continue
            if p.name in ("INDEX.html", "README.md") or "improvement-ideas" in p.name:
                continue
            dt = filename_date(p.name)
            if dt is None or dt.isoformat() != day:
                continue
            if p.name in seen:
                continue
            md = p.with_suffix(".md")
            html = p.with_suffix(".html")
            if suf in {".html", ".json", ".jsonl"} and md.is_file():
                continue
            if suf == ".json" and html.is_file():
                continue
            try:
                rel = str(p.relative_to(inbox))
            except ValueError:
                rel = p.name
            seen.add(p.name)
            label = p.name
            for token in (f"-{day}", f"-{day.replace('-', '')}", p.suffix):
                label = label.replace(token, "")
            label = label.replace("_", " ").strip("- ") or p.stem
            hits.append((label, rel))
    return hits[:24]


def _lede_of(path: Path, cap: int = 240) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for ln in text.splitlines():
        s = ln.strip()
        if s.lower().startswith("lede:"):
            return s[5:].strip()[:cap]
        if s.startswith("# ") or not s or s.startswith("---"):
            continue
        if s.startswith("- "):
            return s[2:].strip()[:cap]
        if s[0].isalnum() or s[0] in "*":
            return s[:cap]
    return ""


def _board_for(day: str) -> str:
    """That day's board when the krew rolled one, else the fleet's current
    board — the rows are filtered by completed_at either way."""
    home = KH.kanban_dir()
    if (home / "boards" / f"daily-{day}" / "kanban.db").is_file():
        return f"daily-{day}"
    try:
        return (home / "current").read_text().strip() or f"daily-{day}"
    except OSError:
        return f"daily-{day}"


def _completions(day: str) -> list[str]:
    """Cards completed on that day (one standing board or a dated one)."""
    try:
        r = subprocess.run(
            [
                "hermes",
                "-p",
                "ada",
                "kanban",
                "--board",
                _board_for(day),
                "list",
                "--status",
                "done",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        rows = json.loads(r.stdout or "[]")
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        ts = t.get("completed_at")
        if not ts:
            continue
        try:
            when = datetime.fromtimestamp(float(ts)).date().isoformat()
        except (TypeError, ValueError, OSError):
            continue
        if when != day:
            continue
        who = t.get("assignee") or "-"
        title = str(t.get("title") or "")[:90]
        out.append(f"{who}: {title}")
    return out[:24]


def _ideas(inbox: Path) -> list[tuple[str, str]]:
    hits = sorted(inbox.glob("*-improvement-ideas-*.md"))
    ideas = inbox / "ideas"
    if ideas.is_dir():
        hits.extend(sorted(ideas.glob("*-improvement-ideas-*.md")))
    seen: set[str] = set()
    out = []
    for p in hits:
        if p.name in seen:
            continue
        seen.add(p.name)
        who = p.name.split("-improvement-ideas", 1)[0]
        lede = _lede_of(p, 180) or "(no lede)"
        out.append((who, lede))
    return out


_NNT_PICK = re.compile(r"^\s*[-*]?\s*nnt-pick\s*:\s*(.+)$", re.I | re.M)


def _nnt_picks(research_md: str) -> list[str]:
    return [m.group(1).strip()[:240] for m in _NNT_PICK.finditer(research_md or "")]


def _top_improvements(path: Path, limit: int = 12) -> list[str]:
    """The agreed list, newest first, from improvements/proposals.jsonl."""
    out: list[str] = []
    try:
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, json.JSONDecodeError):
        return out
    for r in reversed(rows):
        if isinstance(r, dict) and r.get("status", "agreed") == "agreed":
            out.append(f"{r.get('ts', '')} {r.get('title', '')}".strip())
        if len(out) >= limit:
            break
    return out


def _round(path: Path) -> list[dict] | None:
    """The ranked round from improvements/outcomes.json (None = no ledger with a round yet)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rnd = d.get("round") if isinstance(d, dict) else None
    return [r for r in rnd if isinstance(r, dict)] if isinstance(rnd, list) else None


def build_markdown(
    *,
    day: str,
    inbox: Path,
    ms_dir: Path,
    decisions_path: Path,
) -> str:
    compact = day.replace("-", "")
    research_p = _find(inbox, f"team-research-{day}.md", day)
    research_md = research_p.read_text(encoding="utf-8") if research_p else ""
    roles = _roles_from_research(research_md)

    present = []
    missing = []
    for tmpl, label in DAILY_FILES:
        name = tmpl.format(day=day, compact=compact)
        p = _find(inbox, name, day)
        if p is not None:
            try:
                rel = str(p.relative_to(inbox))
            except ValueError:
                rel = name
            present.append((label, rel))
        else:
            missing.append(label)

    extras = _other_day_files(
        inbox, day, {Path(rel).name for _, rel in present}
    )

    kpis_path = ms_dir / "improvements" / "kpis.json"
    kpis = {}
    if kpis_path.is_file():
        try:
            kpis = json.loads(kpis_path.read_text(encoding="utf-8")).get("kpis") or {}
        except (OSError, json.JSONDecodeError):
            kpis = {}

    unlocks = _unlocks_today(decisions_path, day)
    keep_n = sum(1 for u in unlocks if u["disposition"] == "keep")
    land_n = sum(1 for u in unlocks if u["disposition"] == "land-only")
    toss_n = sum(1 for u in unlocks if u["disposition"] == "toss")
    pre_n = sum(1 for u in unlocks if u["disposition"] == "pre-unlock")

    kpi_bits = []
    for kid, k in kpis.items():
        if not isinstance(k, dict):
            continue
        cur = k.get("current")
        if cur is None:
            kpi_bits.append(f"{kid} = no sample yet")
        else:
            unit = k.get("unit") or ""
            kpi_bits.append(f"{kid} = {cur} {unit}".rstrip())
    if not kpi_bits:
        kpi_bits = ["kpis = none registered"]

    n_roles = len(roles)
    n_files = len(present) + len(extras)
    lede = (
        f"Brief of {day}: {n_roles} role notes; "
        f"{n_files} reports on disk; "
        f"{keep_n} keep / {land_n} land-only / {toss_n} toss among that day's applied lands."
    )
    if not research_p:
        lede = (
            f"Brief of {day}. No team-research-{day}.md. "
            f"{len(present)} other daily files are on disk."
        )

    lines = [
        f"# Team daily - {day}",
        "",
        f"lede: {lede}",
        "kpi: " + "; ".join(
            [
                f"roles = {n_roles}",
                f"files = {n_files}",
                f"keep = {keep_n}",
                f"land-only = {land_n}",
            ]
        ),
        "",
        "## What I found",
    ]
    if roles:
        for heading, one in roles.items():
            lines.append(f"- **{heading}:** {one}")
    else:
        lines.append("- No per-role research note for this day.")
    ms_p = _find(inbox, f"ms-{day}.md", day)
    world_p = _find(inbox, f"architect-world-{day}.md", day)
    stack_p = _find(inbox, f"architect-stack-{day}.md", day)
    eff_html = _find(inbox, f"ops-efficiency-{day}.html", day)
    done = _completions(day)
    ideas = _ideas(inbox)

    lines += ["", f"## M&S ({day})"]
    if ms_p:
        lines.append(f"- `{ms_p.name}`: {_lede_of(ms_p) or 'on disk, no lede'}")
    else:
        lines.append(f"- no `ms-{day}.md`")
    lines += ["", f"## Architecture ({day})"]
    if world_p:
        lines.append(f"- world: {_lede_of(world_p) or world_p.name}")
    else:
        lines.append(f"- no architect-world-{day}.md")
    if stack_p:
        lines.append(f"- stack: {_lede_of(stack_p) or stack_p.name}")
    else:
        lines.append(f"- no architect-stack-{day}.md")
    lines += ["", "## Ada efficiency"]
    if eff_html:
        lines.append(f"- charts: `{eff_html.name}` (ops-efficiency). Keep-KPIs live here.")
    else:
        lines.append("- no ops-efficiency HTML on disk")
    lines += ["", f"## Completions ({day})"]
    if done:
        for row in done:
            lines.append(f"- {row}")
    else:
        lines.append("- none listed on the daily board (or kanban unread)")
    picks = _nnt_picks(research_md)
    lines += ["", "## NNT picks — debate today"]
    if picks:
        lines += [f"- {pk}" for pk in picks]
        lines.append("Answer in your improvement-ideas note: `nnt-agree: <repo> — why` / `nnt-pass: <repo> — why`. "
                     "Agreed → team-improve card on Crissa's green queue + the top improvements list.")
    else:
        lines.append("- no `nnt-pick:` lines in the research note (weekly episode; a quiet week is a pass)")
    ev = _find(inbox, f"team-eval-{day}.md", day)
    if ev is not None:
        rows = [ln for ln in ev.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.startswith("| ") and not ln.startswith("| rank") and not ln.startswith("|---")]
        lines += ["", "## Team eval — ranked (every role scored Scout's picks for its own job)"]
        lines += rows[:8] if rows else ["- no scores"]
        if any("Voices not counted" in ln for ln in ev.read_text(encoding="utf-8", errors="replace").splitlines()):
            lines.append("- some voices were not counted (faked or thin); see the eval note")
    rnd = _round(ms_dir / "improvements" / "outcomes.json")
    if rnd is not None:
        lines += ["", "## The round — ranked (vote → Crissa → try → landed → KPI)",
                  "| # | item | stage | team vote | KPI | baseline → now | 7-day |", "|---|---|---|---|---|---|---|"]
        for i, r in enumerate(rnd, 1):
            b, n = r.get("baseline"), r.get("now")
            bn = f"{b if b is not None else '?'} → {n}" if n is not None else ("" if b is None else str(b))
            card = r.get("try") or r.get("gate") or ""
            lines.append(f"| {i} | {str(r.get('item') or '')[:90]}{f' ({card})' if card else ''} | {r.get('stage', '')} | "
                         f"{str(r.get('vote') or '')[:70]} | {r.get('kpi_id', '')} | {bn} | {r.get('measure', '')} |")
        if not rnd:
            lines.append("| – | nothing in the round | | | | | |")
        lines.append("Ranked: measured movers first, then landed, closed-not-landed, in work, Crissa's queue, "
                     "Ops actions. `now` is the KPI's latest sample; the verdict comes 7 days after landing "
                     "(scripts/improvement-outcomes.py).")
    else:
        agreed = _top_improvements(ms_dir / "improvements" / "proposals.jsonl")
        lines += ["", "## Top improvements (agreed)"]
        lines += [f"- {a}" for a in agreed] or ["- none agreed yet"]
    lines += ["", "## Improvement ideas (curated)"]
    if ideas:
        for who, lede in ideas:
            lines.append(f"- **{who}:** {lede}")
    else:
        lines.append("- no `*-improvement-ideas-*.md` in the inbox")

    if extras:
        lines += ["", "## Also that day"]
        for label, rel in extras[:12]:
            p = inbox / rel
            bit = _lede_of(p, 160) if p.is_file() else ""
            if bit:
                lines.append(f"- **{label}:** {bit}")
            else:
                lines.append(f"- `{rel}`")
        if len(extras) > 12:
            lines.append(f"- (+{len(extras) - 12} more in the table below)")

    lines += [
        "",
        "> Anything that helps we keep. Anything that hurts we toss. Neutral and security we land and do not track.",
        "",
        "## Daily files",
        "| report | file |",
        "|---|---|",
    ]
    for label, name in present:
        lines.append(f"| {label} | `{name}` |")
    for label, rel in extras:
        lines.append(f"| {label} | `{rel}` |")
    if missing:
        lines += ["", "## What I could not verify"]
        lines.append(f"- Missing for {day}: " + ", ".join(missing) + ".")
    else:
        lines += ["", "## What I could not verify"]
        lines.append("- nothing: every usual daily file is on disk.")

    lines += [
        "",
        "## Ops KPIs (efficiency report)",
        "| kpi | sample | current | how |",
        "|---|---|---|---|",
    ]
    if kpis:
        for kid, k in kpis.items():
            if not isinstance(k, dict):
                continue
            cur = k.get("current")
            cur_s = "insufficient history" if cur is None and not (k.get("history")) else (
                "—" if cur is None else str(cur)
            )
            hist = k.get("history") or []
            if len(hist) < 3:
                cur_s = f"{cur_s} (n={len(hist)})"
            lines.append(
                f"| `{kid}` | {k.get('sample') or 'snapshot'} | {cur_s} | "
                f"`{k.get('how_measured') or ''}` |"
            )
    else:
        lines.append("| — | — | — | no kpis.json |")

    lines += [
        "",
        f"## Unlocks landed {day}",
        "| env/package | disposition | path on | kpi | claim |",
        "|---|---|---|---|---|",
    ]
    if unlocks:
        for u in unlocks:
            on = u["path_on"]
            on_s = "yes" if on is True else ("no" if on is False else "—")
            lines.append(
                f"| `{u['env']}/{u['package']}` | {u['disposition']} | {on_s} | "
                f"`{u['kpi_id'] or '—'}` | {u['claim']} |"
            )
    else:
        lines.append(f"| — | — | — | — | no applied rows dated {day} |")
    if keep_n:
        lines.append(
            f"- {keep_n} applied land(s) marked keep: those KPIs belong on the ops-efficiency charts."
        )
    if pre_n:
        lines.append(
            f"- {pre_n} applied land(s) dated {day} have no unlock= yet (pre-step). Neutral until Vera answers."
        )

    lines += [
        "",
        "## Next",
        "1. Crissa: `show_me` this HTML when he asks for the team daily / morning brief.",
        "2. Keep-disposition lands: sample the named KPI (ops tok/s = speed_probe, ad hoc).",
        "",
        "---",
        f"sources: team-research-{day}.md · ops-efficiency-{day}.html · "
        f"{os.environ.get('KREW_IMPROVE_DIR') or '~/.krew/memory/improvements'}/kpis.json · decisions.json applied ts={day}",
        "",
    ]
    return "\n".join(lines)


def write_daily(
    *,
    day: str | None = None,
    inbox: Path | None = None,
    ms_dir: Path | None = None,
    decisions_path: Path | None = None,
) -> Path:
    day = day or yesterday_iso()
    inbox = inbox or INBOX
    ms_dir = ms_dir or MS_DIR
    decisions_path = decisions_path or (MS_DIR / "decisions.json")
    if not inbox.is_dir():
        inbox.mkdir(parents=True, exist_ok=True)
    md_path = inbox / f"team-daily-{day}.md"
    md_path.write_text(
        build_markdown(
            day=day, inbox=inbox, ms_dir=ms_dir, decisions_path=decisions_path
        ),
        encoding="utf-8",
    )
    _html_mod.render_file(
        md_path, kind="crissa", title=f"Team daily - {day}", author="Crissa"
    )
    living = inbox / "team-daily.html"
    html = md_path.with_suffix(".html")
    if html.is_file():
        living.write_text(html.read_text(encoding="utf-8"), encoding="utf-8")
    return md_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", default="", help="YYYY-MM-DD (default yesterday)")
    args = ap.parse_args()
    day = args.day or yesterday_iso()
    path = write_daily(day=day)
    print(path)
    print(path.with_suffix(".html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
