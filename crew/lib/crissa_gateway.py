"""Crissa gateway live? When she is down, Lila hosts the Wall.

Composer posts /v1/play to :8100. Occupancy Render/Beast/Off parks
``hermes-crissa-gateway``. the owner 2026-09-14: Lila is host ALWAYS then, and
inherits Auth / Secure / Listening from the same conductor (visit_lamp,
comms, presence). Kill switch ``KREW_LILA_HOST_WHEN_CRISSA_DOWN=0``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives

MODE_FILE = Path(
    os.environ.get("KREW_GPU_MODE", str(KH.hermes_home() / "krew-gpu-mode"))
)
UNIT = os.environ.get("KREW_CRISSA_GATEWAY_UNIT", "hermes-crissa-gateway.service")
PARK_MODES = frozenset({"render", "beast", "off"})


def occupancy_mode(path: Path | None = None) -> str:
    try:
        raw = (path or MODE_FILE).read_text(encoding="utf-8").strip().split()
    except OSError:
        return ""
    return (raw[0] if raw else "").strip().lower()


def crissa_gateway_up(
    *,
    occupancy: str | None = None,
    systemd_active: bool | None = None,
) -> bool:
    """False → Lila hosts. Occupancy park wins; else systemd is-active."""
    if os.environ.get("KREW_LILA_HOST_WHEN_CRISSA_DOWN", "1").strip().lower() in (
        "0", "false", "no", "off",
    ):
        return True
    mode = occupancy if occupancy is not None else occupancy_mode()
    if mode in PARK_MODES:
        return False
    if systemd_active is not None:
        return bool(systemd_active)
    # One host gateway for every profile (KREW_GATEWAY_TOPOLOGY=host): she is up when the host
    # is active AND serves her — a parked Crissa is down with the host unit perfectly healthy.
    # Under the default `units` nothing below changes.
    try:
        import hermes_topology as _topo

        if _topo.is_host():
            return _topo.profile_up("crissa", timeout=1.5, unknown=True)
    except Exception:  # noqa: BLE001 - the wall never falls over a liveness read
        pass
    try:
        p = subprocess.run(
            ["systemctl", "--user", "is-active", UNIT],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        return p.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return True


def lila_host_key(key: str | None) -> bool:
    return str(key or "").startswith("lila-")


# Default when she hosts: working pose on Crissa's desk hub (`down`), same
# as Crissa's `work`. Listening/comms inherit uses lila-reg-listen.
HOST_WORK_KEY = "lila-work"
HOST_LISTEN_KEY = "lila-reg-listen"
HOST_ENTER_KEY = "lila-listen-enter"


def lila_host_default(*, comms: bool = False, listening: bool = False) -> str:
    if comms or listening:
        return HOST_LISTEN_KEY
    return HOST_WORK_KEY


def crissa_idle_steal(key: str | None) -> bool:
    """Bag rest only. Harvest / speak-phoneme / Tune films must play.

    Composer Say and tiles drill POST the selected kit key; remapping every
    Crissa key to lila-work put Lila on the Live stage (the owner 2026-09-14).
    """
    k = str(key or "")
    if k in ("work",):
        return True
    if k.startswith("flourish-") or k.startswith("daydream-"):
        return True
    return False
