# Hindsight (long memory for the dream night)

`crew/scripts/krew_memory.py dream-night` keeps the short and medium memory as files under `KREW_MEMORY_ROOT`
(default `~/.krew/memory`: `day/`, `rolling/3day.md`, `workspace/TODAY.md`, `dreams/`, `archive/`) and, when a
Hindsight API answers at `HINDSIGHT_API_URL` (default `http://127.0.0.1:8888`), retains the day into it,
consolidates and reflects. With no Hindsight the file side still runs and the report says Hindsight was not reached.

Two halves: the Hermes **plugin** (what every profile talks to) and the Hindsight **API** (the store). The plugin
step (`krew-install.py --memory`, or by hand):

```
hermes plugins install vectorize-io/hindsight          # the plugin, once per Hermes home
cp ~/.krew/crew/hindsight/config.example.json ~/.hermes/hindsight/config.json   # the plugin reads only this file
# then in each crew profile's config.yaml (config/config.example.yaml shows the block):
#   memory: {memory_enabled: true, provider: hindsight, nudge_interval: 4, flush_min_turns: 1}
```

Memory ships EMPTY: no bank, no day files, no dreams come with krew; the first night writes the first ones.

krew does not install the Hindsight API. The reference install runs:

- a venv at `~/.krew/hindsight/.venv` with `hindsight-api` (0.10.2) and a Postgres database for its store;
- `krew-hindsight.service` (opt-in: `krew-crew-install --units` writes it, never enables it);
- `hindsight.env` from `hindsight.env.example` (keys only; fill in your own values).

```
python3 -m venv ~/.krew/hindsight/.venv && ~/.krew/hindsight/.venv/bin/pip install hindsight-api==0.10.2
cp ~/.krew/crew/hindsight/hindsight.env.example ~/.krew/hindsight/hindsight.env   # edit it
systemctl --user enable --now krew-hindsight.service
```

`upgrade-0.10.2.sh` is the reference install's 0.10.1 -> 0.10.2 upgrade, kept as the pattern for the next one:
database backup first, offline install of verified wheels, roll back on any failure, a switch file to arm it
(`$HINDSIGHT_BASE/upgrade-0.10.2.on`), `DRY=1` to check every step without changing anything. It runs before each
start of the service and does nothing unless armed and on 0.10.1.

The memory data (day files, dreams, the Hindsight database) never ships.
