#!/usr/bin/env python3
"""krew docs index: semantic + keyword recall over the written record an agent should check first.

Hybrid on purpose. BM25 (SQLite FTS5) wins on exact nouns: switch names, file paths, ids. Vectors win
on the question nobody phrased the same way twice. Both run and the ranks are fused (reciprocal rank).

    python3 docs_index.py build [--source hermes,skills] [--full]
    python3 docs_index.py query "how does a cron job get its own card" [-n 8] [--json] [--full]
    python3 docs_index.py read --ref <ref> [--part N]
    python3 docs_index.py status

What it indexes is settings, not code: `docs.sources` in $KREW_HOME/krew.yaml maps a source name to a
folder (see config/krew.example.yaml). With no sources set it indexes the framework's own docs (from
the framework tree: `docs.hermes_tree`, else $HERMES_TREE, else ~/.hermes/hermes-agent), the skills
under $HERMES_HOME/skills, and `built` ($KREW_HOME/built: what krew-skill-builder and krew-tool-builder made).

Storage: `docs.index_db`, default $KREW_HOME/docs-index/index.db. Embeddings (2026-11-01, the owner: one model on
disk for everything krew and the Crissa kit run): intfloat/multilingual-e5-small's ONNX export at the pinned revision
(E5, the same pin as crew/local-services.json; krew-install --memory fetches it into the shared HF home), run with
onnxruntime + tokenizers on the CPU, mean-pooled and L2-normalised, "passage: " / "query: " prefixed as e5 wants: a
vector from the kit's knowledge index compares with one from here. onnxruntime, tokenizers and numpy come from
`docs.embedder_venv` when set, else the Hindsight venv krew installs ($KREW_HOME/hindsight/.venv), else this
interpreter. `docs.embedder: fastembed` + `docs.embedder_model` is the old path. Without an embedder the index still
builds and answers, BM25-only, and says so in `status`. A model change re-embeds everything on the next build.
Off: KREW_DOCS_INDEX=0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
try:
    from . import krew_hermes as KH
except ImportError:                 # imported alone (tests, scripts)
    import krew_hermes as KH  # noqa: E402

try:
    from . import krew_settings as S                     # inside the framework (plugin package)
except ImportError:                                      # run as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import krew_settings as S                            # noqa: E402

DIM = 384
# The one embedding model (crew/local-services.json `embedding`; tests/test_local_services.py holds them equal).
E5 = {"repo": "intfloat/multilingual-e5-small", "revision": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
      "hf_home": "${XDG_DATA_HOME:-~/.local/share}/crissa-models/hf", "query": "query: ", "passage": "passage: "}
CHUNK = 1200          # chars; a heading's prose, not a paragraph and not a file
# OVERLAP is why the chunk loop stops at the tail: once fewer than OVERLAP chars remain the stride
# would clamp to 1 and emit a near-duplicate chunk per character.
OVERLAP = 150
MAX_FILE = 2_000_000  # bytes; a larger file is skipped and counted in the build stats as `too_big`
TEXT_EXT = {".md", ".mdx", ".txt", ".json", ".yaml", ".yml", ".rst"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv"}
# README.es.md / .zh-CN.md ...: the same page again in another language; it competes for the same rank.
SKIP_NAME = re.compile(r"\.(es|zh-CN|ur-pk|fr|de|ja|ko|pt-BR|ru)\.mdx?$", re.I)


def cfg() -> dict:
    d = S.load("docs")
    home = S.krew_home()
    hermes_home = Path(os.path.expanduser(os.environ.get("HERMES_HOME") or str(KH.hermes_home())))
    tree = Path(os.path.expanduser(str(d.get("hermes_tree") or os.environ.get("HERMES_TREE")
                                       or (hermes_home / "hermes-agent"))))
    return {
        "db": Path(os.path.expanduser(str(d.get("index_db") or (home / "docs-index" / "index.db")))),
        "venv": (Path(os.path.expanduser(str(d["embedder_venv"]))) if d.get("embedder_venv")
                 else (home / "hindsight" / ".venv") if (home / "hindsight" / ".venv").is_dir() else None),
        "embedder": str(d.get("embedder") or "e5"),
        "model": (str(d.get("embedder_model") or "BAAI/bge-small-en-v1.5") if str(d.get("embedder") or "e5") == "fastembed"
                  else f"{E5['repo']}@{E5['revision'][:12]}"),
        "hf_home": Path(os.path.expanduser(re.sub(r"\$\{XDG_DATA_HOME:-([^}]*)\}",
                                                  lambda m: os.environ.get("XDG_DATA_HOME") or m.group(1),
                                                  str(d.get("hf_home") or E5["hf_home"])))),
        "threads": int(d.get("embedder_threads") or 4),
        "batch": int(d.get("embedder_batch") or 64),
        "sources": d.get("sources") or {},
        "hermes_tree": tree,
        "hermes_home": hermes_home,
    }


def on() -> bool:
    return S.switch_on("KREW_DOCS_INDEX")


SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, ref TEXT NOT NULL UNIQUE,
  title TEXT, mtime REAL, sha TEXT, nchunks INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, ord INTEGER, heading TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, heading, content='chunks', content_rowid='id', tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS vecs(chunk_id INTEGER PRIMARY KEY, v BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or cfg()["db"])
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    # A reader waits for a running build instead of failing. Rollback journal (not WAL), the
    # framework's own choice for its SQLite stores.
    con.execute("PRAGMA busy_timeout = 30000")
    con.executescript(SCHEMA)
    return con


def chunk_text(text: str) -> list[tuple[str, str]]:
    """(heading, body) pieces. Markdown headings are the seam; a long section is packed with overlap
    so a sentence is never cut between two chunks that both then miss it."""
    text = text.replace("\r\n", "\n")
    parts: list[tuple[str, str]] = []
    cur_head, buf = "", []
    for ln in text.split("\n"):
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            if buf:
                parts.append((cur_head, "\n".join(buf).strip()))
                buf = []
            cur_head = m.group(2).strip()
            continue
        buf.append(ln)
    if buf:
        parts.append((cur_head, "\n".join(buf).strip()))
    out: list[tuple[str, str]] = []
    for head, body in parts:
        if not body:
            continue
        if len(body) <= CHUNK:
            out.append((head, body))
            continue
        i = 0
        while i < len(body):
            piece = body[i:i + CHUNK]
            if len(piece) == CHUNK:
                cut = max(piece.rfind("\n\n"), piece.rfind(". "), piece.rfind("\n"))
                if cut > CHUNK // 2:
                    piece = piece[:cut + 1]
            out.append((head, piece.strip()))
            if i + len(piece) >= len(body):
                break
            i += max(1, len(piece) - OVERLAP)
    return [(h, b) for h, b in out if len(b) > 40]


def iter_files(base: Path, source: str, stats: dict | None = None):
    if not base.is_dir():
        return
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
            continue
        if any(part in SKIP_DIRS for part in p.parts) or SKIP_NAME.search(p.name):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size == 0:
            continue
        if st.st_size > MAX_FILE:
            if stats is not None:
                stats.setdefault("too_big", []).append(str(p))
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        yield {"source": source, "ref": str(p), "title": p.name, "mtime": st.st_mtime, "text": body}


def hermes_docs_dir(tree: Path) -> Path:
    """The framework's docs (Docusaurus `website/docs/`, older trees `docs/`)."""
    for d in (tree / "website" / "docs", tree / "docs"):
        if d.is_dir():
            return d
    return tree / "website" / "docs"


