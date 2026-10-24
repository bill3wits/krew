#!/usr/bin/env python3
"""krew-review-route — every review request names a reviewer who is not the builder.


`kanban_request_review`, the SAME card moves to `review`, a reviewer approves it or sends it back
with `kanban_request_changes`. Review stands before done, and a FAIL has an actuator that exists.

The hole this closes: hermes reassigns the card only when `reviewer` is given. With none, the card
stays on its builder and the dispatcher spawns the BUILDER to review its own work (7 of the first
18 requests on this board). This is a `pre_tool_call` shell hook on `kanban_request_review`; it
answers hermes's own `modify` so the call proceeds with a reviewer filled in:

    whatever the worker named, or nothing                  ->  KREW_REVIEWER (vera)
    the builder IS vera (nobody reviews their own build)   ->  KREW_REVIEWER_ALT (steward)


review kit and the review skills are hers, so a worker that names steward, ada or itself is
corrected to her. The second name exists for one case only, her own build.

It never blocks: any failure prints `{}` and the call goes through as the worker sent it.
Kill: KREW_REVIEW_ROUTE=0. Log: ~/.hermes/logs/review-route.jsonl (the real home, not a
profile's). Wire: hooks.pre_tool_call, matcher `^kanban_request_review$`, every profile, live + pack.
"""
from __future__ import annotations

import json
import os
import pwd
import sys
import time
from pathlib import Path

TOOL = "kanban_request_review"
REVIEWER = (os.environ.get("KREW_REVIEWER") or "vera").strip().lower()
ALT = (os.environ.get("KREW_REVIEWER_ALT") or "steward").strip().lower()
# Who may be named and left standing: the reviewer alone. KREW_REVIEWERS widens it by hand.
REVIEWERS = tuple(n.strip().lower() for n in
                  (os.environ.get("KREW_REVIEWERS") or REVIEWER).split(",") if n.strip())


def _real_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:  # noqa: BLE001
        return Path.home()


LOG = Path(os.environ.get("KREW_REVIEW_ROUTE_LOG", str(_real_home() / ".hermes" / "logs" / "review-route.jsonl")))


def builder_of(payload: dict) -> str:
    """The profile this call is made FOR: the payload's own `profile` (hermes stamps it at fire
    time, so it is right under one host gateway too), else the worker's pin, else the home's name."""
    name = str(payload.get("profile") or os.environ.get("HERMES_PROFILE")
               or Path(os.environ.get("HERMES_HOME", "")).name or "").strip().lower()
    return "" if name in (".hermes", "default") else name


def route(args: dict, builder: str) -> tuple[str, str]:
    """(reviewer to use, why) — why is "" when the worker's own choice stands."""
    asked = str(args.get("reviewer") or "").strip().lower()
    first = ALT if builder == REVIEWER else REVIEWER
    if not asked:
        return first, "none named"
    if asked == first:
        return asked, ""
    if asked == builder:
        return first, "the builder named itself"
    if builder == REVIEWER or asked not in REVIEWERS:
        return first, f"{asked} is not the reviewer"
    return asked, ""


def _log(row: dict) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        print("{}")
        return 0
    if os.environ.get("KREW_REVIEW_ROUTE", "1").strip().lower() in ("0", "false", "no", "off"):
        print("{}")
        return 0
    try:
        if not isinstance(payload, dict) or payload.get("tool_name") != TOOL:
            print("{}")
            return 0
        args = payload.get("tool_input")
        if not isinstance(args, dict):
            print("{}")
            return 0
        builder = builder_of(payload)
        reviewer, why = route(args, builder)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "card": os.environ.get("HERMES_KANBAN_TASK") or args.get("task_id"),
               "builder": builder, "asked": args.get("reviewer") or None, "reviewer": reviewer, "changed": bool(why), "why": why}
        _log(row)
        if not why:
            print("{}")
            return 0
        print(json.dumps({"action": "modify", "args": {**args, "reviewer": reviewer}}))
        return 0
    except Exception as exc:  # noqa: BLE001 - a hook that raises must never stop a review request
        _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        print("{}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
