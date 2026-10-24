"""krew-install's Control Panel step (called from scripts/krew-install.py): krew's tiles in the Crissa panel.

Crissa owns the one Control Panel (in the Wall); krew only adds its few tiles to the panel's bottom section (the
owner 2026-10-06). This places panel/ (krew-panel-tiles.py + its krew_settings.py copy) under $KREW_HOME/panel and
writes the add-on manifest where the panel looks (panel.tiles.dir, else $ZOEYOS_HOME/panel.d, else ~/.zoeyos/panel.d
when Crissa is installed). With no panel found it writes nothing and says how to add the tiles later. Nothing is
restarted: the panel reads its add-on folder on its own next look.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _tiles_module():
    spec = importlib.util.spec_from_file_location("krew_panel_tiles", REPO / "panel" / "krew-panel-tiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def install(a, kh: Path, place, say) -> bool:
    src = REPO / "panel"
    if not src.is_dir():
        return True
    place(src, kh / "panel", link=a.link, force=a.force, dry=a.dry_run, say=say)
    T = _tiles_module()
    cfg = dict(T.S.load("panel", path=kh / "krew.yaml").get("tiles") or {})
    if not str(cfg.get("janitor_report") or "").strip():
        jd = os.path.expanduser(str(T.S.load("paths", path=kh / "janitor.yaml").get("metrics_dir") or ""))
        cfg["janitor_report"] = (os.environ.get("KREW_JANITOR_SNAPSHOT")
                                 or str(Path(jd or kh / "janitor" / "metrics") / "janitor-latest.json"))
    d, why = T.target_dir(cfg)
    if d is None:
        say(f"Panel:   {why}: python3 {kh / 'panel' / 'krew-panel-tiles.py'} write")
        return True
    if why == "$ZOEYOS_HOME/panel.d" and not d.parent.is_dir():
        # Crissa is not installed here: write nothing into its home (marry says the same right after)
        say(f"Panel:   Crissa not installed (no {d.parent}): python3 {kh / 'panel' / 'krew-panel-tiles.py'} write once it is")
        return True
    if a.dry_run:
        say(f"write    {d / T.NAME}   (krew's {len(T.manifest(cfg)['tiles'])} tiles in the Control Panel's bottom section; {why})")
        return True
    r = T.write(cfg)
    say(f"Panel:   {r['written']}   (krew's {r['tiles']} tiles in the Control Panel's bottom section; {why}). "
        f"Take them out: python3 {kh / 'panel' / 'krew-panel-tiles.py'} remove")
    return True
