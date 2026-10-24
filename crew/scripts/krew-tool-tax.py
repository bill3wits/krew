#!/usr/bin/env python3
"""The every-turn tool tax per profile, measured the way a session builds it.

`hermes -p <p> prompt-size` resolves the config's toolsets BEFORE plugin
discovery, so a plugin-declared toolset (krew-kanban-lean's `kanban_lean`)
reads as empty there and the profile looks 10 tools lighter than it is. A
real session (agent_init) discovers plugins first, then resolves. This does
the same, in a fresh process per profile (HERMES_HOME is read at import).

  python3 scripts/krew-tool-tax.py                # all crew profiles
  python3 scripts/krew-tool-tax.py bash zoey      # some
  python3 scripts/krew-tool-tax.py --json
  python3 scripts/krew-tool-tax.py zoey --platform cli          # her hands' kit
  python3 scripts/krew-tool-tax.py zoey --toolsets team,file    # a kit chosen at spawn (`-t`)

`tools`/`bytes` is every tool the list resolves to; `sent` is the array the model
is sent once Tool Search has put the deferrable ones behind its three-tool bridge,
and `agent.disabled_toolsets` is subtracted the way a session subtracts it.

Bytes = len(json.dumps(definition)) per tool, the definition as the model is sent it,
on the platform list that the room uses (api_server, else the top-level toolsets).
A tool whose own check fails here (no browser, no display) is left out, as a session
leaves it out; a worker's kanban verbs pass theirs only inside a claim, so they stay.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERMES = Path.home() / ".hermes"
VENV_PY = HERMES / "hermes-agent" / "venv" / "bin" / "python"   # pre-09-26 installs
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))


def hermes_python() -> str:
    """The interpreter the launcher itself runs (hermes ships its own since 09-26); the old venv
    only when the launcher cannot say."""
    try:
        import hermes_runtime
        return hermes_runtime.runtime_python() or str(VENV_PY)
    except Exception:  # noqa: BLE001
        return str(VENV_PY)
CREW = ("ada", "bash", "judge", "lila", "scout", "steward", "vera", "zoey")

_INNER = r'''
import json, os, sys
sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))
try:
    import hermes_bootstrap  # noqa: F401  (hermes's own dependencies; the launcher does the same)
except Exception:
    pass
import hermes_yaml as yaml
import logging; logging.disable(logging.CRITICAL)
import model_tools  # noqa: F401  (imports every built-in tool module into the registry)
from hermes_cli.plugins import discover_plugins
discover_plugins()
import toolsets
from tools.registry import registry
cfg = yaml.safe_load(open(os.path.join(os.environ["HERMES_HOME"], "config.yaml"))) or {}
plat_name = os.environ.get("TAX_PLATFORM") or "api_server"
override = [t.strip() for t in (os.environ.get("TAX_TOOLSETS") or "").split(",") if t.strip()]
plat = override or (cfg.get("platform_toolsets") or {}).get(plat_name) or cfg.get("toolsets") or []
disabled = list(((cfg.get("agent") or {}).get("disabled_toolsets")) or [])
try:  # the session's own selection: role toolsets out, disabled toolsets subtracted last
    names = sorted(model_tools._select_tool_names(list(plat), disabled, True))
except Exception:
    names = sorted(set(toolsets.resolve_multiple_toolsets(list(plat))))
# A session drops a tool whose own check fails (no browser, no display). A worker's kanban verbs
# pass theirs only inside a claim, so those are kept here by name.
try:
    passed = {(d.get("function") or {}).get("name"): d for d in registry.get_definitions(set(names), quiet=True)}
except Exception:
    passed = None
rows, defs, unavailable = [], [], []
for n in names:
    e = registry.get_entry(n)
    if e is None:
        continue
    if passed is not None and n not in passed and not n.startswith("kanban_"):
        unavailable.append(n)
        continue
    td = (passed or {}).get(n) or {"type": "function", "function": e.schema}
    rows.append({"tool": n, "toolset": e.toolset, "bytes": len(json.dumps(td))})
    defs.append(td)
by = {}
for r in rows:
    by.setdefault(r["toolset"], [0, 0]); by[r["toolset"]][0] += 1; by[r["toolset"]][1] += r["bytes"]
out = {"tools": len(rows), "bytes": sum(r["bytes"] for r in rows),
       "toolsets": {k: {"tools": v[0], "bytes": v[1]} for k, v in sorted(by.items(), key=lambda kv: -kv[1][1])},
       "top": sorted(rows, key=lambda r: -r["bytes"])[:5], "platform_list": list(plat),
       "platform": "override" if override else plat_name, "disabled_toolsets": disabled,
       "unavailable": unavailable, "rows": rows}
# What the model is actually sent: Tool Search swaps deferrable tools for a three-tool bridge.
sent, deferred, search = defs, [], "off"
try:
    from tools import tool_search as ts
    tcfg = ts.load_config()
    search = tcfg.enabled
    core = ts._core_tool_names()
    for r in rows:
        r["core"] = r["tool"] in core      # a core tool defers by config; a plugin tool needs `eager` to stay
    out["default_defer"] = sorted(ts._DEFAULT_DEFERRED_TOOLS)
    out["defer"] = sorted(tcfg.effective_defer_tools)
    out["eager"] = sorted(getattr(tcfg, "eager_tools", ()) or ())
    if tcfg.enabled != "off":
        try:
            ctx = int(model_tools._resolve_active_context_length() or 0)
        except Exception:
            ctx = 0
        asm = ts.assemble_tool_defs(defs, context_length=ctx or 32768, config=tcfg)
        if asm.activated:
            sent = asm.tool_defs
            kept = {(d.get("function") or {}).get("name") for d in sent}
            deferred = [r for r in rows if r["tool"] not in kept]
except Exception as exc:
    out["tool_search_error"] = str(exc)[:200]
out.update({"tool_search": search,
            "sent_tools": len(sent), "sent_bytes": sum(len(json.dumps(d)) for d in sent),
            "sent_names": sorted((d.get("function") or {}).get("name") or "?" for d in sent),
            "deferred_tools": len(deferred), "deferred_bytes": sum(r["bytes"] for r in deferred),
            "deferred_names": sorted(r["tool"] for r in deferred)})
print(json.dumps(out))
'''


def profile_home(profile: str) -> Path:
    """`default` is the passwd home itself; every other profile lives under profiles/."""
    return HERMES if profile == "default" else HERMES / "profiles" / profile


def measure(profile: str, platform: str = "api_server", toolsets: str = "") -> dict:
    """One kit, sized: `platform` picks the config's platform list, `toolsets` (a,b,c) overrides it
    the way `hermes -t` does. `sent_*` is the array after Tool Search; `tools`/`bytes` is every
    resolved tool, deferred or not."""
    env = dict(os.environ, HERMES_HOME=str(profile_home(profile)), TAX_PLATFORM=platform, TAX_TOOLSETS=toolsets)
    env.pop("HERMES_KANBAN_TASK", None)  # a worker's shell would append the kanban toolset here
    if not (profile_home(profile) / "config.yaml").is_file():
        return {"error": f"no such profile home: {profile_home(profile)}"}  # never bootstrap a foreign home
    r = subprocess.run([hermes_python(), "-I", "-c", _INNER], env=env, capture_output=True, text=True, timeout=180)
    out = (r.stdout or "").strip().splitlines()
    if not out:
        return {"error": (r.stderr or "no output")[-300:]}
    try:
        return json.loads(out[-1])
    except ValueError:
        return {"error": out[-1][:300]}


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    platform, toolsets, rest, it = "api_server", "", [], iter(argv)
    for a in it:
        if a == "--platform":
            platform = next(it, platform)
        elif a == "--toolsets":
            toolsets = next(it, "")
        elif not a.startswith("--"):
            rest.append(a)
    profiles = rest or list(CREW)
    report = {p: measure(p, platform, toolsets) for p in profiles}
    if as_json:
        print(json.dumps(report, indent=1))
        return 0
    for p, d in report.items():
        if "error" in d:
            print(f"{p:8} ERROR {d['error']}")
            continue
        top = ", ".join(f"{k} {v['tools']}/{v['bytes']:,}" for k, v in list(d["toolsets"].items())[:4])
        sent = (f"sent {d['sent_tools']:3} / {d['sent_bytes']:7,} B  deferred {d['deferred_tools']:2}  "
                if "sent_tools" in d else "")
        print(f"{p:8} {d['tools']:3} tools {d['bytes']:7,} B   {sent}{top}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
