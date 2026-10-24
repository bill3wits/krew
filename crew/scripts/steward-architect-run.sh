#!/usr/bin/env bash
# Occupancy + flock + hermes cron run. Never hermes -z.
# Usage: steward-architect-run.sh world|stack|logs|research|sources|ops
set -euo pipefail
JOB="${1:?world|stack|logs|research|sources|ops}"
ROOT=${KREW_CREW_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}   # krew: this checkout or the install, by where the script sits
JOB_STATUS_PY="$ROOT/../window/lib/job_status.py"; [ -f "$JOB_STATUS_PY" ] || JOB_STATUS_PY="$ROOT/lib/job_status.py"   # krew: job_status lives with the window
# Overridable for tests only (tests/test_self_loop_step1.py runs this file against a stub
# hermes); the defaults are the live paths.
LOCK=${ARCHITECT_LOCK:-$HOME/.hermes/architect.lock}
HERMES=${ARCHITECT_HERMES_BIN:-${KREW_HERMES_BIN:-$(command -v hermes || echo "$HOME/.local/bin/hermes")}}
MS=${ARCHITECT_MS_ROOT:-${KREW_MEMORY_ROOT:-$HOME/.krew/memory}/ms}
GPU_MODE_FILE=${ARCHITECT_GPU_MODE_FILE:-$HOME/.hermes/krew-gpu-mode}
MODE=normal
if [ -f "$GPU_MODE_FILE" ]; then
  MODE=$(head -1 "$GPU_MODE_FILE" | tr '[:upper:]' '[:lower:]')
fi
INBOX=${ARCHITECT_INBOX:-${KREW_DELIVERABLES:-$HOME/Desktop/krew-deliverables}}
# One label for a chain the window ticker starts (the User 2026-09-17: "avoiding midnight
# crossovers"): KREW_DAY = the date the User's off-hours opened, from
# $KREW_HOME/krew-day.env via the unit's EnvironmentFile. Unset = the calendar day.
# A label already in the environment means the window ticker started us (the unit's
# EnvironmentFile): a window job must RUN or say it did not — it never skips quietly.
WINDOW_RUN=${KREW_DAY:+1}
TODAY=${KREW_DAY:-$(date +%F)}
export KREW_DAY="$TODAY"   # collect / filer / kanban-snapshot read architect_lib.today()
RUN_START=$(date +%s)
mkdir -p "$INBOX" "$MS"
# The job's own word (self-loop, the User 09-28): one status line per run in
# ~/.hermes/job-status/<job>-<day>.json (lib/job_status.py). The window ticker reads it
# with systemd's verdict: exit 0 and no line, or a line that says failed, is `failed`.
jstat() {
  python3 "$JOB_STATUS_PY" write "$JOB" "$TODAY" "$1" "${2:-}" "${3:-}" || true
}
usage() {
  python3 - <<PY
import json, time
from pathlib import Path
p=Path("$MS/architect-usage.jsonl")
p.parent.mkdir(parents=True, exist_ok=True)
p.open("a").write(json.dumps({
  "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
  "job": "$JOB",
  "event": "$1",
  "occupancy": "$MODE",
  "rc": "$2",
})+"\n")
PY
}

case "$JOB" in
  world) PROFILE=scout; CRON=architect-world; NOTE="$INBOX/architect-world-$TODAY.md" ;;
  stack) PROFILE=scout; CRON=architect-stack; NOTE="$INBOX/architect-stack-$TODAY.md" ;;
  logs)  PROFILE=steward; CRON=steward-logs; NOTE="$INBOX/logs-$TODAY.md" ;;
  research) PROFILE=scout; CRON=team-research; NOTE="$INBOX/team-research-$TODAY.md" ;;
  sources) PROFILE=scout; CRON=team-sources; NOTE="$INBOX/team-sources-$TODAY.md" ;;
  ops) PROFILE=ada; CRON=ops-efficiency; NOTE="$INBOX/ops-efficiency-$TODAY.md" ;;
  *) echo "unknown job $JOB"; exit 2 ;;
esac

