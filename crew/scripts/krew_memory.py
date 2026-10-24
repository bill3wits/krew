#!/usr/bin/env python3
"""Krew short/medium memory on /data — instant recall files.








      rolling/3day-YYYY-MM-DD.md
      workspace/TODAY-YYYY-MM-DD.md
      dreams/ (optional copies if renames)

Per-agent (organized, symlink to SoT — no second copy of truth):
  ~/.hermes/profiles/<agent>/workspace/memory/


      DREAM.md → latest dreams/dream-*.md
  ~/.hermes/profiles/<agent>/memories/
      KREW-TODAY.md, KREW-3DAY.md, KREW-DREAM.md  (same targets)


This module only owns day files + rollups for free-air / crew inject.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from krew_cut import cut  # noqa: E402  (coach, don't cap: a cut says so and where the rest is)
from typing import Any

ROOT = Path(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory").expanduser()
DAY_DIR = ROOT / "day"
ROLL_DIR = ROOT / "rolling"
WS_DIR = ROOT / "workspace"
ARCHIVE_DIR = ROOT / "archive"
DREAM_DIR = ROOT / "dreams"
HERMES_PROFILES = Path.home() / ".hermes" / "profiles"
# Profiles that get the krew brief in workspace + memories (all krew crew).
AGENT_PROFILES = (
    "zoey",
    "ada",
    "lila",
    "vera",
    "bash",
    "scout",
    "steward",
)


def _today() -> str:
    return date.today().isoformat()


def day_path(d: str | None = None) -> Path:
    return DAY_DIR / f"{d or _today()}.jsonl"


def summary_path(d: str | None = None) -> Path:
    return DAY_DIR / f"{d or _today()}.summary.md"


def append_event(
    kind: str,
    text: str = "",
    *,
    source: str = "house",
    meta: dict[str, Any] | None = None,
) -> Path:
    """Append one short-term memory line. Never fails the caller loudly."""
    DAY_DIR.mkdir(parents=True, exist_ok=True)
    path = day_path()
    rec = {
        "ts": time.time(),
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": kind,
        "source": source,
        "text": text or "",
        "meta": meta or {},
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def read_day_events(d: str | None = None, *, limit: int = 200) -> list[dict]:
    path = day_path(d)
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows[-max(1, limit) :]


def today_bullets(*, limit: int = 12) -> str:
    """Compact bullets for free-air inject / Zoey workspace."""
    rows = read_day_events(limit=80)
    if not rows:
        return "(no krew day events yet)"
    # Prefer free-air replies, kanban, system notes
    prefer = {"freeair_reply", "freeair_user", "kanban", "system", "summary"}
    scored = [r for r in rows if r.get("kind") in prefer] or rows
    lines = []
    for r in scored[-limit:]:
        t = (r.get("text") or "").strip().replace("\n", " ")
        if not t:
            continue
        hhmm = ""
        try:
            hhmm = datetime.fromtimestamp(float(r["ts"])).strftime("%H:%M")
        except Exception:
            pass
        lines.append(f"- [{hhmm}] {r.get('kind')}: {cut(t, 160, f'whole event in the day files, {DAY_DIR}')}")
    return "\n".join(lines) if lines else "(no speakable day bullets)"


def _archive_tip(src: Path, dest_dir: Path, dest_name: str) -> Path | None:
    """Copy live tip into archive/ before overwrite. Never delete the archive copy.

    Zoey has re-pulled old context when tips were clobbered in place — archive is
    the durable shelf; live paths stay the single current tip.
    """
    if not src.is_file() or src.stat().st_size == 0:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / dest_name
    if dest.is_file() and dest.stat().st_size == src.stat().st_size:
        # Same size already archived today — still refresh content if mtime newer
        try:
            if dest.stat().st_mtime >= src.stat().st_mtime:
                return dest
        except OSError:
            pass
    try:
        dest.write_bytes(src.read_bytes())
        return dest
    except OSError:
        return None


def latest_dream_path() -> Path | None:
    if not DREAM_DIR.is_dir():
        return None
    dreams = sorted(DREAM_DIR.glob("dream-*.md"))
    return dreams[-1] if dreams else None


def _safe_symlink(link: Path, target: Path) -> None:
    """Point link → target (absolute). Replace stale/wrong links; never follow."""
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        target_abs = target.resolve() if target.exists() else target.absolute()
        if link.is_symlink() or link.exists():
            if link.is_symlink() and link.resolve() == target_abs:
                return
            link.unlink()
        link.symlink_to(target_abs)
    except OSError:
        pass



# and they have not been missed, delete"). It symlinked TODAY/3day/DREAM into seven profiles'
# workspace/memory and memories/KREW-*.md; hermes memory_tool reads only MEMORY.md and

# the 3-day roll, TODAY.md and the Hindsight retain / consolidate / reflect all stay.


def write_workspace_today() -> Path:
    WS_DIR.mkdir(parents=True, exist_ok=True)
    path = WS_DIR / "TODAY.md"
    # Archive previous tip before clobber (date-stamped; same-day rewrites update archive tip)
    _archive_tip(path, ARCHIVE_DIR / "workspace", f"TODAY-{_today()}.md")
    body = (
        f"# Krew today ({_today()})\n\n"
        f"Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## Recent (short-term)\n\n"
        f"{today_bullets()}\n\n"
        f"## 3-day rollup\n\n"
        f"{(ROLL_DIR / '3day.md').read_text(encoding='utf-8') if (ROLL_DIR / '3day.md').is_file() else '(no 3-day rollup yet)'}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def roll_day_summary(d: str | None = None) -> Path:
    """Cheap extractive summary of the day file (no LLM required)."""
    d = d or _today()
    rows = read_day_events(d, limit=500)
    DAY_DIR.mkdir(parents=True, exist_ok=True)
    path = summary_path(d)
    kinds: dict[str, int] = {}
    for r in rows:
        k = str(r.get("kind") or "?")
        kinds[k] = kinds.get(k, 0) + 1
    lines = [
        f"# Day summary {d}",
        "",
        f"Events: {len(rows)}",
        f"Kinds: {', '.join(f'{k}={n}' for k, n in sorted(kinds.items())) or 'none'}",
        "",
        "## Highlights",
        today_bullets(limit=20) if d == _today() else _bullets_from(rows, 20),
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _bullets_from(rows: list[dict], limit: int) -> str:
    out = []
    for r in rows[-limit:]:
        t = (r.get("text") or "").strip().replace("\n", " ")
        if t:
            out.append(f"- {r.get('kind')}: {cut(t, 160, f'whole event in the day files, {DAY_DIR}')}")
    return "\n".join(out) or "(none)"


def roll_3day() -> Path:
    """Medium-term: concatenate last 3 day summaries into rolling/3day.md."""
    ROLL_DIR.mkdir(parents=True, exist_ok=True)
    path = ROLL_DIR / "3day.md"
    # Freeze previous live tip so Dream rewrites never erase history Zoey re-pulls
    _archive_tip(path, ARCHIVE_DIR / "rolling", f"3day-{_today()}.md")
    parts = [f"# 3-day krew rollup (through {_today()})", ""]
    for i in range(2, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        sp = summary_path(d)
        if not sp.is_file():
            # extractive on the fly
            if day_path(d).is_file():
                roll_day_summary(d)
        if sp.is_file():
            parts.append(sp.read_text(encoding="utf-8").rstrip())
            parts.append("")
        else:
            parts.append(f"## {d}\n\n(no day file)\n")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    # also keep a dated snapshot of *this* roll (archive shelf is append-only by date)
    _archive_tip(path, ARCHIVE_DIR / "rolling", f"3day-{_today()}.md")
    write_workspace_today()
    return path




HINDSIGHT_URL = os.environ.get("HINDSIGHT_API_URL", "http://127.0.0.1:8888").rstrip("/")
HINDSIGHT_BANK = os.environ.get("HINDSIGHT_BANK_ID", "hermes")


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 120.0) -> dict:
    import urllib.error
    import urllib.request

    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:400]
        raise RuntimeError(f"HTTP {e.code} {url}: {err}") from e


def _day_content_for_retain(d: str) -> str:
    """Grounded day digest for Hindsight — only real day-file + summary text."""
    rows = read_day_events(d, limit=400)
    parts = [f"Krew day log {d} ({len(rows)} events)."]
    sp = summary_path(d)
    if sp.is_file():
        parts.append(cut(sp.read_text(encoding="utf-8"), 4000, f"the whole summary is {sp}"))
    # Prefer free-air + system kinds for durable facts
    prefer = {"freeair_user", "freeair_reply", "system", "kanban", "summary", "dream"}
    picks = [r for r in rows if r.get("kind") in prefer] or rows
    for r in picks[-40:]:
        t = (r.get("text") or "").strip().replace("\n", " ")
        if t:
            parts.append(f"- [{r.get('kind')}] {cut(t, 200, f'whole event in {day_path(d)}')}")
    return cut("\n".join(parts), 12000, f"the whole day is {day_path(d)}")


RETAIN_SYNC_TIMEOUT_S = float(os.environ.get("KREW_DREAM_RETAIN_SYNC_TIMEOUT_S", "600"))


def retain_sync_on() -> bool:
    return os.environ.get("KREW_DREAM_RETAIN_SYNC", "1").strip().lower() not in ("0", "false", "no", "off")


def hindsight_retain_day(d: str | None = None, *, dry: bool = False) -> dict:
    """Push one day's krew log into Hindsight bank (async fact extract)."""
    d = d or _today()
    content = _day_content_for_retain(d)
    if "0 events" in content.split("\n")[0] and len(content) < 80:
        return {"ok": True, "skipped": True, "reason": "empty day", "day": d}
    item = {
        "content": content,
        "context": f"Krew short-term day log {d} (free-air + system; not presence invent)",
        "document_id": f"krew-day-{d}",
        "timestamp": f"{d}T23:59:00Z",
        "tags": ["house", "day", "dream", d],
        "metadata": {"source": "krew_memory", "day": d},
    }
    if dry:
        return {"ok": True, "dry": True, "chars": len(content), "day": d}
    url = f"{HINDSIGHT_URL}/v1/default/banks/{HINDSIGHT_BANK}/memories"


    # queued behind them. Synchronous, the POST returns only when retain is done, so reflect never overlaps it.
    # Kill: KREW_DREAM_RETAIN_SYNC=0 (back to async, timeout 90).
    sync = retain_sync_on()
    resp = _http_json("POST", url, {"items": [item], "async": not sync},
                      timeout=RETAIN_SYNC_TIMEOUT_S if sync else 90)
    return {"ok": True, "day": d, "retain": resp, "sync": sync}


