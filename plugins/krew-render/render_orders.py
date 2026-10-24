"""Render orders: a written list of renders that empties as it is processed.

An order is one JSON file; anyone (a person, an agent through the `render_order` tool, a script) adds one;
a plain runner (render_runner.py: no LLM) submits them to a ComfyUI server batch by batch.

    <orders>/pending/<id>.json   waiting            <orders>/running/<id>.json   being rendered
    <orders>/done/<id>.json      outputs recorded   <orders>/failed/<id>.json    error or cancelled

A move between states is one `os.rename` (atomic on one filesystem), a write is tmp + `os.replace`: an
order is always whole, in exactly one state.

What an order needs is DERIVED, never typed: the model files its graph names (sizes from the models
folder, sharded folders counted whole), `need_gib`, and its `family` (the main weights: orders of one
family share them, so a batch loads them once).

Folders are settings (`render:` in $KREW_HOME/krew.yaml): orders_dir (default $KREW_HOME/render-orders),
workflows_dir and models_dir (your ComfyUI's API-format workflows and its models folder).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path

try:
    from . import krew_settings as S
    from . import krew_comfy            # the one Comfy location (the Wall's render.json, else KREW_COMFY_*)
except ImportError:                     # run beside render_runner.py as a script
    import krew_settings as S           # noqa: E402
    import krew_comfy                   # noqa: E402


def _paths() -> dict:
    st = S.load("render")
    home = S.krew_home()
    x = lambda k, d: Path(os.path.expanduser(str(st.get(k) or d)))  # noqa: E731
    # workflows and models default to the one Comfy location (krew_comfy.py), shared with the Wall's render module
    return {"orders": x("orders_dir", home / "render-orders"), "workflows": x("workflows_dir", krew_comfy.get("workflows")),
            "models": x("models_dir", krew_comfy.get("models"))}


def orders_root() -> Path:
    return _paths()["orders"]


def workflows_root() -> Path:
    return _paths()["workflows"]


def models_root() -> Path:
    return _paths()["models"]
STATES = ("pending", "running", "done", "failed")
MODEL_EXT = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".sft")
SEED_INPUTS = ("seed", "noise_seed")
# the node types whose model is the MAIN weights (the family key); first one in the graph wins
MAIN_LOADERS = ("UNETLoader", "UnetLoaderGGUF", "CheckpointLoaderSimple", "CheckpointLoader")
# family by the main weights' file name, first match wins (order matters: klein before flux2,
# edit before image)
FAMILIES: tuple[tuple[str, str], ...] = (
    ("flux2-klein", r"klein"),
    ("flux2", r"flux2|flux-2"),
    ("flux1", r"flux1|flux-1"),
    ("qwen-image-21", r"qwen_image_2\.1|qwen-image-2\.1"),  # one model for t2i AND edit
    ("qwen-edit", r"qwen_image_edit|qwen-image-edit"),
    ("qwen-image", r"qwen_image|qwen-image"),
    ("ltx25", r"ltx-2\.5|ltx25"),
    ("ltx23", r"ltx-2\.3|ltx23"),
    ("wan22", r"wan2\.2|wan22"),
    ("sdxl", r"sd_xl|sdxl"),
)
_SHARD = re.compile(r"^(?P<stem>.+)-\d{5}-of-(?P<n>\d{5})(?P<ext>\.[a-z]+)$")


class OrderError(ValueError):
    """The order cannot be made or moved; nothing was written."""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def state_dir(state: str, root: Path | None = None) -> Path:
    if state not in STATES:
        raise OrderError(f"unknown state {state!r}")
    return Path(root) / state if root else orders_root() / state


# ---------------------------------------------------------------- the graph
def load_workflow(workflow: str, *, workflows_dir: Path | None = None) -> tuple[str, dict]:
    """(name, API graph) from a saved workflow name (`flux2-turbo-txt2img`) or a path."""
    p = Path(workflow)
    if not p.is_file():
        d = Path(workflows_dir) if workflows_dir else workflows_root()
        p = d / (workflow if workflow.endswith(".json") else workflow + ".json")
    if not p.is_file():
        raise OrderError(f"no workflow {workflow!r} (looked in {workflows_dir or workflows_root()})")
    try:
        g = json.loads(p.read_text())
    except ValueError as e:
        raise OrderError(f"{p.name} is not JSON: {e}") from e
    if not isinstance(g, dict) or "nodes" in g or not all(isinstance(v, dict) and "class_type" in v
                                                         for v in g.values()):
        raise OrderError(f"{p.name} is not an API-format graph (a UI graph cannot be submitted)")
    return p.stem, g


def apply_overrides(graph: dict, overrides: dict[str, object], *, seed: int | None = None) -> dict:
    """`{"12.text": "a red fox"}` sets node 12's input `text`; `seed` sets every seed input.
    A node that does not exist is refused (a typo must not render the default prompt)."""
    g = json.loads(json.dumps(graph))
    for key, value in (overrides or {}).items():
        node, _, inp = str(key).partition(".")
        if not inp or node not in g:
            raise OrderError(f"override {key!r}: no node {node!r} in the graph (use <node>.<input>)")
        g[node].setdefault("inputs", {})[inp] = value
    if seed is not None:
        n = 0
        for v in g.values():
            for k in SEED_INPUTS:
                if k in (v.get("inputs") or {}):
                    v["inputs"][k] = int(seed)
                    n += 1
        if not n:
            raise OrderError("--seed given but the graph has no seed input")
    return g


# ---------------------------------------------------------------- what it needs
def model_index(models_dir: Path | None = None) -> dict[str, list[Path]]:
    """basename -> every model file with that name under the models dir (symlinks followed)."""
    idx: dict[str, list[Path]] = {}
    root = Path(models_dir) if models_dir else models_root()
    for dirpath, _dirs, files in os.walk(root, followlinks=True):
        for f in files:
            if f.endswith(MODEL_EXT):
                idx.setdefault(f, []).append(Path(dirpath) / f)
    return idx


def _resolve(name: str, idx: dict[str, list[Path]]) -> list[Path]:
    """The files a model input loads: the one named (a sub-path wins over a bare name),
    and for a shard `x-00001-of-00005.ext` every shard of that folder."""
    cands = idx.get(Path(name).name) or []
    if "/" in name:
        exact = [p for p in cands if str(p).endswith("/" + name)]
        cands = exact or cands
    if not cands:
        return []
    p = cands[0]
    m = _SHARD.match(p.name)
    if m:
        shards = sorted(p.parent.glob(f"{m['stem']}-*-of-{m['n']}{m['ext']}"))
        return shards or [p]
    return [p]


def models_of(graph: dict, *, index: dict | None = None) -> dict:
    """{files: [{name, bytes}], missing: [names], main: name|'', need_gib}."""
    idx = index if index is not None else model_index()
    files: dict[str, int] = {}
    missing: list[str] = []
    main = ""
    for nid in sorted(graph, key=lambda k: (len(k), k)):
        node = graph[nid]
        for v in (node.get("inputs") or {}).values():
            if not (isinstance(v, str) and v.endswith(MODEL_EXT)):
                continue
            got = _resolve(v, idx)
            if not got:
                missing.append(v)
                continue
            if not main and node.get("class_type") in MAIN_LOADERS:
                main = v
            for p in got:
                try:
                    files[str(p)] = p.stat().st_size
                except OSError:
                    missing.append(v)
    return {"files": [{"path": k, "bytes": b} for k, b in sorted(files.items())],
            "missing": sorted(set(missing)), "main": main,
            "need_gib": round(sum(files.values()) / 2 ** 30, 2)}


def family_of(main: str) -> str:
    base = Path(main).name.lower()
    for fam, pat in FAMILIES:
        if re.search(pat, base):
            return fam
    return "other:" + (Path(base).stem or "unknown")


# ---------------------------------------------------------------- orders
def new_id(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    return f"ro-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def add(workflow: str, *, overrides: dict | None = None, seed: int | None = None, by: str,
        card: str = "", priority: int = 0, out_dir: str = "", root: Path | None = None,
        workflows_dir: Path | None = None, index: dict | None = None) -> dict:
    """Write one pending order. Refused (nothing written) when the workflow is not an API
    graph, an override names no node, or a model file the graph needs is not on disk."""
    if not by:
        raise OrderError("--by is required: who asked for this render")
    name, graph = load_workflow(workflow, workflows_dir=workflows_dir)
    graph = apply_overrides(graph, overrides or {}, seed=seed)
    m = models_of(graph, index=index)
    if m["missing"]:
        raise OrderError("model files not on disk: " + ", ".join(m["missing"]))
    oid = new_id()
    order = {
        "id": oid, "state": "pending", "workflow": name, "graph": graph,
        "overrides": overrides or {}, "seed": seed, "family": family_of(m["main"]),
        "main": m["main"], "need_gib": m["need_gib"], "models": m["files"],
        "requested_by": by, "card": card, "priority": int(priority),
        "out_dir": out_dir or str((Path(root) if root else orders_root()) / "out" / oid), "created": _now(),
        "seq": time.time_ns(),  # arrival order: two orders in one second must not tie
        "history": [],
    }
    _atomic(state_dir("pending", root) / f"{oid}.json", order)
    return order


def _find(oid: str, root: Path | None = None) -> tuple[str, Path]:
    for st in STATES:
        p = state_dir(st, root) / f"{oid}.json"
        if p.is_file():
            return st, p
    raise OrderError(f"no order {oid!r}")


def show(oid: str, *, root: Path | None = None) -> dict:
    _st, p = _find(oid, root)
    return json.loads(p.read_text())


def orders(state: str | None = None, *, root: Path | None = None) -> list[dict]:
    out = []
    for st in ([state] if state else STATES):
        d = state_dir(st, root)
        for p in sorted(d.glob("ro-*.json")) if d.is_dir() else []:
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                continue
    return out


def move(oid: str, to: str, *, root: Path | None = None, **fields) -> dict:
    """One atomic rename to `to`, then the record updated in place (+ a history line)."""
    st, p = _find(oid, root)
    allowed = {"pending": {"running", "failed"}, "running": {"done", "failed", "pending"},
               "done": set(), "failed": {"pending"}}
    if to not in allowed[st]:
        raise OrderError(f"{oid}: {st} -> {to} is not a move")
    dst = state_dir(to, root) / p.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.rename(p, dst)  # the claim: whoever renames first owns it
    order = json.loads(dst.read_text())
    order.update(fields, state=to)
    order.setdefault("history", []).append({"ts": _now(), "from": st, "to": to,
                                            **({"why": fields["why"]} if "why" in fields else {})})
    _atomic(dst, order)
    return order


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def requeue_orphans(*, root: Path | None = None, alive=_pid_alive) -> list[str]:
    """A running order whose runner is gone goes back to pending (a
    SIGKILLed runner never reaches its `finally`, and its order would sit in running/ for good).
    Off: KREW_ORDERS_REQUEUE=0."""
    if not S.switch_on("KREW_ORDERS_REQUEUE"):
        return []
    back = []
    for o in orders("running", root=root):
        if alive(o.get("runner_pid")):
            continue
        try:
            move(o["id"], "pending", root=root, why=f"runner {o.get('runner_pid')} gone")
            back.append(o["id"])
        except OrderError:
            continue
    return back


def cancel(oid: str, *, by: str, root: Path | None = None) -> dict:
    st, _p = _find(oid, root)
    if st != "pending":
        raise OrderError(f"{oid} is {st}; only a pending order can be cancelled")
    return move(oid, "failed", root=root, why=f"cancelled by {by}", cancelled=True)


# ---------------------------------------------------------------- batches
def plan(pending: list[dict] | None = None, *, root: Path | None = None) -> list[dict]:
    """Pending orders grouped by family: the family with the highest-priority order first,
    then the oldest; inside a batch priority then age. `need_gib` of a batch is the UNION
    of its model files (shared weights counted once) - what high-VRAM would hold resident."""
    rows = pending if pending is not None else orders("pending", root=root)
    fams: dict[str, list[dict]] = {}
    for o in rows:
        fams.setdefault(o["family"], []).append(o)
    batches = []
    for fam, os_ in fams.items():
        os_.sort(key=lambda o: (-int(o.get("priority") or 0), int(o.get("seq") or 0), o.get("created") or ""))
        union: dict[str, int] = {}
        for o in os_:
            for m in o.get("models") or []:
                union[m["path"]] = int(m["bytes"])
        batches.append({"family": fam, "orders": [o["id"] for o in os_], "n": len(os_),
                        "need_gib": round(sum(union.values()) / 2 ** 30, 2),
                        "top_priority": int(os_[0].get("priority") or 0), "oldest": os_[0].get("created") or "",
                        "_seq": min(int(o.get("seq") or 0) for o in os_)})
    batches.sort(key=lambda b: (-b["top_priority"], b["_seq"]))
    for b in batches:
        b.pop("_seq")
    return batches
