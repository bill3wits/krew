#!/usr/bin/env bash
# Team eval: each worker role scores tonight's research picks for its own job,
# then the ranker adds the voices up. 01:00 daily (krew-team-eval.timer),
# forty minutes after Scout's 00:20 research note. Occupancy + flock, same
# shape as steward-architect-run.sh. Never hermes -z.
set -uo pipefail
ROOT=${KREW_CREW_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}   # krew: this checkout or the install, by where the script sits
JOB_STATUS_PY="$ROOT/../window/lib/job_status.py"; [ -f "$JOB_STATUS_PY" ] || JOB_STATUS_PY="$ROOT/lib/job_status.py"   # krew: job_status lives with the window
LOCK=$HOME/.hermes/architect.lock
HERMES=${KREW_HERMES_BIN:-$(command -v hermes || echo "$HOME/.local/bin/hermes")}
INBOX=${KREW_DELIVERABLES:-$HOME/Desktop/krew-deliverables}
# One label for the whole chain: KREW_DAY from the window ticker (the date the User's
# off-hours opened) so research before midnight and scoring after it are one night.
TODAY=${TEAM_EVAL_DAY:-${KREW_DAY:-$(date +%F)}}
export KREW_DAY="$TODAY"   # the ranker gets --day; the filer reads architect_lib.today()
ROLES=${TEAM_EVAL_ROLES:-"ada bash lila vera steward"}
MODE=normal
[ -f $HOME/.hermes/krew-gpu-mode ] && MODE=$(head -1 $HOME/.hermes/krew-gpu-mode | tr '[:upper:]' '[:lower:]')
log() { echo "$(date +%FT%T) team-eval: $*"; }
# The job's own word (self-loop 09-28, lib/job_status.py): the window ticker reads it.
jstat() { python3 "$JOB_STATUS_PY" write team-eval "$TODAY" "$1" "${2:-}" "${3:-}" >/dev/null || true; }
if [ "$MODE" = beast ]; then log "skip occupancy=beast"; jstat skipped "occupancy=beast"; exit 0; fi
if [ ! -f "$INBOX/team-research-$TODAY.md" ]; then
  log "no team-research-$TODAY.md — nothing to score"
  jstat skipped "no team-research-$TODAY.md to score"; exit 0
fi
exec 9>"$LOCK"
if ! flock -w 600 9; then log "skip: architect lock busy"; jstat failed "architect lock busy for 600 s - nothing scored"; exit 0; fi
# A role that writes no note is a missing voice, not a quiet one (2026-09-14 and
# 09-16: two voters of five, nothing said so). Its output is kept, the miss is
# logged, and the run exits 4 so the unit shows it.
RUNLOG=$HOME/.hermes/logs/team-eval
mkdir -p "$RUNLOG"
MISSING=""
noted() { [ -f "$INBOX/$1-improvement-ideas-$TODAY.md" ] || [ -f "$INBOX/ideas/$1-improvement-ideas-$TODAY.md" ]; }
# A role's "${KREW_DELIVERABLES:-$HOME/Desktop/krew-deliverables}" can be its PROFILE home (hermes sets HOME to
# ~/.hermes/profiles/<p>/home): vera scored every item on 09-17 and 09-18 and was counted
# absent both nights. Bring such a note into the shared ideas/ drawer (copy, never move).
# TEAM_EVAL_PROFILE_HOME=0 = the old inbox-only look.
adopt() {
  [ "${TEAM_EVAL_PROFILE_HOME:-1}" = 0 ] && return 0
  local src="$HOME/.hermes/profiles/$1/home/Desktop/krew-deliverables/$1-improvement-ideas-$TODAY.md"
  if ! noted "$1" && [ -f "$src" ]; then
    mkdir -p "$INBOX/ideas" && cp -p "$src" "$INBOX/ideas/" && log "$1 note adopted from its profile home"
  fi
}
for p in $ROLES; do
  adopt "$p"
  if noted "$p"; then log "$p already scored"; continue; fi
  log "$p scoring"
  timeout 900 "$HERMES" -p "$p" cron run team-eval >"$RUNLOG/$p-$TODAY.log" 2>&1 || log "$p run rc=$?"
  adopt "$p"
  if ! noted "$p"; then
    MISSING="$MISSING $p"; log "$p wrote NO note (see $RUNLOG/$p-$TODAY.log)"
  fi
done
find "$RUNLOG" -name '*.log' -mtime +14 -delete 2>/dev/null || true
rc=0
python3 "$ROOT/scripts/team-eval-rank.py" --day "$TODAY" || { rc=$?; log "rank rc=$rc"; }
# Filing is NOT here any more (self-loop step 3): the window row improve-file runs after this
# one and checks the vote itself - a chain cannot gate, `failed` is final and the next row
# starts anyway. This run fails only BELOW QUORUM (the ranker's own rule, 3 real voters);
# a named missing voice with three real voters is reported, not a failed night.
RANK="${KREW_MEMORY_ROOT:-$HOME/.krew/memory}/ms/improvements/rank-$TODAY.json"
[ -n "$MISSING" ] && log "missing voices:$MISSING"
if [ ! -s "$RANK" ]; then
  [ "$rc" = 0 ] && rc=4
  jstat failed "no rank-$TODAY.json written" "$RANK"
elif [ "$rc" = 0 ]; then
  Q=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); v=[x for x in d.get("voters") or [] if x not in (d.get("discounted") or {})]; print(len(v), int(d.get("quorum") or 3))' "$RANK" 2>/dev/null || echo "0 3")
  REAL=${Q% *}; QUORUM=${Q#* }
  if [ "$REAL" -lt "$QUORUM" ]; then
    rc=4; jstat failed "below quorum: $REAL real voter(s) of $QUORUM${MISSING:+; missing:$MISSING}" "$RANK"
  else
    jstat ok "$REAL real voter(s)${MISSING:+; missing:$MISSING}" "$RANK"
  fi
else
  jstat failed "rank exit $rc${MISSING:+; missing voices:$MISSING}" "$RANK"
fi
exit "$rc"