train_busy() {
  pgrep -f 'evening-train-window' >/dev/null 2>&1
}

if [ "$JOB" = research ] && train_busy; then
  python3 - "$NOTE" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(f"# {p.stem}\n\n_skipped evening-train running; catch-up 02:25_\n")
print("stub train", p)
PY
  usage skip-train 0
  jstat skipped "evening-train running; catch-up 02:25" "$NOTE"
  exit 0
fi

if [ "$MODE" = beast ]; then
  python3 - "$NOTE" "$JOB" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1])
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(f"# {p.stem}\n\n_skipped occupancy=beast job={sys.argv[2]}_\n")
print("stub", p)
PY
  usage skip 0
  jstat skipped "occupancy=beast" "$NOTE"
  exit 0
fi

mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
# research is the head of the window's improve chain (team-eval follows its COMPLETION).
# A non-blocking skip here exited 0 having done nothing: the ticker recorded ok and
# team-eval scored a note that did not exist. The usual holder is steward-sweep (~1.5 s,
# hourly) or the previous link of the chain (logs runs up to 18 min), so research and every
# WINDOW run WAIT (30 min); if the lock never frees they exit 75 (tempfail) and the ticker
# records failed. A wall-clock run keeps the old quiet skip.
if [ "$JOB" = research ] || [ -n "${WINDOW_RUN:-}" ]; then
  if ! flock -w "${ARCHITECT_LOCK_WAIT_S:-1800}" 9; then
    echo "FAILED: architect lock busy for ${ARCHITECT_LOCK_WAIT_S:-1800}s — $JOB did not run"
    usage lock 75
    jstat failed "architect lock busy for ${ARCHITECT_LOCK_WAIT_S:-1800}s - did not run"
    exit 75
  fi
elif ! flock -n 9; then
  echo "skip: architect lock busy"
  usage lock 0
  jstat skipped "architect lock busy (wall-clock run)"
  exit 0
fi

if [ "$JOB" = logs ]; then
  python3 "$ROOT/scripts/steward-inbox-archive.py" || true
  python3 "$ROOT/scripts/steward-logs-collect.py" || true
  python3 "$ROOT/scripts/kanban-snapshot.py" || true
  python3 "$ROOT/scripts/ops-efficiency-charts.py" || true
fi
if [ "$JOB" = ops ]; then
  python3 "$ROOT/scripts/kanban-snapshot.py" || true
  python3 "$ROOT/scripts/ops-efficiency-charts.py" || true
fi
if [ "$JOB" = world ] || [ "$JOB" = stack ]; then
  # self-loop 09-28: ONE evening collect (window row architect-collect-evening) writes the
  # day's bundle; world and stack read it and never collect again. On 09-27 the world
  # note said "no bundle" while a later collect wrote INDEX.md at 20:45:09. No bundle =
  # failed, and hermes is not called to write a blind note.
  IDX="$MS/architect-collect/$TODAY/INDEX.md"
  if [ ! -s "$IDX" ]; then
    echo "FAILED: no collect bundle for $TODAY ($IDX missing) - $JOB not run"
    usage no-bundle 3
    jstat failed "no collect bundle for $TODAY (INDEX.md missing); hermes not called" "$IDX"
    exit 3
  fi
fi
if [ "$JOB" = research ] || [ "$JOB" = sources ]; then
  python3 "$ROOT/scripts/team-research-collect.py" || true
  python3 "$ROOT/scripts/kanban-snapshot.py" || true
fi
if [ "$JOB" = research ]; then
  # The repos NNT features are in the video DESCRIPTIONS, not the titles (2026-09-17):
  # adds "## NNT repos" to today's team.md from the feed the collector cached.
  python3 "$ROOT/scripts/nnt-repos.py" || true
  # self-loop step 4: last night's answers, LAST in team.md, before Scout reads it (the
  # cooldown list + the last verdicts, lessons-<day>.md from the outcomes-lessons row)
  python3 "$ROOT/lib/idea_ledger.py" attach-lessons "$MS/improvements/lessons-$TODAY.md" \
    "$MS/architect-collect/$TODAY/team.md" --who "for Scout" || true
