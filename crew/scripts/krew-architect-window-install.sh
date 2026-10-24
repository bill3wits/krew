#!/bin/bash
# krew-architect-window-install — move the architect notes (logs / world / stack) off the wall
# clock and into the User's off-hours (the User 2026-09-17). Run by the User: it needs root for the
# polkit rule, the system units and their timers. Idempotent. collect stays on its 06:50 timer
# until the NNT feed probes (~/.hermes/logs/nnt-feed-probe.log) say the feed answers at night.
# Undo: sudo systemctl enable --now krew-architect-{logs,world,stack}.timer
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
U="$HOME/.config/systemd/user"
echo "1/4 polkit: the house user may START the schedule's system units (start only)"
sudo install -m 0644 "$R/config/polkit/50-krew-window-system-jobs.rules" /etc/polkit-1/rules.d/
echo "2/4 units carry the day label (EnvironmentFile=-$KREW_HOME/krew-day.env)"
for j in logs world stack collect; do
  sudo install -m 0644 "$R/config/systemd/krew-architect-$j.service" /etc/systemd/system/
done
sudo systemctl daemon-reload
echo "3/4 the ticker owns logs/world/stack once their calendar timers are off"
sudo systemctl disable --now krew-architect-logs.timer krew-architect-world.timer krew-architect-stack.timer
echo "4/4 the render slot: its own 2-min ticker, independent of the architect collect jobs"
# The render-window tick must NOT hang off krew-window-tick (it carries pose-freelance +
# improvement-outcomes, and the slot opens once the evening chain has a verdict): it has its
# own user timer, and render-window.py refuses to open the slot before that verdict anyway.
# The House Daily's watch entry (config/krew-window-schedule.json) reads krew-render-window.service.
install -m 0644 "$R"/config/systemd/user/krew-render-window.{service,timer} "$U/"
systemctl --user daemon-reload || true
systemctl --user enable --now krew-render-window.timer || echo "WARN: user systemd not up yet; run 'systemctl --user enable --now krew-render-window.timer' after login"
python3 "$R/scripts/krew-window-tick.py" --facts | head -12
python3 "$R/scripts/render-window.py" facts | head -12
systemctl list-timers --all --no-pager | grep -E "krew-architect|team-research" || true
systemctl --user list-timers --all --no-pager | grep "krew-render-window" || true
