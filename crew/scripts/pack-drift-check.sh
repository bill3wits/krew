#!/bin/bash
# pack-drift-check — does what krew COMMITTED actually match what runs?
#
# 2026-08-26: four files from commit 343d60d ("Plan: clean Hermes tree for
# update; Ada SOUL owns zoey-not-worker") were committed to krew and
# never deployed. The commit made them look shipped. Live Ada had no Zoey
# doctrine at all while the pack asserted "Live guard is Ada SOUL" — so the
# only real guard was the core patch we were about to delete.
#
# A krew commit is NOT a deploy. Nothing reconciles them on its own and
# git cannot show you the gap. This is the thing that notices.
#
# Drift in the KIT heals itself first (2026-09-16, the User: "our skills indelible"):
# krew-kit-reapply.sh deep-merges pack YAML over live (upstream-only keys kept),
# deploys house skills and re-pins kanban_lean before this check runs, so what
# is left below is real drift — SOUL, plugins, BRIDGE toolset shrink — and that
# decision stays human. Deploying a config whole-file is still never automatic.
# Heal off: KREW_KIT_HEAL=0 or ~/.hermes/krew-kit-reapply.off.
set -uo pipefail

REPO=${KREW_CREW_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}   # krew: this checkout or the install, by where the script sits
PACKS=${KREW_PACKS_ROOT:-$HOME/.krew/packs}   # krew: the packs checkout (distributions/, plugins/), as krew-config-restore reads it
LIVE=$HOME/.hermes/profiles
H=${KREW_HERMES_BIN:-$(command -v hermes || echo "$HOME/.local/bin/hermes")}
LOG=$HOME/.hermes/logs/pack-drift.log

log() { printf '%s %s\n' "$(date -Is)" "$*" >> "$LOG"; }

# Stale-ref guard (2026-09-17, t_4edcf110 / t_df1c04ca): the pack IS the
# landing truth, but only while origin/main is CURRENT. Measured 09-17:
# `git fetch` fails on expired creds AND the repo 404s even on the
# unauthenticated `git ls-remote` probe (adversarial review confirmed: the
# "ls-remote works" reading was a PIPESTATUS-of-head artefact), and
# origin/main sits 18 days behind a branch 1168 ahead — every in-flight
# commit then shows up as "committed but not live", which mass-filed 14/14
# profile files at 12:00. So: when HEAD is far ahead (>100) AND the fetch
# fails, the check cannot verify — it logs UNVERIFIED and files no card.
# Restoring creds (fetch OK again) re-arms carding; if the ref ever gets
# pushed, ahead shrinks below the threshold on the next fetch.
# Override: PACK_DRIFT_FORCE=1 files the card anyway.
if [ "${PACK_DRIFT_FORCE:-0}" != 1 ]; then
  AHEAD=$( ( cd "$REPO" && git rev-list --count "origin/main..HEAD" 2>/dev/null ) || echo 0)
  case "$AHEAD" in ''|*[!0-9]*) AHEAD=0 ;; esac
  if [ "$AHEAD" -gt 100 ]; then
    FETCH_OK=$( ( cd "$REPO" && timeout 15 git fetch origin >/dev/null 2>&1 ) && echo 1 || echo 0)
    if [ "$FETCH_OK" != 1 ]; then
      REASON="UNVERIFIED — origin/main stale (commit $(git -C "$REPO" log -1 --format=%cs origin/main 2>/dev/null), HEAD ${AHEAD} ahead, fetch fails): packs NOT compared, no card filed"
      log "$REASON"
      echo "$REASON"
      exit 0
    fi
  fi
fi

drift=()

# profile files (whole-file sync targets). config.yaml occupancy/Turbo pins
# (krew-mode, TurboKrew) are PIN, not DRIFT — clobbering them puts Vera/
# Steward back on crew during Turbo or Ada's decomposer onto ops.
CLASSIFY="$REPO/scripts/pack_drift.py"
if [ "${KREW_KIT_HEAL:-1}" != 0 ] && [ -x "$REPO/scripts/krew-kit-reapply.sh" ]; then
  KREW_KIT_REASON=nightly-heal bash "$REPO/scripts/krew-kit-reapply.sh" >/dev/null 2>&1 \
    && log "kit heal ran before the check" || log "kit heal returned non-zero (see krew-kit-reapply.log)"