fi

usage start 0
set +e
# Feb 31 schedule never auto-fires; systemd is the ticker.
"$HERMES" -p "$PROFILE" cron run "$CRON"
rc=$?
set -e
usage end "$rc"
# The scout's LLM names its note by the calendar date. A run that crossed midnight
# wrote team-research-<tomorrow>.md; give it the chain's label so team-eval finds it.
if [ "$JOB" = research ] && [ ! -f "$NOTE" ]; then
  CAL="$INBOX/team-research-$(date +%F).md"
  if [ "$CAL" != "$NOTE" ] && [ -f "$CAL" ] && [ "$(stat -c %Y "$CAL")" -ge "$RUN_START" ]; then
    mv "$CAL" "$NOTE" && echo "renamed $(basename "$CAL") -> $(basename "$NOTE") (KREW_DAY label)"
  fi
fi
# The job's own word: hermes returns 0 when its model 404s (09-27 logs, 3 s), so the rc
# alone proves nothing. A note written THIS run is the artifact; a thin research night
# (`ideas: none`) is still a note and passes.
# The exit code IS the gate (t_969679a2): the 09-28 night ended text_response with no
# write_file and the unit still exited 0, so the ticker read the systemd success and
# team-eval started on top of the miss. The note gate now flips the exit code too —
# a failed line is not a word, it is the verdict.
EXIT="$rc"
if [ "$rc" != 0 ]; then
  jstat failed "hermes cron run $CRON exit $rc" "$NOTE"
  EXIT=1
elif [ ! -s "$NOTE" ]; then
  jstat failed "no note written: $(basename "$NOTE")" "$NOTE"
  EXIT=1
elif [ "$(stat -c %Y "$NOTE")" -lt "$RUN_START" ]; then
  jstat failed "note not written this run: $(basename "$NOTE") is older than the run" "$NOTE"
  EXIT=1
else
  jstat ok "" "$NOTE"
  # The window recovered: yesterday's failed line is history, not tonight's verdict
  # (09-29: the unit stayed red a day on research-2026-09-28, t_96f37501 follow-up).
  python3 - "$JOB" "$TODAY" "$(dirname "$JOB_STATUS_PY")" <<'PY' || true
import json, sys
sys.path.insert(0, sys.argv[3])   # krew: the quoted heredoc expands nothing; the dir comes in as an argument
import job_status
print("cleared stale", json.dumps(job_status.clear_stale(sys.argv[1], sys.argv[2])))
PY
fi
# ONE filing path (self-loop step 3, the User 09-28): the window row improve-file (after
# team-eval) files the improvement round - research, sources, stack and team-eval no longer
# call the filer. The Sunday ops run keeps its own call, for its own note only (ops-action).
# self-loop step 4: the voters score from the research note, so the same lessons go on its end,
# under their own heading (not Scout's words; no line in it is an item)
if [ "$JOB" = research ] && [ -s "$NOTE" ]; then
  python3 "$ROOT/lib/idea_ledger.py" attach-lessons "$MS/improvements/lessons-$TODAY.md" "$NOTE" \
    --who "appended by the runner for the voters" || true
fi
if [ "$JOB" = stack ] && [ "$rc" = 0 ]; then
  python3 "$ROOT/scripts/steward-spike-maybe.py" || true
fi
if [ "$JOB" = ops ]; then
  python3 "$ROOT/scripts/steward-file-architect-cards.py" --ops || true
  # self-loop step 5: the weekly self-review + self-claims.json, BESIDE this note (which stays:
  # it is the ops-action source). The window has no weekday, so it rides the Sunday unit.
  python3 "$ROOT/scripts/self-review.py" || true
fi
if [ "$JOB" = research ] || [ "$JOB" = sources ] || [ "$JOB" = ops ]; then
  python3 "$ROOT/scripts/kanban-snapshot.py" || true
fi
# HTML copy/print siblings (M&S + research + architect notes).
python3 "$ROOT/scripts/krew-report-html.py" --job "$JOB" --day "$TODAY" || true
exit "$EXIT"
