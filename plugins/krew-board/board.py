"""The board (Hermes kanban) in one tool, over the `hermes kanban` CLI: the reference install's team_board,
team_time and team_config_audit, lifted out of its front-door plugin whole (the owner 2026-10-10).

A front-door profile (Crissa) that carries the whole built-in `kanban` toolset pays for 14 tool schemas on
every turn and uses a handful of verbs. `team_board` is those verbs in one tool: list, show, create,
comment, unblock, complete, answer. It shells out to `hermes kanban ... --json`, the same shared board every
profile reads, so it needs no board path and no database access of its own.

Nothing an agent relies on is cut silently. A list longer than one page says how many more there
are and how to get them (`offset=`); a card's body, block reason and comments come back whole with
`full=true`, and without it every shortened field says how much was left out.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
try:
    from . import krew_hermes as KH
except ImportError:                 # imported alone (tests, scripts)
    import krew_hermes as KH  # noqa: E402

try:
    from . import krew_settings as S
except ImportError:                 # imported alone (tests, scripts)
    import krew_settings as S  # noqa: E402

# ── team_board: the board in one tool ────────────────────────────────────
# Crissa carried the whole `kanban` toolset — 14 tools, 23 KB on every turn —
# and called it once in twelve days.
# What her doctrine needs is six verbs: list, show, create (assignee ada,
# triage), comment, unblock, complete (crissa-needs, crissa-big-push, SOUL).
# One tool, ~0.7 KB, over the `hermes kanban` CLI (the shared SQLite board).
_BOARD_ACTIONS = ("list", "show", "create", "comment", "unblock", "complete", "answer")
_BOARD_STATUS = ("archived", "blocked", "done", "ready", "review", "running", "scheduled", "todo", "triage")
_BOARD_MARK = {"done": "✓", "blocked": "⊘", "running": "▶", "review": "◔", "ready": "○",
               "todo": "○", "triage": "?", "scheduled": "◷", "archived": "·"}

# Crissa's list (the Control Panel's list tile). Not the crew board.
# ask = waits on the owner (yes/no). task = steps she (or the crew) owns.
_LIST_ACTIONS = ("list", "show", "ask", "task", "note", "done")
_LIST_WHO = ("ada", "crissa", "bash", "lila", "steward", "vera")


def _login_home() -> Path:
    """Login-user home. Hermes cron rewrites HOME to the profile home."""
    override = (os.environ.get("KREW_REAL_HOME") or "").strip()
    if override:
        return Path(override)
    try:
        import pwd
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        return Path.home()


# Tests monkeypatch this name. The same list team-crissa reads and writes (KREW_TEAM_LIST_ROOT, else
# $KREW_HOME/team/list), so a ruling landed here clears her row there.
_LIST_ROOT = Path(os.path.expanduser(os.environ.get("KREW_TEAM_LIST_ROOT") or str(S.krew_home() / "team" / "list")))


def _board_tally(rows: list[dict]) -> str:
    """Stable per-status counts so a list call is the number, not a hunt."""
    from collections import Counter
    c = Counter(str(r.get("status") or "?") for r in rows)
    parts = []
    for st in _BOARD_STATUS:
        n = c.pop(st, 0)
        if n:
            parts.append(f"{st} {n}")
    for st, n in sorted(c.items()):
        parts.append(f"{st} {n}")
    return " · ".join(parts)


_DONE = frozenset({"done", "complete", "completed"})
_ARCHIVED = frozenset({"archived"})
# Hermes dispatch always forwards these. They are never a board filter.
_RUNTIME_KW = frozenset({
    "task_id", "session_id", "user_task", "enabled_tools", "parent_agent",
    "callback", "tool_call_id", "turn_id", "api_request_id", "scope",
})
_HOUR_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _board_ts(t: dict) -> float:
    for k in ("completed_at", "created_at", "started_at"):
        v = t.get(k)
        if v in (None, "", 0, "0"):
            continue
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip()
        try:
            return float(s)
        except ValueError:
            pass
        try:
            return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
    return 0.0


def _board_hhmm(t: dict) -> str:
    ts = _board_ts(t)
    if not ts:
        return ""
    return time.strftime("%H:%M", time.localtime(ts))


def _parse_window(v) -> float | None:
    """Minutes/hours from whatever the child sent: 2, 2h, 120, 'last two hours'."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if v <= 0:
            return None
        return float(v) * 3600 if v <= 48 else float(v) * 60
    s = str(v or "").strip().lower()
    if not s:
        return None
    m = re.search(r"(\d+)\s*h", s)
    if m:
        return int(m.group(1)) * 3600
    m = re.search(r"(\d+)\s*m(?:in)?", s)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(one|two|three|four|five|six)\s+hour", s)
    if m:
        return _HOUR_WORDS[m.group(1)] * 3600
    if s.isdigit():
        n = int(s)
        return n * 3600 if n <= 48 else n * 60
    m = re.search(r"(\d+)\s*hour", s)
    if m:
        return int(m.group(1)) * 3600
    return None