def sources_map(c: dict | None = None, stats: dict | None = None) -> dict:
    c = c or cfg()
    conf = c["sources"]
    if not conf:
        # `built`: what krew-skill-builder / krew-tool-builder made and installed ($KREW_HOME/built), so the crew and
        # Crissa can find a profile's own new skill or tool, not only the shared ones
        conf = {"hermes": str(hermes_docs_dir(c["hermes_tree"])), "skills": str(c["hermes_home"] / "skills"),
                "built": str(S.krew_home() / "built")}
    out = {}
    for name, spec in conf.items():
        path = spec.get("path") if isinstance(spec, dict) else spec
        if str(path).strip() == "hermes-docs":
            path = str(hermes_docs_dir(c["hermes_tree"]))
        base = Path(os.path.expanduser(str(path)))
        out[str(name)] = (lambda b=base, n=str(name): iter_files(b, n, stats))
    return out


_EMB: dict = {}


class E5Onnx:
    """e5's ONNX export on the CPU: tokenizer.json + model.onnx from the pinned snapshot; mean pooling over the
    attention mask, then L2 (embed() normalises)."""

    def __init__(self, snap: Path, threads: int):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np = np
        self.tok = Tokenizer.from_file(str(snap / "onnx" / "tokenizer.json"))
        self.tok.enable_truncation(max_length=512)
        self.tok.enable_padding()
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(str(snap / "onnx" / "model.onnx"), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}

    def embed(self, texts: list[str], batch_size: int = 64):
        np = self.np
        for i in range(0, len(texts), batch_size):
            enc = self.tok.encode_batch(texts[i:i + batch_size])
            ids = np.array([e.ids for e in enc], dtype="int64")
            mask = np.array([e.attention_mask for e in enc], dtype="int64")
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run(None, feed)[0]
            m = mask[..., None].astype("float32")
            yield from (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)


