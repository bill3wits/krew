#!/usr/bin/env python3
"""krew: self-review.py (lifted from the reference install; see crew/README.md)."""
















from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import idea_ledger  # noqa: E402
import self_claims as sc  # noqa: E402

IMPROVE = Path(os.environ.get("KREW_IMPROVE_DIR", (os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory") + "/ms/improvements")))
TRAIL = Path(os.environ.get("KREW_MS_TRAIL", (os.path.expanduser(os.environ.get("KREW_MEMORY_ROOT") or "~/.krew/memory") + "/ms/reports/ms-trail-LATEST.json")))
INBOX = Path(os.environ.get("KREW_INBOX", str(Path(os.path.expanduser(os.environ.get("KREW_DELIVERABLES") or "~/Desktop/krew-deliverables")))))


def pct(v) -> str:
    return "—" if v is None else f"{round(100 * v)} %"


def build(now: dt.datetime, *, healing=None) -> dict:
    week = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
    since = (now - dt.timedelta(days=7)).date().isoformat()
    m = sc.maintaining(TRAIL)
    h = healing(now) if healing else sc.healing(now, kpis_path=IMPROVE / "kpis.json")
    i = sc.improving(IMPROVE / "outcomes.json", now)
    rows = [r for r in idea_ledger.load(IMPROVE / "ideas.jsonl") if str(r.get("day") or "") >= since]
    funnel: dict[str, set] = {}
    for r in rows:
        funnel.setdefault(str(r.get("event")), set()).add(r.get("key"))
    state = idea_ledger.fold(idea_ledger.load(IMPROVE / "ideas.jsonl"))
    repeats = sorted((s for s in state.values() if len(s.get("debated_days") or []) >= 2
                      and max(s.get("debated_days") or [""]) >= since and not s.get("agreed_days")),
                     key=lambda s: -len(s["debated_days"]))
    claims = {"week": week, "generated_at": now.isoformat(timespec="seconds"),
              "maintaining": {k: m.get(k) for k in ("value", "landed", "attempted", "rolled", "day", "source")},
              "healing": {k: h.get(k) for k in ("value", "cleared", "detected", "days")} | {"source": "lib/self_claims.healing (named actuator logs)"},
              "improving": {k: i.get(k) for k in ("value", "better", "shipped", "unmeasured", "days", "source")}}
    return {"week": week, "since": since, "claims": claims, "maintaining": m, "healing": h, "improving": i,
            "funnel": {k: len(v) for k, v in sorted(funnel.items())},
            "repeats": [{"key": s["key"], "nights": len(s["debated_days"]), "best_sum": s.get("best_sum")} for s in repeats[:12]]}


def trend(history: list[dict], cur: dict) -> list[dict]:
    rows = [h for h in history if h.get("week") != cur["week"]][-3:] + [cur]
    return [{"week": r["week"], "maintaining": r["maintaining"].get("value"), "healing": r["healing"].get("value"),
             "improving": r["improving"].get("value")} for r in rows]


def render(r: dict, tr: list[dict]) -> str:
    m, h, i = r["maintaining"], r["healing"], r["improving"]
    out = [f"# Self-review — {r['week']}", "",
           "_What the krew did for itself this week, measured. Each number has its own denominator; "
           "a zero denominator says so instead of a percentage._", "",
           "## The three claims", "",
           f"- **Self-maintaining:** {m['text']} ({pct(m.get('value'))})",
           f"- **Self-healing:** {h['text']} ({pct(h.get('value'))})"]
    for a in h.get("actuators") or []:
        out.append(f"  - {a['actuator']}: {a['cleared']} of {a['detected']} — {a['proxy']}")
    br = h.get("breaker") or {}
    out.append(f"  - breaker: {br.get('mode')}, {br.get('trips')} trip(s); false-alarm label {br.get('false_alarm_label')}")
    t = h.get("touches") or {}
    if t:
        out.append(f"  - user touches (residual, down is better): now {t.get('now')}; published baseline median {t.get('baseline_median')}")
    out += [f"- **Self-improving:** {i['text']} ({pct(i.get('value'))})", "",
            "## Shipped changes (landed with a joined commit, 30 days)", ""]
    for it in i.get("items") or []:
        conf = it.get("confounded")
        out.append(f"- {str(it.get('item'))[:90]} — {it.get('sha')} ({it.get('review')}); {it.get('kpi_id')}: "
                   f"{it.get('measure') or 'not measured yet'}" + (f"; confounded: {'; '.join(map(str, conf))[:120]}" if conf else ""))
    if not i.get("items"):
        out.append("- nothing shipped")
    out += ["", f"## Idea funnel since {r['since']} (distinct ideas per event, from the ledger)", ""]
    out += [f"- {k}: {v}" for k, v in r["funnel"].items()] or ["- nothing recorded"]
    out += ["", "## Repeats (debated 2+ nights, never agreed — the cooldown list)", ""]
    out += [f"- {x['key'][5:] if x['key'].startswith('repo:') else x['key']}: {x['nights']} nights, best sum {x['best_sum']}"
            for x in r["repeats"]] or ["- none"]
    out += ["", "## 4-week trend", "", "| week | maintaining | healing | improving |", "|---|---|---|---|"]
    out += [f"| {w['week']} | {pct(w['maintaining'])} | {pct(w['healing'])} | {pct(w['improving'])} |" for w in tr]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    if os.environ.get("KREW_SELF_REVIEW", "1").strip().lower() in ("0", "false", "no", "off"):
        print("self-review off (KREW_SELF_REVIEW=0)")
        return 0
    now = dt.datetime.now()
    r = build(now)
    hist_p = IMPROVE / "self-claims-history.jsonl"
    try:
        history = [json.loads(l) for l in hist_p.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        history = []
    tr = trend(history, r["claims"])
    md = render(r, tr)
    if a.dry:
        print(md)
        return 0
    INBOX.mkdir(parents=True, exist_ok=True)
    (INBOX / f"self-review-{r['week']}.md").write_text(md, encoding="utf-8")
    (INBOX / f"self-review-{r['week']}.json").write_text(json.dumps(r | {"trend": tr}, indent=2, default=str) + "\n")
    tmp = IMPROVE / "self-claims.json.tmp"
    tmp.write_text(json.dumps(r["claims"], indent=2) + "\n")
    tmp.replace(IMPROVE / "self-claims.json")
    history = [h for h in history if h.get("week") != r["week"]] + [r["claims"]]
    hist_p.write_text("".join(json.dumps(h) + "\n" for h in history))
    print(f"self-review: {INBOX / ('self-review-' + r['week'] + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
