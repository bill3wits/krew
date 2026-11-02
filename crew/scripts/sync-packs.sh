#!/usr/bin/env bash
#
#   hermes-packs/distributions/<profile>/{SOUL.md,config.yaml,distribution.yaml}
# is the truth; ~/.hermes/profiles/<profile>/ is an installation of it.
#
# § Tools for any agent. Edit the pack, then this script. Not live YAML.
#
#   sync-packs.sh check    → report drift, exit 1 if any (use in a nightly/pre-update check)
#   sync-packs.sh restore  → YAML deep-merge packs OVER live (dry-run).
#                            --apply to write. SOUL.md is NOT in this tool —
#                            markdown is a per-file cp. Lists REPLACE (toolsets).
#
#   sync-packs.sh deploy   → packs → live, WHOLE FILE. Discards live-only keys.
#                            Prefer restore. Do not deploy distributions while
#                            BRIDGE live (~/.hermes/config.yaml) is longer than
#
#   sync-packs.sh capture  → live → packs (ONLY after deliberate live tuning; then commit)
#   sync-packs.sh skills   → check krew skills.
#   sync-packs.sh skills deploy | deploy-skills
#                            copy SKILL.md pack → live (skills tree only).
#   sync-packs.sh skills capture | capture-skills
#
# Why restore exists: `deploy` and `capture` each lose one side. deploy keeps the
# krew and throws away every new key an update introduced; capture keeps the
# update and enshrines whatever just clobbered us. restore is the merge — see
# scripts/krew-config-restore.py.
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/../lib/krew_hermes.sh"   # $KREW_HERMES_HOME: where Hermes lives
DIST="${KREW_PACKS_ROOT:-$HOME/.krew/packs}/distributions"
LIVE="$KREW_HERMES_HOME/profiles"
# Plugins are packs too (PLUG-4): hermes-packs/plugins/<plugin>/ was a dead second
# copy nobody deployed from. It is now authoritative on the same check/deploy/capture
# contract as distributions/, so plugin edits can't silently diverge from ~/.hermes.
PLUG_DIST="${KREW_PACKS_ROOT:-$HOME/.krew/packs}/plugins"
PLUG_LIVE="$KREW_HERMES_HOME/plugins"
#
# diffed, and BRIDGE-profile.yaml had already drifted invisibly.
FILES=(SOUL.md config.yaml profile.yaml distribution.yaml hindsight/config.json)
MODE="${1:-check}"
# Two-arg form: `skills deploy` used to be a silent check ($1=skills, $2 ignored).
if [ "$MODE" = skills ]; then
  case "${2:-check}" in
    deploy) MODE=deploy-skills ;;
    capture) MODE=capture-skills ;;
    *) MODE=check-skills ;;
  esac
fi
drift=0
SKILLS_ONLY=0
case "$MODE" in
  skills|check-skills|deploy-skills|capture-skills) SKILLS_ONLY=1 ;;
esac

# restore is a merge, not a copy — hand it to the tool that knows how to merge.
# Everything below this point is whole-file copy logic and cannot express
# "keep the key upstream just added".
if [ "$MODE" = restore ]; then
  PY="${DISPLAY_PY:-$KREW_HERMES_HOME/hermes-agent/venv/bin/python}"
  [ -x "$PY" ] || PY=python3
  shift || true
  exec "$PY" "${KREW_CREW_ROOT:-$HOME/.krew/crew}/scripts/krew-config-restore.py" "$@"
fi

# hermes-achievements is upstream Hermes; unmanaged on purpose.
UNMANAGED_PLUGINS="hermes-achievements"