def _plain_ask(p: dict) -> str:
    """Scout/list job in plain words. look= was the 08:32 empty-ask shape."""
    return str(
        p.get("ask") or p.get("text") or p.get("look")
        or p.get("query") or p.get("q") or p.get("job") or ""
    ).strip()


def _drop_runtime(p: dict) -> dict:
    return {k: v for k, v in p.items() if k not in _RUNTIME_KW}


def _window_seconds(p: dict) -> float | None:
    for k in ("since", "hours", "last", "window", "within"):
        sec = _parse_window(p.get(k))
        if sec:
            return sec
    skip = {"ask", "text", "job", "action", "status", "assignee", "id", "task",
            "body", "title", "offset", "full", "triage"} | _RUNTIME_KW
    for k, v in p.items():
        if k in skip:
            continue
        sec = _parse_window(v)
        if sec:
            return sec
    blob = " ".join(str(p.get(k) or "") for k in ("ask", "text", "job", "q", "query"))
    return _parse_window(blob)


def _board_head(rows: list[dict], st: str = "", window_s: float | None = None) -> str:
    n = len(rows)
    if st:
        head = f"{n} card{'s' if n != 1 else ''} · {st}"
    else:
        done = sum(1 for r in rows if str(r.get("status") or "") in _DONE)
        arch = sum(1 for r in rows if str(r.get("status") or "") in _ARCHIVED)
        open_n = n - done - arch
        head = f"{open_n} open · done {done}"
        if arch:
            head += f" · archived {arch}"
    if window_s:
        if window_s % 3600 == 0:
            w = f"{int(window_s // 3600)}h"
        elif window_s % 60 == 0:
            w = f"{int(window_s // 60)}m"
        else:
            w = f"{int(window_s)}s"
        head += f" · last {w}"
    return head


def _board_line(t: dict) -> str:
    """One row: mark id status assignee title HH:MM. The title whole (krew: nothing cut silently)."""
    st = str(t.get("status") or "?")
    line = "%s %s %-8s %-8s %s" % (_BOARD_MARK.get(st, "·"), str(t.get("id") or "?"),
                                   st, str(t.get("assignee") or "-"), str(t.get("title") or ""))
    hh = _board_hhmm(t)
    return f"{line} {hh}" if hh else line


# the owner 2026-10-06: every comment was cut at 200 chars with no mark, so Lila's round-3
# look verdict stopped at "...wedge feet, matte black," right before FAIL, and the block reason (where she
# wrote "verdict is FAIL") was never shown: `kanban show --json` carries it only in `events`. A helper
# asked for "only pass or fail" chose "pass" and the card was closed on it. Never cut silently.
_SHOW_COMMENT_MAX = 4000
_SHOW_BODY_MAX = 1500
_SHOW_COMMENTS = 5
_PAGE = 30           # rows per list page; the reply says when there are more and how to get them (krew)


def _clip_marked(text: str, limit: int | None) -> str:
    """The whole text, or `limit` chars and an explicit marker - never a silent cut."""
    text = str(text or "")
    if limit is None or len(text) <= limit:
        return text
    return text[:limit] + f" [shortened: {len(text) - limit} more chars; show with full=true for all of it]"


def _board_block_reason(t: dict) -> tuple[str, str, bool]:
    """(reason, kind, from_history). The task row when it carries one, else the latest
    `blocked` event in the card's history (where the board actually keeps it)."""
    blocked = t.get("blocked") if isinstance(t.get("blocked"), dict) else {}
    reason = t.get("blocked_reason") or t.get("block_reason") or blocked.get("reason") or ""
    kind = t.get("blocked_kind") or blocked.get("kind") or ""
    if reason:
        return str(reason), str(kind or ""), False
    for ev in reversed(t.get("events") or []):
        if isinstance(ev, dict) and ev.get("kind") == "blocked":
            pl = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            if pl.get("reason"):
                return str(pl["reason"]), str(pl.get("kind") or kind or ""), True
    return "", str(kind or ""), False


