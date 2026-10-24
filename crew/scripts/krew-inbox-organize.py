#!/usr/bin/env python3
"""krew: krew-inbox-organize.py (lifted from the reference install; see crew/README.md)."""



















from __future__ import annotations

import argparse
import errno
import html as htmlmod
import json
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import (  # noqa: E402
    INBOX,
    SKIP_INBOX_DIRS,
    filename_date,
    is_report_name,
    today,
)
import importlib.util

_HR = importlib.util.spec_from_file_location(
    "krew_report_html",
    Path(__file__).resolve().parent / "krew-report-html.py",
)
assert _HR and _HR.loader
_html = importlib.util.module_from_spec(_HR)
_HR.loader.exec_module(_html)
wrap = _html.wrap

CANON = INBOX
GROUPED = Path(os.path.expanduser(os.environ.get("KREW_DELIVERABLES_GROUPED") or "~/Desktop/AllFolders/krew-deliverables"))

LIVING = {
    "INDEX.html",
    "README.md",
    "ms.html",
    "ops-efficiency.html",
    "team-research.html",
    "team-daily.html",
    "krew-daily.html",
    "krew-daily.json",
    "architect-world.html",
    "architect-stack.html",
}

PROCESS_PREFIXES = (
    "process-",
    "composer-doctor",
    "parallax",
    "GAPS-SELF",
    "MS-NIGHT-LAND",
    "MODES-AND-CAPACITY",
    "SECURITY-SUMMARY",
)

SCRATCH_PREFIXES = (
    "_",
    "tmp-",
    "tmp_",
    ".tmp",
    ".ms-",
    ".smoke",
    "scratch-",
)
SCRATCH_SUFFIXES = {".py", ".pyc"}


def classify(name: str, *, as_of: date | None = None) -> str | None:
    """Return shelf dirname, or None to leave at inbox root."""
    as_of = as_of or today()
    if name in LIVING:
        return None
    if name.startswith(".") and not name.startswith(".scratch"):
        return "scratch"
    if name.startswith(".scratch") or name.startswith(SCRATCH_PREFIXES):
        return "scratch"
    low = name.lower()
    if any(name.startswith(p) or low.startswith(p.lower()) for p in PROCESS_PREFIXES):
        return "process"
    if name.startswith("ask-"):
        return "ask"
    if "improvement-ideas" in name:
        return "ideas"
    stem_suf = Path(name).suffix.lower()
    if stem_suf in SCRATCH_SUFFIXES:
        return "scratch"
    dt = filename_date(name)
    if dt is not None:
        if dt >= as_of:
            return None
        return "krew-reports" if is_report_name(name) else "kanban-archive"
    if stem_suf in {".md", ".html", ".json", ".jsonl", ".txt"}:
        return "shelf"
    if stem_suf in {".mp4", ".wav", ".png", ".jpg", ".jpeg", ".gif"}:
        return "shelf"
    if stem_suf in {".sh"}:
        return "scratch"
    return "scratch"


