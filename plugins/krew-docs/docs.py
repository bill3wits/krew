"""krew_docs: the docs lookup an agent checks first, and the reference suggestions its owner reviews.

  lookup   ranked passages from the docs index (docs_index.py), only from the configured sources
  read     one passage whole (ref= part=), or a document's outline (ref= alone)
  suggest  a reference worth adding goes to a REVIEW QUEUE the owner approves; nothing suggested
           enters the index on its own

The query runs docs_index.py in a child process (`docs.index_python`, default this interpreter), so an
embedder installed in its own venv never loads into the agent's process. Read-only: the index is never
built or written from here.

A passage longer than `docs.chars` is shortened and says so, with the exact `read` call that returns it
whole. Settings: `docs:` in $KREW_HOME/krew.yaml. Off: KREW_DOCS=0.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import krew_settings as S

HERE = Path(__file__).resolve().parent
DEFAULTS = {"n": 5, "chars": 1200, "timeout_s": 30, "sources_allowed": []}


def on() -> bool:
    return S.switch_on("KREW_DOCS")


def settings(override: dict | None = None) -> dict:
    d = dict(DEFAULTS)
    d.update(S.load("docs"))
    d.update(override or {})
    d["index_python"] = os.path.expanduser(str(d.get("index_python") or sys.executable))
    d["index_script"] = os.path.expanduser(str(d.get("index_script") or (HERE / "docs_index.py")))
    d["queue"] = os.path.expanduser(str(d.get("queue") or (S.krew_home() / "docs-suggestions.jsonl")))
    return d


def _run(argv: list[str], cfg: dict, runner=None) -> tuple[object, str]:
    run = runner or subprocess.run
    try:
        r = run([cfg["index_python"], cfg["index_script"], *argv], capture_output=True, text=True,
                timeout=float(cfg["timeout_s"]))
    except subprocess.TimeoutExpired:
        return None, f"the docs index took longer than {cfg['timeout_s']} s; try fewer words"
    except OSError as e:
        return None, f"the docs index is not reachable ({e})"
    if r.returncode != 0:
        return None, f"the docs index failed: {(r.stderr or '').strip()}"
    try:
        return json.loads(r.stdout or "null"), ""
    except ValueError:
        return None, "the docs index answered something that is not JSON"


def lookup(q: str, *, source: str = "", cfg: dict | None = None, runner=None) -> str:
    cfg = cfg or settings()
    q = (q or "").strip()
    if not q:
        return 'krew_docs: no question. {"action": "lookup", "query": "how does a cron job get its own card"}'
    allowed = [str(s) for s in (cfg.get("sources_allowed") or [])]
    if source and allowed and source not in allowed:
        return f"krew_docs: source {source!r} is not one I can read. Sources: {', '.join(allowed)}"
    want = [source] if source else allowed
    # Ask for more than we keep: the source filter runs after the rank fusion.
    hits, err = _run(["query", q, "-n", str(max(int(cfg["n"]) * 6, 30)), "--json", "--full"], cfg, runner)
    if err:
        return f"krew_docs: {err}"
    hits = hits if isinstance(hits, list) else []
    keep = [h for h in hits if not want or h.get("source") in want][: int(cfg["n"])]
    if not keep:
        where = ", ".join(want) if want else "the index"
        return f"krew_docs: nothing on record for {q!r} in {where}. Say so; do not guess."
    limit = int(cfg["chars"])
    lines = [f"{len(keep)} passages for {q!r} (cite the ref when you answer):"]
    for i, h in enumerate(keep, 1):
        ref = str(h.get("ref") or "")
        head = f" > {h['heading']}" if h.get("heading") else ""
        text = re.sub(r"\s+", " ", str(h.get("text") or "")).strip()
        if len(text) > limit:
            text = (text[:limit] + f" ... [shortened: {len(text) - limit} more chars. The whole passage: "
                    f'krew_docs {{"action": "read", "ref": {json.dumps(ref)}, "part": {h.get("part", 0)}}}]')
        lines.append(f"[{i}] {h.get('source')}:{ref}{head} (part {h.get('part')} of {h.get('parts')})\n{text}")
    return "\n\n".join(lines)


def read(ref: str, part=None, *, cfg: dict | None = None, runner=None) -> str:
    cfg = cfg or settings()
    ref = (ref or "").strip()
    if not ref:
        return 'krew_docs: read needs ref= (from a lookup), and part= for one whole passage.'
    argv = ["read", "--ref", ref] + (["--part", str(int(part))] if part not in (None, "") else [])
    got, err = _run(argv, cfg, runner)
    if err:
        return f"krew_docs: {err}"
    if not isinstance(got, dict) or not got.get("ok"):
        return f"krew_docs: {(got or {}).get('error') if isinstance(got, dict) else 'no answer'}"
    if "text" in got:
        head = f" > {got['heading']}" if got.get("heading") else ""
        return f"{got['source']}:{ref}{head} (part {got['part']} of {got['parts']}, whole)\n{got['text']}"
    rows = "\n".join(f"  part {o['part']}: {o['heading'] or '(no heading)'}" for o in got.get("outline") or [])
    return f"{got['source']}:{ref}: {got['parts']} parts. Read one with part=N.\n{rows}"


def suggest(title: str, where: str, why: str, *, by: str = "", cfg: dict | None = None, now: float | None = None,
            tags: list | None = None) -> str:
    """Append one suggestion to the review queue. The owner approves; nothing is indexed here."""
    cfg = cfg or settings()
    title, where, why = (title or "").strip(), (where or "").strip(), (why or "").strip()
    if not (title and where and why):
        return "krew_docs: a suggestion needs title, where (a URL or path) and why. Nothing was queued."
    q = Path(cfg["queue"])
    q.parent.mkdir(parents=True, exist_ok=True)
    rows = read_queue(q)
    if any(r.get("where") == where for r in rows):
        return f"krew_docs: {where} is already in the review queue."
    ts = time.time() if now is None else now
    row = {"id": f"s{int(ts)}-{len(rows) + 1}", "ts": ts, "by": by, "title": title, "where": where, "why": why,
           "status": "pending"}
    if tags:
        row["tags"] = [str(t) for t in tags]
    with q.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return f"confirmed: queued {row['id']} for review (not in the index until it is approved)."


def read_queue(path: Path | None = None) -> list[dict]:
    p = Path(path or settings()["queue"])
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def handle(p: dict, *, cfg: dict | None = None, runner=None, by: str = "") -> str:
    if not on():
        return "krew_docs: docs lookup is off (KREW_DOCS=0)."
    action = str(p.get("action") or "lookup").strip().lower()
    if action == "suggest":
        return suggest(str(p.get("title") or ""), str(p.get("where") or p.get("url") or ""), str(p.get("why") or ""),
                       by=by, cfg=cfg, tags=p.get("tags") if isinstance(p.get("tags"), list) else None)
    if action == "read":
        return read(str(p.get("ref") or ""), p.get("part"), cfg=cfg, runner=runner)
    return lookup(str(p.get("query") or p.get("q") or p.get("ask") or ""), source=str(p.get("source") or ""),
                  cfg=cfg, runner=runner)


DESCRIPTION = (
    "The docs database: the agent framework's docs, the skills, and whatever folders this install indexes. "
    "Check it FIRST for any how-to question (how does X work, how do I Y, what does this setting do) and cite "
    "the ref you used. action=lookup with query in plain words; action=read with ref (and part) for a whole "
    "passage. When you find a good outside reference, action=suggest with title, where (URL or path) and why: "
    "it goes to the owner's review queue, never straight into the database."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["lookup", "read", "suggest"], "description": "lookup (default), read or suggest"},
        "query": {"type": "string", "description": "lookup: the question in plain words"},
        "source": {"type": "string", "description": "lookup, optional: one source"},
        "ref": {"type": "string", "description": "read: the ref a lookup returned"},
        "part": {"type": "integer", "description": "read: the part number a lookup returned"},
        "title": {"type": "string", "description": "suggest: the reference's title"},
        "where": {"type": "string", "description": "suggest: its URL or file path"},
        "why": {"type": "string", "description": "suggest: what it answers, in one sentence"},
    },
}
