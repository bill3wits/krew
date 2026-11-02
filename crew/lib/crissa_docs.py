"""krew: crissa_docs.py (lifted from the reference install; see crew/README.md)."""

















from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from krew_cut import cut  # beside it in crew/lib
import krew_hermes as KH  # noqa: E402  where Hermes lives

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
CONFIG = Path(os.environ.get("KREW_CRISSA_DOCS_CONFIG") or ROOT / "config" / "crissa-docs.json")

DEFAULTS = {
    # Never `memory` (a coding session's private notes) or `kanban`/`deliverables`/`archive`
    # (the board and the krew's output answer "what happened", not "how do I").
    "sources": ["hermes", "hermes-changes", "knowledge", "skills", "docs", "rails", "render", "places"],
    "n": 5,
    "chars": 700,
    "timeout_s": 20,
    "index_python": os.environ.get("KREW_DOCS_INDEX_PYTHON") or str(HOME / ".krew/embedder-venv/bin/python"),
    "index_script": os.environ.get("KREW_DOCS_INDEX_SCRIPT") or str(KH.hermes_home() / "plugins/krew-docs/docs_index.py"),
    "queue": str(KH.hermes_home() / "crissa-docs-suggestions.jsonl"),
}


def on() -> bool:
    return os.environ.get("KREW_CRISSA_DOCS", "1").strip().lower() not in ("0", "false", "no", "off")


def settings(path: Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = path or CONFIG
    try:
        cfg.update(json.loads(Path(p).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    for k in ("index_python", "index_script", "queue"):
        cfg[k] = os.path.expanduser(str(cfg[k]))
    return cfg


def _query(q: str, cfg: dict, runner=None) -> tuple[list[dict], str]:
    py = cfg["index_python"] if Path(cfg["index_python"]).exists() else "python3"
    # Ask for more than we keep: the source filter runs after the rank fusion.
    argv = [py, cfg["index_script"], "query", q, "-n", str(max(int(cfg["n"]) * 6, 30)), "--json", "--full"]
    run = runner or subprocess.run
    try:
        r = run(argv, capture_output=True, text=True, timeout=float(cfg["timeout_s"]))
    except subprocess.TimeoutExpired:
        return [], "the docs index took too long; try fewer words"
    except OSError as e:
        return [], f"the docs index is not reachable ({e})"
    if r.returncode != 0:
        return [], f"the docs index failed: {(r.stderr or '').strip()[-200:]}"
    try:
        hits = json.loads(r.stdout or "[]")
    except ValueError:
        return [], "the docs index answered something that is not a result list"
    return hits if isinstance(hits, list) else [], ""


def lookup(q: str, *, source: str = "", cfg: dict | None = None, runner=None) -> str:
    cfg = cfg or settings()
    q = (q or "").strip()
    if not q:
        return 'krew_docs: no question. {"action": "lookup", "query": "how does a cron job get its own card"}'
    allowed = [s for s in cfg["sources"]]          # [] = every source the index holds
    if source:
        if source not in allowed:
            return f"krew_docs: source {source!r} is not one I can read. Sources: {', '.join(allowed)}"
        allowed = [source]
    hits, err = _query(q, cfg, runner)
    if err:
        return f"krew_docs: {err}"
    keep = [h for h in hits if not allowed or h.get("source") in allowed][: int(cfg["n"])]
    if not keep:
        return f"krew_docs: nothing on record for {q!r} in {', '.join(allowed)}. Say so; do not guess."
    lines = [f"{len(keep)} passages for {q!r} (cite the ref when you answer):"]
    for i, h in enumerate(keep, 1):
        ref = str(h.get("ref") or "")
        head = f" > {h['heading']}" if h.get("heading") else ""
        text = cut(re.sub(r"\s+", " ", str(h.get("text") or "")).strip(), int(cfg["chars"]),
                   f"read the whole passage: docs_index.py read --ref {ref}")
        lines.append(f"[{i}] {h.get('source')}:{ref}{head}\n{text}")
    return "\n\n".join(lines)


def suggest(title: str, where: str, why: str, *, by: str = "crissa", cfg: dict | None = None,
            now: float | None = None, tags: list[str] | None = None) -> str:
    """Append one suggestion to the review queue. The User approves; nothing is indexed here."""
    cfg = cfg or settings()
    title, where, why = (title or "").strip(), (where or "").strip(), (why or "").strip()
    if not (title and where and why):
        return 'krew_docs: a suggestion needs title, where (a URL or path) and why. Nothing was queued.'
    q = Path(cfg["queue"])
    q.parent.mkdir(parents=True, exist_ok=True)
    rows = read_queue(q)
    if any(r.get("where") == where for r in rows):
        return f"krew_docs: {where} is already in the review queue."
    ts = time.time() if now is None else now
    row = {"id": f"s{int(ts)}-{len(rows) + 1}", "ts": ts, "by": by, "title": title,
           "where": where, "why": why, "status": "pending"}
    if tags:
        row["tags"] = [str(t)[:40] for t in tags][:8]
    with q.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return f"confirmed: queued {row['id']} for the user's review (not in the index until he approves)."


def read_queue(path: Path | None = None) -> list[dict]:
    p = Path(path or settings()["queue"])
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def write_queue(rows: list[dict], path: Path | None = None) -> None:
    p = Path(path or settings()["queue"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, p)


def handle(p: dict, *, cfg: dict | None = None, runner=None, by: str = "crissa") -> str:
    """The tool's one entry. action = lookup (default) | suggest."""
    if not on():
        return "krew_docs: docs lookup is off (KREW_CRISSA_DOCS=0)."
    action = str(p.get("action") or "lookup").strip().lower()
    if action == "suggest":
        return suggest(str(p.get("title") or ""), str(p.get("where") or p.get("url") or ""),
                       str(p.get("why") or ""), by=by or "crissa", cfg=cfg)
    return lookup(str(p.get("query") or p.get("q") or p.get("ask") or ""),
                  source=str(p.get("source") or ""), cfg=cfg, runner=runner)


DESCRIPTION = (
    "The house's docs database: the hermes docs, the krew knowledge wiki, the skills, and the krew places "
    "(where things are: product reference photos, render outputs, ready-to-post drafts). "
    "Check it FIRST for any how-to question (how does X work, how do I Y, what does this setting do) "
    "and cite the ref you used. action=lookup with query in plain words. "
    "When you find a good outside reference, action=suggest with title, where (URL or path) and why: "
    "it goes to the user's review queue, never straight into the database."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["lookup", "suggest"], "description": "lookup (default) or suggest"},
        "query": {"type": "string", "description": "lookup: the question in plain words"},
        "source": {"type": "string", "description": "lookup, optional: one source, e.g. hermes"},
        "title": {"type": "string", "description": "suggest: the reference's title"},
        "where": {"type": "string", "description": "suggest: its URL or file path"},
        "why": {"type": "string", "description": "suggest: what it answers, in one sentence"},
    },
}