def _board_show_text(t: dict, full: bool = False) -> str:
    """A card: title · status · assignee · the block reason (the question, on a needs_input card;
    the last one from its history on a card that has moved on) · the body · the last comments, whole.
    Anything left out says so; full=true returns every field and comment whole (krew)."""
    com_max, body_max = (None, None) if full else (_SHOW_COMMENT_MAX, _SHOW_BODY_MAX)
    lines = [f"{t.get('id')} · {t.get('status')} · {t.get('assignee') or '-'} · {str(t.get('title') or '')}"]
    reason, kind, hist = _board_block_reason(t)
    if reason:
        label = "blocked" if (str(t.get("status") or "") == "blocked" or not hist) else "last blocked"
        lines.append(f"{label}{(' ' + kind) if kind else ''}: {_clip_marked(reason, com_max)}")
    body = str(t.get("body") or "").strip()
    if body:
        lines.append("body: " + _clip_marked(body, body_max))
    comments = [c for c in (t.get("comments") or []) if isinstance(c, dict)]
    keep = comments if full else comments[-_SHOW_COMMENTS:]
    if len(comments) > len(keep):
        lines.append(f"[{len(comments) - len(keep)} earlier comments not shown; the last {len(keep)} follow. "
                     "show with full=true for every comment]")
    for c in keep:
        lines.append(f"— {c.get('author') or '?'}: "
                     f"{_clip_marked(c.get('body') or c.get('text') or '', com_max)}")
    return "\n".join(lines)


def _list_dir(which: str) -> Path:
    return Path(_LIST_ROOT) / which


def _list_read_dir(which: str) -> list[dict]:
    d = _list_dir(which)
    items = []
    if not d.is_dir():
        return items
    for fpath in sorted(d.glob("*.json")):
        try:
            item = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(item, dict):
            item["_file"] = fpath.name
            item["_status"] = which
            items.append(item)
    return items


def _list_write(item: dict, which: str = "pending") -> Path:
    d = _list_dir(which)
    d.mkdir(parents=True, exist_ok=True)
    item_id = str(item.get("id") or "").strip()
    fpath = d / f"{item_id}.json"
    blob = {k: v for k, v in item.items() if not str(k).startswith("_")}
    fpath.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")
    return fpath


