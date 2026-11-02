#!/usr/bin/env python3
"""The efficient frontier of a worker's kit: what each tool costs on every call against how often
a session actually uses it. Read-only; it proposes, it does not write a config.

  scripts/crew-frontier.py                       # ada bash lila steward vera, last 7 days
  scripts/crew-frontier.py bash --days 14 --json --out <file>

Cost model (house measurements, see docs/checkpoints/lighter-prompts-20260930):
  eager      the schema rides every model call of every session (cold prefix ~0.09 s per 1K tokens;
             warm it is nearly free in time, but it takes context and invites a wrong pick)
  deferred   one to two extra round trips (~2 s each) each time a session first reaches for it
So a tool used in fewer than COLD_PCT of sessions is cheaper deferred, and one used in more than
HOT_PCT is cheaper sent. A core tool moves with `tools.tool_search.defer`; a plugin tool is deferred
by upstream whatever the config says, so keeping one needs `tools.tool_search.eager` (house patch 130).
The verbs a worker cannot finish without are never proposed for deferral.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HERMES = KH.hermes_home()
WORKERS = ("ada", "bash", "lila", "steward", "vera")
COLD_PCT, HOT_PCT, MIN_BYTES = 1.0, 3.0, 400
BRIDGE = ("tool_search", "tool_describe", "tool_call")
NEVER_DEFER = frozenset({
    "terminal", "read_file", "write_file", "patch", "search_files", "execute_code", "skill_view",
    "delegate_task", "kanban_show", "kanban_complete", "kanban_block", "kanban_comment", "kanban_heartbeat",
    "kanban_create",
})
# The board's own verbs are protocol, not convenience: a cold one is REPORTED, never proposed.
PROTOCOL_TOOLSETS = ("kanban", "kanban_lean")


def _tax():
    spec = importlib.util.spec_from_file_location("krew_tool_tax_cf", ROOT / "scripts" / "krew-tool-tax.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def usage(db: Path, since: float, source: str = "kanban") -> dict:
    """{sessions, calls, bridge_calls, per tool: sessions using it, calls} - a bridged call counts for
    the tool it invoked."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    by_session, by_call = collections.Counter(), collections.Counter()
    sessions = calls = bridge = 0
    try:
        for (sid,) in con.execute("select id from sessions where source=? and started_at>=?", (source, since)).fetchall():
            sessions += 1
            seen = set()
            for (tc,) in con.execute("select tool_calls from messages where session_id=? and role='assistant' "
                                     "and tool_calls is not null", (sid,)):
                try:
                    parsed = json.loads(tc)
                except (ValueError, TypeError):
                    continue
                for t in parsed if isinstance(parsed, list) else []:
                    fn = (t or {}).get("function") or {}
                    name = fn.get("name") or ""
                    calls += 1
                    if name in BRIDGE:
                        bridge += 1
                    if name == "tool_call":
                        try:
                            inner = json.loads(fn.get("arguments") or "{}").get("calls") or []
                        except (ValueError, TypeError, AttributeError):
                            inner = []
                        for x in inner if isinstance(inner, list) else []:
                            n = (x.get("name") if isinstance(x, dict) else "") or ""
                            if n:
                                by_call[n] += 1
                                seen.add(n)
                    elif name not in BRIDGE:
                        by_call[name] += 1
                        seen.add(name)
            for n in seen:
                by_session[n] += 1
    finally:
        con.close()
    return {"sessions": sessions, "calls": calls, "bridge_calls": bridge,
            "by_session": dict(by_session), "by_call": dict(by_call)}