def hindsight_consolidate(*, dry: bool = False) -> dict:
    if dry:
        return {"ok": True, "dry": True, "action": "consolidate"}
    url = f"{HINDSIGHT_URL}/v1/default/banks/{HINDSIGHT_BANK}/consolidate"
    resp = _http_json("POST", url, {}, timeout=90)
    return {"ok": True, "consolidate": resp}


REFLECT_CLIENT_TIMEOUT_S = 180


class ReflectFailed(RuntimeError):
    """Reflect gave up; `attempts` says how long each try took and how it ended."""

    def __init__(self, msg: str, attempts: list[dict]):
        super().__init__(msg)
        self.attempts = attempts


def reflect_end_kind(e: BaseException) -> str:
    """client_timeout | server_error | error - from the exception the HTTP call raised."""
    import socket
    import urllib.error
    if isinstance(e, (TimeoutError, socket.timeout)):
        return "client_timeout"
    if isinstance(e, urllib.error.URLError) and isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout)):
        return "client_timeout"
    if isinstance(e, RuntimeError) and str(e).startswith("HTTP "):
        return "server_error"
    return "error"


def hindsight_reflect_day(d: str | None = None, *, dry: bool = False) -> dict:
    """Dream pass: reflect over bank with day-focused query; write dream file."""
    d = d or _today()
    DREAM_DIR.mkdir(parents=True, exist_ok=True)
    query = (
        f"Krew day {d}: What durable facts about {os.environ.get('KREW_DREAM_PEOPLE') or 'the household'}, the room, free-air voice, "
        f"and crew kanban work should Crissa remember? Prefer concrete events from retained "
        f"krew-day logs. No invented presence counts. Short bullets only."
    )
    if dry:
        return {"ok": True, "dry": True, "query": query}
    url = f"{HINDSIGHT_URL}/v1/default/banks/{HINDSIGHT_BANK}/reflect"



    # (the server's per-call LLM limit - HINDSIGHT_API_REFLECT_LLM_TIMEOUT - is longer than one try from here
    # once it is 120); "server_error" = Hindsight answered with an error; "ok". The attempts ride in the night's
    # report, on success AND on failure (ReflectFailed carries them).
    resp = None
    last_err = None
    attempts: list[dict] = []
    for _attempt in range(2):
        t0 = time.monotonic()
        try:
            resp = _http_json(
                "POST",
                url,
                {"query": query, "budget": "mid", "max_tokens": 400},
                timeout=REFLECT_CLIENT_TIMEOUT_S,
            )
            attempts.append({"attempt": _attempt + 1, "s": round(time.monotonic() - t0, 1), "end": "ok"})
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            attempts.append({"attempt": _attempt + 1, "s": round(time.monotonic() - t0, 1),
                             "end": reflect_end_kind(e), "error": str(e)[:160]})
            time.sleep(2)
    if resp is None:
        # Loop only exits with resp None when _http_json raised; last_err is then set.
        raise ReflectFailed(str(last_err), attempts) from last_err
    text = (resp.get("text") or "").strip() or "(empty reflect)"
    out = DREAM_DIR / f"dream-{d}.md"
    # If re-running the same day, freeze prior dream body first
    _archive_tip(out, ARCHIVE_DIR / "dreams", f"dream-{d}-{datetime.now().strftime('%H%M%S')}.md")
    out.write_text(
        f"# Dream · {d}\n\n"
        f"Bank: `{HINDSIGHT_BANK}` · API: `{HINDSIGHT_URL}`\n\n"
        f"{text}\n",
        encoding="utf-8",
    )
    # also stamp day log
    append_event("dream", cut(text, 500, f"the dream file for {d} has it whole"), source="krew_memory.dream", meta={"day": d})
    return {"ok": True, "day": d, "path": str(out), "chars": len(text), "attempts": attempts}


