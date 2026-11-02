#!/usr/bin/env bash
# kanban-lean.sh on|off|status — the workers' kanban kit, lean or fat, with
#
#
#   on   : bash/lila/steward/vera get toolset `kanban_lean` in place of `kanban`
#          (every toolsets list, pack AND live), the krew-kanban-lean plugin is
#          enabled and linked into each worker profile, and copied live.
#   off  : the exact reverse — `kanban` back, plugin disabled. The fat built-in
#          set is what Hermes ships; nothing to reinstall.
#   status: what each worker carries now, pack and live.
#
# Workers are kanban-dispatched processes: they read config at spawn, so no
# gateway restart is needed. Ada and Crissa are not touched. After on/off run
# `hermes -p bash prompt-size` and `scripts/krew-pin-check.py` (pack == live).
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/../lib/krew_hermes.sh"   # $KREW_HERMES_HOME: where Hermes lives
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-status}"
PLUG=krew-kanban-lean
WORKERS="bash lila steward vera"
case "$MODE" in
  on|off|status) ;;
  *) echo "usage: $0 on|off|status" >&2; exit 2 ;;
esac
# Hermes discovers plugins under HERMES_HOME/plugins — the PROFILE dir, not
# ~/.hermes/plugins (that is why every worker profile carries symlinks for
# team-crissa). The shared copy lives in ~/.hermes/plugins;
# each worker gets a symlink to it. Missing that link = plugin silently absent
#
# fell to 23 tools with no kanban verbs at all). off removes the links.
if [ "$MODE" = on ]; then
  mkdir -p "$KREW_HERMES_HOME/plugins/$PLUG"
  cp "$ROOT/hermes-packs/plugins/$PLUG/plugin.yaml" "$ROOT/hermes-packs/plugins/$PLUG/__init__.py" "$KREW_HERMES_HOME/plugins/$PLUG/"
  for w in $WORKERS; do
    mkdir -p "$KREW_HERMES_HOME/profiles/$w/plugins"
    ln -sfn "$KREW_HERMES_HOME/plugins/$PLUG" "$KREW_HERMES_HOME/profiles/$w/plugins/$PLUG"
  done
fi
if [ "$MODE" = off ]; then
  for w in $WORKERS; do rm -f "$KREW_HERMES_HOME/profiles/$w/plugins/$PLUG"; done
fi
/usr/bin/python3 - "$MODE" "$ROOT" $WORKERS <<'PY'
import os, re, sys, yaml
from pathlib import Path
mode, root, *workers = sys.argv[1:]
PLUG = "krew-kanban-lean"
for w in workers:
    for cfg in (Path(root) / "hermes-packs/distributions" / w / "config.yaml",
                Path(os.environ["KREW_HERMES_HOME"]) / "profiles" / w / "config.yaml"):
        s = cfg.read_text(encoding="utf-8")
        d = yaml.safe_load(s) or {}
        has_lean = "kanban_lean" in (d.get("toolsets") or [])
        en = list(((d.get("plugins") or {}).get("enabled")) or [])
        if mode == "status":
            link = (Path(os.environ["KREW_HERMES_HOME"]) / "profiles" / w / "plugins" / PLUG).is_symlink()
            print(f"{w:8} {'pack' if 'hermes-packs' in str(cfg) else 'live':4} toolsets={'kanban_lean' if has_lean else ('kanban' if 'kanban' in (d.get('toolsets') or []) else 'NONE')} plugin={'on' if PLUG in en else 'off'} link={'yes' if link else 'no'}")
            continue
        want_lean = mode == "on"
        # toolsets lists: text edit so comments and order survive
        if want_lean:
            s = re.sub(r"^(\s*)- kanban\n", r"\1- kanban_lean\n", s, flags=re.M)
        else:
            s = re.sub(r"^(\s*)- kanban_lean\n", r"\1- kanban\n", s, flags=re.M)
        # plugins.enabled + entries: yaml-level edit of just that block
        d = yaml.safe_load(s) or {}
        pl = d.setdefault("plugins", {}) or {}
        en = list(pl.get("enabled") or [])
        entries = dict(pl.get("entries") or {})
        if want_lean:
            if PLUG not in en:
                en.append(PLUG)
        else:
            en = [x for x in en if x != PLUG]
        entries.pop(PLUG, None)          # no override capability: none is needed
        # re-emit only the plugins block in place
        block = yaml.safe_dump({"plugins": {**pl, "enabled": en, "entries": entries}}, sort_keys=False, allow_unicode=True)
        m = re.search(r"^plugins:\n(?:[ \t].*\n|\n)*", s, flags=re.M)
        if m:
            s = s[:m.start()] + block + s[m.end():]
        else:
            s = s.rstrip("\n") + "\n" + block
        cfg.write_text(s, encoding="utf-8")
        d2 = yaml.safe_load(s)
        assert ("kanban_lean" in d2["toolsets"]) == want_lean and ("kanban" in d2["toolsets"]) != want_lean, (w, cfg)
        assert (PLUG in (d2["plugins"].get("enabled") or [])) == want_lean, (w, cfg)
        print(f"{w:8} {'pack' if 'hermes-packs' in str(cfg) else 'live':4} → {'kanban_lean + plugin on' if want_lean else 'kanban, plugin off'}")
PY
if [ "$MODE" != status ]; then
  echo "next: hermes -p bash prompt-size · python3 $ROOT/scripts/krew-pin-check.py"
fi
