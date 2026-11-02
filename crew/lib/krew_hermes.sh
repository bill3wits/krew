# krew_hermes.sh: krew_hermes.py's rule for shell scripts (the same order, kept equal by tests/test_hermes_home.py).
# Source it, then use "$KREW_HERMES_HOME" (exported, so a python heredoc reads the same root):
#   . "$(dirname "$(readlink -f "$0")")/../lib/krew_hermes.sh"
# KREW_HERMES_ROOT, else HERMES_REAL_HOME or KREW_REAL_HOME + /.hermes, else $HERMES_HOME (a profile's
# own <root>/profiles/<name> reads <root>), else ~/.hermes (a profile's own HOME, <root>/profiles/<name>/home, reads
# <root>). The owner 2026-11-01: a krew beside another Hermes (HERMES_HOME=~/.hermes-krew) read the other one's board.
krew_hermes_home() {
  if [ -n "${KREW_HERMES_ROOT:-}" ]; then printf '%s\n' "$KREW_HERMES_ROOT"; return; fi
  if [ -n "${HERMES_REAL_HOME:-}" ]; then printf '%s/.hermes\n' "$HERMES_REAL_HOME"; return; fi
  if [ -n "${KREW_REAL_HOME:-}" ]; then printf '%s/.hermes\n' "$KREW_REAL_HOME"; return; fi
  if [ -n "${HERMES_HOME:-}" ]; then
    if [ "$(basename "$(dirname "$HERMES_HOME")")" = profiles ]; then dirname "$(dirname "$HERMES_HOME")"
    else printf '%s\n' "$HERMES_HOME"; fi
    return
  fi
  case "$HOME" in
    */profiles/*/home) dirname "$(dirname "$(dirname "$HOME")")" ;;
    *) printf '%s/.hermes\n' "$HOME" ;;
  esac
}
KREW_HERMES_HOME="$(krew_hermes_home)"
export KREW_HERMES_HOME
