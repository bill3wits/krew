"""krew-install's Control Panel step (called from scripts/krew-install.py): krew's tiles in the Crissa panel.

Crissa owns the one Control Panel (in the Wall); krew only adds its few tiles to the panel's bottom section (the
owner 2026-10-06). This places panel/ (krew-panel-tiles.py + its krew_settings.py copy) under $KREW_HOME/panel and
writes the add-on manifest where the panel looks (panel.tiles.dir, else $CRISSA_HOME/panel.d, else ~/.crissa/panel.d
when Crissa is installed). With no panel found it writes nothing and says how to add the tiles later. Nothing is
restarted: the panel reads its add-on folder on its own next look.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _tiles_module():
    spec = importlib.util.spec_from_file_location("krew_panel_tiles", REPO / "panel" / "krew-panel-tiles.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# THE ENGINE TILES ONLY WHEN AN ENGINE IS THE BRAIN (crissatest 2026-10-31, the User: "krew came up not the models
# though"): on the claude brain the Control Panel listed krew-engine@ops / @crew, never installed or used, as down.
# krew init passes the brains' URLs (KREW_BRAIN_URLS); when none of them is this machine's, the engines are not
# tiled. A panel.tiles.engines the user set is kept. Kill: KREW_TILES_ENGINES_BY_BRAIN=0.
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "0.0.0.0")


def engines_unused(cfg: dict, env=None) -> str:
    """'' = tile the engines as before; else why not."""
    env = os.environ if env is None else env
    if str(env.get("KREW_TILES_ENGINES_BY_BRAIN", "1")).strip().lower() in ("0", "false", "no", "off"):
        return ""
    if "engines" in cfg:
        return ""
    urls = str(env.get("KREW_BRAIN_URLS") or "").split()
    if not urls:
        return ""
    if any(h in u for u in urls for h in LOCAL_HOSTS):
        return ""
    return "the brains are " + ", ".join(sorted(set(urls))) + " (no engine on this machine)"


def install(a, kh: Path, place, say) -> bool:
    src = REPO / "panel"
    if not src.is_dir():
        return True
    if os.environ.get("KREW_WALL_REFUSED"):           # krew init: a Crissa home it was not given (krew_install_homes)
        say(f"refused  Control Panel tiles: {os.environ['KREW_WALL_REFUSED']}")
        return True
    place(src, kh / "panel", link=a.link, force=a.force, dry=a.dry_run, say=say)
    T = _tiles_module()
    cfg = dict(T.S.load("panel", path=kh / "krew.yaml").get("tiles") or {})
    if not str(cfg.get("janitor_report") or "").strip():
        jd = os.path.expanduser(str(T.S.load("paths", path=kh / "janitor.yaml").get("metrics_dir") or ""))
        cfg["janitor_report"] = (os.environ.get("KREW_JANITOR_SNAPSHOT")
                                 or str(Path(jd or kh / "janitor" / "metrics") / "janitor-latest.json"))
    no_engines = engines_unused(cfg)
    if no_engines:
        cfg["engines"] = {}
        say(f"Panel:   no engine tiles: {no_engines}")
    d, why = T.target_dir(cfg)
    if d is None:
        say(f"Panel:   {why}: python3 {kh / 'panel' / 'krew-panel-tiles.py'} write")
        return True
    if why == "$CRISSA_HOME/panel.d" and not d.parent.is_dir():
        # Crissa is not installed here: write nothing into its home (marry says the same right after)
        say(f"Panel:   Crissa not installed (no {d.parent}): python3 {kh / 'panel' / 'krew-panel-tiles.py'} write once it is")
        return True
    if a.dry_run:
        say(f"write    {d / T.NAME}   (krew's {len(T.manifest(cfg)['tiles'])} tiles in the Control Panel's bottom section; {why})")
        return True
    r = T.write(cfg)
    say(f"Panel:   {r['written']}   (krew's {r['tiles']} tiles in the Control Panel's bottom section; {why}). "
        f"Take them out: python3 {kh / 'panel' / 'krew-panel-tiles.py'} remove")
    knowledge(d.parent, kh, say)
    return True


KNOWLEDGE_MODEL = "intfloat/multilingual-e5-small"     # crew/local-services.json `embedding.repo`


def knowledge(state: Path, kh: Path, say) -> Path:
    """<Crissa state home>/knowledge.d/krew.json (the kit's knowledge index, 2026-11-01): krew's docs, its shipped
    skills and what the krew builds ($KREW_HOME/built), plus krew's own docs index, so Crissa can explain any of
    them. `krew-panel-tiles.py remove` takes it out with the tiles."""
    f = state / "knowledge.d" / "krew.json"
    body = {"id": "krew", "title": "Krew",
            "folders": {"docs": str(REPO / "docs"), "skills": str(REPO / "skills"), "built": str(kh / "built")},
            "index_db": str(kh / "docs-index" / "index.db"), "model": KNOWLEDGE_MODEL}
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, f)
    say(f"Knowledge: {f}   (krew's docs, skills and built/ in Crissa's knowledge index)")
    return f
