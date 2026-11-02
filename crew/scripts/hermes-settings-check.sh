#!/usr/bin/env bash
# Detect drift between the LIVE hermes settings and the copies committed here.
#
#
# after their updates. How can we protect?"):
#   `hermes update` hard-resets ~/.hermes/hermes-agent — the CODE checkout. Our
#
#   tier0.yaml, profiles/*/config.yaml, profiles/*/SOUL.md, ~/.hermes/config.yaml
#   and the systemd drop-ins are all outside that repo.
#
#   The real exposure is CONFIG MIGRATION. ~/.hermes/config.yaml carries
#
#   migration that rewrites config files — the same files that hold toolsets and
#   the tier0 pre_tool_call hooks. A migration could silently drop a capability
#   grant or a gate.
#
# So: snapshot + verify. Run this after every `hermes update`.
#
#   scripts/hermes-settings-check.sh            # report drift
#   scripts/hermes-settings-check.sh --snapshot # re-baseline after an intended change
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/../lib/krew_hermes.sh"   # $KREW_HERMES_HOME: where Hermes lives
HH="${HERMES_HOME:-$KREW_HERMES_HOME}"
SNAP="${KREW_SETTINGS_SNAPSHOT:-${KREW_HOME:-$HOME/.krew}/settings-snapshot}"   # tier0.yaml, ada-SOUL.md, profiles/
export PYTHONPATH="$(cd "$(dirname "$0")/../lib" && pwd)${PYTHONPATH:+:$PYTHONPATH}"   # krew_yaml
LIVE_T0="$HH/security/tier0.yaml"
LIVE_SOUL="$HH/profiles/ada/SOUL.md"
# Profiles come from the packs, not a list. A handwritten list kept naming
#
PROFILES=()
for d in "${KREW_PACKS_ROOT:-$HOME/.krew/packs}"/distributions/*/; do
  [ -d "$d" ] || continue
  PROFILES+=("$(basename "$d")")
done
# no packs checkout: every profile in the Hermes home
if [ ${#PROFILES[@]} -eq 0 ]; then
  for d in "$HH"/profiles/*/; do [ -d "$d" ] && PROFILES+=("$(basename "$d")"); done
fi
rc=0

snapshot() {
  mkdir -p "$SNAP/profiles"
  cp "$LIVE_T0" "$SNAP/tier0.yaml"
  cp "$LIVE_SOUL" "$SNAP/ada-SOUL.md"
  python3 - "$SNAP" "${PROFILES[@]}" <<'PY'
import os, sys, json, pathlib
import krew_yaml as yaml
HH = pathlib.Path(os.environ.get('HERMES_HOME') or os.environ['KREW_HERMES_HOME'])
snap = pathlib.Path(sys.argv[1]); out = {}
for p in sys.argv[2:]:
    f = HH/"profiles"/p/"config.yaml"
    if not f.is_file(): continue
    d = yaml.safe_load(f.read_text()) or {}
    hooks = (d.get("hooks") or {}).get("pre_tool_call") or []
    out[p] = {"toolsets": d.get("toolsets") or [],
              "platform_toolsets": dict(d.get("platform_toolsets") or {}),
              "pre_tool_call": [{"matcher": h.get("matcher"), "command": h.get("command")} for h in hooks],
              "dispatch_in_gateway": (d.get("kanban") or {}).get("dispatch_in_gateway")}
(snap/"profiles/capability-snapshot.json").write_text(json.dumps(out, indent=2, sort_keys=True)+"\n")
print(f"snapshotted {len(out)} profiles")
PY
  echo "baseline updated in $SNAP"
}

if [ "${1:-}" = "--snapshot" ]; then snapshot; exit 0; fi

echo "== hermes settings drift =="

diff -q "$SNAP/tier0.yaml" "$LIVE_T0" >/dev/null 2>&1 \
  && echo "  ok    tier0.yaml" \
  || { echo "  DRIFT tier0.yaml — the gate policy changed"; diff "$SNAP/tier0.yaml" "$LIVE_T0" | head -20; rc=1; }

diff -q "$SNAP/ada-SOUL.md" "$LIVE_SOUL" >/dev/null 2>&1 \
  && echo "  ok    ada SOUL.md" \
  || { echo "  DRIFT ada SOUL.md — coaching changed"; rc=1; }

