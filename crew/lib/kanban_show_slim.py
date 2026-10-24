"""krew: kanban_show_slim.py (lifted from the reference install; see crew/README.md)."""



















from __future__ import annotations

import json
import re

EVENTS_KEPT = 8
RUNS_KEPT = 3
FIELD_CAP = 300
POINTER = "(whole text is in worker_context)"


def _cap(value, limit: int = FIELD_CAP):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… [{len(value) - limit} chars more]"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
        return value if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} chars more]"
    return value


def _whole_text_in(text: str, context: str) -> bool:
    text = (text or "").strip()
    return bool(text) and text in context


_RECENT_WORK = re.compile(r"(?ms)^## Recent work by @[^\n]*\n.*?(?=^## |\Z)")


def slim(result: str, own_task: str | None = None) -> str | None:
    """The slimmed JSON string, or None when `result` should be left exactly as it is.
    `own_task` is the card the caller is working (HERMES_KANBAN_TASK); None = a reader of any card."""
    try:
        d = json.loads(result)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("worker_context"), str) or not d["worker_context"].strip():
        return None
    ctx = d["worker_context"]
    out = dict(d)
    folded: dict = {}
    shown = (d.get("task") or {}).get("id") if isinstance(d.get("task"), dict) else None
    if not (own_task and shown and own_task == shown):
        lean_ctx = _RECENT_WORK.sub("", ctx)
        if len(lean_ctx) < len(ctx):
            folded["recent_work_section_chars"] = len(ctx) - len(lean_ctx)
            out["worker_context"] = lean_ctx.rstrip() + "\n"

    task = d.get("task")
    if isinstance(task, dict) and _whole_text_in(task.get("body") or "", ctx):
        out["task"] = {**task, "body": POINTER}
        folded["body"] = len(task["body"])

    comments = d.get("comments")
    if isinstance(comments, list) and comments:
        kept = [c for c in comments if not (isinstance(c, dict) and _whole_text_in(c.get("body") or "", ctx))]
        if len(kept) != len(comments):
            out["comments"] = kept
            folded["comments_in_context"] = len(comments) - len(kept)

    events = d.get("events")
    if isinstance(events, list) and events:
        tail = events[-EVENTS_KEPT:]
        out["events"] = [({**e, "payload": _cap(e.get("payload"))} if isinstance(e, dict) else e) for e in tail]
        if len(events) > len(tail):
            folded["older_events"] = len(events) - len(tail)

    runs = d.get("runs")
    if isinstance(runs, list) and runs:
        tail = runs[-RUNS_KEPT:]
        out["runs"] = [({k: _cap(v) for k, v in r.items()} if isinstance(r, dict) else r) for r in tail]
        if len(runs) > len(tail):
            folded["older_runs"] = len(runs) - len(tail)

    if folded:
        out["folded_into_worker_context"] = folded
    text = json.dumps(out, ensure_ascii=False)
    return text if len(text) < len(result) else None
