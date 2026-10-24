#!/usr/bin/env python3
"""Prompt weight per turn, per session, per profile - the yardstick for every kit change.

  scripts/prompt-weight.py zoey --session freeair-1790769122      # one session, turn by turn
  scripts/prompt-weight.py zoey --since "2026-09-30 07:58" --source api_server
  scripts/prompt-weight.py --summary --since "2026-09-29"         # every profile, medians per source
  scripts/prompt-weight.py zoey --summary --json --out docs/checkpoints/<landing>/before.json

Characters come from the session store (opened read-only); prompt tokens from agent.log. The
tool array is not stored with a session, so `tools` is the profile's kit sized by
scripts/krew-tool-tax.py for the session's platform (`--no-kits` leaves it out; a run started
with `-t` wears a smaller kit than its platform's, so treat `tools` on a oneshot as the ceiling).
Nothing here writes anywhere but `--out`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import prompt_weight as pw  # noqa: E402

PROFILES = ("zoey", "ada", "bash", "lila", "steward", "vera", "scout", "judge", "default")


def _when(text: str | None) -> float | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"cannot read a time from {text!r} (YYYY-MM-DD[ HH:MM[:SS]])")


def _tool_tax():
    spec = importlib.util.spec_from_file_location("krew_tool_tax", ROOT / "scripts" / "krew-tool-tax.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def kit_chars(profile: str, cache: dict, tax) -> dict:
    """{platform: chars of the tool array as sent}; one measurement per (profile, platform)."""
    if profile not in cache:
        cache[profile] = {}
        for platform in ("api_server", "cli", "cron"):
            d = tax.measure(profile, platform)
            cache[profile][platform] = d.get("sent_bytes")
    return cache[profile]


def _n(v) -> str:
    return "-" if v is None else f"{int(v):,}"


def print_session(s: dict) -> None:
    at = dt.datetime.fromtimestamp(s["started"]).strftime("%m-%d %H:%M:%S") if s["started"] else "?"
    print(f"\n{s['session']}  {s['source']}  {at}  system {_n(s['system'])}  tools {_n(s['tools'])}  "
          f"calls {_n(s['api_calls'])}  input tokens {_n(s['input_tokens'])}")
    print("  turn  time      said   recall  bullets  carried   results   history   request   first    peak  calls   sec")
    for t in s["turns"]:
        tm = dt.datetime.fromtimestamp(t["at"]).strftime("%H:%M:%S") if t["at"] else "?"
        print(f"  {t['turn']:>4}  {tm}  {t['said']:>5,}  {t['recall']:>7,}  {t['bullets']:>7}  {t['carried']:>7,}  "
              f"{t['results']:>8,}  {t['history']:>8,}  {t['request']:>8,}  {_n(t.get('first_tokens')):>6}  "
              f"{_n(t.get('peak_tokens')):>6}  {_n(t.get('calls')):>5}  {t.get('seconds', '-'):>5}")


def print_summary(profile: str, summary: dict) -> None:
    for source, d in summary.items():
        print(f"{profile:8} {source:11} sessions {d['sessions']:>4}  turns {d['turns']:>5}  system {_n(d['system']):>7}  "
              f"tools {_n(d['tools']):>7}  first {_n(d['first_tokens']):>6} tok  peak {_n(d['peak_tokens']):>6} tok  "
              f"recall/later turn {_n(d['recall_per_later_turn']):>6}  turns with recall {d['turns_with_recall']:>4}  "
              f"results/turn {_n(d['results_per_turn']):>7}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profiles", nargs="*")
    ap.add_argument("--session", action="append", default=[])
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--source", help="api_server | oneshot | cron | cli | kanban")
    ap.add_argument("--min-turns", type=int, default=1)
    ap.add_argument("--last", type=int, default=0, help="only the newest N sessions per profile")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--no-kits", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    profiles = a.profiles or (["zoey"] if a.session else list(PROFILES))
    since, until = _when(a.since), _when(a.until)
    tax, kits = (None if a.no_kits else _tool_tax()), {}
    report: dict = {"made": dt.datetime.now().isoformat(timespec="seconds"),
                    "window": {"since": a.since, "until": a.until, "source": a.source}, "profiles": {}}
    for profile in profiles:
        home = pw.profile_home(profile)
        db = home / "state.db"
        if not db.is_file():
            report["profiles"][profile] = {"error": f"no session store at {db}"}
            continue
        con = pw.open_ro(db)
        try:
            ids = a.session or pw.sessions_between(con, since, until, a.source)
            if a.last:
                ids = ids[-a.last:]
            calls = pw.log_calls(home, set(ids))
            kit = kit_chars(profile, kits, tax) if tax else {}
            sessions = []
            for sid in ids:
                src = (con.execute("select source from sessions where id=?", (sid,)).fetchone() or [None])[0]
                tools = kit.get(pw.SOURCE_PLATFORM.get(src or "", "cli")) if kit else None
                s = pw.weigh_session(con, sid, calls.get(sid), tools)
                if s and len(s["turns"]) >= a.min_turns:
                    sessions.append(s)
        finally:
            con.close()
        report["profiles"][profile] = {"kit_chars": kit, "summary": pw.summarize(sessions),
                                       "sessions": [] if a.summary else sessions}
        if a.json:
            continue
        if a.summary:
            print_summary(profile, report["profiles"][profile]["summary"])
        else:
            for s in sessions:
                print_session(s)
    if a.json:
        print(json.dumps(report, indent=1))
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(report, indent=1), encoding="utf-8")
        tmp.replace(out)
        print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
