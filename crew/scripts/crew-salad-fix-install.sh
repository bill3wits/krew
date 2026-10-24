#!/usr/bin/env bash
# Crew salad fix (the User 2026-09-18 "go"): prefix caching OFF + MTP OFF on crew :8005.
# Installs the two drop-ins from the repo into the live unit dir and reloads systemd.
# The restart is separate and yours — run it in a gap (in-flight worker calls die):
#   systemctl --user restart crew.service
# Undo: rm ~/.config/systemd/user/crew.service.d/{30-prefix-cache-off,40-mtp-off}.conf
#       && systemctl --user daemon-reload && systemctl --user restart crew.service
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)/engines/crew.service.d"   # krew: the drop-ins ship beside the engine units
D="$HOME/.config/systemd/user/crew.service.d"
mkdir -p "$D"
echo "1/3 drop-ins → $D"
install -m 644 "$R/30-prefix-cache-off.conf" "$D/30-prefix-cache-off.conf"
install -m 644 "$R/40-mtp-off.conf" "$D/40-mtp-off.conf"
rm -f "$D/30-prefix-cache-off.conf.disabled"
echo "2/3 daemon-reload"
systemctl --user daemon-reload
echo "3/3 crew will start with:"
systemctl --user show crew.service -p Environment --value | tr ' ' '\n' | grep -E '^CREW_' || true
echo
echo "Now, in a gap (0 running on the board):  systemctl --user restart crew.service"
echo "Verify: journalctl --user -u crew.service -o cat | grep -E 'prefix|speculative' | tail -3"
