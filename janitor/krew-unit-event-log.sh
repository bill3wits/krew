#!/usr/bin/env bash
# Durable unit start/stop/restart audit trail (krew Janitor; systemd/user/krew-unit-events.conf.example).
# Invoked from systemd ExecStartPre / ExecStopPost drop-ins (best-effort).
# Does not block unit start (always exit 0).
set -u
PHASE="${1:-unknown}"   # start-pre | stop-post | restart | manual
UNIT="${2:-unknown}"
RESULT="${3:-}"
EXIT_CODE="${4:-}"
LOG_DIR="${KREW_UNIT_EVENT_LOG_DIR:-${KREW_HOME:-$HOME/.krew}/unit-events}"
LOG_FILE="${LOG_DIR}/unit-events.jsonl"
mkdir -p "$LOG_DIR" 2>/dev/null || true

# Caller context (best effort — systemd often has empty PPID chain)
PPID_CMD=""
if [[ -r "/proc/${PPID:-0}/cmdline" ]]; then
  PPID_CMD="$(tr '\0' ' ' <"/proc/${PPID}/cmdline" 2>/dev/null | head -c 240 || true)"
fi
# Walk a few parents for human-visible "who"
TREE=""
p="${PPID:-1}"
for _ in 1 2 3 4 5; do
  [[ -r "/proc/$p/cmdline" ]] || break
  c="$(tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null | head -c 120 || true)"
  TREE="${TREE}${TREE:+ | }pid=$p cmd=${c:-?}"
  p="$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null || echo 0)"
  [[ "$p" -gt 1 ]] 2>/dev/null || break
done

TS="$(date -Iseconds 2>/dev/null || date)"
# JSON-ish single line without requiring python
# Escape quotes in free text
esc() { printf '%s' "${1:-}" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
line="{\"ts\":\"$TS\",\"phase\":\"$(esc "$PHASE")\",\"unit\":\"$(esc "$UNIT")\",\"service_result\":\"$(esc "$RESULT")\",\"exit_code\":\"$(esc "$EXIT_CODE")\",\"uid\":$(id -u 2>/dev/null || echo 0),\"pid\":$$,\"ppid\":${PPID:-0},\"ppid_cmd\":\"$(esc "$PPID_CMD")\",\"proc_tree\":\"$(esc "$TREE")\",\"host\":\"$(hostname 2>/dev/null || echo unknown)\"}"
printf '%s\n' "$line" >>"$LOG_FILE" 2>/dev/null || true
# Mirror short line for journal
echo "[crew-unit-event] $PHASE unit=$UNIT result=$RESULT exit=$EXIT_CODE tree=$TREE" >&2 || true
exit 0
