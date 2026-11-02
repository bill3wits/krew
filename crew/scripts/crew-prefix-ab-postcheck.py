#!/usr/bin/env python3
"""Post-restart-window verification for crew prefix-cache A/B (card t_85380287).

One-shot, runs from a systemd user timer after the off-hours restart window
(2026-09-22 01:00-04:00 EDT). Does exactly the card's step 5:

  1. curl-equivalent health check on crew :8005 (/v1/models).
  2. one fresh sample row via scripts/crew-prefix-ab-measure.py (appends to
     ~/.hermes/logs/crew-prefix-ab.jsonl).
  3. posts the health result + new sample row as a comment on t_85380287
     (via `hermes -p bash kanban comment` — the house way; this script must
     never touch kanban.db directly).

Measurement only. It never restarts anything and never creates cards.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HOME = Path(os.environ.get("HERMES_REAL_HOME", str(Path.home())))
RAILS = Path(os.environ.get("KREW_CREW_ROOT") or Path(__file__).resolve().parents[1])   # krew: the installed crew tree
CARDS = ["t_85380287"]
MODELS = os.environ.get("KREW_CREW_MODELS", "http://127.0.0.1:8005/v1/models")
HOLD = Path("/tmp/crew-prefix-ab-postcheck.txt")


def curl(url: str, timeout: int = 10) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode(errors="replace")
            return r.status == 200, body
    except Exception as e:  # engine down is a result, not a crash
        return False, f"{type(e).__name__}: {e}"


def health() -> dict:
    ok, body = curl(MODELS)
    if ok:
        try:
            data = json.loads(body).get("data", [])
            ids = [d.get("id") for d in data]
            root = data[0].get("root", "") if data else ""
        except Exception:
            ids, root = ["<unparsed>"], body[:120]
        return {"healthy": True, "ids": ids, "root": root}
    return {"healthy": False, "error": body[:300]}


def sample() -> dict | None:
    """Run the measure script (appends a row) and return the newest JSONL row."""
    measure = RAILS / "scripts/crew-prefix-ab-measure.py"
    try:
        subprocess.run([sys.executable, str(measure)], capture_output=True,
                       text=True, timeout=120, check=True)
    except Exception as e:
        return None
    out = KH.hermes_home() / "logs/crew-prefix-ab.jsonl"
    try:
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        return json.loads(lines[-1])
    except Exception:
        return None


def _hermes() -> str:
    cands = [
        Path.home() / ".local/bin/hermes",
        KH.hermes_home() / "bin/hermes",
        KH.hermes_home() / "profiles/bash/home/.local/bin/hermes",
        Path("~/projects/hermes-agent/.venv/bin/hermes"),
    ]
    for cand in cands:
        if cand.exists():
            return str(cand)
    import shutil
    return shutil.which("hermes") or "hermes"


def board_comment(task: str, body: str) -> bool:
    hermes = _hermes()
    cmd = [hermes, "-p", "bash", "kanban", "comment", task, body,
           "--author", "bash"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    no_post = "--no-post" in argv
    now = datetime.now().isoformat(timespec="seconds")
    h = health()
    row = sample()

    lines = [f"Post-window verification (t_85380287 step 5), {now}."]
    if h["healthy"]:
        lines.append(f"HEALTHY — crew :8005 /v1/models answers; model ids {h['ids']}; "
                     f"root {h.get('root', '')}.")
    else:
        lines.append(f"UNHEALTHY — crew :8005 /v1/models FAILED: {h.get('error', '')}")
    if row:
        lines.append("Sample row: " + json.dumps(row))
    else:
        lines.append("Sample row: measure script failed or JSONL unreadable.")
    lines.append("(posted by crew-prefix-ab-postcheck.py, one-shot timer)")

    for t in CARDS:
        if no_post:
            break
        board_comment(t, "\n".join(lines))

    HOLD.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0 if h["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
