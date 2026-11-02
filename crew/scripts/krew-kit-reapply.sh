#!/usr/bin/env bash
# krew-kit-reapply — put the house's kit back on top of whatever `hermes update`
# (or a stray hand) left in the live install, then bounce only the gateways whose
# YAML actually changed. Idempotent: a run with nothing to do writes one log line.
#
# the User 2026-09-16: "our skills indelible so these circus days never occur … configs
# to catch all new features in the updates, but our old kits to be re-written."
#
# The kit is already defined in three places; this is the one hand that lands all
# of them, automatically:
#   YAML      hermes-packs/distributions/<who>/config.yaml  → krew-config-restore.py
#             (deep merge, house key wins, upstream-only keys KEPT — new features stay)
#   skills    hermes-packs/plugins/krew-kitting/allowlist.yaml (inside restore) +
#             hermes-packs/skills/ → sync-packs.sh skills deploy
#   board     kanban_lean toolsets + per-profile plugin links → kanban-lean.sh on
#   code      config/hermes/patches/*.patch → hermes-tree.py (verify here; update
#             re-applies them and then calls this script)
# BRIDGE (~/.hermes/config.yaml) is included only when its toolsets list would not
# shrink (the pack landmine PACK-SYNC.md names); otherwise it is reported, not written.
# SOUL.md and plugins are never copied here — they are deliberate per-file moves
# (pack-drift-check files a card when they drift).
#
# Triggers: krew-kit-reapply.path (the hermes tree's .git/logs/HEAD moved =
# `hermes update` ran), hermes-tree.py update (explicit), pack-drift-check nightly
# (heal before filing a card). Off switch: ~/.hermes/krew-kit-reapply.off or
# KREW_KIT_REAPPLY=0. --dry-run reports; --no-bounce restores without restarts.
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/../lib/krew_hermes.sh"   # $KREW_HERMES_HOME: where Hermes lives
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${KREW_HERMES:-$KREW_HERMES_HOME}"
LOG="$HERMES_HOME/logs/krew-kit-reapply.log"
LOCK="$HERMES_HOME/krew-kit-reapply.lock"
PY="$HERMES_HOME/hermes-agent/venv/bin/python"; [ -x "$PY" ] || PY=python3
DRY=0; BOUNCE=1; REASON="${KREW_KIT_REASON:-manual}"; SETTLE="${KREW_KIT_SETTLE_S:-0}"
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --no-bounce) BOUNCE=0 ;;
    --reason=*) REASON="${a#--reason=}" ;;
    --settle=*) SETTLE="${a#--settle=}" ;;
  esac
done
log() { printf '%s kit-reapply[%s]: %s\n' "$(date -Is)" "$REASON" "$*" | tee -a "$LOG" >&2; }
mkdir -p "$(dirname "$LOG")"
if [ "${KREW_KIT_REAPPLY:-1}" = 0 ] || [ -f "$HERMES_HOME/krew-kit-reapply.off" ]; then
  log "off switch set — nothing done"; exit 0
fi
exec 9>"$LOCK"
if ! flock -n 9; then log "another reapply holds $LOCK — skipping"; exit 0; fi

# A path trigger fires while `hermes update` is still writing; let it finish.
if [ "$SETTLE" -gt 0 ]; then
  sleep "$SETTLE"
  for _ in $(seq 1 30); do
    pgrep -f 'hermes update|hermes-tree.py update' >/dev/null || break
    sleep 10
  done
fi

changed=0; rc=0
snap() { for w in ada crissa; do md5sum "$HERMES_HOME/profiles/$w/config.yaml" 2>/dev/null | cut -d' ' -f1; done | tr '\n' ' '; }
before="$(snap)"

# 1. YAML kit — pack over live, upstream keys kept. Dry-run first so the log shows the plan.
plan="$($PY "$REPO/scripts/krew-config-restore.py" 2>&1)"; prc=$?
if [ $prc -ne 0 ]; then log "restore dry-run failed rc=$prc: $(echo "$plan" | tail -2 | tr '\n' ' ')"; rc=1
elif echo "$plan" | grep -q "would change"; then
  log "restore plan: $(echo "$plan" | grep -E '^[A-Za-z_-]+/(config|profile)\.yaml: [0-9]+ house' | tr '\n' ';' | cut -c1-400)"
  if echo "$plan" | grep -A3 '^BRIDGE-config.yaml:' | grep -q '~ toolsets'; then
    log "BRIDGE toolsets would change — leaving BRIDGE alone (deliberate hand: sync-packs.sh restore --apply --profile BRIDGE)"
    only=1
  else only=0; fi
  if [ $DRY = 0 ]; then
    if [ $only = 1 ]; then
      for w in ada bash lila scout steward vera crissa; do $PY "$REPO/scripts/krew-config-restore.py" --apply --profile "$w" >>"$LOG" 2>&1 || rc=1; done
    else
      $PY "$REPO/scripts/krew-config-restore.py" --apply >>"$LOG" 2>&1 || rc=1
    fi
    changed=1; log "restore applied (rc=$rc)"
  fi