def _list_is_users(item: dict) -> bool:
    """An item on the list that the TEAM did not file is the owner's, whatever card it names.

    2026-09-24 01:48: Crissa answered `go` on the restart-window card and `_list_clear_card`
    swept every pending item with that card_id — including "Reboot the machine tonight?", which
    the restart window had filed (source `restart-window`) for the owner's YES. The executor
    only reads decided/, so nothing rebooted; but the question left the list unanswered and
    she was asked again five times. `_list_prune_stale` had the same hole (a card that leaves
    the Decide queue takes the owner's ask with it). A foreign `source` means: not ours to close;
    the owner marks it, or its filer withdraws it. Kill: KREW_CRISSA_LIST_FOREIGN=0."""
    if os.environ.get("KREW_CRISSA_LIST_FOREIGN", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    src = str(item.get("source") or "").strip().lower()
    return bool(src) and src not in _LIST_WHO


def _list_clear_card(card_id: str, note: str = "") -> int:
    cid = str(card_id or "").strip()
    if not cid:
        return 0
    n = 0
    for item in _list_read_dir("pending"):
        if str(item.get("card_id") or "") != cid:
            continue
        if _list_is_users(item):
            continue                       # filed for the owner by another tool: not ours to close
        item["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if note:
            notes = list(item.get("notes") or [])
            notes.append({"ts": item["done_at"], "text": note[:500]})
            item["notes"] = notes
        fname = str(item.get("_file") or "")
        _list_write(item, "done")
        if fname:
            try:
                (_list_dir("pending") / fname).unlink()
            except FileNotFoundError:
                pass
        n += 1
    return n


def _hermes_bin() -> str:
    return S.hermes_bin()


def _kanban_cli(args: list, timeout: float = 25.0, env: dict | None = None) -> tuple[int, str]:
    try:
        r = subprocess.run([_hermes_bin(), "kanban", *args], capture_output=True, text=True, timeout=timeout,
                           **({"env": env} if env is not None else {}))
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return 1, f"{e.__class__.__name__}: {str(e)[:120]}"


def _answer_holds(text: str, body: str = "") -> bool:
    """True when her ruling must NOT unblock the card.

    `answer` was always comment + unblock, so every ruling handed the card
    back to the dispatcher: "Park." on a card was claimed and spawned 21 s
    later (2026-09-16 23:03), and a `try` on a `gate: green` card releases a
    do-not-implement card to a worker. kanban-loop consumes both from the
    comment — Park schedules the card, a gate ruling cuts the try / archives.
    Kill CRISSA_ANSWER_HOLD=0 (every answer unblocks again)."""
    import re as _re
    if os.environ.get("CRISSA_ANSWER_HOLD", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    t = _re.sub(r"^unblock\b[\s:,.;!-]*", "", (text or "").strip(), flags=_re.I)
    first = _re.split(r"[\s:,.;!—-]+", t.lower(), 1)[0] if t else ""
    if first in ("park", "parked", "drop", "archive"):
        return True
    # `later <date>` is a park with an alarm (2026-09-18): a "later 2026-09-18" card was
    # unblocked here, claimed and re-blocked untyped within a minute; nothing read the date.
    # The answer handler re-blocks it `dependency` with the date; kanban-loop is the alarm.
    if first == "later":
        return True
    # A restart-window card is ruled in words too: the restart window reads
    # the comment and carries it out (2026-09-17). An unblock hands it to a worker.
    return bool(_re.search(r"^gate: (green|restart-window)\s*$", body or "", _re.M))


def _done_closes_on() -> bool:
    """KREW_CRISSA_DONE_CLOSES=1 (OFF by default): her `done` closes the card in the same step (the owner 2026-10-03)."""
    return os.environ.get("KREW_CRISSA_DONE_CLOSES", "").strip().lower() in ("1", "on", "true", "yes")


def _is_done_ruling(text: str) -> bool:
    """True only when the ruling's FIRST word is `done` (a leading UNBLOCK: tolerated) - the same one spelling
    kanban-loop's verdict_of reads. 'complete the smoke first', 'donezo' or a done mid-sentence is not it."""
    import re as _re
    t = _re.sub(r"^unblock\b[\s:,.;!-]*", "", (text or "").strip(), flags=_re.I)
    first = _re.split(r"[\s:,.;!—-]+", t.lower(), 1)[0] if t else ""
    return first == "done"


def _answer_done(tid: str, text: str, cli, clear) -> str:
    """Her `done` closes the card where it stands (the owner 2026-10-03). It used to UNBLOCK: the card went
    `ready`, the dispatcher claimed it inside a minute (an unassigned card goes to kanban.default_assignee) and
    a worker ran it, while kanban-loop's done-close comes every 20 min. Her words are
    already on the card as a comment; `complete` carries them as the result. Never an unblock: if complete is
    refused (a park in `scheduled` cannot be completed), the ruling is held and kanban-loop's run_done_rulings
    closes it."""
    rc, out = cli(["complete", tid, "--result", (text or "")[:500]])
    n = clear(tid, note=text)
    tail = "" if n else " (no list row)"
    if rc == 0:
        return f"Landed on {tid}: done - closed in the same step (no unblock, no worker) and cleared Crissa's list." + tail
    return (f"Landed on {tid}: done held for kanban-loop ({out[:80]}) - no unblock, the card stays off the "
            "dispatcher - and cleared Crissa's list." + tail)


def _later_date(text: str) -> str:
    """'YYYY-MM-DD' from a `later <date>` ruling (a leading UNBLOCK: tolerated), else ''."""
    import re as _re
    t = _re.sub(r"^unblock\b[\s:,.;!-]*", "", (text or "").strip(), flags=_re.I)
    m = _re.match(r"^\s*later\b[\s:,-]*(\d{4}-\d{2}-\d{2})", t, _re.I)
    return m.group(1) if m else ""


def _card_body(tid: str, cli=_kanban_cli) -> str:
    rc, out = cli(["show", tid, "--json"])
    if rc != 0 or "{" not in out:
        return ""
    try:
        data = json.loads(out[out.index("{"):])
    except Exception:  # noqa: BLE001
        return ""
    t = data.get("task") if isinstance(data.get("task"), dict) else data
    return str((t or {}).get("body") or "") if isinstance(t, dict) else ""


def _xspaces_drill_beat() -> dict | None:
    """The live room lap's current beat while a lap runs (its runner writes ~/.hermes/xspaces-drill.active, the
    file team-crissa reads too), else None. A stale file (its runner gone) is ignored."""
    if os.environ.get("KREW_XSPACES_DRILL_TAG", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        d = json.loads((KH.hermes_home() / "xspaces-drill.active").read_text())
        os.kill(int(d["pid"]), 0)
        return d if d.get("beat") and d.get("day") else None
    except Exception:
        return None


# A COMMERCIAL RENDER CARD CARRIES ITS ENGINES (the owner 2026-10-05, live room drill gap 9): the hands paraphrased "qwen-
# image-2512, then wan22, no flux" away, and Lila's default was flux2-turbo, which is not for publishing. A render card
# whose words say commercial / marketing / hero / ad gets the licence line in code. Off: KREW_COMMERCIAL_RENDER_LINE=0.
COMMERCIAL_RENDER_LINE = ("Engines (commercial use): still = qwen-image-2512 (Apache 2.0), clip = wan22-i2v from that still, "
                          "or ltx23-* (Lightricks LTX community licence: commercial under $10M annual revenue, outputs labelled AI-generated, "
                          "no competing product). Never flux2-*, ltx25-* or Qwen-Image 2.1 - not licensed for "
                          "publishing.")


def _commercial_render_line(title: str, text: str) -> str:
    if os.environ.get("KREW_COMMERCIAL_RENDER_LINE", "1").strip().lower() in ("0", "false", "no", "off"):
        return text
    words = f"{title} {text}"
    if (re.search(r"\b(?:commercial|marketing|hero|ads?|launch|promo)\b", words, re.I)
            and re.search(r"\b(?:render|clip|still|picture|image|video|photo|shot)\b", words, re.I)
            and COMMERCIAL_RENDER_LINE not in (text or "")):
        return ((text or "").rstrip() + "\n\n" + COMMERCIAL_RENDER_LINE).strip()
    return text


def config_audit(*, toolsets: dict, cfg: dict, config_path: str, pack_hint: str = "") -> str:
    """What am I configured with, what does Hermes offer, what am I missing?

    Added 2026-08-11 (the owner: "do they have the tools to evaluate their own
    configs relative to the hermes docs — what they have vs what is
    available — to base suggested edits on"). Every crew profile carries the
    `team` toolset, so putting it here reaches all of them at once.
    The catalogue and the config are read by the caller (krew-board's register), so this stays testable.
    """
    # Chat/social integrations — real, but noise for a crew. Folded
    # into a count so the interesting gaps are actually readable.
    CHATTY = ("discord", "slack", "telegram", "whatsapp", "signal", "sms",
              "email", "matrix", "mattermost", "feishu", "dingtalk", "wecom",
              "weixin", "qqbot", "yuanbao", "bluebubbles", "spotify",
              "x_search", "webhook")
    TOOLSETS = toolsets
    path = Path(config_path)

    profile = path.parent.name if path.parent.parent.name == "profiles" else "default (BRIDGE)"
    have = list(cfg.get("toolsets") or [])
    plat = cfg.get("platform_toolsets") or {}
    deleg = cfg.get("delegation") or {}

    def _is_chatty(n):
        return n.startswith("hermes-") or any(c in n for c in CHATTY)

    missing = [n for n in sorted(TOOLSETS) if n not in have and not _is_chatty(n)]
    chatty_missing = [n for n in sorted(TOOLSETS) if n not in have and _is_chatty(n)]

    out = [f"profile: {profile}   ({path})", ""]
    out.append(f"YOU HAVE ({len(have)}): " + ", ".join(sorted(have)))
    # A grant that is missing from the platform you actually run on is a
    # silent no-op — the single most common config mistake here.
    gaps = {p: [t for t in have if t not in (v or [])] for p, v in plat.items()}
    gaps = {p: g for p, g in gaps.items() if g}
    if gaps:
        out.append("")
        out.append("WARNING — granted at top level but MISSING on these platforms "
                   "(so they do nothing there):")
        for p, g in gaps.items():
            out.append(f"  {p}: missing {', '.join(g)}")
    out.append("")
    out.append(f"AVAILABLE TO YOU, NOT GRANTED ({len(missing)}):")
    for n in missing:
        desc = ((TOOLSETS[n] or {}).get("description") or "").split(".")[0][:88]
        out.append(f"  {n:16} — {desc}")
    if chatty_missing:
        out.append(f"  (+{len(chatty_missing)} chat/social integrations not listed)")
    out.append("")
    if deleg:
        out.append(f"SUBAGENTS: max_concurrent_children={deleg.get('max_concurrent_children')} "
                   f"max_spawn_depth={deleg.get('max_spawn_depth')} "
                   f"orchestrator_enabled={deleg.get('orchestrator_enabled')} "
                   f"child_model={deleg.get('model') or 'inherit'} "
                   f"child_url={deleg.get('base_url') or 'inherit'}"
                   + ("" if "delegation" in have else "  ← policy set but toolset NOT granted"))
    else:
        out.append("SUBAGENTS: no delegation block — Hermes defaults apply"
                   + ("" if "delegation" in have else " (and the toolset is not granted)"))
    out.append("")
    out.append("TO PROPOSE A CHANGE: edit the PACK — "
               + (f"{pack_hint} — " if pack_hint else "this profile's config.yaml in your profile packs — ")
               + "never the live profile alone; packs are the deployment target and the next "
               "deploy overwrites live-only edits. Say WHY the "
               "toolset serves your role, then let the owner decide.")
    return "\n".join(out)[:6000]


_BOARD_READ_WORDS = frozenset({
    "card", "thread", "card_thread", "read", "read card", "detail", "details", "view", "get", "get_card",
    "open", "body", "comments", "history", "info", "inspect", "look"})


def _truthy(v) -> bool:
    return v in (True, 1) or str(v).strip().lower() in ("1", "true", "yes", "on")


def params(args: tuple, kwargs: dict) -> dict:
    p: dict = {}
    if args and isinstance(args[0], dict):
        p.update(args[0])
    p.update({k: v for k, v in kwargs.items() if k not in _RUNTIME_KW})
    for k in _RUNTIME_KW:
        p.pop(k, None)
    return p


def team_board(p: dict, *, cli=_kanban_cli, author: str = "", default_assignee: str = "") -> str:
    """The board (kanban) in one tool. action list|show|create|comment|unblock|complete|answer.
    `cli(args) -> (rc, text)` runs `hermes kanban`; `author` signs comments and created cards (the calling
    profile, or `board.author`); `default_assignee` is who a created card goes to (`board.default_assignee`)."""
    action = str(p.get("action") or "list").strip().lower()
    tid = str(p.get("id") or p.get("task") or p.get("card") or p.get("card_id") or "").strip()
    text = str(p.get("text") or p.get("body") or "").strip()
    # THE CARD-READ PATH (the owner 2026-09-30): in one week the hands asked to read a card as
    # card / thread / detail / read / get / view / body / comments / open / history ~230 times and every
    # one was refused ("action is one of ...") - a step of a four-step run each. Reading a card is
    # `show`; a read word with a card id IS show. `file` with a title is create. Nothing else is guessed.
    if action not in _BOARD_ACTIONS:
        if action in _BOARD_READ_WORDS and tid:
            action = "show"
        elif action in ("file", "new", "add") and str(p.get("title") or "").strip():
            action = "create"
    if action not in _BOARD_ACTIONS:
        return "team_board action is one of: " + ", ".join(_BOARD_ACTIONS) + ". Nothing changed."
    if action == "list":
        args_ = ["list", "--json"]
        st = str(p.get("status") or "").strip().lower()
        if st in _BOARD_STATUS:
            args_ += ["--status", st]
        who = str(p.get("assignee") or "").strip()
        if who:
            args_ += ["--assignee", who]
        rc, out = cli(args_)
        if rc != 0:
            return f"The board did not answer ({out[:160]})."
        try:
            rows = json.loads(out[out.index("["):]) if "[" in out else []
        except Exception:
            return f"The board answered oddly ({out[:160]})."
        if not rows:
            return "The board is empty" + (f" for status {st}" if st else "") + "."
        rows = [r for r in rows if isinstance(r, dict)]
        window_s = _window_seconds(p)
        if window_s:
            floor = time.time() - window_s
            rows = [r for r in rows if _board_ts(r) >= floor]
        rows.sort(key=_board_ts, reverse=True)
        if not rows:
            return "Nothing in that window" + (f" for status {st}" if st else "") + "."
        head = _board_head(rows, st=st if st in _BOARD_STATUS else "", window_s=window_s) + " · " + _board_tally(rows)
        # krew: a page, and the rest said with the call that gets it (the reference cut at 30 with "…")
        try:
            offset = max(0, int(p.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        page = rows[offset:offset + _PAGE]
        rest = len(rows) - offset - len(page)
        return head + "\n" + "\n".join(_board_line(r) for r in page) + (
            f"\n[{rest} more not shown: list again with offset={offset + len(page)}, "
            "or narrow with status=, assignee= or since=]" if rest > 0 else "")
    if action == "show":
        if not tid:
            return "show needs id= (t_…). Nothing changed."
        rc, out = cli(["show", tid, "--json"])
        if rc != 0:
            return f"No card {tid} ({out[:120]})."
        try:
            data = json.loads(out[out.index("{"):]) if "{" in out else {}
        except Exception:
            return out[:600]
        t = data.get("task") if isinstance(data.get("task"), dict) else data
        if isinstance(data.get("comments"), list) and isinstance(t, dict) and "comments" not in t:
            t = dict(t, comments=data["comments"])
        if isinstance(data.get("events"), list) and isinstance(t, dict) and "events" not in t:
            t = dict(t, events=data["events"])   # the block reason lives in the history
        return _board_show_text(t if isinstance(t, dict) else data, full=_truthy(p.get("full")))
    if action == "create":
        title = str(p.get("title") or "").strip()
        if not title:
            return "create needs title=. Nothing filed."
        who = str(p.get("assignee") or default_assignee or "").strip()
        drill = _xspaces_drill_beat()
        if drill:
            # LIVE ROOM DRILL HYGIENE (the owner 2026-10-05, plan gap 8): a card a scripted lap causes is tagged and
            # keyed on its beat and day, so lap two reads lap one's card instead of filing a twin, and the Daily
            # and KPIs can tell drill cards from crew work. Off: KREW_XSPACES_DRILL_TAG=0.
            if not title.lower().startswith("[drill]"):
                title = f"[drill] {title}"
        text = _commercial_render_line(title, text)
        args_ = ["create", title] + (["--assignee", who] if who else []) + (["--created-by", author] if author else [])
        if drill:
            args_ += ["--idempotency-key", f"xspaces:{drill['beat']}:{drill['day']}"]
        if text:
            args_ += ["--body", text]
        if p.get("triage") is None or p.get("triage") in (True, "true", "1", 1):
            args_.append("--triage")
        rc, out = cli(args_)
        if rc != 0:
            return f"Not filed ({out[:160]})."
        import re as _re
        m = _re.search(r"\bt_[0-9a-f]{6,}\b", out)
        return f"Filed{(' for ' + who) if who else ''}: {title}" + (f" ({m.group(0)})" if m else "") + "."
    if action == "comment":
        if not tid or not text:
            return "comment needs id= and text=. Nothing changed."
        rc, out = cli(["comment", tid, text] + (["--author", author] if author else []))
        return f"Commented on {tid}." if rc == 0 else f"Comment did not land ({out[:120]})."
    if action == "unblock":
        if not tid:
            return "unblock needs id=. Nothing changed."
        args_ = ["unblock", tid] + (["--reason", text] if text else [])
        rc, out = cli(args_)
        if rc != 0:
            return f"Unblock did not land ({out[:120]})."
        _list_clear_card(tid, note=text or "unblocked")
        return f"Unblocked {tid}."
    if action == "complete":
        if not tid:
            return "complete needs id=. Nothing changed."
        args_ = ["complete", tid] + (["--result", text] if text else [])
        rc, out = cli(args_)
        if rc != 0:
            return f"Complete did not land ({out[:120]})."
        _list_clear_card(tid, note=text or "completed")
        return f"Completed {tid}."
    if action == "answer":
        # Land the owner's answer on the card: comment + unblock + clear Crissa's list.
        if not tid or not text:
            return "answer needs id= and text= (the owner's words). Nothing changed."
        rc, out = cli(["comment", tid, text] + (["--author", author] if author else []))
        if rc != 0:
            return f"Comment did not land ({out[:120]}). List still open."
        if _done_closes_on() and _is_done_ruling(text):
            # `done` and only `done` closes here; every other ruling below is unchanged (the owner 2026-10-03)
            return _answer_done(tid, text, cli, _list_clear_card)
        if _answer_holds(text, _card_body(tid, cli)):
            # Park, or any ruling on a green gate: the words are the ruling,
            # kanban-loop lands it. An unblock here respawns the work.
            n = _list_clear_card(tid, note=text)
            d = _later_date(text)
            if d:
                # A dated park is ONE write, straight to `scheduled` (2026-09-27). It used to be an
                # unblock followed by a `dependency` block: between the two the card was live, and
                # hermes re-kinds, specifies and decomposes a block it thinks is recurring — 3 of 38
                # dated parks held, and a deferred proposal was built the same morning. `scheduled`
                # is not dispatchable and `unblock` is its only exit; kanban-loop run_later reads the
                # date from this ruling and returns the card to the Decide queue that day.
                # Kill: KREW_CRISSA_LATER_SCHEDULES=0 (the old two-step).
                if os.environ.get("KREW_CRISSA_LATER_SCHEDULES", "1").strip().lower() in ("0", "false", "no", "off"):
                    rc_u, out_u = cli(["unblock", tid, "--reason", f"later {d}: re-blocking as a dated park"])
                    rc_b, out_b = cli(["block", tid, f"later {d}: kanban-loop puts it back on the Decide queue then",
                                       "--kind", "dependency"])
                    where = "unblocked (a parent is still open)" if rc_u == 0 else "still blocked"
                else:
                    rc_b, out_b = cli(["schedule", tid,
                                       f"later {d}: parked by Crissa's ruling; kanban-loop puts it back on the Decide queue then"])
                    where = "where it was"
                if rc_b != 0:
                    cli(["comment", tid, f"later {d}: dated park did not land ({out_b[:80]}); card is {where}"]
                        + (["--author", author] if author else []))
                    return (f"Landed on {tid}: later {d} noted, but the dated park did not land "
                            f"({out_b[:80]}); the card is {where}. Cleared Crissa's list.")
                return (f"Landed on {tid}: parked until {d} (scheduled; kanban-loop returns it to the "
                        "Decide queue that day) and cleared Crissa's list." + ("" if n else " (no list row)"))
            return (f"Landed on {tid}: ruling held for kanban-loop (no unblock — the card stays "
                    "off the dispatcher) and cleared Crissa's list." + ("" if n else " (no list row)"))
        rc2, out2 = cli(["unblock", tid, "--reason", text[:200]])
        if rc2 != 0:
            return f"Commented on {tid} but unblock did not land ({out2[:120]}). List still open."
        n = _list_clear_card(tid, note=text)
        return f"Landed on {tid} and cleared Crissa's list." + ("" if n else " (no list row)")
    return "Nothing changed."


def team_time(*args, **kwargs):
    """Wall-clock. Only when asked or the reply needs minutes. Not a greeting."""
    import datetime as _dt

    now = _dt.datetime.now().astimezone()
    return now.strftime("%-I:%M %p on %A, %B %-d, %Y (%Z)")


# krew's public names for the reference's private ones (krew-board's register and tests use these)
ACTIONS, STATUS, MARK = _BOARD_ACTIONS, _BOARD_STATUS, _BOARD_MARK
DONE, ARCHIVED, RUNTIME_KW, HOUR_WORDS, READ_WORDS = _DONE, _ARCHIVED, _RUNTIME_KW, _HOUR_WORDS, _BOARD_READ_WORDS
PAGE, SHOW_BODY, SHOW_COMMENT, SHOW_COMMENTS = _PAGE, _SHOW_BODY_MAX, _SHOW_COMMENT_MAX, _SHOW_COMMENTS
tally, row_ts, hhmm, parse_window, window_seconds = _board_tally, _board_ts, _board_hhmm, _parse_window, _window_seconds
head, line, clip_marked, block_reason, show_text = _board_head, _board_line, _clip_marked, _board_block_reason, _board_show_text
hermes_bin, kanban_cli = _hermes_bin, _kanban_cli