def e5_snapshot(c: dict) -> Path:
    """The pinned snapshot in the shared HF home: the standard hub cache (<HF_HOME>/hub/models--...) first, else the
    same layout straight under it (a cache set with HF_HUB_CACHE=<HF_HOME>), so one copy on disk serves both."""
    tail = Path("models--" + E5["repo"].replace("/", "--")) / "snapshots" / E5["revision"]
    for base in (c["hf_home"] / "hub", c["hf_home"]):
        if (base / tail / "onnx" / "model.onnx").is_file():
            return base / tail
    return c["hf_home"] / "hub" / tail


def embedder(quiet: bool = False):
    if "m" in _EMB:
        return _EMB["m"]
    c = cfg()
    if c["venv"] is not None:
        sp = list(c["venv"].glob("lib/python*/site-packages"))
        if sp:
            sys.path.insert(0, str(sp[0]))
    try:
        os.environ.setdefault("OMP_NUM_THREADS", str(c["threads"]))
        if c["embedder"] == "fastembed":
            from fastembed import TextEmbedding
            _EMB["m"] = TextEmbedding(c["model"], threads=c["threads"])
        else:
            snap = e5_snapshot(c)
            if not (snap / "onnx" / "model.onnx").is_file():
                raise FileNotFoundError(f"{snap / 'onnx' / 'model.onnx'} (krew-install --memory fetches it)")
            _EMB["m"] = E5Onnx(snap, c["threads"])
    except Exception as e:  # noqa: BLE001
        if not quiet:
            print(f"krew docs index: no embedder ({type(e).__name__}: {e}); BM25 only", file=sys.stderr)
        _EMB["m"] = None
    return _EMB["m"]


def embed(texts: list[str], kind: str = "passage") -> list[bytes]:
    m = embedder()
    if not m:
        return []
    import numpy as np
    if cfg()["embedder"] != "fastembed":
        texts = [E5[kind] + t for t in texts]
    out = []
    for v in m.embed(texts, batch_size=cfg()["batch"]):
        a = np.asarray(v, dtype="float32")
        n = float(np.linalg.norm(a)) or 1.0
        out.append((a / n).tobytes())
    return out


