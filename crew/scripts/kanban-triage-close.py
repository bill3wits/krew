#!/usr/bin/env python3
"""krew: kanban-triage-close.py (lifted from the reference install; see crew/README.md)."""










from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))


def close_triage(kb, conn, tid: str, result: str, author: str = "kanban-loop") -> dict:
    row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
    status = row[0] if row else None
    if status != "triage":
        return {"ok": False, "card": tid, "error": f"not a triage card (status={status})"}
    if not kb.specify_triage_task(conn, tid, author=author):
        return {"ok": False, "card": tid, "error": "specify refused"}
    if not kb.complete_task(conn, tid, result=result, summary=result):
        now = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
        return {"ok": False, "card": tid, "error": f"complete refused after specify (status={now[0] if now else None})"}
    return {"ok": True, "card": tid, "status": "done"}


def _board_db() -> Path:
    home = Path(os.environ.get("HERMES_REAL_HOME", str(Path.home()))) / ".hermes" / "kanban"
    cur = home / "current"
    slug = (cur.read_text(encoding="utf-8").strip() if cur.is_file() else "") or "daily"
    return home / "boards" / slug / "kanban.db"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    import hermes_runtime
    hermes_runtime.ensure(__file__)
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect
    out = close_triage(kb, connect(_board_db()), argv[1], argv[2])
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
