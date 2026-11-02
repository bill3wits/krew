"""What counts as the LAN for kill-search (cloned from Crissa's crissa_net, 2026-10-06).

The private address ranges plus localhost by default; an install edits them in $KREW_HOME/crissa-wall.json
(`lan_ranges`, `lan_hosts`; a key there replaces the default). A host NAME other than the listed ones is not
the LAN: it is resolved only when `resolve=True`, and then every address it resolves to must be inside.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from pathlib import Path

CONFIG_NAME = "crissa-wall.json"      # in $KREW_HOME; config/crissa-wall.example.json is the template
DEFAULTS = {"lan_ranges": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128"],
            "lan_hosts": ["localhost"]}


def krew_home() -> Path:
    """krew's settings folder: $KREW_HOME, else ~/.krew."""
    return Path(os.path.expanduser(os.environ.get("KREW_HOME") or "~/.krew"))


def config(home: Path | None = None) -> dict:
    out = dict(DEFAULTS)
    for p in ((home or krew_home()) / CONFIG_NAME,):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.update({k: v for k, v in d.items() if not k.startswith("_")})
    return out


def lan_networks(home: Path | None = None) -> list:
    nets = []
    for r in config(home).get("lan_ranges") or []:
        try:
            nets.append(ipaddress.ip_network(str(r), strict=False))
        except ValueError:
            continue                          # a bad row is ignored, never widens the LAN
    return nets


def is_lan(host: str, *, home: Path | None = None, resolve: bool = False) -> bool:
    """True when `host` (an address or a name) is inside the configured LAN."""
    h = str(host or "").strip().strip("[]").lower()
    if not h:
        return False
    cfg = config(home)
    if h in {str(x).lower() for x in cfg.get("lan_hosts") or []}:
        return True
    nets = lan_networks(home)
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        if not resolve:
            return False
        try:
            addrs = {i[4][0] for i in socket.getaddrinfo(h, None)}
        except OSError:
            return False
        return bool(addrs) and all(any(ipaddress.ip_address(a.split("%")[0]) in n for n in nets) for a in addrs)
    return any(addr in n for n in nets)