else log "restore: live carries every house setting"; fi

# 1b. Skills a bundled sync added (the User 2026-10-02). A hermes update's bundled-skill sync copies skill FILES
#     into every profile and changes no config, so step 1 restores nothing and the kit is never re-applied
#     (09-26: hermes-agent landed in judge, whose allowlist keeps nothing bundled). Every crew profile's on-disk
#     skill that its allowlist does not keep is ADDED to its live skills.disabled - additive, never re-enables;
#     with nothing new on disk it writes nothing. Kill KREW_KIT_DISABLE_UNLISTED=0.
if [ $DRY = 0 ]; then out="$($PY "$REPO/scripts/krew-kit-disable-unlisted.py" 2>&1)"; urc=$?
else out="$($PY "$REPO/scripts/krew-kit-disable-unlisted.py" --dry-run 2>&1)"; urc=$?; fi
if [ $urc -ne 0 ]; then rc=1; log "disable-unlisted failed rc=$urc: $(echo "$out" | tail -2 | tr '\n' ' ')"
elif echo "$out" | grep -qE 'disabled [0-9]|would disable [0-9]'; then
  [ $DRY = 0 ] && changed=1
  log "$(echo "$out" | grep -E 'disable' | tr '\n' ';' | cut -c1-400)"
fi

# 2. Skills — house SKILL.md trees onto the profiles that carry them.
if [ $DRY = 0 ]; then
  out="$(bash "$REPO/scripts/sync-packs.sh" skills deploy 2>&1)";
  n=$(echo "$out" | grep -c 'deployed pack to live'); [ "$n" -gt 0 ] && { changed=1; log "skills deploy: $n file(s)"; }
else
  n=$(bash "$REPO/scripts/sync-packs.sh" skills 2>&1 | grep -c '^DRIFT\|^MISSING live'); [ "$n" -gt 0 ] && log "skills: $n would deploy"
fi

# 3. Board kit — a fat restore cannot stick (KANBAN-AND-HERMES-PINS.md §2).
if [ $DRY = 0 ]; then
  bash "$REPO/scripts/kanban-lean.sh" on >>"$LOG" 2>&1 || { rc=1; log "kanban-lean on failed"; }
fi

# 4. Code queue — report only; hermes-tree.py update owns the re-apply.
if ! python3 "$REPO/scripts/hermes-tree.py" verify >/dev/null 2>&1; then
  log "hermes-tree VERIFY RED — run: python3 scripts/hermes-tree.py status (patches missing/stale or uncovered edits)"; rc=1
fi

# 5. Bounce only what changed. Ada always reads config at start; Crissa is the room
#    voice — skip her while a line is on air and say so.
after="$(snap)"
if [ $DRY = 0 ] && [ $BOUNCE = 1 ] && [ "$before" != "$after" ]; then
  set -- $before; b_ada=${1:-}; b_crissa=${2:-}; set -- $after; a_ada=${1:-}; a_crissa=${2:-}
  # lib/hermes_topology restarts what answers for the profile: her unit today, the profile inside
  # the ONE host gateway after the cutover (KREW_GATEWAY_TOPOLOGY=host) — nobody else reconnects.
  topo="$REPO/lib/hermes_topology.py"   # krew's own crew/lib copy (this script's crew/), never another tree's
  if [ ! -f "$topo" ]; then log "missing: $topo — the changed gateway config is not applied: restart ada/crissa by hand"; rc=1
  else
  if [ "$b_ada" != "$a_ada" ]; then python3 "$topo" restart ada && log "ada gateway restarted (config changed)"; fi
  if [ "$b_crissa" != "$a_crissa" ]; then
    if curl -fsS --max-time 3 "${CRISSA_PLAYER:-http://127.0.0.1:8100}/v1/state" 2>/dev/null | grep -q '"on_air": *true'; then log "crissa config changed but a line is on air — restart her gateway by hand: systemctl --user restart hermes-crissa-gateway"; rc=1
    else python3 "$topo" restart crissa && log "crissa gateway restarted (config changed)"; fi
  fi
  fi
fi

# 6. Pin-check closes the loop.
if [ $DRY = 0 ]; then
  if python3 "$REPO/scripts/krew-pin-check.py" >/dev/null 2>&1; then log "pin-check ok (changed=$changed)"; else log "pin-check RED after reapply"; rc=1; fi
fi
exit $rc
