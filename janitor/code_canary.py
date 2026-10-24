"""The code canary: did the house's installed Python lose its source?

2026-09-22 15:20-15:21 a kanban worker (t_95882010, "/tmp inodes full") wrote its own unlink
script after bash's approvals deny rule refused `rm -rf` (T0 did not fire); at 15:21:29-30 every
.py under the hermes venvs, three plugins and Steward's uv python was gone. Same minute; the
mechanism is not proven (the script's roots were the three /tmp trees; its `DirEntry.is_dir()`
follows symlinks by default, but those trees are gone and no link out of them was seen). The dist-info
survived, so `import yaml` still "worked" (an empty namespace package), `uv sync` believed all
was installed, and nothing noticed for 14 hours. A `.py` count per tree takes ~30 ms; this
module keeps a baseline and names a drop.

Two readers:
  * worker exit (`scripts/run_kanban_task.sh`): the card that just ran is the first suspect,
    every other live `kanban-worker-*` scope a co-suspect — workers run concurrently, so the
    exit that SEES a drop did not necessarily cause it.
  * the janitor tick: the backstop for damage from outside a card (a session, a hand).

Modes (`KREW_CODE_CANARY`, else `code_canary.mode` in janitor.yaml): `report` (DEFAULT — the machine arms new gates report-only):
event line + stdout, nothing on the board. `act`: also a `cause: damage` comment on the
suspect card(s) and ONE Zoey question per incident (Ada card, blocked needs_input, the
restart-window shape). `off`: nothing. It never repairs; the recipe is `code_canary.recipe`.

`record_check()` is the deep check for after a restore or update: every file each
dist-info RECORD lists must exist. The 09-22 restore would have read "interpreter only".
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable

import sys as _sys
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))
import krew_settings  # noqa: E402


def _cfg(section: str) -> dict:
    """One section of $KREW_HOME/janitor.yaml, or of $KREW_JANITOR_CONFIG (config/janitor.example.yaml lists every key)."""
    return krew_settings.load(section, path=os.environ.get("KREW_JANITOR_CONFIG") or krew_settings.krew_home() / "janitor.yaml")


def _p(v) -> Path:
    return Path(os.path.expanduser(str(v)))


_C = _cfg("code_canary")
HOME = Path(os.environ.get("HERMES_REAL_HOME") or Path.home())
HERMES_DIR = _p(_C.get("hermes_dir") or HOME / ".hermes")
REPO = Path(__file__).resolve().parents[1]

STATE = Path(os.environ.get("KREW_CODE_CANARY_STATE") or HERMES_DIR / "code-canary.json")
EVENTS = Path(os.environ.get("KREW_CODE_CANARY_EVENTS") or HERMES_DIR / "code-canary.jsonl")
HERMES = os.environ.get("KREW_HERMES_BIN", str(HOME / ".local" / "bin" / "hermes"))
AUTHOR = "code-canary"

# A drop is a fall of more than DROP_FRAC against the last clean count, or below HIGH_FRAC of
# the tree's high-water (so a slow ratchet cannot walk it down 19 % at a time). An update or
# an M&S uninstall moves a few percent; the 09-22 wipe moved 100 %.
DROP_FRAC = float(os.environ.get("KREW_CODE_CANARY_DROP_FRAC", "0.2"))
HIGH_FRAC = float(os.environ.get("KREW_CODE_CANARY_HIGH_FRAC", "0.5"))

# Live service envs only (`code_canary.service_envs` under `code_canary.envs_root`; none by default).
SERVICE_ENVS = tuple(str(e) for e in (_C.get("service_envs") or ()))
ENVS_ROOT = _C.get("envs_root")
# krew's own code by default; `code_canary.code_trees` ({name: path}) replaces it
CODE_TREES = {str(k): _p(v) for k, v in (_C.get("code_trees") or {}).items()} or {
    "krew/janitor": REPO / "janitor", "krew/rails": REPO / "rails"}
MS_UNIT = str(_C.get("ms_unit") or "krew-ms-nightly.service")
RECIPE = str(_C.get("recipe") or "restore the tree from a known-good copy, or reinstall the environment")


def mode() -> str:
    m = (os.environ.get("KREW_CODE_CANARY") or str(_C.get("mode") or "report")).strip().lower()
    return m if m in ("off", "report", "act") else "report"


def _site_packages(root: Path) -> list[Path]:
    return sorted(root.glob("lib/python3*/site-packages"))


def hermes_live_envs(installs: Path | None = None) -> dict[str, Path]:
    """{"hermes-env/<install id>": site-packages} for the environment each install's facts.json names now.
    A new generation after an update is a new path under the same name: counts move a little, the
    canary treats that as an update; a hollowed one alarms."""
    out: dict[str, Path] = {}
    for facts in sorted((installs or HERMES_DIR / "installs").glob("*/facts.json")):
        try:
            env = Path(json.loads(facts.read_text())["packages"]["venv"]["environment"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for sp in _site_packages(env):
            out[f"hermes-env/{facts.parent.name[:8]}"] = sp
    return out


def default_trees() -> dict[str, Path]:
    """Every tree the 09-22 wipe reached, plus the service envs and the repo's own code."""
    trees: dict[str, Path] = {}
    ha = HERMES_DIR / "hermes-agent"
    # `hermes-agent/venv` left this list on 2026-09-30: the 09-26 hermes retired it, the launcher runs
    # from installs/<id>/environments/<gen>/venv (named by facts.json), and a canary on a venv nothing
    # runs from reassured nobody while four "lands" went into it.
    for sp in _site_packages(ha / ".venv"):
        trees["hermes-agent/.venv"] = sp
    for name, sp in hermes_live_envs().items():
        trees[name] = sp
    trees["hermes/plugins"] = HERMES_DIR / "plugins"
    trees["hermes/security"] = HERMES_DIR / "security"
    for prof in sorted((HERMES_DIR / "profiles").glob("*/home/.local/share/uv/python")):
        trees[f"uv-python/{prof.parts[-6]}"] = prof
    for env in SERVICE_ENVS:
        for sp in (_site_packages(_p(ENVS_ROOT) / env) if ENVS_ROOT else []):
            trees[f"env/{env}"] = sp
    trees.update(CODE_TREES)
    return trees


