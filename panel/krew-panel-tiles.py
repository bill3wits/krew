#!/usr/bin/env python3
"""krew-panel-tiles: krew's few tiles in the bottom section of the Crissa Control Panel.

The owner 2026-10-06: one Control Panel, in the Wall (Crissa owns it); Hermes adds just its few extra tiles in the
bottom section, and without krew the Wall says "Hermes krew plugin not installed". So krew ships no panel. It writes
one add-on manifest (schema crissa-panel-addon/v1) into the panel's add-on folder, and the panel draws the tiles:

  * the host gateway (every profile on one gateway), with its state, journal, script and Restart/Start/Stop
  * every per-profile gateway that is on the machine (one match; a masked one shows parked, off by design)
  * the Hermes dashboard, the same full tile, plus its page (the panel's Open key)
  * crew/kanban: the loop's last run (krew-run-status writes how it ended, after every tick, into
    $KREW_HOME/run-status/krew-kanban-loop.service.json: ok, off by its switch, or failed with systemd's result and
    the error line), judged stale by the panel; a click pages krew-kanban-loop.timer's journal (timer + loop), and
    the tile's key is Run now (one tick of the loop, then the panel names why it failed if it did)
  * the Janitor: its report (level + headline of the snapshot the janitor writes every tick), judged stale by the
    panel; a click pages krew-janitor.timer's journal, and the tile's key is Run now
  * whatever else this machine's Hermes leans on, from `panel.tiles.extra` in krew.yaml (same shape as a tile)
  * every approved row of the one services list ($KREW_HOME/services.json, crew/lib/krew_services.py) that has a
    unit of ours (the Wall's own rows are its, not krew's)
  * the "modes" section (2026-10-09, the parity build): the reference Control Panel's Mode row as krew tiles, since
    the GPU mode is krew's (the keeper, krew-comfy and the engines read the flag file): Standard / Beast / Render /
    All OS Off each a `set` tile that writes one word into the flag (~/.hermes/krew-gpu-mode by default,
    panel.tiles.gpu_mode_file) once panel.tiles.mode_keys is true, the two engines (krew-engine@ops, krew-engine@crew)
    as unit tiles, and ComfyUI's unit with its page (the Render Studio's chips are the Wall's; its renderer is this
    tile). docs/PANEL-CONTRACT.md part 2 says what the rebuilt panel does with `set`; a v1 panel would read a tile
    with none of its four keys as an error, so the mode keys wait for the panel that honours them.
  * the "krew services" section (the owner 2026-10-08): krew's own loops, each a status tile with Run now and Off:
    the keeper, the hold sweep, the steward sweep, the restart window, the window tick, the M&S nightly, the dream
    night, Hindsight, the docs index, the boot check and the render window. They are labeled `section: "krew
    services"`, apart from "Wall services"; docs/PANEL-CONTRACT.md says what the panel must do with `section`,
    `run` and `off` (a v1 panel ignores those keys and draws the tiles in its add-on section as plain tiles).

    krew-panel-tiles show      # the manifest it would write, and where (writes nothing)
    krew-panel-tiles write     # write it (krew-install does this when it finds the panel)
    krew-panel-tiles remove    # take it out: the bottom section goes back to its placeholder

Settings: `panel.tiles` in $KREW_HOME/krew.yaml (config/krew.example.yaml lists every key with the reference
install's values as `# ref:` lines). Where the manifest goes: panel.tiles.dir, else $CRISSA_HOME/panel.d, else
~/.crissa/panel.d when ~/.crissa exists; with none of them, nothing is written and it says so.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import krew_settings as S  # noqa: E402
import krew_hermes as KH  # noqa: E402  where Hermes lives

SCHEMA = "crissa-panel-addon/v1"
ADDON_ID = "krew"             # the slot the Crissa panel's shipped settings expect
NAME = "krew.json"
DEFAULTS = {
    "dir": "",
    "title": "Krew",
    "gateway": "hermes-gateway.service",
    "gateway_port": 8642,
    "gateway_label": "Host gateway - every profile on one gateway",
    "profile_gateways": "hermes-*-gateway.service",
    "profile_label": "{unit} - one profile's gateway",
    "dashboard": "hermes-dashboard.service",
    "dashboard_port": 9119,
    "dashboard_label": "Hermes - dashboard",
    "dashboard_url": "",
    "kanban": "krew-kanban-loop.timer",
    "kanban_label": "Crew kanban - the loop: greens a try, reframes a repeated failure, retreats to Crissa",
    "kanban_status": "",
    "kanban_stale_s": 3600,
    "janitor": True,
    "janitor_report": "",
    "janitor_unit": "krew-janitor.timer",
    "janitor_label": "Janitor - what it watches",
    "extra": [],
    "services": True,              # tiles from the services list ($KREW_HOME/services.json)
    "krew_section": True,          # the "krew services" section (the loops, each with Run now / Off)
    "section_title": "krew services",
    "modes": True,                 # the "modes" section: the engines, ComfyUI, and (mode_keys) the GPU mode row
    "modes_title": "modes",
    "mode_keys": False,            # true = the four `set` tiles (Standard / Beast / Render / All OS Off); a v1 panel
                                   #   reads a tile with no unit, match, status or href as an error, so the rebuilt
                                   #   panel's owner flips this once it honours `set` (docs/PANEL-CONTRACT.md)
    "gpu_mode_file": "",           # "" = KREW_GPU_MODE_FILE, else ~/.hermes/krew-gpu-mode (what the keeper and krew-comfy read)
    "engines": {"ops": 8000, "crew": 8005},   # krew-engine@<name>.service and its port (engines/README.md)
    "comfy_unit": "comfyui.service",          # "" = no ComfyUI tile
    "comfy_url": "",               # "" = render.comfy_url in krew.yaml, else http://127.0.0.1:8188
}

# The reference Control Panel's Mode row (its words): one `set` tile each; the value is the flag file's first word.
GPU_MODES = (
    ("standard", "Standard - both brains up (ops and crew)", "normal"),
    ("beast", "Beast - the crew's brain alone; ops parked", "beast"),
    ("render", "Render - the GPU to ComfyUI; the crew's brain parked", "render"),
    ("all-os-off", "All OS Off - parks every GPU tenant (ops, crew, the voice, Comfy); pure computer", "off"),
)

# krew's own loops, one tile each: the timer or service whose Run now starts one tick, the status file it writes
# (where there is one), its off switch: a file under $KREW_HOME the launcher (krew-crew-run) or the script itself
# honours, or `unit:<name>` = the unit to disable. What the panel does with `run` and `off`: docs/PANEL-CONTRACT.md.
KREW_LOOPS = (
    ("keeper", "Keeper - the lane watchdog", "krew-keeper.service", "keeper/status.json", "krew-keeper.off"),
    ("hold-sweep", "Board gate - routes blocked cards every 2 minutes", "krew-hold-sweep.timer",
     "run-status/krew-hold-sweep.service.json", "kanban-board-gate.off"),
    ("steward-sweep", "Steward sweep - the hourly stewardship card (off until enabled)", "steward-sweep.timer",
     "run-status/steward-sweep.service.json", "steward-sweep.off"),
    ("restart-window", "Restart window - restarts owed, ruled on a card", "krew-restart-window.timer",
     "state/restart-window.json", "restart-window.off"),
    ("window-tick", "Window tick - starts the night's jobs", "krew-window-tick.timer",
     "run-status/krew-window-tick.service.json", "krew-window-tick.off"),
    ("ms-nightly", "M&S nightly - the machine map and report", "krew-ms-nightly.service",
     "ms/reports/ms-trail-LATEST.json", "krew-ms.off"),
    ("dream-night", "Dream night - memory rolled and retained", "krew-dream-night.service",
     "memory/dreams/LATEST.json", "krew_memory.off"),
    ("hindsight", "Hindsight - long memory", "krew-hindsight.service", "", "unit:krew-hindsight.service"),
    ("memory-db", "Memory store - Hindsight's vectors (PostgreSQL + pgvector, this machine)", "krew-memory-db.service", "",
     "unit:krew-memory-db.service"),
    ("search", "Search - SearXNG on this machine", "krew-searxng.service", "", "unit:krew-searxng.service"),
    ("docs-index", "Docs index - the night's rebuild", "krew-docs-index-night.service",
     "run-status/krew-docs-index-night.service.json", "nightly-krew-index.off"),
    ("boot-check", "Boot check - did every service come back", "fleet-bootcheck.service", "", "unit:fleet-bootcheck.service"),
    ("render-window", "Render window - holds renders for the off-hours", "krew-render-window.timer",
     "run-status/krew-render-window.service.json", "render-window.off"),
)


def settings(cfg: dict | None = None) -> dict:
    got = (S.load("panel").get("tiles") if cfg is None else cfg) or {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (got if isinstance(got, dict) else {}).items() if v is not None})
    return out


def _janitor_report(t: dict) -> str:
    if str(t.get("janitor_report") or "").strip():
        return os.path.expanduser(str(t["janitor_report"]).strip())
    if os.environ.get("KREW_JANITOR_SNAPSHOT"):
        return os.environ["KREW_JANITOR_SNAPSHOT"]
    jy = S.load("paths", path=os.environ.get("KREW_JANITOR_CONFIG") or S.krew_home() / "janitor.yaml")
    d = os.path.expanduser(str(jy.get("metrics_dir") or "")) or str(S.krew_home() / "janitor" / "metrics")
    return str(Path(d) / "janitor-latest.json")


def _run_status(t: dict) -> str:
    """Where krew-run-status writes the loop's last run: $KREW_HOME/run-status/<the timer's service>.json."""
    if str(t.get("kanban_status") or "").strip():
        return os.path.expanduser(str(t["kanban_status"]).strip())
    unit = str(t["kanban"])
    svc = unit[: -len(".timer")] + ".service" if unit.endswith(".timer") else unit
    return str(S.krew_home() / "run-status" / f"{svc}.json")


def manifest(cfg: dict | None = None) -> dict:
    t = settings(cfg)
    tiles = []
    if t.get("gateway"):
        tiles.append({"unit": t["gateway"], "label": t["gateway_label"], "port": t.get("gateway_port") or None})
    if t.get("profile_gateways"):
        tiles.append({"match": t["profile_gateways"], "label": t["profile_label"]})
    if t.get("dashboard"):
        port = t.get("dashboard_port") or None
        url = str(t.get("dashboard_url") or "").strip() or (f"http://127.0.0.1:{port}/" if port else "")
        tiles.append(dict({"unit": t["dashboard"], "label": t["dashboard_label"], "port": port},
                          **({"href": url} if url else {})))
    if t.get("kanban"):
        tiles.append({"label": t["kanban_label"], "status": _run_status(t),
                      "stale_s": int(t.get("kanban_stale_s") or 3600), "unit": t["kanban"]})
    if t.get("janitor"):
        tiles.append({"label": t["janitor_label"], "status": _janitor_report(t), "stale_s": 900,
                      "unit": t.get("janitor_unit") or None})
    for x in t.get("extra") or []:
        if isinstance(x, dict) and (x.get("unit") or x.get("match") or x.get("href") or x.get("status")):
            tiles.append({k: v for k, v in x.items() if v not in (None, "")})
    tiled = {x.get("unit") for x in tiles if x.get("unit")}
    if t.get("services"):
        for x in services_tiles():
            if x["unit"] not in tiled:
                tiles.append(x)
                tiled.add(x["unit"])
    if t.get("krew_section"):
        tiles += krew_section_tiles(str(t.get("section_title") or "krew services"))
    sections = [str(t.get("section_title") or "krew services")] if t.get("krew_section") else []
    if t.get("modes"):
        title = str(t.get("modes_title") or "modes")
        sections.append(title)
        tiles += [x for x in modes_tiles(t, title) if x.get("unit") not in tiled or x.get("set")]
    tiles = [{k: v for k, v in x.items() if v is not None} for x in tiles]
    return {"schema": SCHEMA, "id": ADDON_ID, "title": t.get("title") or "Krew", "by": "krew-panel-tiles",
            "sections": sections, "tiles": tiles}


def _services_module():
    """crew/lib/krew_services.py beside this checkout ($KREW_HOME/crew/lib when installed), by path."""
    import importlib.util
    for lib in (_HERE.parent / "crew" / "lib" / "krew_services.py", S.krew_home() / "crew" / "lib" / "krew_services.py"):
        if lib.is_file():
            spec = importlib.util.spec_from_file_location("krew_panel_services", lib)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def services_tiles() -> list:
    """One tile per approved services row with a unit of krew's own (the Wall's rows are the Wall's)."""
    mod = _services_module()
    if mod is None or not mod.path().is_file():
        return []
    try:
        return mod.panel_tiles(mod.load())
    except Exception:  # noqa: BLE001 - a broken list never breaks the panel's add-on
        return []


