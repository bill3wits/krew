# The Control Panel contract between krew and the Crissa Wall

Crissa owns the one Control Panel, in the Wall. krew ships no panel: it writes one add-on manifest into the panel's
add-on folder (`panel.tiles.dir` in `krew.yaml`, else `$ZOEYOS_HOME/panel.d`, else `~/.zoeyos/panel.d`) and the
panel draws the tiles. This file is the contract the Wall's rebuild needs to honour, written from krew's side
(the owner 2026-10-08). Part 1 is what the Wall already does (schema `zoeyos-panel-addon/v1`, its reader
`lib/zoeyos_panel_addons.py`); part 2 is what krew's manifest now carries and what the panel must do with it.

## 1. What stands today (v1)

* The panel names no add-on in code. Its settings (`addons`) say where manifests live (`paths`, every `*.json`
  there is one add-on), the bottom section's heading (`section`, default "Add-ons"), and which add-ons it expects
  (`slots`: `{id, title, missing}`).
* A manifest: `{"schema": "zoeyos-panel-addon/v1", "id": "krew", "title": "...", "by": "krew-panel-tiles",
  "tiles": [...]}`. The first manifest for an id wins; a manifest that cannot be read, or a tile that breaks a
  rule, is named in `errors` and skipped. The panel never fails because of an add-on.
* Tile kinds, decided in this order from the keys present:
  * `status`: `{"label", "status": "/path/report.json", "stale_s": 900, "unit": "x.timer"}` paints the `level`
    (`ok|warn|bad`) and `headline` of the JSON file, judged stale by the panel from its `epoch` or `ts`; with a
    `unit`, the tile's key is Run now (one start of that unit) and a click pages its journal.
  * `match`: `{"match": "prefix-*-suffix.service", "label": "{unit} - words"}`, one tile per unit that matches.
  * `unit`: `{"unit": "name.service", "label": "Name - what it is", "port": 1234, "href": "http://..."}`: a full
    tile (state, journal, script, Restart / Start / Stop, Open when `href`), and All OS Off covers its unit.
  * `link`: `{"label", "href"}`: a page, no unit.
  * Optional on any tile: `port` (1..65535), `scope` (`user` | `system`, default user), `href` (http(s) only).
* A slot with no manifest is a placeholder tile: the off mark and `missing` ("krew not installed"), not counted
  as a service. Unknown keys on a tile are dropped silently, so a newer manifest still draws on an older panel.

## 2. What krew's manifest adds (v1 fields, forward-compatible)

krew's manifest keeps the v1 shape and adds three keys. A v1 panel ignores them and draws every tile in its
add-on section; the rebuilt panel must honour them:

| key | on | what the panel does |
|---|---|---|
| `sections` | the manifest | `["krew services"]`: the add-on's tiles are drawn in one labeled section per entry, in this order, apart from the Wall's own sections ("Wall services" stays the Wall's). A tile with no `section` goes under the add-on's `title`. |
| `section` | a tile | the section this tile is drawn in (one of `sections`). |
| `run` | a tile | `{"unit": "x.timer" \| "x.service"}`: the tile's key is **Run now**: `systemctl --user start <unit>` (scope `system` when the tile says so), then the panel names why it failed if it did. Same as a status tile's `unit` today; `run` makes it explicit for tiles that are not status tiles. |
| `off` | a tile | `{"file": "/abs/path.off"}` or `{"unit": "x.timer" \| "x.service"}`: the tile's second key is **Off** / **On**. With `file`: Off creates the file (empty), On removes it; the tile shows the off mark while the file exists. With `unit`: Off is `systemctl --user disable --now <unit>`, On is `enable --now` (scope `system` when the tile says so). The panel never deletes anything else. |
| `id` | a tile | a stable id (`krew-keeper`), so the panel can keep per-tile state (last Run now result) across republishes. |
| `set` | a tile | `{"file": "/abs/path", "value": "word"}` (2026-10-09): the tile is a **mode key**: pressing it writes `value` as the file's first line (the rest of the file kept), and the tile is lit while the file's first word equals `value`. krew's "modes" section uses it for the GPU mode row (Standard / Beast / Render / All OS Off on the flag the keeper, krew-comfy and the engines read; the reference Control Panel's Mode row). The file must be an absolute path under the add-on's own home or the Hermes home; anything else is an `errors` row. A v1 panel ignores the key and draws the tile as a plain label. |

Rules the rebuilt panel must keep:

* A tile with `section` but no `sections` entry of that name is drawn under the add-on's title, never dropped.
* `run` and `off` act only on the unit or file the manifest names; the unit must pass the panel's own unit check
  (`[A-Za-z0-9@._:-]{1,120}`), the file must be an absolute path under the add-on's own home (`$KREW_HOME`).
  Anything else is an `errors` row, and the key is not drawn.
* A status tile whose `status` file does not exist yet reads "no report yet" (warn), never an error.
* When krew is not installed (no manifest), the slot shows the placeholder, dimmed, with "krew not installed":
  nothing is lit, no key, not counted as a service.
* The Wall's own services never appear in krew's section: krew writes tiles only for units of its own and for
  the approved rows of `services.json` that are not the Wall's (`zoeyos: true` rows are left out).

## 3. What krew writes today (`panel/krew-panel-tiles.py show`)

1. the host gateway, every per-profile gateway, the dashboard, crew/kanban, the Janitor (as before);
2. `panel.tiles.extra` from `krew.yaml`;
3. one `unit` tile per approved row of `$KREW_HOME/services.json` with a unit of krew's own;
4. the "krew services" section: keeper, hold sweep, steward sweep, restart window, window tick, M&S nightly,
   dream night, Hindsight, docs index, boot check, render window. Each: `id`, `label`, `unit`, `section`,
   `run: {unit}`, `off: {file: $KREW_HOME/<loop>.off}`, and where the loop writes one, `status` + `stale_s`.
5. the "modes" section (2026-10-09): the two engines
   (the `krew-engine@` template for ops :8000 and crew :8005) as unit tiles, and ComfyUI's unit with its
   page, and, once `panel.tiles.mode_keys` is true, the GPU mode row as four `set` tiles on the mode flag (a v1 panel
   would read a tile with none of its four keys as an error, so they wait for the panel that honours `set`). The
   rest of the reference panel's rows (All OS Off's wall side, ZOS desktop, Camera, Voice, Security, the
   lamp test, the rail keys, the Render Studio stage) act on the Wall's own services and stay the Wall's.

The off files are the loops' own switches: a crew script started through `krew-crew-run` is not run while
`$KREW_HOME/<script stem>.off` exists (krew-keeper.off, kanban-board-gate.off, steward-sweep.off, krew_memory.off,
nightly-krew-index.off, render-window.off); the window ticker, the restart window and M&S read their own
(`krew-window-tick.off`, `restart-window.off`, `krew-ms.off`). So the panel's Off key and the loop agree without a
restart. Hindsight and the boot check are units with no script switch: their Off disables the unit.