def frontier(kit: dict, use: dict) -> dict:
    n = use["sessions"] or 1
    deferred = set(kit.get("deferred_names") or [])
    rows = []
    for r in kit["rows"]:
        pct = round(100.0 * use["by_session"].get(r["tool"], 0) / n, 1)
        rows.append({"tool": r["tool"], "toolset": r["toolset"], "bytes": r["bytes"], "core": bool(r.get("core")),
                     "state": "deferred" if r["tool"] in deferred else "eager", "sessions_pct": pct,
                     "calls": use["by_call"].get(r["tool"], 0)})
    every_cold = [r for r in rows if r["state"] == "eager" and r["sessions_pct"] < COLD_PCT and r["bytes"] >= MIN_BYTES
                  and r["tool"] not in NEVER_DEFER and r["core"]]
    cold = [r for r in every_cold if r["toolset"] not in PROTOCOL_TOOLSETS]
    protocol = [r for r in every_cold if r["toolset"] in PROTOCOL_TOOLSETS]
    hot = [r for r in rows if r["state"] == "deferred" and r["sessions_pct"] >= HOT_PCT]
    defer = (set(kit.get("defer") or kit.get("default_defer") or []) | {r["tool"] for r in cold}) \
        - {r["tool"] for r in hot if r["core"]}
    eager = set(kit.get("eager") or []) | {r["tool"] for r in hot if not r["core"]}
    sent_now = kit.get("sent_bytes") or 0
    saved = sum(r["bytes"] for r in cold)
    added = sum(r["bytes"] for r in hot)
    return {"sessions": use["sessions"], "calls": use["calls"], "bridge_calls": use["bridge_calls"],
            "sent_bytes": sent_now, "sent_tools": kit.get("sent_tools"), "cold_eager": cold, "hot_deferred": hot,
            "cold_protocol_verbs": protocol,
            "propose": {"defer": sorted(defer), "eager": sorted(eager),
                        "bytes_off_every_call": saved, "bytes_onto_every_call": added,
                        "bridged_calls_saved": sum(r["calls"] for r in hot)},
            "rows": sorted(rows, key=lambda r: (-r["sessions_pct"], -r["bytes"]))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profiles", nargs="*")
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    tax = _tax()
    report = {"made": time.strftime("%Y-%m-%dT%H:%M:%S"), "days": a.days, "cold_pct": COLD_PCT, "hot_pct": HOT_PCT,
              "profiles": {}}
    for prof in a.profiles or WORKERS:
        kit = tax.measure(prof, "cli")
        db = HERMES / "profiles" / prof / "state.db"
        if "rows" not in kit or not db.is_file():
            report["profiles"][prof] = {"error": kit.get("error") or f"no session store at {db}"}
            continue
        report["profiles"][prof] = frontier(kit, usage(db, time.time() - a.days * 86400))
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(report, indent=1))
        return 0
    for prof, d in report["profiles"].items():
        if "error" in d:
            print(f"{prof}: {d['error']}")
            continue
        p = d["propose"]
        print(f"\n{prof}: {d['sessions']} sessions, {d['calls']:,} tool calls, {d['bridge_calls']:,} through the bridge "
              f"({100 * d['bridge_calls'] / max(d['calls'], 1):.1f} %); {d['sent_tools']} tools / {d['sent_bytes']:,} B sent")
        print("  cold but sent (under %.0f %% of sessions): %s" % (COLD_PCT, ", ".join(
            f"{r['tool']} {r['bytes']:,}B {r['sessions_pct']}%" for r in sorted(d["cold_eager"], key=lambda r: -r["bytes"])) or "-"))
        print("  hot but deferred (over %.0f %% of sessions): %s" % (HOT_PCT, ", ".join(
            f"{r['tool']} {r['sessions_pct']}% {r['calls']} calls{'' if r['core'] else ' (plugin)'}" for r in d["hot_deferred"]) or "-"))
        print("  cold board verbs (reported, not proposed): %s" % (", ".join(
            f"{r['tool']} {r['bytes']:,}B {r['sessions_pct']}%" for r in d["cold_protocol_verbs"]) or "-"))
        print(f"  proposal: -{p['bytes_off_every_call']:,} B off every call, +{p['bytes_onto_every_call']:,} B on, "
              f"{p['bridged_calls_saved']} bridged calls a week become direct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