sync_one() {  # sync_one <label> <pack-file> <live-file>
  local n="$1" p="$2" l="$3"
  if [ ! -f "$p" ]; then
    # live-only file: pack is the deployment target, so this is drift too
    [ -f "$l" ] || return 0
    echo "MISSING pack  $n"
    drift=1
    case "$MODE" in
      capture) mkdir -p "$(dirname "$p")"; cp "$l" "$p"; echo "   → captured live to pack" ;;
    esac
    return 0
  fi
  if [ ! -f "$l" ]; then
    echo "MISSING live  $n"
    drift=1
    case "$MODE" in
      deploy|deploy-skills) mkdir -p "$(dirname "$l")"; cp "$p" "$l"; echo "   → deployed pack to live" ;;
    esac
    return 0
  fi
  if ! diff -q "$p" "$l" >/dev/null 2>&1; then
    echo "DRIFT  $n  (pack $(wc -l <"$p") lines vs live $(wc -l <"$l"))"
    drift=1
    case "$MODE" in
      deploy|deploy-skills)  cp "$p" "$l"; echo "   → deployed pack to live" ;;
      capture|capture-skills) cp "$l" "$p"; echo "   → captured live to pack" ;;
    esac
  fi
}

if [ "$SKILLS_ONLY" = 0 ]; then
for d in "$DIST"/*/; do
  n=$(basename "$d")
  for f in "${FILES[@]}"; do
    sync_one "$n/$f" "$d$f" "$LIVE/$n/$f"
  done
done

#
# tirith fail-closed posture, the T0 pre_tool_call hook, and the kanban
# orchestrator_profile/default_assignee the dashboard resolves from. It was
# covered by NOTHING until now, so when the kanban dashboard rewrote it via
# POST /orchestration (which strips every comment on the YAML round-trip),
# `check` still reported "no drift" and the loss was invisible.
sync_one "BRIDGE-config.yaml" "$DIST/BRIDGE-config.yaml" "$KREW_HERMES_HOME/config.yaml"
sync_one "BRIDGE-profile.yaml" "$DIST/BRIDGE-profile.yaml" "$KREW_HERMES_HOME/profile.yaml"
sync_one "BRIDGE-SOUL.md" "$DIST/BRIDGE-SOUL.md" "$KREW_HERMES_HOME/SOUL.md"
sync_one "BRIDGE-hindsight.json" "$DIST/BRIDGE-hindsight.json" "$KREW_HERMES_HOME/hindsight/config.json"

# Live-only plugin dirs (e.g. hermes-achievements once): invisible to the loop
# below because it iterates pack-side dirs only. Report them as drift.
if [ -d "$PLUG_LIVE" ]; then
  for d in "$PLUG_LIVE"/*/; do
    n=$(basename "$d")
    [ -d "$PLUG_DIST/$n" ] || {
      echo "LIVE-ONLY plugin  $n (not in packs)"
      case " $UNMANAGED_PLUGINS " in
        *" $n "*) echo "   (unmanaged, expected)" ;;
        *) drift=1 ;;
      esac
    }
  done
fi

