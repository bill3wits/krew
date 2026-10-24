#!/usr/bin/env python3
"""Shared paths and occupancy for Steward architect jobs.

Authority: docs/STEWARD-ARCHITECT.md
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
KREW_MEMORY_ROOT = os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory")  # krew: the house kept this on a data disk

HOME = Path.home()
INBOX = Path(os.path.expanduser(os.environ.get("KREW_DELIVERABLES") or "~/Desktop/krew-deliverables"))
KREW_REPORTS = INBOX / "krew-reports"
KANBAN_ARCHIVE = INBOX / "kanban-archive"
KREW_RENDERS = INBOX / "krew-renders"
MS_DIR = Path((KREW_MEMORY_ROOT + "/ms"))
COLLECT_ROOT = MS_DIR / "architect-collect"
VERDICTS = MS_DIR / "architect-verdicts.jsonl"
COVERAGE = MS_DIR / "architect-coverage.jsonl"
USAGE = MS_DIR / "architect-usage.jsonl"
LOCK = HOME / ".hermes" / "architect.lock"
GPU_MODE_FILE = Path(
    os.environ.get("KREW_GPU_MODE_FILE", str(HOME / ".hermes" / "krew-gpu-mode"))
)
SPIKES = HOME / "spikes"
BUNDLE_CAP = 24 * 1024
SPIKE_CEILING_BYTES = 2 * 1024 * 1024 * 1024
NNT_CHANNEL_ID = "UCNZEktrsM5oJZ-MK4jKPMOQ"  # measured 2026-08-25
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8080")
GH_TOKEN_ENV = "KREW_ARCHITECT_GH_TOKEN"
DOCS = Path(os.path.expanduser(os.environ.get("KREW_DOCS_DIR")
                               or os.path.join(os.environ.get("KREW_HOME") or "~/.krew", "docs")))
MS_LIVING = DOCS / "STEWARD-MS-REPORT.md"
KREW_BUILD = DOCS / "KREW-BUILD.md"

REPORT_PREFIXES = (
    "architect-",
    "ms-",
    "logs-",
    "spike-",
    "daily-digest-",
    "team-research-",
    "team-daily-",
    "team-sources-",
    "ops-efficiency-",
    "pa-ab-",
)
IMPROVE_DIR = MS_DIR / "improvements"
KPI_SAMPLE_ADHOC = "adhoc"
KPI_SAMPLE_SNAPSHOT = "snapshot"

STACK_PIECES: tuple[tuple[str, str], ...] = (
    ("brains", "Brains / vLLM / MTP / KV"),
    ("hermes", "Hermes / kanban"),
    ("presence", "Presence / door VL"),
    ("camera", "Camera / PTZ"),
    ("face", "Face / krew-id"),
    ("voice_emb", "Voice emb"),
    ("stt", "STT"),
    ("tts", "TTS"),
    ("wall", "Wall / VoiceOnly"),
    ("train", "Train"),
    ("render", "Render / Comfy"),
    ("security", "Host security / T0 / doctor"),
    ("memory", "Memory / Hindsight"),
)

# fnmatch (not regex). Profile-wide on Steward. Do not add git push*.
DENY_GLOBS: tuple[str, ...] = (
    "*systemctl*restart*ops*",
    "*systemctl*stop*ops*",
    "*systemctl*kill*ops*",
    "*systemctl*restart*crew*",
    "*systemctl*stop*crew*",
    "*systemctl*kill*crew*",
    "*systemctl*restart*cosy*",
    "*systemctl*stop*cosy*",
    "*systemctl*kill*cosy*",
    "*systemctl*restart*turbo-voice*",
    "*systemctl*stop*turbo-voice*",
    "*systemctl*kill*turbo-voice*",
    "*systemctl*restart*rail-*",
    "*systemctl*stop*rail-*",
    "*systemctl*kill*rail-*",
    "*systemctl*restart*audio8*",
    "*systemctl*stop*audio8*",
    "*systemctl*kill*audio8*",
    "*start-ops.sh*",
    "*start-crew.sh*",
    "*start-beast.sh*",
    "*vllm serve*",
    "*/hf/hub-local*",
    "*chattr*",
)

DENY_SHOULD_MATCH: tuple[str, ...] = (
    "systemctl --user restart ops.service",
    "sudo systemctl stop crew",
    "systemctl --user restart turbo-voice.service",   # krew: the reference voice unit; its ear unit example is the Wall's and left out
    "systemctl --user stop audio8-asr.service",
    "bash ~/bin/start-beast.sh",
    "vllm serve Qwen/Qwen3-8B",
    "huggingface-cli download --local-dir ~/hf/hub-local foo",
    "sudo chattr -i /path",
)

DENY_SHOULD_MISS: tuple[str, ...] = (
    "systemctl --user is-active ops.service",
    "git push origin main",
    "git status",
    "curl -sS http://127.0.0.1:8000/v1/models",
    "uv pip install pytest",
)

_DATE_DASH = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_DATE_COMPACT = re.compile(r"daily-digest-(20\d{6})")


def today() -> date:
    """The day this run belongs to. A chain the window ticker starts is handed ONE
    label (KREW_DAY=YYYY-MM-DD, the date the User's off-hours opened) so a run at
    23:50 and the next at 00:10 agree; nothing in the chain asks the clock."""
    raw = os.environ.get("KREW_DAY", "").strip()
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return date.today()


def read_gpu_mode() -> str:
    try:
        first = GPU_MODE_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except (OSError, IndexError):
        return "normal"
    if first in {"render", "normal", "beast"}:
        return first
    return "normal"


def skip_llm(mode: str | None = None) -> bool:
    """Beast: skip all architect LLM jobs (122B VRAM)."""
    return (mode or read_gpu_mode()) == "beast"


def brain_for_mode(mode: str | None = None) -> str:
    """Kanban/architect brain is crew in every occupancy. Zoey MOS is Beast-only."""
    _ = mode or read_gpu_mode()
    return "crew"


def allow_spike(mode: str | None = None) -> bool:
    m = mode or read_gpu_mode()
    if m in {"beast", "render"}:
        return False
    hour = datetime.now().hour
    if 18 <= hour or hour < 2:
        return False
    return True


def filename_date(name: str) -> date | None:
    m = _DATE_DASH.search(name)
    if m:
        y, mo, d = m.group(1).split("-")
        try:
            return date(int(y), int(mo), int(d))
        except ValueError:
            return None
    m = _DATE_COMPACT.search(name)
    if m:
        s = m.group(1)
        try:
            return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def is_report_name(name: str) -> bool:
    return name.startswith(REPORT_PREFIXES)


def deny_matches(command: str) -> str | None:
    candidate = command.lower().strip()
    for pattern in DENY_GLOBS:
        if fnmatch.fnmatchcase(candidate, pattern.lower()):
            return pattern
    return None


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


KPI_HISTORY_MIN = 3

CARD_ASSIGNEE = {
    "architect-try": "ada",
    "architect-watch": "ada",
    "team-improve": "ada",
    "team-source": "ada",
    "team-source-drop": "ada",
    "ops-action": "steward",
}


def ops_action_id(title: str, kind: str = "ops-action") -> str:
    """Stable id. Do not use hash() — PYTHONHASHSEED rotates it."""
    raw = f"{kind.strip().lower()}\n{title.strip()}".encode()
    return "oa_" + hashlib.sha256(raw).hexdigest()[:12]


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def open_ops_action_ids(rows: list[dict]) -> list[str]:
    return [
        str(a["ops_action_id"])
        for a in rows
        if a.get("status") == "open" and a.get("ops_action_id")
    ]


def all_ops_action_ids(rows: list[dict]) -> set[str]:
    return {str(a["ops_action_id"]) for a in rows if a.get("ops_action_id")}


def kpi_history_n(kpis: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for kid, k in (kpis or {}).items():
        if not isinstance(k, dict):
            continue
        hist = k.get("history") or []
        out[str(kid)] = len(hist) if isinstance(hist, list) else 0
    return out


def kpi_insufficient(
    hist_n: dict[str, int], min_n: int = KPI_HISTORY_MIN
) -> list[str]:
    return sorted(k for k, n in hist_n.items() if n < min_n)


def load_kpis(path: Path | None = None) -> dict:
    p = path or (IMPROVE_DIR / "kpis.json")
    if not p.is_file():
        return {"kpis": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"kpis": {}}
    kpis = data.get("kpis") if isinstance(data, dict) else None
    if not isinstance(kpis, dict):
        return {"kpis": {}}
    return data


def register_kpi(
    *,
    kpi_id: str,
    name: str,
    unit: str,
    direction: str,
    how_measured: str,
    sample: str = KPI_SAMPLE_ADHOC,
    target=None,
    path: Path | None = None,
) -> dict:
    """Add a KPI so ops-efficiency can chart it. Does not invent samples.

    sample=adhoc: Vera/Steward run the probe on land (vLLM speed_probe).
    sample=snapshot: kanban-snapshot.py records it. Do not snapshot a
    400-token ops probe.
    """
    kid = (kpi_id or "").strip()
    if not kid:
        raise ValueError("kpi_id is required")
    d = (direction or "").strip().lower()
    if d not in ("up", "down"):
        raise ValueError("direction must be up or down")
    samp = (sample or KPI_SAMPLE_ADHOC).strip().lower()
    if samp not in (KPI_SAMPLE_ADHOC, KPI_SAMPLE_SNAPSHOT):
        raise ValueError("sample must be adhoc or snapshot")
    p = path or (IMPROVE_DIR / "kpis.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    data = load_kpis(p)
    kpis = data.setdefault("kpis", {})
    existing = kpis.get(kid)
    if isinstance(existing, dict):
        if how_measured and not existing.get("how_measured"):
            existing["how_measured"] = how_measured
        if samp and not existing.get("sample"):
            existing["sample"] = samp
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return existing
    row = {
        "kpi_id": kid,
        "name": name or kid,
        "unit": unit or "",
        "direction": d,
        "baseline": None,
        "target": target,
        "current": None,
        "added_at": datetime.now().date().isoformat(),
        "how_measured": how_measured,
        "sample": samp,
        "linked_proposals": [],
        "history": [],
    }
    kpis[kid] = row
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return row


def plan_filings(
    cards: list[tuple[str, str]], existing_ids: set[str]
) -> list[dict]:
    """Assignee + action id + skip-if-already-ledgers. No hermes."""
    planned: list[dict] = []
    for kind, title in cards:
        rec: dict = {
            "kind": kind,
            "title": title,
            "assignee": CARD_ASSIGNEE.get(kind, "ada"),
            "skip": False,
        }
        aid = ops_action_id(title, kind)
        rec["idemp"] = f"{kind}:{aid}"
        if kind == "ops-action":
            rec["ops_action_id"] = aid
            rec["skip"] = aid in existing_ids
        planned.append(rec)
    return planned


def recent_world_slugs(days: int = 14) -> list[str]:
    """Titles/slugs already named in the last N days of world notes."""
    cutoff = today() - timedelta(days=days)
    slugs: list[str] = []
    roots = [INBOX, KREW_REPORTS]
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("architect-world-*.md"):
            dt = filename_date(p.name)
            if dt is None or dt < cutoff:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if "github.com/" in line.lower():
                    slugs.append(line.strip()[:200])
    return slugs


def collect_dir(day: date | None = None) -> Path:
    d = day or today()
    return COLLECT_ROOT / d.isoformat()


def stub_note(path: Path, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# {path.stem}\n\n_skipped {datetime.now().isoformat(timespec='seconds')} "
        f"reason={reason} occupancy={read_gpu_mode()}_\n",
        encoding="utf-8",
    )


SKIP_INBOX_DIRS = {
    "krew-reports",
    "krew-reports-archive",
    "kanban-archive",
    "daily-kanban-archive",
    "krew-renders",
    "ask",
    "process",
    "shelf",
    "scratch",
    "ideas",
}


def plan_archive_moves(inbox: Path, as_of: date | None = None) -> list[tuple[Path, Path]]:
    as_of = as_of or today()
    moves: list[tuple[Path, Path]] = []
    if not inbox.is_dir():
        return moves
    for child in sorted(inbox.iterdir()):
        if child.name in SKIP_INBOX_DIRS or child.is_dir():
            continue
        if not child.is_file():
            continue
        dt = filename_date(child.name)
        if dt is None or dt >= as_of:
            continue
        dest_root = KREW_REPORTS if is_report_name(child.name) else KANBAN_ARCHIVE
        dest = dest_root / dt.isoformat() / child.name
        moves.append((child, dest))
    return moves
