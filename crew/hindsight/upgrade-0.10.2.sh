#!/bin/bash
#
#
# Runs as `ExecStartPre=-` (drop-in hindsight.service.d/40-upgrade-0102.conf), as the service user, with the
# service STOPPED - so the running 0.10.1 never sees half-new files. Idempotent: a no-op once on 0.10.2.
# The `-` means a failure here never blocks the start: 0.10.1 just comes up as before.
#
# Order: database backup FIRST (0.10.2 adds six alembic migrations, run at startup - a pin alone is no
# rollback), then an OFFLINE install of two verified wheels (hindsight-api + hindsight-api-slim 0.10.2; nothing
# else - 0.10.2 declares the same dependencies as 0.10.1). Any failure puts 0.10.1 back from the rollback wheelhouse.
#
#
# Rollback after it ran: docs/checkpoints/hindsight-0102-20261002/ROLLBACK.md
set -u
BASE=${HINDSIGHT_BASE:-$HOME/.krew/hindsight}
ENV=$BASE/.venv
PY=$ENV/bin/python
WH=$BASE/wheelkrew-0.10.2
RB=$BASE/wheelkrew-rollback-0.10.1
ON=$BASE/upgrade-0.10.2.on
LOG=$BASE/upgrade-0.10.2.log
UV=${UV:-$(command -v uv || echo $HOME/.local/bin/uv)}
DRY=${DRY:-0}

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }
ver() { "$PY" -c 'import importlib.metadata as m; print(m.version("hindsight-api"))' 2>/dev/null; }
install() {  # $1 = wheelhouse, then pins
    local wh=$1; shift
    "$UV" pip install --python "$PY" --no-index --find-links "$wh" --no-deps "$@" >> "$LOG" 2>&1
}

[ -e "$ON" ] || { log "switch off ($ON absent): nothing done"; exit 0; }
cur=$(ver)
[ "$cur" = "0.10.2" ] && { log "already 0.10.2: nothing done"; exit 0; }
[ "$cur" = "0.10.1" ] || { log "installed is '$cur', not 0.10.1: not touching it"; exit 0; }
[ -n "${HINDSIGHT_API_DATABASE_URL:-}" ] || { log "no HINDSIGHT_API_DATABASE_URL: not upgrading without a backup"; exit 0; }
for f in hindsight_api-0.10.2-py3-none-any.whl hindsight_api_slim-0.10.2-py3-none-any.whl; do
    [ -f "$WH/$f" ] || { log "missing $WH/$f: not upgrading"; exit 0; }
done

mkdir -p "$BASE/backups"
dump=$BASE/backups/hindsight-pre-0.10.2-$(date +%Y%m%d-%H%M%S).dump
[ "$DRY" = 1 ] && dump=${DRY_DUMP:-/tmp/hindsight-dry-$$.dump}
if ! pg_dump --format=custom --file="$dump" "$HINDSIGHT_API_DATABASE_URL" 2>>"$LOG"; then
    rm -f "$dump"; log "pg_dump FAILED: not upgrading (0.10.1 starts)"; exit 0
fi
log "database backup $dump ($(stat -c%s "$dump") bytes)"

if [ "$DRY" = 1 ]; then
    "$UV" pip install --python "$PY" --no-index --find-links "$WH" --no-deps --dry-run \
        hindsight-api==0.10.2 hindsight-api-slim==0.10.2 >> "$LOG" 2>&1 \
        && log "DRY: offline install would succeed; nothing changed" || log "DRY: offline install would FAIL"
    rm -f "$dump"; exit 0
fi

if install "$WH" hindsight-api==0.10.2 hindsight-api-slim==0.10.2 && [ "$(ver)" = "0.10.2" ]; then
    log "installed hindsight-api 0.10.2 (migrations run at this start; backup $dump)"
else
    log "install FAILED: putting 0.10.1 back from $RB"
    install "$RB" hindsight-api==0.10.1 hindsight-api-slim==0.10.1 \
        && log "0.10.1 restored" || log "ROLLBACK INSTALL FAILED - needs hands"
fi
exit 0
