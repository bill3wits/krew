---
name: ops-toks
description: >
  Measure ops/crew tok/s from Control Panel :8086 (Engine journal line)
  and record it for Crissa's report. Bash only. Never Crissa.
version: 1.0.0
author: krew
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [bash, ops, kpi, vllm]
---

# Ops tok/s — Bash measures, Crissa reports

Crissa has no terminal and no this skill. She puts **Crew** (`:8086`) on the
glass and reads `ops_tok_s` from the efficiency / team-daily page.

You run the number:

```bash
python3 ~/.krew/crew/scripts/ops-toks.py --lane ops
python3 ~/.krew/crew/scripts/ops-toks.py --lane ops --record
```

That is the same Engine line as CP `:8086` / `journalctl --user -u ops`
(`Avg generation throughput`). **Do not record idle** (`Running: 0`) unless
`--idle-ok` — a 5 tok/s leftover interval is not a keep-sample.

Quiet-lane load test (hitches her mouth; keep A/B only):

```bash
python3 ~/.krew/crew/scripts/ops-toks.py --lane ops --probe --record
```

That wraps `bench/nvfp4-speed/speed_probe.py`. Not the morning brief.

KPI id: `ops_tok_s` (crew: `crew_tok_s` if registered). Charts:
`ops-efficiency.html`. Crystal: command + the printed `gen=` line.

Ada files you this card. Do not bounce ops/crew.
