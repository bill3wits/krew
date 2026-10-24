#!/usr/bin/env bash
# 02:00 — summarize the previous calendar day, then shelve the inbox.
set -euo pipefail
ROOT=${KREW_CREW_ROOT:-$HOME/.krew/crew}
DAY=${KREW_DAY:-$(date -d yesterday +%F)}   # KREW_DAY = the ticker's label when it starts this run
# The round's ledger fresh at the page (read-only on the boards; a try Zoey greened after the
# window's own improvement-outcomes run still makes this morning's table).
python3 "$ROOT/scripts/improvement-outcomes.py" || echo "improvement-outcomes failed rc=$?"
python3 "$ROOT/scripts/zoey-team-daily.py" --day "$DAY"
# Krew Daily: the window ticker builds it as the off-hours close (job `krew-daily`).
# Until that ticker is installed, or on a night it did not fire, this run builds it, so
# the page is never older than the brief. KREW_DAILY=0 turns the page off.
python3 - "$ROOT" <<'PY' || python3 "$ROOT/scripts/krew-daily.py" || echo "krew-daily build failed rc=$?"
import json, sys, time
from pathlib import Path
try:
    m = json.loads((Path.home() / "Desktop/krew-deliverables/krew-daily.json").read_text())
    built = time.mktime(time.strptime(m["built_at"][:19], "%Y-%m-%dT%H:%M:%S"))
    sys.exit(0 if time.time() - built < 45 * 60 else 1)     # the ticker just built it
except Exception:
    sys.exit(1)
PY
python3 "$ROOT/scripts/krew-inbox-organize.py"
date -Iseconds > "$HOME/.hermes/morning-brief-ready"
echo "morning brief ready $DAY"
