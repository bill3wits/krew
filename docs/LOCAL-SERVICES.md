# Local services: memory and search on this machine (2026-11-01)

**Asked** (the owner, 2026-11-01): "EVERYthing we run is local. Very important. The data not so much as the structure
and we need to ship on installation as is all local. Cant you copy searxng and hindsight and vector db from the code?"

Before this, krew's README said "krew does not install the Hindsight API". `krew-hindsight.service` shipped with no venv
or env behind it, `--memory` ran `hermes plugins install vectorize-io/hindsight` (a GitHub fetch that Hermes's
security scan can block), and SearXNG was not installed at all: a crew searched through Hermes's keyless cloud ring.

## What init installs now (unless `--no-memory` / `--no-search`)

Every pin is in `crew/local-services.json`; `scripts/krew_install_local.py` reads nothing else. Everything is user
space on loopback: no sudo, no docker. Each download is checked against its pin; each step is idempotent (what is in
place and matches is kept and said).

| piece | where | from | unit |
|---|---|---|---|
| Hindsight plugin | `$HERMES_HOME/plugins/hindsight` | **vendored**: `crew/hindsight/plugin/hindsight` (MIT, upstream `hindsight-integrations/hermes` at 176f8c2 minus its tests and banner image), refused unless its tree sha is the pin; its pip pin `hindsight-client==0.10.2` into Hermes's runtime (and a row in `hermes-env-pins` so a Hermes update puts it back) | - |
| Hindsight API | `~/.krew/hindsight/.venv` | `torch==2.14.0` from the CPU index first, then `hindsight-api==0.10.2` (+ `sqlalchemy<2.1`) | `krew-hindsight` |
| vector store | `~/.krew/hindsight/pgdata` | pg0 (a hindsight-api dependency): PostgreSQL 18.1 + pgvector 0.8.5 in one binary, no download, password made at install (`db.env`, 0600) | `krew-memory-db` |
| embedding model | `${XDG_DATA_HOME:-~/.local/share}/crissa-models/hf` | `intfloat/multilingual-e5-small` at a pinned revision, fetched once (the safetensors for Hindsight, the onnx files sha-checked for the Crissa kit's index: one model on disk for both; the kit's lock wins when the kit is here) | - |
| search | `~/.krew/searxng` | SearXNG at a pinned commit (AGPL-3.0: fetched at install, never shipped in krew), archive or tree sha checked; `settings.yml` loopback, own secret, limiter off, json on, the two Tor engines off | `krew-searxng` |
| profiles | each `config.yaml` + `.env` | `web.search_backend: searxng`, `keyless_fallback: false`, `keyless_rescue: false`, `SEARXNG_URL`; every other `web` key kept | - |

`hindsight.env` points Hindsight's LLM at the install's own crew brain (`--memory-brain`, init passes it), sets the
model to its local snapshot path and `HF_HUB_OFFLINE=1`, so at run time Hindsight's one outbound call is that brain.

**The docs index shares the model** (the owner via 84, 2026-11-01: one model on disk): `krew_docs` embeds with the same
e5 ONNX export (onnxruntime + tokenizers from the Hindsight venv, "passage: " / "query: " prefixes, mean-pooled,
L2), and re-embeds everything once when the model it was built with changes (it was BAAI/bge-small via fastembed;
`docs.embedder: fastembed` keeps that path). Either HF layout is read (the standard `<HF_HOME>/hub/models--...`, or the
same straight under HF_HOME), so the kit's copy and krew's are one. With Crissa here, krew-install also writes
`<Crissa state home>/knowledge.d/krew.json` beside its panel manifest (krew's docs, shipped skills, `$KREW_HOME/built`,
and this index): the kit's knowledge index reads krew live; `krew-panel-tiles.py remove` takes it out.

**Units are enabled only on an own install**: own homes AND units written to `~/.config/systemd/user`. A scratch
install, or units written anywhere else, gets the commands printed and nothing run. All three units are in krew's
panel manifest, so the Crissa Control Panel's All OS Off / On stop and start them with the rest.

**Tests never reach the real thing**: under pytest a real download, pip, venv or systemctl is refused
(`KREW_LOCAL_REAL=1` allows); an install a test runs gets the local file steps only.

## Proof (1e, 2026-11-01, this machine)

A scratch home (`HOME=<scratch>`), the pins on ports 18887/18888/18890 (another krew holds 8888 here):

- `krew-install.py init --no-hermes --no-discover --yes --endpoint http://127.0.0.1:8000/v1 --model ops` with a
  stand-in `hermes`: plugin vendored (no `hermes plugins install` call), `hindsight-client 0.10.2` in the stand-in
  runtime, hindsight-api 0.10.2 + torch 2.14.0+cpu + SQLAlchemy 2.0.55 + pg0-embedded 0.15.2 (venv 2.0 GB), the model
  (919 MB with the onnx export), SearXNG 1c10abe (venv 76 MB), every profile pointed at it. A rerun keeps everything.
- The three units' own commands started (the store, Hindsight up in 12 s, SearXNG), listening on 127.0.0.1 only.
- **Memory**: retain one fact (200, 2.4 s, the brain extracted it), recall it by a question (found, 0.1 s).
- **Search**: one query, 20 results from loopback.
- **Every TCP connection** of those processes, sampled every 0.2 s: Hindsight only to 127.0.0.1 (the brain :8000, its
  store); SearXNG to its engines (Wikimedia, a Microsoft address) and loopback. Nothing else.
- Stopped cleanly, nothing left running. The scratch install (about 3 GB) was deleted after these numbers were
  written here.

Found on the way and fixed: under `HOME=<scratch>` the scratch homes look like the own ones, and the first proof run
reached the user's systemd manager with a `daemon-reload` and two `enable` attempts that failed ("unit does not
exist"; the units were in the scratch folder). The unit folder now decides as well.

## Switches and rollback

`--no-memory`, `--no-search` (init); `--memory`, `--search` alone; `KREW_LOCAL_SERVICES=<file>` reads other pins (a
proof on other ports). Rollback: `systemctl --user disable --now krew-searxng krew-hindsight krew-memory-db`, delete
`~/.krew/hindsight` and `~/.krew/searxng`, set `memory_enabled: false` and drop the `web` keys in the profiles.
