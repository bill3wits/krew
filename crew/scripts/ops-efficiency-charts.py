#!/usr/bin/env python3
"""Daily dated ops-efficiency HTML + living symlink.

Archive-by-filename-date requires ops-efficiency-YYYY-MM-DD.html.

Charts: one time series per tracked KPI, with a vertical marker at the
date that KPI was added. Below: registry of tracked changes, then
action items (Steward closes ops-action: cards).
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import INBOX, MS_DIR, today  # noqa: E402

CSS = """
body{font-family:ui-sans-serif,system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 16px;color:#1a1a1a;line-height:1.45}
h1{font-size:1.6rem;margin-bottom:.25rem}
h2{font-size:1.15rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.25rem}
.muted{color:#555}
.chart{background:#fafafa;border:1px solid #ddd;padding:12px 12px 4px;margin:1rem 0}
.caption{font-size:.9rem;color:#444}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top}
th{font-size:.8rem;text-transform:uppercase;letter-spacing:.04em;color:#555}
code{font-size:.9em}
.note{background:#fff8e5;border:1px solid #ead7a0;padding:8px 12px;margin:1rem 0}
"""


def _day(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _chart(
    points: list[tuple[float, float]],
    added_ts: float | None,
    *,
    w: int = 720,
    h: int = 180,
) -> str:
    if not points:
        return '<p class="muted">insufficient history — do not stop-tracking</p>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xmin, xmax = min(xs), max(xs)
    if added_ts is not None:
        xmin = min(xmin, added_ts)
        xmax = max(xmax, added_ts)
    if xmin == xmax:
        xmax = xmin + 86400.0
    ymin, ymax = min(ys), max(ys)
    if ymin == ymax:
        ymin -= 1.0
        ymax += 1.0
    pad_l, pad_r, pad_t, pad_b = 52, 18, 22, 36
    inner_w = w - pad_l - pad_r
    inner_h = h - pad_t - pad_b

    def xy(ts: float, val: float) -> tuple[float, float]:
        x = pad_l + (ts - xmin) / (xmax - xmin) * inner_w
        y = pad_t + (ymax - val) / (ymax - ymin) * inner_h
        return x, y

    pts = " ".join(f"{xy(t, v)[0]:.1f},{xy(t, v)[1]:.1f}" for t, v in points)
    parts = [
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#fafafa"/>',
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{h - pad_b}" stroke="#ccc"/>',
        f'<line x1="{pad_l}" y1="{h - pad_b}" x2="{w - pad_r}" y2="{h - pad_b}" stroke="#ccc"/>',
        f'<text x="8" y="{pad_t + 4}" font-size="11" fill="#555">{ymax:g}</text>',
        f'<text x="8" y="{h - pad_b}" font-size="11" fill="#555">{ymin:g}</text>',
        f'<text x="{pad_l}" y="{h - 10}" font-size="11" fill="#555">{_day(xmin)}</text>',
        f'<text x="{w - pad_r - 70}" y="{h - 10}" font-size="11" fill="#555">{_day(xmax)}</text>',
        f'<polyline fill="none" stroke="#222" stroke-width="1.6" points="{pts}"/>',
    ]
    for t, v in points:
        x, y = xy(t, v)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="#222"/>')
    if added_ts is not None:
        ax, _ay = xy(added_ts, ymin)
        label = f"added {_day(added_ts)}"
        parts.append(
            f'<line class="added" x1="{ax:.1f}" y1="{pad_t}" x2="{ax:.1f}" '
            f'y2="{h - pad_b}" stroke="#c45" stroke-dasharray="4 3"/>'
        )
        parts.append(
            f'<text x="{min(ax + 4, w - 120):.1f}" y="{pad_t - 6}" '
            f'font-size="11" fill="#c45">{html.escape(label)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _added_for(kpi: dict) -> tuple[str, float | None]:
    if kpi.get("added_at"):
        day = str(kpi["added_at"])
        hist = kpi.get("history") or []
        ts = None
        if hist and isinstance(hist[0], dict):
            try:
                ts = float(hist[0].get("ts"))
            except (TypeError, ValueError):
                ts = None
        return day, ts
    hist = kpi.get("history") or []
    if hist and isinstance(hist[0], dict) and hist[0].get("ts") is not None:
        ts = float(hist[0]["ts"])
        return _day(ts), ts
    return "", None


def _points_from_kpi(kpi: dict) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for row in kpi.get("history") or []:
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row["ts"])
            val = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append((ts, val))
    return out


def _points_from_snaps(snaps: list[dict], field: str) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s in snaps:
        try:
            ts = float(s.get("unix") or 0)
            val = float(s.get(field))
        except (TypeError, ValueError):
            continue
        if ts:
            out.append((ts, val))
    return out


def _esc(v) -> str:
    if v is None or v == "":
        return "—"
    return html.escape(str(v))


def render(inbox: Path, ms_dir: Path, day: str | None = None) -> Path:
    inbox.mkdir(parents=True, exist_ok=True)
    snaps = _load_jsonl(ms_dir / "kanban-snapshots.jsonl")[-400:]
    kpis_path = ms_dir / "improvements" / "kpis.json"
    kpis: dict = {}
    if kpis_path.is_file():
        try:
            kpis = json.loads(kpis_path.read_text(encoding="utf-8")).get("kpis") or {}
        except json.JSONDecodeError:
            kpis = {}
    actions = _load_jsonl(ms_dir / "improvements" / "actions.jsonl")
    events = _load_jsonl(ms_dir / "improvements" / "events.jsonl")
    day = day or today().isoformat()

    html_parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        f"<title>Ops efficiency {html.escape(day)}</title>",
        f"<style>{CSS}</style></head><body>",
        f"<h1>Ops efficiency {html.escape(day)}</h1>",
        '<p class="muted">Daily charts. Ada writes the Sunday narrative. '
        "Steward files and closes <code>ops-action:</code> cards. "
        "No KPI history ≠ a clean health report.</p>",
        '<p class="note">Insufficient history (fewer than 3 points) is '
        "<strong>not</strong> stop-tracking. Thin week with enough history "
        "and no movement: <code>no actions</code> is a pass.</p>",
        "<h2>Time charts</h2>",
    ]

    # Prefer KPI history; fall back to snapshot series for open/blocked.
    fallback = {
        "kanban_open": _points_from_snaps(snaps, "open"),
        "kanban_blocked": _points_from_snaps(snaps, "blocked"),
        "kanban_oldest_ready_sec": _points_from_snaps(snaps, "oldest_ready_age_seconds"),
    }
    names = {
        "kanban_open": "Kanban open",
        "kanban_blocked": "Kanban blocked",
        "kanban_oldest_ready_sec": "Oldest ready card (sec)",
    }
    ids = list(dict.fromkeys([*kpis.keys(), *fallback.keys()]))
    for kid in ids:
        kpi = kpis.get(kid) or {}
        title = kpi.get("name") or names.get(kid) or kid
        pts = _points_from_kpi(kpi) or fallback.get(kid) or []
        added_day, added_ts = _added_for(kpi)
        if not added_ts and pts:
            added_ts = pts[0][0]
            added_day = added_day or _day(added_ts)
        html_parts.append('<div class="chart">')
        html_parts.append(f"<h3>{html.escape(str(title))} <code>{html.escape(kid)}</code></h3>")
        html_parts.append(_chart(pts, added_ts))
        n = len(pts)
        last = f"{pts[-1][1]:g}" if pts else "—"
        hist_note = "insufficient history" if n < 3 else f"n={n}"
        html_parts.append(
            f'<p class="caption">{html.escape(hist_note)} · last={html.escape(last)} · '
            f"added {html.escape(added_day or 'not yet')}</p>"
        )
        html_parts.append("</div>")

    html_parts.append("<h2>Tracked changes</h2>")
    html_parts.append(
        "<p class=\"muted\">Date added is the first sample (or "
        "<code>added_at</code>). Last delta is newest minus previous sample.</p>"
    )
    html_parts.append(
        "<table><thead><tr><th>KPI</th><th>Added</th><th>Sample</th><th>Points</th>"
        "<th>Current</th><th>Baseline</th><th>Target</th><th>Last delta</th></tr></thead><tbody>"
    )
    if not kpis:
        html_parts.append('<tr><td colspan="8" class="muted">no KPIs registered</td></tr>')
    for kid, kpi in kpis.items():
        pts = _points_from_kpi(kpi)
        added_day, _ts = _added_for(kpi)
        delta = "—"
        if len(pts) >= 2:
            delta = f"{pts[-1][1] - pts[-2][1]:g}"
        elif len(pts) == 1:
            delta = "first sample"
        n = len(pts)
        n_cell = f"{n} (insufficient)" if n < 3 else str(n)
        html_parts.append(
            "<tr>"
            f"<td><code>{html.escape(kid)}</code><br>{_esc(kpi.get('name'))}</td>"
            f"<td>{_esc(added_day or 'no history yet')}</td>"
            f"<td>{_esc(kpi.get('sample') or 'snapshot')}</td>"
            f"<td>{html.escape(n_cell)}</td>"
            f"<td>{_esc(kpi.get('current'))}</td>"
            f"<td>{_esc(kpi.get('baseline'))}</td>"
            f"<td>{_esc(kpi.get('target'))}</td>"
            f"<td>{html.escape(delta)}</td>"
            "</tr>"
        )
    html_parts.append("</tbody></table>")

    started = [e for e in events if e.get("kind") == "measure" and e.get("old") is None]
    if started:
        html_parts.append("<h3>Tracking started</h3><ul>")
        seen: set[str] = set()
        for e in started:
            kid = str(e.get("kpi_id") or "")
            if not kid or kid in seen:
                continue
            seen.add(kid)
            html_parts.append(
                f"<li><code>{html.escape(kid)}</code> added {_esc(_day(e.get('ts')))} "
                f"— {_esc(e.get('note'))}</li>"
            )
        html_parts.append("</ul>")

    html_parts.append("<h2>Action items</h2>")
    html_parts.append(
        '<p class="muted">Team reads this report. Steward files '
        "<code>ops-action:</code> cards and closes the loop. Ada does not implement. "
        "Verbs: <code>keep-baseline</code>, <code>stop-tracking</code>, "
        "<code>investigate</code>.</p>"
    )
    open_a = [a for a in actions if a.get("status") == "open"]
    closed_a = [a for a in actions if a.get("status") == "closed"]
    html_parts.append("<h3>Open</h3><ul>")
    if not open_a:
        html_parts.append(
            "<li>none — Sunday Ada may add <code>ops-action:</code> lines; "
            "a thin week with enough history is a pass.</li>"
        )
    for a in open_a[-40:]:
        html_parts.append(
            f"<li><code>{_esc(a.get('ops_action_id'))}</code> {_esc(a.get('title'))}</li>"
        )
    html_parts.append("</ul><h3>Closed</h3><ul>")
    if not closed_a:
        html_parts.append("<li>none</li>")
    for a in closed_a[-20:]:
        html_parts.append(
            f"<li><code>{_esc(a.get('ops_action_id'))}</code> {_esc(a.get('title'))}</li>"
        )
    html_parts.append("</ul></body></html>\n")

    payload_kpis = {}
    for kid in ids:
        kpi = kpis.get(kid) or {}
        pts = _points_from_kpi(kpi) or fallback.get(kid) or []
        added_day, _ts = _added_for(kpi)
        n = len(pts)
        payload_kpis[kid] = {
            "name": kpi.get("name") or names.get(kid) or kid,
            "current": kpi.get("current"),
            "history_n": n,
            "status": "insufficient history" if n < 3 else "ok",
            "added": added_day or None,
        }
    last_ts = snaps[-1].get("ts") if snaps else None
    payload = {
        "date": day,
        "charts": f"ops-efficiency-{day}.html",
        "kpis": payload_kpis,
        "open_ops_action_ids": [
            a.get("ops_action_id") for a in open_a if a.get("ops_action_id")
        ],
        "snapshots_n": len(snaps),
        "last_snapshot_ts": last_ts,
        "law": (
            "No KPI history ≠ healthy. n<3 → insufficient history; "
            "do not stop-tracking."
        ),
    }

    dated = inbox / f"ops-efficiency-{day}.html"
    dated.write_text("".join(html_parts), encoding="utf-8")
    (inbox / f"ops-efficiency-{day}.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    living = inbox / "ops-efficiency.html"
    try:
        if living.is_symlink() or living.exists():
            living.unlink()
        living.symlink_to(dated.name)
    except OSError:
        living.write_text(dated.read_text(encoding="utf-8"), encoding="utf-8")
    return dated


def main() -> int:
    path = render(INBOX, MS_DIR)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