def krew_section_tiles(section: str) -> list:
    """The "krew services" section: krew's loops, each a status tile with Run now (its unit) and Off (its switch)."""
    kh = S.krew_home()
    out = []
    for key, label, unit, status, off in KREW_LOOPS:
        tile = {"id": "krew-" + key, "label": label, "unit": unit, "section": section, "run": {"unit": unit},
                "off": ({"unit": off[5:]} if off.startswith("unit:") else {"file": str(kh / off)})}
        if key == "boot-check":
            tile["scope"] = "system"
        if status:
            tile.update(status=str(kh / status), stale_s=86400 if key in ("ms-nightly", "dream-night", "docs-index")
                        else 3600)
        out.append(tile)
    return out


def gpu_mode_file(t: dict) -> str:
    if str(t.get("gpu_mode_file") or "").strip():
        return os.path.expanduser(str(t["gpu_mode_file"]).strip())
    return os.path.expanduser(os.environ.get("KREW_GPU_MODE_FILE") or str(KH.hermes_home() / "krew-gpu-mode"))


def modes_tiles(t: dict, section: str) -> list:
    """The "modes" section: the Mode row as `set` tiles on the GPU flag, the engines and ComfyUI as unit tiles."""
    flag = gpu_mode_file(t)
    out = []
    if t.get("mode_keys"):
        for key, label, word in GPU_MODES:
            out.append({"id": "krew-mode-" + key, "label": label, "section": section,
                        "set": {"file": flag, "value": word}})
    engines = t.get("engines") if isinstance(t.get("engines"), dict) else {}
    for name, port in engines.items():
        out.append({"id": f"krew-engine-{name}", "unit": f"krew-engine@{name}.service", "section": section,
                    "label": f"{str(name).capitalize()} - the engine serving `{name}`",
                    "port": int(port) if port else None})
    comfy = str(t.get("comfy_unit") or "").strip()
    if comfy:
        url = str(t.get("comfy_url") or "").strip() or str((S.load("render") or {}).get("comfy_url") or "").strip() \
            or "http://127.0.0.1:8188"
        try:
            port = int(url.rsplit(":", 1)[1].split("/")[0])
        except (IndexError, ValueError):
            port = None
        out.append({"id": "krew-comfy", "unit": comfy, "section": section, "port": port, "href": url,
                    "label": "ComfyUI - the renderer (krew-render files its orders; Render mode gives it the GPU)"})
    return out


