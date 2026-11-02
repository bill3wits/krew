# Hindsight (long memory for the dream night)

`crew/scripts/krew_memory.py dream-night` keeps the short and medium memory as files under `KREW_MEMORY_ROOT`
(default `~/.krew/memory`: `day/`, `rolling/3day.md`, `workspace/TODAY.md`, `dreams/`, `archive/`) and, when a
Hindsight API answers at `HINDSIGHT_API_URL` (default `http://127.0.0.1:8888`), retains the day into it,
consolidates and reflects. With no Hindsight the file side still runs and the report says Hindsight was not reached.

Two halves: the Hermes **plugin** (what every profile talks to) and the Hindsight **API** (the store). Both come with
`krew-install.py --memory` (init does it unless `--no-memory`), all on this machine (`docs/LOCAL-SERVICES.md`):

- the plugin from `crew/hindsight/plugin/hindsight` (upstream's MIT plugin, vendored at the commit
  `crew/local-services.json` pins, refused if its tree sha differs; never `hermes plugins install`) + its one pip pin
  in Hermes's runtime, and `$HERMES_HOME/hindsight/config.json` from `config.example.json` (the plugin reads only it);
- `~/.krew/hindsight/.venv` with `hindsight-api` 0.10.2 on CPU torch, `hindsight.env` written for you (0600: the store,
  the embedding model's local path, your brain endpoint, `HF_HUB_OFFLINE=1`);
- the store: `krew-memory-db.service`, PostgreSQL 18 + pgvector from pg0 in that venv, loopback, its password made at
  install (`db.env`, 0600);
- `krew-hindsight.service`, enabled and started.

Each profile's config.yaml carries `memory: {memory_enabled: true, provider: hindsight, nudge_interval: 4,
flush_min_turns: 1}` (config/config.example.yaml). Memory ships EMPTY: no bank, no day files, no dreams come with
krew; the first night writes the first ones. `hindsight.env.example` stays as the list of keys.

`upgrade-0.10.2.sh` is the reference install's 0.10.1 -> 0.10.2 upgrade, kept as the pattern for the next one:
database backup first, offline install of verified wheels, roll back on any failure, a switch file to arm it
(`$HINDSIGHT_BASE/upgrade-0.10.2.on`), `DRY=1` to check every step without changing anything. It runs before each
start of the service and does nothing unless armed and on 0.10.1.

The memory data (day files, dreams, the Hindsight database) never ships.