def build(which: list[str] | None = None, *, full: bool = False, con: sqlite3.Connection | None = None,
          progress=print) -> dict:
    if not on():
        return {"ok": True, "killed": True}
    con = con or connect()
    stats: dict = {"seen": 0, "new": 0, "updated": 0, "skipped": 0, "chunks": 0, "embedded": 0, "pruned": 0}
    had = (con.execute("SELECT v FROM meta WHERE k = 'model'").fetchone() or [None])[0]
    if embedder(quiet=True) and had not in (None, cfg()["model"]):
        full = True                     # another model's vectors never mix with this one's: re-embed everything
        stats["model_change"] = f"{had} -> {cfg()['model']}"
    smap = sources_map(stats=stats)
    which = which or list(smap)
    for name in which:
        if name not in smap:
            raise SystemExit(f"unknown source {name!r}; have {', '.join(smap)}")
        live: set[str] = set()
        for doc in smap[name]():
            live.add(doc["ref"])
            stats["seen"] += 1
            sha = hashlib.sha1(doc["text"].encode("utf-8", "replace")).hexdigest()
            row = con.execute("SELECT id, sha FROM docs WHERE ref = ?", (doc["ref"],)).fetchone()
            if row and row["sha"] == sha and not full:
                stats["skipped"] += 1
                continue
            pieces = chunk_text(doc["text"])
            if not pieces:
                stats["skipped"] += 1
                continue
            with con:
                if row:
                    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE doc_id = ?", (row["id"],))]
                    if ids:
                        qs = ",".join("?" * len(ids))
                        con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({qs})", ids)
                        con.execute(f"DELETE FROM vecs WHERE chunk_id IN ({qs})", ids)
                        con.execute("DELETE FROM chunks WHERE doc_id = ?", (row["id"],))
                    con.execute("UPDATE docs SET title=?, mtime=?, sha=?, nchunks=? WHERE id=?",
                                (doc["title"], doc["mtime"], sha, len(pieces), row["id"]))
                    doc_id = row["id"]
                    stats["updated"] += 1
                else:
                    cur = con.execute("INSERT INTO docs(source, ref, title, mtime, sha, nchunks) VALUES (?,?,?,?,?,?)",
                                      (doc["source"], doc["ref"], doc["title"], doc["mtime"], sha, len(pieces)))
                    doc_id = cur.lastrowid
                    stats["new"] += 1
                cids = []
                for i, (head, body) in enumerate(pieces):
                    cur = con.execute("INSERT INTO chunks(doc_id, ord, heading, text) VALUES (?,?,?,?)",
                                      (doc_id, i, head, body))
                    cids.append(cur.lastrowid)
                    con.execute("INSERT INTO chunks_fts(rowid, text, heading) VALUES (?,?,?)", (cur.lastrowid, body, head))
                stats["chunks"] += len(pieces)
                vecs = embed([f"{h}\n{b}" if h else b for h, b in pieces])
                if vecs:
                    con.executemany("INSERT OR REPLACE INTO vecs(chunk_id, v) VALUES (?,?)", list(zip(cids, vecs)))
                    stats["embedded"] += len(vecs)
            if progress and stats["seen"] % 200 == 0:
                progress(f"  {name}: {stats['seen']} seen, {stats['chunks']} chunks")
        stats["pruned"] += prune(con, name, live)
    with con:
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('built_at', ?)", (str(time.time()),))
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('model', ?)",
                    (cfg()["model"] if embedder(quiet=True) else "none (BM25 only)",))
    return stats


def prune(con: sqlite3.Connection, source: str, live: set[str]) -> int:
    """Drop rows for refs this scan did not see: an index that outlives its corpus is confidently wrong."""
    gone = [r["id"] for r in con.execute("SELECT id, ref FROM docs WHERE source = ?", (source,)) if r["ref"] not in live]
    with con:
        for doc_id in gone:
            ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))]
            if ids:
                qs = ",".join("?" * len(ids))
                con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({qs})", ids)
                con.execute(f"DELETE FROM vecs WHERE chunk_id IN ({qs})", ids)
                con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            con.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
    return len(gone)


def query(q: str, n: int = 8, *, con: sqlite3.Connection | None = None, source: str | None = None) -> list[dict]:
    """BM25 and cosine, fused by reciprocal rank (k=60)."""
    con = con or connect()
    K = 60
    ranks: dict[int, float] = {}
    why: dict[int, list[str]] = {}
    # Each token quoted: a bare hyphenated id is FTS5 syntax ("no such column"); a quoted token is a phrase.
    toks = re.findall(r"[A-Za-z0-9_][A-Za-z0-9_.-]{2,}", q)
    fts_q = " OR ".join('"' + t.replace('"', "") + '"' for t in toks) or ('"' + q.replace('"', "") + '"')
    try:
        rows = con.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 60",
                           (fts_q,)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for i, r in enumerate(rows):
        ranks[r[0]] = ranks.get(r[0], 0.0) + 1.0 / (K + i + 1)
        why.setdefault(r[0], []).append("bm25")
    qv = embed([q], kind="query")
    if qv:
        import numpy as np
        vrows = con.execute("SELECT chunk_id, v FROM vecs").fetchall()
        if vrows:
            ids = np.array([r[0] for r in vrows])
            mat = np.frombuffer(b"".join(r[1] for r in vrows), dtype="float32").reshape(len(ids), DIM)
            sims = mat @ np.frombuffer(qv[0], dtype="float32")
            for i, j in enumerate(np.argsort(-sims)[:60]):
                cid = int(ids[j])
                ranks[cid] = ranks.get(cid, 0.0) + 1.0 / (K + i + 1)
                why.setdefault(cid, []).append(f"vec {float(sims[j]):.2f}")
    out = []
    for cid in sorted(ranks, key=lambda c: -ranks[c]):
        row = con.execute("SELECT c.text, c.heading, c.ord, d.source, d.ref, d.title, d.nchunks FROM chunks c "
                          "JOIN docs d ON d.id = c.doc_id WHERE c.id = ?", (cid,)).fetchone()
        if not row or (source and row["source"] != source):
            continue
        out.append({"score": round(ranks[cid], 5), "why": ",".join(why.get(cid, [])), "source": row["source"],
                    "ref": row["ref"], "title": row["title"], "heading": row["heading"], "part": row["ord"],
                    "parts": row["nchunks"], "text": row["text"]})
        if len(out) >= n:
            break
    return out


