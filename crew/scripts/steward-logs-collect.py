#!/usr/bin/env python3
"""Last-24h log extract for Steward logs job. No LLM. Cap 24 KiB."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import BUNDLE_CAP, COLLECT_ROOT, MS_DIR, HOME, today  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

# Portable product units. Site-local units load from ~/.krew/site-units.json → steward_log_units,
# or KREW_SITE_UNITS / KREW_STEWARD_LOG_UNITS. See config/site-units.example.json.
_PORTABLE_UNITS = [
    "audio8-asr.service",
    "audio8-tts.service",
    "ops.service",
    "crew.service",
    # the ONE host gateway serves every profile (the per-profile gateway units are retired)
    "hermes-gateway.service",
]


def _services_units() -> list[str]:
    """The one services list ($KREW_HOME/services.json, crew/lib/krew_services.py); [] without it."""
    try:
        import importlib.util
        lib = Path(__file__).resolve().parents[1] / "lib" / "krew_services.py"
        spec = importlib.util.spec_from_file_location("steward_logs_services", lib)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return list(mod.site_units(mod.load()).get("steward_log_units") or []) if mod.path().is_file() else []
    except Exception:  # noqa: BLE001
        return []


def _load_units() -> list[str]:
    raw = os.environ.get("KREW_STEWARD_LOG_UNITS")
    if raw:
        return list(json.loads(raw))
    sv = _services_units()
    if sv:
        return sv
    path = Path(os.environ.get("KREW_SITE_UNITS") or (HOME / ".krew/site-units.json"))
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            site = data.get("steward_log_units")
            if isinstance(site, list) and site:
                return list(site)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return list(_PORTABLE_UNITS)


UNITS = _load_units()


def jctl(unit: str) -> str:
    since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        out = subprocess.check_output(
            [
                "journalctl",
                "--user",
                "-u",
                unit,
                "--since",
                since,
                "-p",
                "warning",
                "--no-pager",
                "-n",
                "40",
            ],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        out = f"(journal fail {e})\n"
    return f"## {unit}\n{out.strip()}\n"


def tail_file(path: Path, n: int = 40) -> str:
    if not path.is_file():
        return f"## {path} missing\n"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError as e:
        return f"## {path} {e}\n"
    return f"## {path}\n" + "\n".join(lines) + "\n"


def main() -> int:
    dest = COLLECT_ROOT / today().isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = [f"# logs-collect {today().isoformat()}\n"]
    budget = BUNDLE_CAP
    for u in UNITS:
        piece = jctl(u)
        if len(piece) > 1800:
            piece = piece[:1800] + "\n_truncated_\n"
        if budget - len(piece) < 0:
            chunks.append("_cap reached before remaining units_\n")
            break
        chunks.append(piece)
        budget -= len(piece)
    jan = Path(os.path.expanduser(os.environ.get("KREW_METRICS_ROOT") or "~/.krew/metrics")) / f"janitor-{datetime.now():%Y%m%d}.jsonl"
    if jan.is_file():
        chunks.append(tail_file(jan, 8))
    doctor = MS_DIR / "hermes" / "doctor-LATEST.txt"
    chunks.append(tail_file(doctor, 40))
    sec = MS_DIR / "hermes" / "security-LATEST.json"
    chunks.append(tail_file(sec, 40))
    t0 = KH.hermes_home() / "security" / "tier0-hits.jsonl"
    chunks.append(tail_file(t0, 20))
    for prof in ("steward", "ada", "crissa", "scout"):
        chunks.append(tail_file(KH.hermes_home() / "profiles" / prof / "logs" / "errors.log", 20))
    body = "\n".join(chunks)
    if len(body.encode()) > BUNDLE_CAP:
        body = body[:BUNDLE_CAP] + "\n_truncated to bundle cap_\n"
    (dest / "logs.md").write_text(body, encoding="utf-8")
    print(dest / "logs.md", len(body.encode()), "B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