def trees(scope: str = "full") -> dict[str, Path]:
    """`fast` (every worker exit, ~0.09 s): everything but the service envs, which are ~90 % of
    the cost (474 runs on 09-19 x 0.7 s = 5.5 CPU-min a day, and 0.7 s on every card).
    `full` (the janitor, every 5 min): all of it. `KREW_CODE_CANARY_TREES=name=path:...`
    replaces the list outright (tests, a probe)."""
    raw = os.environ.get("KREW_CODE_CANARY_TREES", "").strip()
    if not raw:
        t = default_trees()
        return t if scope == "full" else {k: v for k, v in t.items() if not k.startswith("env/")}
    out = {}
    for part in raw.split(":"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = Path(v)
    return out


def count_py(root: Path) -> int | None:
    """Regular `.py` files under root. NEVER follows a symlink — that is the whole lesson.
    A tree that is not there is None, not 0 (a missing tree and an emptied one both alarm)."""
    if not root.is_dir():
        return None
    n = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for f in filenames:
            if f.endswith(".py") and not os.path.islink(os.path.join(dirpath, f)):
                n += 1
    return n


def _load() -> dict:
    try:
        d = json.loads(STATE.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
    os.replace(tmp, STATE)


def live_worker_cards() -> list[str]:
    """Cards with a live worker scope — the co-suspects. Two names: the wrapper's
    `kanban-worker-<card>-<pid>.scope` and hermes's own `hermes-worker-kanban-<card>-run-<run>.scope`."""
    try:
        out = subprocess.run(["systemctl", "--user", "list-units", "--type=scope", "--state=running",
                              "--plain", "--no-legend", "kanban-worker-*", "hermes-worker-kanban-*"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return []
    return sorted({m.group(1) for m in re.finditer(r"(?:kanban-worker-|hermes-worker-kanban-)(t_[0-9a-f]{8})-", out)})


def cards_run_since(since: float) -> list[str]:
    """Cards with a worker run overlapping (since, now) — the janitor's suspects, since the
    janitor exits no card. Read-only (mode=ro, never immutable: the board is live)."""
    import sqlite3
    cur = HERMES_DIR / "kanban" / "current"
    try:
        board = cur.read_text().strip()
        db = HERMES_DIR / "kanban" / "boards" / board / "kanban.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        rows = conn.execute("SELECT DISTINCT task_id FROM task_runs WHERE started_at <= ? "
                            "AND (ended_at IS NULL OR ended_at >= ?)", (time.time(), since)).fetchall()
        conn.close()
        return sorted(r[0] for r in rows)
    except Exception:  # noqa: BLE001
        return []


def ms_running() -> bool:
    """M&S's apply-green legitimately shrinks an env; say so beside a drop, never hide it."""
    try:
        return subprocess.run(["systemctl", "--user", "is-active", "--quiet", MS_UNIT],
                              timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def judge(prev: dict, now: dict[str, int | None]) -> list[dict]:
    """Drops of `now` against `prev` ({tree: {baseline, high}}). A tree never seen before is
    not a drop — it becomes a baseline."""
    drops = []
    for name, n in now.items():
        p = prev.get(name)
        if not p:
            continue
        base, high = int(p.get("baseline") or 0), int(p.get("high") or 0)
        if base <= 0:
            continue
        if n is None or n < base * (1 - DROP_FRAC) or n < high * HIGH_FRAC:
            drops.append({"tree": name, "was": base, "high": high, "now": n})
    return drops


def check(*, card: str = "", source: str = "worker", paths: dict[str, Path] | None = None,
          scope: str = "full") -> dict:
    """Count, judge, re-baseline what is clean. A dropped tree keeps its OLD baseline, so the
    alarm stands until the tree is repaired or `rebaseline()` is run on purpose."""
    paths = paths or trees(scope)
    t0 = time.monotonic()
    now = {k: count_py(p) for k, p in paths.items()}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        st = _load()
        prev = st.get("trees") or {}
        drops = judge(prev, now)
        dropped = {d["tree"] for d in drops}
        stamp = time.time()
        for k, n in now.items():
            if k in dropped or n is None:
                continue
            e = prev.get(k) or {}
            prev[k] = {"baseline": n, "high": max(n, int(e.get("high") or 0)), "path": str(paths[k]),
                       "at": stamp}
        st["trees"] = prev
        _save(st)
    # An incident starts at the last time its OWN trees counted clean. Per tree, never one
    # krew-wide clock: a fast worker-exit check never looks at the service envs, so a shared
    # "last clean" would move while an env stayed dropped — and every janitor tick after it
    # would file a fresh Zoey card for the same incident.
    since = min((float(prev[t].get("at") or 0) for t in dropped), default=0.0)
    res = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": source, "card": card, "mode": mode(),
           "ok": not drops, "drops": drops, "ms": round((time.monotonic() - t0) * 1000, 1),
           "trees": len(now), "clean_at": since}
    if drops:
        others = live_worker_cards() if card else cards_run_since(since)
        res["suspects"] = ([card] if card else []) + [c for c in others if c != card]
        res["ms_running"] = ms_running()
        # One event per incident, not one per tick: a standing drop re-reads every 5 min.
        sig = f"{int(since)}:{','.join(sorted(dropped))}"
        res["repeat"] = st.get("last_alert") == sig
        if not res["repeat"]:
            _event(res)
            with open(STATE.with_suffix(".lock"), "w") as lk:
                fcntl.flock(lk, fcntl.LOCK_EX)
                cur = _load()
                cur["last_alert"] = sig
                _save(cur)
    return res


def rebaseline(paths: dict[str, Path] | None = None, only: Iterable[str] = ()) -> dict:
    """Accept the current counts (after a repair, or a planned removal). `only` accepts named
    trees and leaves the rest — accepting the owner's change must not also accept a real drop
    somewhere else. A tree that is now gone is forgotten."""
    paths = paths or trees()
    only = set(only)
    if only - set(paths):
        raise KeyError(f"no such tree: {sorted(only - set(paths))}")
    now = {k: count_py(p) for k, p in paths.items() if not only or k in only}
    with open(STATE.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        stamp = time.time()
        st = _load() if only else {}
        tr = st.setdefault("trees", {})
        for k, n in now.items():
            if n is None:
                tr.pop(k, None)
            else:
                tr[k] = {"baseline": n, "high": n, "path": str(paths[k]), "at": stamp}
        st.pop("last_alert", None)
        _save(st)
    _event({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "rebaseline", "ok": True,
            "trees": {k: n for k, n in now.items()}})
    return st


def _event(row: dict) -> None:
    try:
        EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def describe(res: dict) -> str:
    parts = [f"{d['tree']} {d['was']} -> {'GONE' if d['now'] is None else d['now']} .py" for d in res["drops"]]
    s = "; ".join(parts)
    if res.get("suspects"):
        s += f" | suspects: {', '.join(res['suspects'])}"
    if res.get("ms_running"):
        s += " | M&S nightly is running (apply-green can shrink an env)"
    return s


def _kanban(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run([HERMES, "-p", "ada", "kanban", *args], capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def act(res: dict) -> list[str]:
    """In `act` mode only: a `cause: damage` line on each suspect card and ONE Zoey question
    per incident (keyed on the last clean check, so every exit that sees the same drop joins
    the same card). Returns what it did."""
    if res["ok"] or mode() != "act":
        return []
    done = []
    line = ("cause: damage — installed Python lost its source while this card's worker was live: "
            + describe(res) + ". Recipe: " + RECIPE)
    for c in res.get("suspects") or []:
        rc, _ = _kanban("comment", c, line[:1800], "--author", AUTHOR)
        done.append(f"comment {c} rc={rc}")
    key = f"code-canary:{int(res.get('clean_at') or 0)}"
    title = f"Code canary: {', '.join(d['tree'] for d in res['drops'])[:120]} lost its .py files"
    body = (line + "\n\nThe canary never repairs. Rule: restore | rebaseline (a planned removal) | hands."
            "\nRebaseline: janitor/krew-code-canary.py rebaseline")
    rc, out = _kanban("create", title, "--assignee", "ada", "--body", body[:5500],
                      "--created-by", AUTHOR, "--idempotency-key", key)
    m = re.search(r"\b(t_[0-9a-f]{8})\b", out)
    if rc == 0 and m:
        rc2, _ = _kanban("block", m.group(1), "Code canary: restore | rebaseline | hands?", "--kind", "needs_input")
        if rc2 != 0:
            _kanban("archive", m.group(1))
        done.append(f"zoey {m.group(1)} block rc={rc2}")
    else:
        done.append(f"zoey card not filed rc={rc}")
    return done


def record_check(site_packages: Path) -> dict:
    """Every file each dist-info RECORD lists must exist (the deep check after a restore)."""
    pkgs, missing_pkgs, missing_files, no_record = 0, [], 0, []
    for di in sorted(site_packages.glob("*.dist-info")):
        pkgs += 1
        rec = di / "RECORD"
        if not rec.is_file():
            no_record.append(di.name)
            continue
        miss = 0
        for line in rec.read_text(errors="replace").splitlines():
            rel = line.split(",", 1)[0].strip()
            if not rel or "__pycache__" in rel or rel.endswith(".pyc"):
                continue
            if not (site_packages / rel).exists():
                miss += 1
        if miss:
            missing_pkgs.append({"dist": di.name, "missing": miss})
            missing_files += miss
    return {"site_packages": str(site_packages), "packages": pkgs, "ok": not missing_pkgs and not no_record,
            "missing_files": missing_files, "broken": missing_pkgs, "no_record": no_record}


def site_packages_of(targets: Iterable[str]) -> list[Path]:
    out = []
    for t in targets:
        p = Path(t)
        out += [p] if p.name == "site-packages" else _site_packages(p)
    return out