fi
for d in "$PACKS"/distributions/*/; do
  p=$(basename "$d")
  for f in SOUL.md config.yaml profile.yaml distribution.yaml; do
    [ -f "$d$f" ] || continue
    if [ ! -f "$LIVE/$p/$f" ]; then
      drift+=("MISSING-LIVE $p/$f")
    elif [ "$f" = "config.yaml" ] && [ -f "$CLASSIFY" ]; then
      kind=$(python3 "$CLASSIFY" "$d$f" "$LIVE/$p/$f" 2>/dev/null || echo drift)
      case "$kind" in
        match) ;;
        pin) log "PIN $p/$f (occupancy/turbo; not a clobber)" ;;
        *) drift+=("DRIFT $p/$f") ;;
      esac
    elif ! diff -q "$d$f" "$LIVE/$p/$f" >/dev/null 2>&1; then
      drift+=("DRIFT $p/$f")
    fi
  done
done

# plugins
for d in "$PACKS"/plugins/*/; do
  n=$(basename "$d")
  for f in "$d"*.py "$d"plugin.yaml; do
    [ -f "$f" ] || continue
    b=${f#"$d"}
    lv="$HOME/.hermes/plugins/$n/$b"
    if [ ! -f "$lv" ]; then
      drift+=("MISSING-LIVE plugin $n/$b")
    elif ! diff -q "$f" "$lv" >/dev/null 2>&1; then
      drift+=("DRIFT plugin $n/$b")
    fi
  done
done

# skills — sync-packs already knows how to compare these
while read -r line; do
  [ -n "$line" ] && drift+=("$line")
done < <("$REPO/scripts/sync-packs.sh" skills 2>/dev/null | grep '^DRIFT' || true)

if [ ${#drift[@]} -eq 0 ]; then
  log "clean — packs match live"
  echo "packs match live"
  exit 0
fi

printf 'pack/live drift (%d):\n' "${#drift[@]}"
for d in "${drift[@]}"; do echo "  $d"; log "$d"; done

# Surface it where work surfaces. Replace the previous card so this never
# becomes a nightly pile.
PREV=$($H kanban list 2>/dev/null | grep 'pack drift:' | head -1 | awk '{print $2}')
[ -n "$PREV" ] && $H kanban archive "$PREV" >/dev/null 2>&1

BODY="These paths are committed in krew but do not match what is running.
A commit is not a deploy — someone has to reconcile them.

$(printf '%s\n' "${drift[@]}")

Skills/SOUL/plugins: copy pack -> live, then restart that profile's gateway.
config.yaml: decide deliberately — deploy is a whole-file clobber; restore is
the deep merge. Do not blind-deploy a config. Occupancy/Turbo pins
(model.base_url, Vera/Steward ops during TurboKrew, Render default_assignee)
are live krew-mode — never clobber those back to pack crew. Ada
kanban_decomposer.api_key (a local placeholder) and skills.disabled
kanban-orchestration-patterns are pack toolkit pins — keep them."

# 2026-09-18 (the User: "They never get their updates and so correct our corrections. Stop them"):
# a drift card sent the crew to copy pack -> live, which reverted hand edits made on the live
# side (the Profiles page, a SOUL fix). Drift is now a REPORT — this log and the House Daily
# row — and the move (capture live -> pack, or deploy pack -> live) is a human's or a
# session's, made with both copies in view. KREW_PACK_DRIFT_CARDS=1 files the card again.
if [ "${KREW_PACK_DRIFT_CARDS:-0}" = "1" ]; then
  $H kanban create "pack drift: ${#drift[@]} path(s) committed but not live" \
    --body "$BODY" --assignee ada >/dev/null 2>&1 \
    && log "filed drift card for ada"
else
  log "report-only: ${#drift[@]} path(s) differ; no card (KREW_PACK_DRIFT_CARDS=1 to file)"
fi
exit 0