def target_dir(cfg: dict | None = None, env: dict | None = None) -> "tuple[Path | None, str]":
    """(folder, why) for the manifest, or (None, why) when no Crissa Control Panel is found."""
    e = os.environ if env is None else env
    t = settings(cfg)
    if str(t.get("dir") or "").strip():
        return Path(os.path.expanduser(str(t["dir"]).strip())), "panel.tiles.dir in krew.yaml"
    zh = str(e.get("CRISSA_HOME") or "").strip()
    if zh:
        return Path(os.path.expanduser(zh)) / "panel.d", "$CRISSA_HOME/panel.d"
    home = Path(os.path.expanduser("~/.crissa"))
    if home.is_dir():
        return home / "panel.d", "~/.crissa/panel.d"
    return None, ("no Crissa Control Panel found (set panel.tiles.dir in krew.yaml, or CRISSA_HOME); "
                  "run krew-panel-tiles write once it is installed")


def write(cfg: dict | None = None, env: dict | None = None) -> dict:
    d, why = target_dir(cfg, env)
    if d is None:
        return {"ok": False, "written": None, "why": why}
    d.mkdir(parents=True, exist_ok=True)
    f = d / NAME
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest(cfg), indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, f)
    return {"ok": True, "written": str(f), "why": why, "tiles": len(manifest(cfg)["tiles"])}


def remove(cfg: dict | None = None, env: dict | None = None) -> dict:
    d, why = target_dir(cfg, env)
    f = d / NAME if d else None
    k = d.parent / "knowledge.d" / NAME if d else None       # krew's knowledge manifest (krew-install writes it)
    if k is not None and k.exists():
        k.unlink()
    if f is None or not f.exists():
        return {"ok": True, "removed": None, "why": "nothing to remove" if d else why}
    f.unlink()
    return {"ok": True, "removed": str(f)}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("verb", choices=("show", "write", "remove"))
    a = ap.parse_args(argv)
    if a.verb == "show":
        d, why = target_dir()
        print(json.dumps({"dir": str(d) if d else None, "why": why, "manifest": manifest()}, indent=1))
        return 0
    r = write() if a.verb == "write" else remove()
    print(json.dumps(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
