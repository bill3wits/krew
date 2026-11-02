#!/usr/bin/env bash
# 02:25: run research only if midnight skipped for evening-train.
set -euo pipefail
# 02:25 is AFTER the User's off-hours close, so the calendar says tomorrow while the chain's
# label is the date the window OPENED (KREW_DAY). Ask the one reader; no window written or an
# unreadable site = the calendar day, as before.
ROOT=${KREW_CREW_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}   # krew: this checkout or the install, by where the script sits
TODAY=$(python3 - <<PY 2>/dev/null || date +%F
import sys, datetime as dt
sys.path.insert(0, "$ROOT/lib")
sys.path.insert(0, "$ROOT/../window/lib")   # krew: krew_hours lives with the window
import krew_hours
oh = krew_hours.off_hours()
now = dt.datetime.now()
# The MOST RECENT opening: today once we are past the opening time, else yesterday.
# (krew_hours.started_on() only looks back while the window is still open — at 02:25 it
# answers today, which is the wrong label for last night's chain.)
day = now.date() if oh["source"] != "init" or (now.hour * 60 + now.minute) >= oh["start"] else now.date() - dt.timedelta(days=1)
print(day.isoformat())
PY
)
export KREW_DAY="$TODAY"
NOTE=${KREW_DELIVERABLES:-$HOME/Desktop/krew-deliverables}/team-research-$TODAY.md
if [ -f "$NOTE" ] && ! grep -q 'skipped evening-train' "$NOTE"; then
  echo "research already wrote $NOTE"
  exit 0
fi
exec "$ROOT/scripts/steward-architect-run.sh" research