def read(ref: str, part: int | None = None, *, con: sqlite3.Connection | None = None) -> dict:
    """One whole passage (part=N), or the document's outline: every part's number and heading."""
    con = con or connect()
    d = con.execute("SELECT id, source, title, nchunks FROM docs WHERE ref = ?", (ref,)).fetchone()
    if not d:
        return {"ok": False, "error": f"no document {ref!r} in the index"}
    if part is not None:
        c = con.execute("SELECT heading, text FROM chunks WHERE doc_id = ? AND ord = ?", (d["id"], int(part))).fetchone()
        if not c:
            return {"ok": False, "error": f"{ref!r} has parts 0..{d['nchunks'] - 1}; no part {part}"}
        return {"ok": True, "ref": ref, "source": d["source"], "part": int(part), "parts": d["nchunks"],
                "heading": c["heading"], "text": c["text"]}
    outline = [{"part": r["ord"], "heading": r["heading"]} for r in
               con.execute("SELECT ord, heading FROM chunks WHERE doc_id = ? ORDER BY ord", (d["id"],))]
    return {"ok": True, "ref": ref, "source": d["source"], "parts": d["nchunks"], "outline": outline}


def status(con: sqlite3.Connection | None = None) -> dict:
    c = cfg()
    con = con or connect()
    meta = {r[0]: r[1] for r in con.execute("SELECT k, v FROM meta")}
    per = {r["source"]: r["n"] for r in con.execute("SELECT source, COUNT(*) AS n FROM docs GROUP BY source")}
    return {"db": str(c["db"]), "size_mb": round(c["db"].stat().st_size / 1e6, 1) if c["db"].is_file() else 0,
            "docs": con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
            "chunks": con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "vectors": con.execute("SELECT COUNT(*) FROM vecs").fetchone()[0],
            "by_source": per, "sources": {k: str(v) for k, v in (c["sources"] or {}).items()} or "defaults",
            "model": meta.get("model", "?"),
            "built_at": (time.strftime("%Y-%m-%d %H:%M", time.localtime(float(meta["built_at"])))
                         if meta.get("built_at") else "never")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--source", default="")
    b.add_argument("--full", action="store_true")
    q = sub.add_parser("query")
    q.add_argument("q")
    q.add_argument("-n", type=int, default=8)
    q.add_argument("--source", default=None)
    q.add_argument("--json", action="store_true")
    q.add_argument("--full", action="store_true", help="print each passage whole (the default prints 320 chars and says so)")
    r = sub.add_parser("read")
    r.add_argument("--ref", required=True)
    r.add_argument("--part", type=int, default=None)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        t0 = time.time()
        st = build([s for s in a.source.split(",") if s] or None, full=a.full)
        print(json.dumps(st))
        print(f"built in {time.time() - t0:.1f}s -> {cfg()['db']}")
    elif a.cmd == "status":
        print(json.dumps(status(), indent=1))
    elif a.cmd == "read":
        print(json.dumps(read(a.ref, a.part), indent=1))
    else:
        hits = query(a.q, a.n, source=a.source)
        if a.json:
            print(json.dumps(hits, indent=1))
            return 0
        if not hits:
            print("nothing on record.")
            return 0
        for h in hits:
            head = f" > {h['heading']}" if h["heading"] else ""
            print(f"\n{h['source']}:{Path(h['ref']).name}{head}  (part {h['part']} of {h['parts']}; {h['why']})")
            text = h["text"]
            if not a.full and len(text) > 320:
                text = text[:320] + f" ... [{len(text) - 320} more chars: --full, or read --ref <ref> --part {h['part']}]"
            print("  " + text.replace("\n", "\n  "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