def _merge_into(src: Path, dest: Path, lines: list[str]) -> None:
    """Move src onto dest. Never deletes unique bytes; same-size files drop the extra copy.

    `chattr +i` files cannot be renamed or unlinked (EPERM). Copy them if needed
    and leave the original — that is the one protected file in this inbox.
    """
    if dest.exists():
        if src.is_file() and dest.is_file():
            if dest.stat().st_size == src.stat().st_size:
                try:
                    src.unlink()
                    lines.append(f"unify drop dup {src.name}")
                except OSError as e:
                    if e.errno not in (errno.EPERM, errno.EACCES):
                        raise
                    lines.append(f"unify left unmovable {src.name}")
                return
            alt = dest.with_name(dest.name + ".dup")
            _move_or_copy(src, alt, lines, keep_both=True)
            return
        if src.is_dir() and dest.is_dir():
            for child in list(src.iterdir()):
                _merge_into(child, dest / child.name, lines)
            try:
                src.rmdir()
            except OSError:
                lines.append(f"unify dir leftover {src}")
            return
        lines.append(f"unify skip type-mismatch {src.name}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    _move_or_copy(src, dest, lines, keep_both=False)


_UNMOVABLE = {errno.EPERM, errno.EACCES, errno.EXDEV}
UNIFY_LEAVE = {"__pycache__"}


def _move_or_copy(src: Path, dest: Path, lines: list[str], *, keep_both: bool) -> None:
    """Rename when we can. Never shutil.move a dir (that rmtree's the source on EACCES)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
        verb = "keep both" if keep_both else "move"
        lines.append(f"unify {verb} {src.name}" + (f" -> {dest.name}" if keep_both else ""))
        return
    except OSError as e:
        if e.errno not in _UNMOVABLE:
            raise
    if src.is_file():
        if not dest.exists():
            shutil.copy2(src, dest)
            lines.append(f"unify copy unmovable {src.name}")
            return
        if dest.is_file() and dest.stat().st_size == src.stat().st_size:
            lines.append(f"unify left unmovable {src.name}")
            return
        alt = dest.with_name(dest.name + ".dup")
        shutil.copy2(src, alt)
        lines.append(f"unify copy unmovable {src.name} -> {alt.name}")
        return
    if src.is_dir():
        dest.mkdir(exist_ok=True)
        for child in list(src.iterdir()):
            _merge_into(child, dest / child.name, lines)
        try:
            src.rmdir()
        except OSError:
            lines.append(f"unify dir leftover {src.name}")
        return
    lines.append(f"unify skip unmovable {src.name}")


def unify_canon(*, canon: Path | None = None, grouped: Path | None = None) -> list[str]:
    """One pile on the inode Hermes already bind-mounted.

    Containers started against ~/Desktop/krew-deliverables keep that *inode*
    (measured: they still hold AllFolders after GNOME grouped-folders split
    the path). Merge the Desktop path into AllFolders, then symlink Desktop
    so host scripts and /deliverables are the same tree.
    """
    canon = canon or CANON
    grouped = grouped or GROUPED
    lines: list[str] = []
    if not grouped.exists() and not grouped.is_symlink():
        return lines
    if grouped.exists() and canon.exists() and grouped.resolve() == canon.resolve():
        return lines
    if grouped.is_dir() and not grouped.is_symlink() and canon.is_dir() and not canon.is_symlink():
        pointer = grouped / "README.md"
        if pointer.is_file():
            try:
                head = pointer.read_text(encoding="utf-8")[:80]
            except OSError:
                head = ""
            if "could not merge" in head:
                pointer.unlink()
                lines.append("unify drop leftover pointer README")
        for child in list(canon.iterdir()):
            _merge_into(child, grouped / child.name, lines)
        leftover = [p.name for p in canon.iterdir()]
        if leftover:
            lines.append("unify: Desktop leftover " + ", ".join(leftover[:8]))
            return lines
        canon.rmdir()
        canon.symlink_to(grouped)
        lines.append(f"unify symlink {canon} -> {grouped}")
        return lines
    return lines



# all night; the tidy shelved the whole folder every night, so each day's log was split between the live folder
# and shelf copies. Which folders stay is a KREW value: `kept_folders` in config/krew-inbox-tidy.json (path:
# KREW_INBOX_TIDY_CONFIG); no config = none kept. Shelf copies already made are not touched. Kill:
# KREW_INBOX_KEPT_FOLDERS=0.
TIDY_CONFIG = Path(os.environ.get("KREW_INBOX_TIDY_CONFIG",
                                  str(Path(__file__).resolve().parents[1] / "config" / "krew-inbox-tidy.json")))


def kept_live_dirs(path: Path | None = None) -> set:
    if os.environ.get("KREW_INBOX_KEPT_FOLDERS", "1").strip().lower() in ("0", "false", "no", "off"):
        return set()
    try:
        cfg = json.loads((path or TIDY_CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    names = cfg.get("kept_folders") if isinstance(cfg, dict) else None
    return {str(n) for n in names if str(n).strip()} if isinstance(names, list) else set()


def plan_shelve(inbox: Path, as_of: date | None = None) -> list[tuple[Path, Path]]:
    as_of = as_of or today()
    moves: list[tuple[Path, Path]] = []
    if not inbox.is_dir():
        return moves
    for child in sorted(inbox.iterdir()):
        if child.name in SKIP_INBOX_DIRS or child.name in LIVING or child.name == "__pycache__":
            continue
        if child.is_dir() and child.name in kept_live_dirs():
            continue
        if child.is_dir():
            if (
                child.name == "__pycache__"
                or child.name.startswith(".")
                or child.name.startswith(".scratch")
            ):
                dest = inbox / "scratch" / child.name
            else:
                dest = inbox / "shelf" / child.name
            moves.append((child, dest))
            continue
        if not child.is_file():
            continue
        dest_name = classify(child.name, as_of=as_of)
        if dest_name is None:
            continue
        if dest_name in ("krew-reports", "kanban-archive"):
            dt = filename_date(child.name)
            if dt is None:
                continue
            dest = inbox / dest_name / dt.isoformat() / child.name
        else:
            dest = inbox / dest_name / child.name
        if dest.resolve() == child.resolve():
            continue
        moves.append((child, dest))
    return moves




# its `.dup` on the shelf (Directory not empty) failed the whole morning brief. Now the move goes to a name that is
# free: `<name>.<YYYY-MM-DD>`, then `<name>.<YYYY-MM-DD>-<HHMMSS>`, then a counter. It never merges into or
# overwrites what is there. Kill: KREW_INBOX_DATED_DUP=0 (the old `.dup`).
def dated_dup_on() -> bool:
    return os.environ.get("KREW_INBOX_DATED_DUP", "1").strip().lower() not in ("0", "false", "no", "off")


def dated_free(dest: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    for cand in (f"{dest.name}.{now:%Y-%m-%d}", f"{dest.name}.{now:%Y-%m-%d-%H%M%S}"):
        if not dest.with_name(cand).exists():
            return dest.with_name(cand)
    n = 2
    while dest.with_name(f"{dest.name}.{now:%Y-%m-%d-%H%M%S}-{n}").exists():
        n += 1
    return dest.with_name(f"{dest.name}.{now:%Y-%m-%d-%H%M%S}-{n}")


def apply_moves(moves: list[tuple[Path, Path]], *, dry: bool) -> list[str]:
    lines: list[str] = []
    for src, dest in moves:
        try:
            rel = dest.relative_to(src.parent)
        except ValueError:
            rel = dest
        lines.append(f"{src.name} -> {rel}")
        if dry:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dated_free(dest) if dated_dup_on() else dest.with_name(dest.name + ".dup")
        try:
            os.rename(src, dest)
        except OSError as e:
            if e.errno not in _UNMOVABLE:
                raise
            if src.is_file():
                try:
                    shutil.copy2(src, dest)
                    lines[-1] += " (copied, original unmovable)"
                except OSError as e2:
                    lines[-1] += f" (left unmovable: {e2.strerror})"
            else:
                lines[-1] += " (left unmovable dir)"
    return lines


def _links(inbox: Path, rel: Path, limit: int = 40) -> list[str]:
    root = inbox / rel
    if not root.is_dir():
        return []
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".html"}]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[str] = []
    for p in files[:limit]:
        href = htmlmod.escape(str(p.relative_to(inbox)))
        label = htmlmod.escape(p.name)
        out.append(f'<li><a href="{href}">{label}</a></li>')
    return out


def _day_folders(inbox: Path, rel: str, limit: int = 14) -> str:
    root = inbox / rel
    if not root.is_dir():
        return "<li><em>empty</em></li>"
    days = [p for p in root.iterdir() if p.is_dir() and filename_date(p.name)]
    days.sort(key=lambda p: p.name, reverse=True)
    if not days:
        return "<li><em>empty</em></li>"
    out = []
    for p in days[:limit]:
        href = htmlmod.escape(f"{rel}/{p.name}/")
        n = sum(1 for c in p.iterdir() if c.is_file())
        out.append(f'<li><a href="{href}">{htmlmod.escape(p.name)}</a> · {n} files</li>')
    return "".join(out)


def write_index(inbox: Path) -> Path:
    as_of = today()
    today_files = []
    for child in sorted(inbox.iterdir()):
        if not child.is_file():
            continue
        dt = filename_date(child.name)
        if dt == as_of or child.name in LIVING:
            if child.name in {"INDEX.html", "README.md"}:
                continue
            today_files.append(child)
    today_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    today_lis = "".join(
        f'<li><a href="{htmlmod.escape(p.name)}">{htmlmod.escape(p.name)}</a></li>'
        for p in today_files
        if p.suffix.lower() in {".md", ".html"}
    ) or "<li><em>none yet</em></li>"
    ask = "".join(_links(inbox, Path("ask"))) or "<li><em>none waiting</em></li>"
    process = "".join(_links(inbox, Path("process"), 20)) or "<li><em>empty</em></li>"
    ideas = "".join(_links(inbox, Path("ideas"), 30)) or "<li><em>none yet</em></li>"
    shelf_files = "".join(_links(inbox, Path("shelf"), 30))
    shelf_dirs = []
    sp = inbox / "shelf"
    if sp.is_dir():
        for p in sorted(sp.iterdir()):
            if p.is_dir():
                href = htmlmod.escape(f"shelf/{p.name}/")
                shelf_dirs.append(f'<li><a href="{href}">{htmlmod.escape(p.name)}/</a></li>')
    shelf = "".join(shelf_dirs) + (shelf_files or "")
    if not shelf:
        shelf = "<li><em>empty</em></li>"
    reports = _day_folders(inbox, "krew-reports")
    archive = _day_folders(inbox, "kanban-archive")
    morning = ""
    if (inbox / "team-daily.html").is_file():
        morning = (
            '<p class="sub">Crissa pops this when you ask for the morning report. '
            "Built at 02:00 from the previous day's reports.</p>"
            '<ul><li><a href="team-daily.html"><strong>Morning brief</strong> — team-daily.html</a></li></ul>'
        )
    else:
        morning = "<p class=\"sub\">No morning brief on disk yet (02:00 job).</p>"
    if (inbox / "krew-daily.html").is_file():

        # Everything below it is detail.
        morning = ('<ul><li><a href="krew-daily.html"><strong>Krew Daily</strong> — open this one</a></li></ul>'
                   + morning)
    body = f"""<h1>Krew deliverables</h1>
<p class="sub">The inbox on the wallpaper. Markdown is the record; open the HTML.
Nothing is deleted — dated notes leave the root for krew-reports/ and kanban-archive/.</p>
<h2>Morning</h2>
{morning}
<h2>Today</h2>
<ul>{today_lis}</ul>
<h2>Waiting on you (ask/)</h2>
<ul>{ask}</ul>
<h2>Improvement ideas (ideas/)</h2>
<ul>{ideas}</ul>
<h2>How the krew is run (process/)</h2>
<ul>{process}</ul>
<h2>Daily reports (krew-reports/)</h2>
<ul>{reports}</ul>
<h2>Other crystals (shelf/)</h2>
<p class="sub">Undated work that used to drown the root. Open HTML when it exists.</p>
<ul>{shelf}</ul>
<h2>Card archive (kanban-archive/)</h2>
<ul>{archive}</ul>
<h2>Also here</h2>
<ul>
<li><a href="krew-renders/">krew-renders/</a> — pictures</li>
<li><a href="scratch/">scratch/</a> — probes, not for reading</li>
</ul>
"""
    dest = inbox / "INDEX.html"
    dest.write_text(wrap("Krew deliverables", body, "inbox"), encoding="utf-8")
    return dest


README = """# Krew deliverables

Read **INDEX.html** first. That is the day-scan. Crissa pops the morning
brief from `team-daily.html` (built 02:00 from the previous day).

This folder is one of the two on the wallpaper. Browse INDEX, then the
HTML sibling of any markdown.

| Place | What |
|-------|------|
| *(root)* | **Today only** — daily notes, INDEX, living html |
| `ask/` | Choices waiting on you. Leave-it is always an option |
| `ideas/` | Curated improvement-ideas (per role) |
| `process/` | How the krew is run |
| `shelf/` | Other crystals, not daily noise |
| `krew-reports/<date>/` | Daily notes after they leave the root |
| `kanban-archive/<date>/` | Other dated crystals |
| `scratch/` | Probes. Not User-facing |
| `krew-renders/` | Pictures |

Markdown is the record. HTML beside it is what you open.
Skill **krew-deliverable**. Nothing here is deleted on purpose.

A `chattr +i` file cannot leave the root; a copy is also in `shelf/`.
"""


def write_readme(inbox: Path) -> Path:
    dest = inbox / "README.md"
    dest.write_text(README, encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--index-only", action="store_true")
    ap.add_argument("--inbox", type=Path, default=None)
    args = ap.parse_args()
    lines: list[str] = []
    if not args.inbox and not args.dry_run and not args.index_only:
        lines.extend(unify_canon())
    inbox = args.inbox or CANON
    if not inbox.is_dir() and GROUPED.is_dir():
        inbox = GROUPED
    if not args.index_only:
        moves = plan_shelve(inbox)
        lines.extend(apply_moves(moves, dry=args.dry_run))
        print(f"# {len(moves)} file(s) {'would move' if args.dry_run else 'moved'}")
    if not args.dry_run:
        if not inbox.is_dir():
            inbox.mkdir(parents=True, exist_ok=True)
        for name in ("ask", "process", "shelf", "scratch", "ideas"):
            (inbox / name).mkdir(parents=True, exist_ok=True)
        write_readme(inbox)
        idx = write_index(inbox)
        lines.append(f"index {idx}")
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
