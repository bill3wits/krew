"""kanban_show says everything twice; say it once (the owner 2026-09-30, "slim kanban_show results").

hermes's kanban_show returns the card as structured fields (`task.body`, every `comments[]` body, every
`events[]` payload, every `runs[]` row) AND as `worker_context`, the prompt-ready text built from the same
rows (body, prior attempts, parent results, the comment thread). Measured on a week of worker sessions:
11-13K chars a call (the ones the store kept whole; 40 % were larger still), 39.8 M chars on one coder - 41 %
of everything its tools returned - and `worker_context` alone is 43 % of it, a second copy.

`slim(result)` keeps `worker_context` whole and drops only what it already says:
  - `task.body`      replaced by a pointer when the whole body is in the context;
  - `comments`       a comment is dropped when its whole body is in the context; one the context
                     truncated (over 2K) or left out (older than the newest 30) is KEPT in full;
  - `events`         the newest EVENTS_KEPT, payload capped (a `commented` payload is the comment again);
  - `runs`           the newest RUNS_KEPT, fields capped (the context carries the attempts in full).
  - `## Recent work by @<assignee>`   left out when the card shown is NOT the caller's own task: it is the
                     assignee's last few finished cards, there for the worker doing the card, and 18-27 %
                     of the context for a reader (a sweep, an orchestrator, a reviewer) who never uses it.
Counts of what was folded are stated, with how to get the whole record (`full_record`), so nothing disappears
silently; a capped field says how much was cut. Everything else is untouched.
A result that is not this shape (an error, a card with no context) is returned as None: leave it alone.
"""
from __future__ import annotations

import json
import re

EVENTS_KEPT = 8
RUNS_KEPT = 3
FIELD_CAP = 300
POINTER = "(whole text is in worker_context)"
FULL_RECORD = ("team_board action=show id=<id> full=true (or `hermes kanban show <id> --json`) returns every "
               "event, run and comment whole")


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
        folded["full_record"] = FULL_RECORD
        out["folded_into_worker_context"] = folded
    text = json.dumps(out, ensure_ascii=False)
    return text if len(text) < len(result) else None