# Plugins: every tracked file under the pack plugin dir (skip caches).
if [ -d "$PLUG_DIST" ]; then
  for d in "$PLUG_DIST"/*/; do
    n=$(basename "$d")
    # union of pack-side and live-side files so new live files are caught too
    while IFS= read -r rel; do
      [ -n "$rel" ] || continue
      sync_one "plugins/$n/$rel" "$d$rel" "$PLUG_LIVE/$n/$rel"
    done < <(
      { [ -d "$d" ] && find "$d" -type f -not -path '*/__pycache__/*' -printf '%P\n'
        [ -d "$PLUG_LIVE/$n" ] && find "$PLUG_LIVE/$n" -type f -not -path '*/__pycache__/*' -printf '%P\n'
      } 2>/dev/null | sort -u
    )
  done
fi
fi

# Krew-owned skills. Pack tree: hermes-packs/skills/<name>/SKILL.md
# Live dest: ~/.hermes/profiles/<profile>/skills/<subdir>/<name>/SKILL.md
# Wrong-depth installs (profile/skills/<name> instead of skills/house/<name>)
# are drift; deploy writes the house/ path and removes the misplaced copy.
SKILLS_ROOT="${KREW_PACKS_ROOT:-$HOME/.krew/packs}/skills"

#
# Canonical (<profile>/skills/<sub>/<name>) first; if absent, the skill's
# <name>/SKILL.md is located anywhere under the profile's skills/ tree
# (alternate depth) and synced against that. MISSING live fires only when the
# skill is genuinely not installed in that profile.
live_skill_path() {  # name profile subdir
  local name="$1" prof="$2" sub="$3"
  local l="$LIVE/$prof/skills/$sub/$name/SKILL.md"
  if [ -f "$l" ]; then
    printf '%s' "$l"
    return 0
  fi
  local hit
  while IFS= read -r hit; do
    [ -n "$hit" ] || continue
    [ "$hit" = "$LIVE/$prof/skills/$name/SKILL.md" ] && continue
    printf '%s' "$hit"
    return 0
  done < <(find "$LIVE/$prof/skills" -path "*/$name/SKILL.md" -type f 2>/dev/null)
  return 1
}

sync_skill() {  # name profile subdir
  local name="$1" prof="$2" sub="$3"
  local p="$SKILLS_ROOT/$name/SKILL.md"
  local l wrong
  l=$(live_skill_path "$name" "$prof" "$sub") || l=""
  wrong="$LIVE/$prof/skills/$name/SKILL.md"
  if [ ! -f "$p" ]; then
    echo "MISSING pack skill  $name"
    drift=1
    return 0
  fi
  if [ -n "$l" ] && [ -f "$wrong" ] && [ "$wrong" != "$l" ]; then
    echo "WRONG-DEPTH skill  $prof/skills/$name  (want skills/$sub/$name)"
    drift=1
    case "$MODE" in
      deploy|deploy-skills)
        mkdir -p "$(dirname "$l")"
        cp "$p" "$l"
        rm -f "$wrong"
        rmdir "$(dirname "$wrong")" 2>/dev/null || true
        echo "   → deployed $name to $prof/skills/$sub/ and removed misplaced copy"
        ;;
    esac
  fi
  if [ -n "$l" ]; then
    sync_one "skills/$prof/$sub/$name" "$p" "$l"
  else
    # genuinely absent: sync against the canonical path so the MISSING-live /
    #
    sync_one "skills/$prof/$sub/$name" "$p" "$LIVE/$prof/skills/$sub/$name/SKILL.md"
  fi
}