python3 - "$SNAP" "${PROFILES[@]}" <<'PY' || rc=1
import os, sys, json, pathlib
import krew_yaml as yaml
HH = pathlib.Path(os.environ.get('HERMES_HOME') or os.environ['KREW_HERMES_HOME'])
snap = pathlib.Path(sys.argv[1]); base_f = snap/"profiles/capability-snapshot.json"
if not base_f.is_file():
    print("  DRIFT no capability baseline — run --snapshot"); sys.exit(1)
base = json.loads(base_f.read_text()); bad = 0
for p in sys.argv[2:]:
    f = HH/"profiles"/p/"config.yaml"
    if not f.is_file():
        if p in base: print(f"  DRIFT {p}: config.yaml MISSING"); bad = 1
        continue
    d = yaml.safe_load(f.read_text()) or {}
    hooks = (d.get("hooks") or {}).get("pre_tool_call") or []
    live = {"toolsets": d.get("toolsets") or [],
            "platform_toolsets": dict(d.get("platform_toolsets") or {}),
            "pre_tool_call": [{"matcher": h.get("matcher"), "command": h.get("command")} for h in hooks],
            "dispatch_in_gateway": (d.get("kanban") or {}).get("dispatch_in_gateway")}
    want = base.get(p)
    if want is None:
        print(f"  NEW   {p}: not in baseline"); bad = 1; continue
    if live == want:
        print(f"  ok    {p}")
        continue
    bad = 1
    print(f"  DRIFT {p}:")
    if live["toolsets"] != want["toolsets"]:
        print(f"          toolsets {want['toolsets']} -> {live['toolsets']}")
    if not live["pre_tool_call"]:
        print("          *** pre_tool_call GATE REMOVED ***")
    elif live["pre_tool_call"] != want["pre_tool_call"]:
        print("          pre_tool_call hook changed")
    if live["dispatch_in_gateway"] != want["dispatch_in_gateway"]:
        print(f"          dispatch_in_gateway {want['dispatch_in_gateway']} -> {live['dispatch_in_gateway']}")
sys.exit(1 if bad else 0)
PY

# enforce must survive: it lives in a systemd drop-in, outside HERMES_HOME.
# Every INSTALLED gateway unit is checked — derived, never a list: the list
#
UNITS="${HERMES_GATEWAY_UNIT_DIR:-$HOME/.config/systemd/user}"
n_gw=0
# `hermes-*gateway` on purpose: it matches the per-profile units AND the one host gateway
# (hermes-gateway.service, KREW_GATEWAY_TOPOLOGY=host). A masked unit is a symlink to /dev/null
# and is skipped: after the cutover the four old units are masked, not deleted.
for f in "$UNITS"/hermes-*gateway.service; do
  [ -f "$f" ] || continue
  [ "$(readlink -f "$f")" = /dev/null ] && continue
  g="$(basename "$f" .service)"; n_gw=$((n_gw + 1))
  [ -f "$UNITS/$g.service.d/tier0.conf" ] || { echo "  DRIFT $g: tier0.conf drop-in MISSING (enforce lost)"; rc=1; }
done
[ "$n_gw" -gt 0 ] || { echo "  DRIFT no hermes-*-gateway.service (nor hermes-gateway.service) under $UNITS — nothing to check"; rc=1; }
# the unit that spawns the workers: Ada's, or the one host gateway (lib/hermes_topology)
topo="$(dirname "$(readlink -f "$0")")/../lib/hermes_topology.py"
if [ -f "$topo" ]; then
  disp="$(python3 "$topo" unit ada 2>/dev/null || true)"
else
  disp=""; echo "  note  lib/hermes_topology.py is missing: the dispatcher unit is taken as hermes-ada-gateway.service"
fi
mode="$(systemctl --user show "${disp:-hermes-ada-gateway.service}" -p Environment --value 2>/dev/null | tr ' ' '\n' | grep KREW_T0_MODE || true)"
[ "$mode" = "KREW_T0_MODE=enforce" ] \
  && echo "  ok    tier0 mode: enforce" \
  || { echo "  DRIFT tier0 mode is '${mode:-unset}' — expected enforce"; rc=1; }

[ $rc -eq 0 ] && echo "== no drift ==" || echo "== DRIFT FOUND — review before trusting the gates =="
exit $rc