def _run_krew_dream_innate(*, dry: bool = False) -> dict:
    """Fable's innate rollup: Hermes sessions/titles + kanban runs → Hindsight.

    scripts/krew-dream.py --rollup 3 --retain
    """
    script = Path(__file__).resolve().parent / "krew-dream.py"
    if not script.is_file():
        return {"ok": False, "error": "krew-dream.py missing"}
    cmd = ["python3", str(script), "--rollup", "3"]
    if dry:
        cmd.append("--dry")
    else:
        cmd.append("--retain")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        return {
            "ok": r.returncode == 0,
            "rc": r.returncode,
            "stdout": (r.stdout or "")[-1200:],
            "stderr": (r.stderr or "")[-400:],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def dream_failed_steps(steps: list[dict]) -> list[str]:
    """Names of the steps that did not happen: an `*_error` row, or a step whose
    own result says ok: false."""
    out: list[str] = []
    for st in steps:
        for name, val in st.items():
            if name.endswith("_error"):
                out.append(name[: -len("_error")])
            elif isinstance(val, dict) and val.get("ok") is False:
                out.append(name)
    return out


def dream_night(*, day: str | None = None, dry: bool = False, skip_reflect: bool = False,
                catchup: bool = False) -> dict:
    ""
















    # Night job at 03:15 processes "yesterday" so the full calendar day is closed.
    if day is None:
        day = (date.today() - timedelta(days=1)).isoformat()
    report: dict[str, Any] = {"day": day, "dry": dry, "steps": []}
    if catchup:
        report["catchup"] = True

    # 1) Innate Hermes rollup + retain (Fable)
    report["steps"].append({"krew_dream_innate": _run_krew_dream_innate(dry=dry)})

    # 2) Free-air short-term rolls
    for d in {day, _today()}:
        if day_path(d).is_file() or d == _today():
            p = roll_day_summary(d)
            report["steps"].append({"roll_day": str(p)})

    p3 = roll_3day()
    report["steps"].append({"roll_3day": str(p3)})

    # 3) Free-air day into Hindsight (separate document_id; does not re-run innate)
    try:
        ret = hindsight_retain_day(day, dry=dry)
        report["steps"].append({"retain_freeair_day": ret})
    except Exception as e:
        report["steps"].append({"retain_freeair_error": str(e)[:200]})

    # 4) One consolidate + one Dream reflect for the bank
    try:
        cons = hindsight_consolidate(dry=dry)
        report["steps"].append({"consolidate": cons})
    except Exception as e:
        report["steps"].append({"consolidate_error": str(e)[:200]})

    if not skip_reflect:
        try:
            ref = hindsight_reflect_day(day, dry=dry)
            report["steps"].append({"reflect": ref})
        except Exception as e:
            step = {"reflect_error": str(e)[:200]}
            if getattr(e, "attempts", None):
                step["reflect_attempts"] = e.attempts
            report["steps"].append(step)

    ws = write_workspace_today()
    report["steps"].append({"workspace": str(ws)})

    # of its steps were refused, and the night still wrote "ok": true).
    failed = dream_failed_steps(report["steps"])
    report["failed"] = failed
    report["ok"] = not failed
    logp = ROOT / "dream-night.log"
    try:
        with logp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), **report}, default=str) + "\n")
    except OSError:
        pass
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="krew memory files (KREW_MEMORY_ROOT) + the dream night")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("append")
    a.add_argument("--kind", required=True)
    a.add_argument("--text", default="")
    a.add_argument("--source", default="cli")
    sub.add_parser("today")
    sub.add_parser("roll-day")
    sub.add_parser("roll-3day")
    sub.add_parser("workspace")
    d = sub.add_parser(
        "dream-night",
        help="Combined: roll-day + roll-3day + Hindsight retain/consolidate/reflect",
    )
    d.add_argument("--day", default=None, help="YYYY-MM-DD (default: yesterday)")
    d.add_argument("--dry", action="store_true")
    d.add_argument("--skip-reflect", action="store_true")
    d.add_argument("--catchup", action="store_true",
                  help="Re-run a closed day (idempotent; the full pipeline, since "
                       "a failed night skipped everything after the blocked step)")
    r = sub.add_parser("retain-day", help="Only push one day into Hindsight")
    r.add_argument("--day", default=None)
    r.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if args.cmd == "append":
        p = append_event(args.kind, args.text, source=args.source)
        print(p)
    elif args.cmd == "today":
        print(today_bullets())
    elif args.cmd == "roll-day":
        print(roll_day_summary())
    elif args.cmd == "roll-3day":
        print(roll_3day())
    elif args.cmd == "workspace":
        print(write_workspace_today())
    elif args.cmd == "dream-night":
        rep = dream_night(day=args.day, dry=args.dry, skip_reflect=args.skip_reflect,
                          catchup=args.catchup)
        print(json.dumps(rep, indent=2, default=str))
        if not rep.get("ok") and os.environ.get("KREW_DREAM_EXIT_HONEST", "1") != "0":
            return 5   # cron mails nothing, but the log line and the rc are true
    elif args.cmd == "retain-day":
        print(json.dumps(hindsight_retain_day(args.day, dry=args.dry), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
