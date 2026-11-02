#!/usr/bin/env python3
"""krew: nightly-krew-index.py (lifted from the reference install; see crew/README.md)."""






















from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))

DEFAULT_SOURCES: list[str] = []          # [] = every source in krew.yaml docs.sources


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def settings() -> dict:
    """krew_index: from M&S's settings when M&S is installed, then KREW_INDEX_* from crew.yaml on top."""
    s: dict = {}
    try:
        import ms_settings
        s = ms_settings.get("krew_index", {}) or {}
    except Exception:  # noqa: BLE001 - no M&S: crew.yaml alone
        s = {}
    s = dict(s) if isinstance(s, dict) else {}
    for key, env in (("enabled", "KREW_NIGHTLY_INDEX"), ("sources", "KREW_INDEX_SOURCES"),
                     ("cpu_quota", "KREW_INDEX_CPU_QUOTA"), ("threads", "KREW_INDEX_THREADS"),
                     ("timeout_s", "KREW_INDEX_TIMEOUT_S")):
        if os.environ.get(env):
            s[key] = os.environ[env]
    return s


def enabled(s: dict) -> bool:
    env = os.environ.get("MS_NIGHTLY_KREW_INDEX")
    if env not in (None, ""):
        return _truthy(env)
    return _truthy(s.get("enabled", False))


def command(s: dict) -> tuple[list[str], dict]:
    sources = s.get("sources") or DEFAULT_SOURCES
    if isinstance(sources, str):
        sources = [x.strip() for x in sources.split(",") if x.strip()]
    quota = str(s.get("cpu_quota") or "300%")
    py = os.path.expanduser(os.environ.get("KREW_DOCS_INDEX_PYTHON") or "~/.krew/embedder-venv/bin/python")
    py = py if os.path.exists(py) else sys.executable
    script = os.path.expanduser(os.environ.get("KREW_DOCS_INDEX_SCRIPT") or str(KH.hermes_home() / "plugins/krew-docs/docs_index.py"))
    cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "-p", f"CPUQuota={quota}",
           py, script, "build"] + (["--source", ",".join(sources)] if sources else [])
    env = dict(os.environ)
    env.setdefault("KREW_INDEX_THREADS", str(int(s.get("threads") or 3)))  # docs.embedder_threads wins in krew.yaml
    return cmd, env


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", help="print the command, run nothing")
    a = ap.parse_args(argv)
    s = settings()
    if not enabled(s):
        print("krew-index: skipped (off - KREW_NIGHTLY_INDEX in crew.yaml, M&S krew_index.enabled, or MS_NIGHTLY_KREW_INDEX)")
        return 0
    cmd, env = command(s)
    print("krew-index: " + shlex.join(cmd), flush=True)
    if a.dry:
        return 0
    try:
        r = subprocess.run(cmd, env=env, timeout=int(s.get("timeout_s") or 3600))
    except subprocess.TimeoutExpired:
        print("krew-index: TIMEOUT")
        return 124
    print(f"krew-index: rc={r.returncode}")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
