"""Every announcer line, one file a day (the User 2026-09-30: "I want to hear more of what kanban does, to learn
from it ... Log every announcer line, including skipped and summarised ones, to a daily file the House Daily links
to").

    <inbox>/announcer/announcer-YYYY-MM-DD.log     one line per line the announcer was given, tab-separated:
    HH:MM:SS  event  category  reason  text

events: spoken | skipped (said why) | failed (the synth or the player broke) | summarised (folded into a roundup,
the reason names it) | dropped (thrown away unspoken: queue overflow, quiet hours, a hold). The inbox is the House
Daily's own folder, so the page links the file by a relative path. Appends only, never raises (a log must never
cost the announcer a line). Kill: KREW_ANNOUNCE_LOG=0.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

INBOX = Path(os.environ.get("KREW_DAILY_INBOX", str(Path.home() / "Desktop" / "krew-deliverables")))
DIR = Path(os.environ.get("KREW_ANNOUNCE_LOG_DIR", str(INBOX / "announcer")))
ON = os.environ.get("KREW_ANNOUNCE_LOG", "1").strip().lower() not in ("0", "false", "no", "off")
EVENTS = ("spoken", "skipped", "failed", "summarised", "dropped")


def path_for(day: dt.date, folder: Path | None = None) -> Path:
    return (folder or DIR) / f"announcer-{day:%Y-%m-%d}.log"


def _clean(v: object, limit: int) -> str:
    return " ".join(str(v or "").replace("\t", " ").split())[:limit]


def record(event: str, text: str, *, category: str = "", reason: str = "", ts: float | None = None,
           folder: Path | None = None) -> None:
    if not ON:
        return
    try:
        when = dt.datetime.fromtimestamp(ts or dt.datetime.now().timestamp())
        p = path_for(when.date(), folder)
        p.parent.mkdir(parents=True, exist_ok=True)
        line = "\t".join((f"{when:%H:%M:%S}", _clean(event, 12), _clean(category, 12), _clean(reason, 160),
                          _clean(text, 600))) + "\n"
        with open(p, "a", encoding="utf-8") as fh:            # one short append per line: safe across processes
            fh.write(line)
    except Exception:  # noqa: BLE001
        pass


def read(day: dt.date, folder: Path | None = None) -> list[dict]:
    out = []
    try:
        lines = path_for(day, folder).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split("\t")
        if len(parts) >= 5:
            out.append({"time": parts[0], "event": parts[1], "category": parts[2], "reason": parts[3],
                        "text": "\t".join(parts[4:])})
    return out