run_skills() {
  sync_skill ada-orchestrate ada house
  sync_skill ada-ops-efficiency ada house
  sync_skill krew-deliverable ada house
  sync_skill krew-deliverable bash house
  sync_skill krew-deliverable lila house
  sync_skill krew-deliverable steward house
  sync_skill krew-deliverable vera house
  sync_skill krew-deliverable crissa house
  sync_skill crissa-her-wall crissa house
  sync_skill crissa-map crissa house
  sync_skill crissa-list crissa house
  sync_skill ada-map ada house
  sync_skill bash-map bash house
  sync_skill lila-map lila house
  sync_skill steward-map steward house
  sync_skill vera-map vera house
  sync_skill scout-map scout house
  sync_skill crissa-auth crissa house
  sync_skill crissa-needs crissa house
  sync_skill crissa-surface-art crissa house
  sync_skill crissa-big-push crissa house
  sync_skill crissa-render-cycle crissa house
  sync_skill crissa-cua crissa house
  sync_skill crissa-report crissa house
  sync_skill crissa-morning crissa house
  sync_skill composer-coach crissa krew   # Crissa coaches the Composer on ? (2026-09-18)
  sync_skill krew-judge judge krew    # the Judge protocol; ada carries it so the loop/gate can pin it (2026-09-18)
  sync_skill krew-judge ada house
  sync_skill steward-sweep steward house
  sync_skill steward-logs steward house
  sync_skill steward-spike steward house
  sync_skill scout-architect-world scout house
  sync_skill scout-architect-stack scout house
  sync_skill scout-team-research scout house
  sync_skill scout-team-sources scout house
  sync_skill scout-one-off scout house
  sync_skill ops-toks bash house
  sync_skill krew-render lila house
  sync_skill krew-render bash house
  # Renderers and reviewers only (README + Crissa SOUL: generate is not on her
  # belt). Crissa was trimmed — do not put the recipe table back on her index.
  sync_skill krew-render vera house
  sync_skill pose-flourish lila house
  sync_skill pose-flourish bash house
  sync_skill pose-flourish vera house
  sync_skill krew-vlm lila house
  sync_skill krew-vlm bash house
  sync_skill krew-vlm vera house
  sync_skill viseme-bank-map lila house
  sync_skill viseme-bank-map bash house
  sync_skill mouth-place-and-shape lila house
  sync_skill mouth-place-and-shape bash house
  sync_skill mouth-place-and-shape vera house
  sync_skill render-lane lila house
  sync_skill render-lane vera house
  sync_skill render-lane bash house
  sync_skill render-lane ada house
  #
  sync_skill adversarial-gate lila software-development
  sync_skill adversarial-gate bash software-development
  sync_skill adversarial-gate steward software-development
  # The Control Panel as tools. Everyone who works a card can read the house.
  # Not scout: no `team` toolset, so the tools would not resolve.
  sync_skill krew-panel ada house
  sync_skill krew-panel bash house
  sync_skill krew-panel lila house
  sync_skill krew-panel steward house
  sync_skill krew-panel vera house
  sync_skill krew-panel crissa house
  # CPU renderers — Lila (and her catalog). Crissa is trimmed: wall art is
  # crissa-surface-art only. Do not reinstall glyph-density / picture-to-glyph /
  # lila-glyph-catalog onto her; that would put Comfy-adjacent recipe text
  # back on the voice index. krew-render stays on lila/bash/vera (Composer).
  sync_skill glyph-density lila house
  sync_skill lila-glyph-catalog lila house
  sync_skill text-to-ascii lila house
  sync_skill picture-to-glyph lila house
  sync_skill crissa-surface-art lila house
}

case "$MODE" in
  skills|check-skills)
    MODE=check
    run_skills
    if [ "$drift" = 0 ]; then echo "skills == live (no drift)"; fi
    exit "$drift"
    ;;
  deploy-skills)
    MODE=deploy
    run_skills
    exit 0
    ;;
  capture-skills)
    MODE=capture
    run_skills
    exit 0
    ;;
esac

# Skills also run on check + deploy so nightly cannot miss a misplaced
# crissa-big-push. Capture of profiles must not overwrite repo-ahead SKILL.md.
if [ "$MODE" != "capture" ]; then
  run_skills
fi

if [ "$MODE" = deploy ] && [ "$drift" = 1 ]; then
  # lib/hermes_topology: her unit today, the profile inside the ONE host gateway after the cutover.
  topo="$(dirname "$(readlink -f "$0")")/../lib/hermes_topology.py"   # krew's own crew/lib copy, beside this script
  if [ ! -f "$topo" ]; then
    echo "missing: $topo — the crissa/ada gateways were not restarted after the deploy; restart them by hand"
  else
  for p in crissa ada; do
    python3 "$topo" up "$p" && python3 "$topo" restart "$p" && echo "restarted $p gateway"
  done
  fi
  # keep ada's read-only self-inspection mount in step (stale copies told the
  #
  "${KREW_CREW_ROOT:-$HOME/.krew/crew}/scripts/refresh-coder-mounts.sh" && echo "profiles-ro refreshed"
fi

if [ "$drift" = 0 ]; then echo "packs == live (no drift)"; fi
[ "$MODE" = check ] && exit "$drift"
exit 0
