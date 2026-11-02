"""krew: krew_places.py (lifted from the reference install; see crew/README.md)."""













from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("KREW_PLACES_CONFIG") or ROOT / "config" / "krew-places.json")
SKIP = {".git", "__pycache__", "node_modules", ".thumbnails"}
NOTE_CHARS = 4000


def on() -> bool:
    return os.environ.get("KREW_PLACES", "1").strip().lower() not in ("0", "false", "no", "off")


def settings(path: Path | None = None) -> dict:
    try:
        return json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"places": []}


def _x(p: str) -> Path:
    return Path(os.path.expanduser(str(p)))


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _folders(root: Path, max_depth: int) -> list[Path]:
    """Every folder under root (root included) that holds at least one file, up to max_depth below root."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        depth = len(d.relative_to(root).parts)
        dirnames[:] = sorted(n for n in dirnames if n not in SKIP and not n.startswith("."))
        if depth >= max_depth:
            dirnames[:] = []
        if any(not f.startswith(".") for f in filenames):
            out.append(d)
    return sorted(out)


def _files(d: Path) -> list[tuple[str, int, float]]:
    rows = []
    for p in sorted(d.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            try:
                st = p.stat()
            except OSError:
                continue
            rows.append((p.name, st.st_size, st.st_mtime))
    return rows


def _notes(d: Path, names: list[str]) -> str:
    parts = []
    for n in names:
        p = d / n
        if p.is_file():
            try:
                parts.append(f"{n}:\n" + p.read_text(encoding="utf-8", errors="replace").strip()[:NOTE_CHARS])
            except OSError:
                continue
    return "\n\n".join(parts)


def _card_title(card: str) -> str:
    """The card's title from the kanban boards (read-only), so a render folder answers to its words."""
    if not card:
        return ""
    import sqlite3
    base = Path(os.environ.get("KREW_KANBAN_BOARDS") or (KH.kanban_dir() / "boards"))
    for db in sorted(base.glob("*/kanban.db")):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                r = con.execute("SELECT title FROM tasks WHERE id = ?", (card,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if r and r[0]:
            return str(r[0])[:200]
    return ""


def _order(orders: Path, oid: str) -> dict:
    for state in ("done", "running", "pending", "failed"):
        p = orders / state / f"{oid}.json"
        if p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {"state": state}
            prompt = ""
            for node in (d.get("graph") or {}).values():
                if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                    prompt = str((node.get("inputs") or {}).get("text") or "")
                    break
            return {"state": d.get("state") or state, "card": d.get("card") or "",
                    "card_title": _card_title(str(d.get("card") or "")), "workflow": d.get("workflow") or "",
                    "created": d.get("created") or "", "requested_by": d.get("requested_by") or "",
                    "prompt": prompt[:400]}
    return {}


ORDER_ID = re.compile(r"\bro-\d{8}-\d{6}-[A-Za-z0-9]+\b")


def _order_links(cfg: dict, note_names: list[str]) -> dict[str, list[str]]:
    """order id -> the folders whose notes name it ("Used by render order ro-..." in a refs SOURCES.md), each as
    "<folder> (<its first heading>)", so the render folder answers to the product's name too."""
    links: dict[str, list[str]] = {}
    for pl in cfg.get("places") or []:
        root = _x(pl.get("root") or "")
        if (pl.get("kind") or "folders") != "folders" or not root.is_dir():
            continue
        for f in _folders(root, int(pl.get("max_depth") or 3)):
            notes = _notes(f, note_names)
            ids = set(ORDER_ID.findall(notes))
            if not ids:
                continue
            first = next((ln.strip("# ").strip() for ln in notes.splitlines()[1:] if ln.strip()), "")
            for oid in ids:
                links.setdefault(oid, []).append(f"{_home(f)}/" + (f" ({first[:160]})" if first else ""))
    return links


def _home(p: Path) -> str:
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h + "/") or s == h else s


def walk(cfg: dict | None = None) -> list[dict]:
    """The rows: [{"ref", "title", "mtime", "text", "place"}], overview first per place."""
    if not on():
        return []
    cfg = cfg or settings()
    note_names = list(cfg.get("notes") or ["README.md", "SOURCES.md"])
    rows: list[dict] = []
    links = _order_links(cfg, note_names)
    for pl in cfg.get("places") or []:
        root = _x(pl.get("root") or "")
        if not str(root) or not root.is_dir():
            continue
        name = str(pl.get("name") or root.name)
        kind = pl.get("kind") or "folders"
        if kind == "render-orders":
            folders = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        else:
            folders = [f for f in _folders(root, int(pl.get("max_depth") or 3)) if f != root or _files(root)]
        head = [f"# Krew place: {name}", f"where: {_home(root)}/",
                f"pattern: {pl.get('pattern') or _home(root) + '/<folder>/'}"]
        if pl.get("what"):
            head.append(f"what: {pl['what']}")
        if pl.get("rule"):
            head.append(f"rule: {pl['rule']}")
        lines = []
        newest = root.stat().st_mtime
        for f in folders:
            files = _files(f)
            mtime = max([m for _, _, m in files] + [f.stat().st_mtime])
            newest = max(newest, mtime)
            rel = f.relative_to(root).as_posix() if f != root else "."
            body = [f"# {name}: {rel}", f"folder: {_home(f)}/", f"part of: {name} ({_home(root)}/)"]
            if pl.get("what"):
                body.append(f"what this place is: {pl['what']}")
            if pl.get("rule"):
                body.append(f"rule: {pl['rule']}")
            summary = ""
            if kind == "render-orders":
                o = _order(_x(pl.get("orders") or root.parent), f.name)
                if o:
                    body.append(f"render order {f.name}: state {o.get('state')}, card {o.get('card') or '-'}, "
                                f"workflow {o.get('workflow') or '-'}, filed {o.get('created') or '-'}")
                    if o.get("card_title"):
                        body.append(f"card {o.get('card')}: {o['card_title']}")
                    for ref in links.get(f.name, []):
                        body.append(f"made with reference photos: {ref}")
                    if o.get("prompt"):
                        body.append(f"prompt (opening): {o['prompt']}")
                    summary = f"card {o.get('card') or '-'}" + (f" ({o['card_title']})" if o.get("card_title") else "") + \
                        "".join(f", refs {r.split(' (')[0]}" for r in links.get(f.name, [])) + \
                        f", {o.get('workflow') or '-'}, {o.get('state')}"
            body.append(f"files ({len(files)}):")
            body += [f"- {n} ({_size(s)}, {time.strftime('%Y-%m-%d %H:%M', time.localtime(m))})" for n, s, m in files]
            notes = _notes(f, note_names)
            if notes:
                body += ["", notes]
                first = next((ln.strip("# ").strip() for ln in notes.splitlines()[1:] if ln.strip()), "")
                summary = summary or first[:120]
            rows.append({"place": name, "ref": f"{f}/", "title": f"{name}: {rel}", "mtime": mtime,
                         "text": "\n".join(body)})
            lines.append(f"- {_home(f)}/ - {len(files)} files" + (f" - {summary}" if summary else ""))
        rows.insert(len(rows) - len(folders), {
            "place": name, "ref": f"{root}/", "title": f"Krew place: {name}", "mtime": newest,
            "text": "\n".join(head + [f"folders ({len(folders)}):"] + (lines or ["- (none yet)"]))})
    return rows


def render_map(rows: list[dict]) -> str:
    out = ["# Krew places (generated by lib/krew_places.py from what is on disk; do not edit)",
           f"written {time.strftime('%Y-%m-%d %H:%M %Z')}. Ask it with krew_docs lookup; the kinds of thing are in docs/FILESYSTEM.md.", ""]
    for r in rows:
        if r["title"].startswith("Krew place: "):
            out += ["", r["text"].replace("# Krew place:", "## ", 1).replace("##  ", "## ", 1)]
    return "\n".join(out).strip() + "\n"


def write_map(rows: list[dict], cfg: dict | None = None) -> Path | None:
    cfg = cfg or settings()
    if not on() or not cfg.get("map_file"):
        return None
    p = _x(cfg["map_file"])
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(render_map(rows), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        return None
    return p


def iter_docs(source: str = "places", cfg: dict | None = None):
    """krew-index's source: one walk, the map file rewritten from it, then the rows."""
    cfg = cfg or settings()
    rows = walk(cfg)
    if rows:
        write_map(rows, cfg)
    for r in rows:
        yield {"source": source, "ref": r["ref"], "title": r["title"], "mtime": r["mtime"], "text": r["text"]}


if __name__ == "__main__":
    import sys
    rows = walk()
    if "--write" in sys.argv:
        print(write_map(rows) or "map not written")
    else:
        sys.stdout.write(render_map(rows))
