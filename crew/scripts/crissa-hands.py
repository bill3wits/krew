#!/usr/bin/env python3
"""Gateway Crissa's one tool: run her CLI kit as a child, then hand the
answer back to the mouth.


This process is `hermes -p crissa --cli` — the same kit a TUI session has.
Not a krew spawn. Not delegate_task. Nested calls refuse.

Usage: python3 scripts/crissa-hands.py "put the board up"
"""
from __future__ import annotations

import argparse
import fcntl
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))  # krew_hermes: where Hermes lives
import krew_hermes as KH  # noqa: E402

HERMES_DIR = Path(os.environ.get("KREW_HERMES") or KH.hermes_home())
HERMES = Path(os.environ.get("HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes")))
LOCK = HERMES_DIR / "krew-hands.lock"


HANDS_CONFIG = Path(__file__).resolve().parent.parent / "config" / "crissa-hands.json"


def _hands_config() -> dict:
    import json as _json
    try:
        d = _json.loads(HANDS_CONFIG.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def setting(env: str, key: str, default):
    raw = os.environ.get(env, "").strip()
    if raw:
        return raw
    v = _hands_config().get(key, default)
    return v


def flag(env: str, key: str) -> bool:
    v = setting(env, key, False)
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


TIMEOUT = int(os.environ.get("KREW_HANDS_TIMEOUT", "70"))
MAX_TURNS = int(setting("KREW_HANDS_TURNS", "turns", 4))
RUN_BUDGET = int(setting("KREW_HANDS_BUDGET", "budget", 45))
NESTED_FLAG = "KREW_HANDS_NESTED"


def gpu_mode(path: Path | None = None) -> str:
    p = path or (HERMES_DIR / "krew-gpu-mode")
    try:
        first = p.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
    except OSError:
        return "normal"
    return first if first in {"render", "normal", "beast", "off"} else "normal"


DISPLAY_STEPS = (
    "This is a display job. Exactly this, then stop: one show_me with what= the screen he named "
    "(kanban, a page name, a doc); then one show_me what=shot to confirm it landed; then say in "
    "one sentence what is on the glass. If show_me refuses, say what it said. No other tools.\n"
)



# reach it (krew_docs, lib/crissa_docs.py), and her lessons had nowhere to go: the memory toolset was in no kit
# she wears day to day. Both lines and both toolsets ride one switch each, OFF by default (byte-for-byte today's
# child); the overnight drill runs its after-arm with them on. KREW_HANDS_DOCS=1, KREW_HANDS_LESSONS=1.
DOCS_LINE = (
    "How-to questions (how does X work, how do I Y, what does a setting do): krew_docs lookup FIRST, "
    "answer from what it returns and name the ref. Nothing on record: say so, do not guess. "
    "A good outside reference you used: krew_docs suggest (title, where, why); it goes to his review.\n"

    # web_extract on file:// paths, and a page "opened in a tab" went to the helper's own headless browser.
    "Anything about THIS krew (its files, switches, timers, cards, numbers) is never a web_search or web_extract: "
    "krew_docs, or read_file on the path you were given. Opening or showing a page or file FOR HIM is show_me "
    "what=<file or screen>, never browser_*.\n"

    # the outcomes file only says the KPI measured better 7 days after that try landed.
    "Numbers: say what was MEASURED and when (\"measured 136.3 a week after, up from 97.7\"), never that one change "
    "caused, moved, took or drove a number.\n"
)
SEARCH_LINE = (
    "Where something lives (a checkpoint, a config, a log, a folder): look it up in FILESYSTEM.md via krew_docs "
    "first (krew_docs lookup, query \"FILESYSTEM.md where is <thing>\") and go straight to the path it names.\n"
    "Only if that does not name it: ONE search_files with target \"files\", path the folder you were given, "
    "pattern \"**/*<word>*/**\" for a FOLDER (it lists the files inside) or \"*<word>*\" for a file, <word> the most "
    "distinctive word; then read_file the hit. Never repeat search_files with variations; if that one search finds "
    "nothing, say so.\n"
    "Answering about a card: always name its id (t_ and eight characters) in the answer.\n"
)


SCREEN_ERRAND = re.compile(r"\b(?:on your (?:own |private )?screen|on her screen|your own browser)\b", re.I)
SCREEN_ERRAND_LINE = ("This job asks for YOUR SCREEN: do it there - open the page with the browser tools and read it from "
                      "the page (browser_navigate / browser_snapshot, computer_use to look). Do not fetch it with "
                      "web_extract or web_search instead.\n")


def screen_errand_line(ask: str) -> str:
    ""

    if not flag("KREW_HANDS_SCREEN_ERRAND", "screen_errand") or not SCREEN_ERRAND.search(ask or ""):
        return ""
    return SCREEN_ERRAND_LINE


def screen_line() -> str:
    """Tell her she HAS a screen, but only while it is up and checked (10-03: with computer_use in her kit she still
    answered 'I've got no display or window of my own' and called nothing)."""
    try:
        if not private_screen_ready():
            return ""
    except Exception:  # noqa: BLE001
        return ""
    return ("You have your own private screen (nobody else's desktop). For anything on YOUR screen - list its windows, "
            "capture it, click or type on it - use computer_use with delivery_mode \"foreground\"; apps by their "
            "window class from list_windows.\n")


def search_hints_on() -> bool:
    return flag("KREW_HANDS_SEARCH_HINT", "search_hints")


LESSON_LINE = (
    "After a tool call fails, or after you solve a job you had not done before: one memory call, action add, "
    "target memory, one line: 'When <job>: <what worked or what failed and why>.' Never memory search.\n"
)


def docs_on() -> bool:
    return flag("KREW_HANDS_DOCS", "docs")


def lessons_on() -> bool:
    return flag("KREW_HANDS_LESSONS", "lessons")


def with_extras(kit: str | None) -> str | None:
    """Add `docs` / `memory` to a NAMED kit when their switches are on. None (the whole CLI kit) stays None."""
    if kit is None:
        return None
    names = [n for n in kit.split(",") if n]
    for on, name in ((docs_on(), "docs"), (lessons_on(), "memory")):
        if on and name not in names:
            names.append(name)
    return ",".join(names)



# computer_use click from a hands run landed on display :0 at (537,1254) - his desktop; the capability manifest
# scopes app windows, never a screen point. Her own screen is hermes's Bot Desktop (`hermes computer-use screen
# start`), which publishes <profile>/bot-desktop/env; hermes points every cua-driver it spawns at it. Until that
# file exists, computer_use is taken out of every hands kit. ON by default; kill KREW_HANDS_CUA_GUARD=0.
BOT_DESKTOP_ENV = HERMES_DIR / "profiles" / "crissa" / "bot-desktop" / "env"


def cua_guard_on() -> bool:
    return os.environ.get("KREW_HANDS_CUA_GUARD", "1").strip().lower() not in ("0", "false", "no", "off")


def private_screen_ready(env_file: Path | None = None) -> bool:
    ""
    try:
        text = (env_file or BOT_DESKTOP_ENV).read_text(encoding="utf-8")
    except OSError:
        return False
    m = re.search(r"(?m)^\s*(?:export\s+)?DISPLAY=['\"]?(:\d+)", text)
    if not (bool(m) and m.group(1) not in (":0", ":1")):
        return False
    if env_file is not None:      # a test's own env file: the isolation record is lib/crissa_her_screen's to test
        return True

    lib = str(Path(__file__).resolve().parent.parent / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        import crissa_her_screen
    except ImportError:
        return False
    return crissa_her_screen.isolated()


def guard_cua(kit: str | None, *, config: Path | None = None, env_file: Path | None = None) -> str | None:
    if not cua_guard_on() or private_screen_ready(env_file):
        return kit
    if kit is None:  # the whole CLI kit: name it, minus computer_use
        cfg: dict = {}
        try:
            import krew_yaml as yaml
            cfg = yaml.safe_load((config or CRISSA_CONFIG).read_text()) or {}
        except Exception:  # noqa: BLE001
            cfg = {}
        names = [str(t) for t in ((cfg.get("platform_toolsets") or {}).get("cli") or [])] or FULL_TOOLSETS_FALLBACK.split(",")
        names = [n for n in names if n and n not in NEVER_IN_HANDS]
    else:
        names = [n for n in kit.split(",") if n]
    return ",".join(n for n in names if n != "computer_use")



# template (qwen3.8-froggeric) honours a per-message marker that overrides it for ONE request: <|think_on|>,
# <|think_low|>, <|think_off|>. KREW_HANDS_THINK: unset/off = no marker (today's child, byte for byte); on = full
# thinking; low = low-effort thinking; auto = low for multi-step / screen / search jobs, none for a lookup or a read.
_THINK_AUTO = re.compile(r"\b(?:find|search|then|errand|screen|browser|compare|why|fix|plan)\b|\bopen (?:the |a )?\S+\.[a-z]{2,}", re.I)


def think_marker(ask: str) -> str:
    mode = os.environ.get("KREW_HANDS_THINK", "").strip().lower()
    if mode == "on":
        return "<|think_on|>\n"
    if mode == "low":
        return "<|think_low|>\n"
    if mode == "auto" and _THINK_AUTO.search(ask or ""):
        return "<|think_low|>\n"
    return ""




# errand and the hands made 16 search_files calls and never called krew_docs, twice. A prompt line had not held.
# So this script runs the lookup itself (~1-2 s, lib/crissa_docs) on the job's words minus the shell noise and hands
# the passages over with the job. Config `docs_preload` / env KREW_HANDS_DOCS_PRELOAD (on by default in the file).
_DOCS_ASK = re.compile(
    r"\b(?:where (?:does|is|do|are|did)|what (?:turns|switches|shuts)|turns? (?:it |them )?off|off.?switch|kill.?switch|"
    r"how (?:does|do|is|are|the|a|an|it)|explain how|house.?docs|grep|in hermes|hermes docs|what does .{0,40} do)\b", re.I)
_SHELL_NOISE = re.compile(r"`[^`]*`|\bgrep\b(?:\s+-\S+)*|\S*/\S+|(?<!\w)--?[a-z][\w-]*|2>/dev/null|\|\s*\w+(?:\s+-?\d+)?", re.I)


_TOOL_WORDS = re.compile(r"\b(?:krew_docs|krew docs|lookup|look up|search(?: the)?(?: house)? docs(?: for)?|"
                         r"name the ref|cite the ref|answer in \w+ sentences?)\b:?", re.I)


def docs_query(ask: str) -> str:
    q = _TOOL_WORDS.sub(" ", _SHELL_NOISE.sub(" ", ask or ""))
    q = re.sub(r"[\"'*]+", " ", q)
    return " ".join(q.split())[:300]


def docs_preload(ask: str, lookup=None) -> str:
    if not (flag("KREW_HANDS_DOCS_PRELOAD", "docs_preload") and docs_on()):
        return ""
    if not _DOCS_ASK.search(ask or "") or is_display_ask(ask) or re.search(r"\bt_[0-9a-f]{8}\b", ask or ""):
        return ""
    q = docs_query(ask)
    if not q:
        return ""
    try:
        if lookup is None:
            lib = str(Path(__file__).resolve().parent.parent / "lib")
            if lib not in sys.path:
                sys.path.insert(0, lib)
            import crissa_docs
            lookup = crissa_docs.lookup
        text = lookup(q)
    except Exception:  # noqa: BLE001 - no preload; the docs line still stands
        return ""
    if not text or text.startswith("krew_docs:"):
        return ""
    return ("krew_docs has ALREADY run for this job - its passages are below. Answer from them in two sentences "
            "and name the ref. Only if they do not answer it: ONE more krew_docs lookup with different words, or "
            "read_file of a path they name. Never search_files, never web.\n" + _cut(text, 3500, "krew_docs read <ref> has the rest") + "\n")



# its engines suspended (brave / duckduckgo / startpage rate-limited or CAPTCHA'd, bing matching one word), so the hands
# made 8-16 web_search calls of junk and ran out of steps. Hacker News' public search (hn.algolia.com, no key) answers
# "what are people saying about X this week" in one GET; this script fetches it and hands the titles over with the job.
# Config `web_preload` / env KREW_HANDS_WEB_PRELOAD. The real fix is SearXNG's engines (root-owned settings).
_WEB_ASK = re.compile(r"\b(?:search the web|on the web|web search|what (?:are )?(?:people|folks|they) (?:say|are saying|think)|"
                      r"this week|latest on|in the news|hacker news|reddit|angles?)\b", re.I)
_WEB_STOP = set((
    "a an the and or of for to in on at by with about this that these those what who how why is are was were be been it its "
    "me my our your their people folks say saying said says think search web look up find give two three four angles angle "
    "week weeks today latest news one some any from into than then them they we you i do does did please run focus get real "
    "posts post thread threads reddit hacker hn x twitter last days day r localllama selfhosted mid enthusiast practitioner "
    "skeptic distinct only each line source sources return answer tell").split())


def web_query(ask: str) -> str:
    words: list[str] = []
    for w in re.findall(r"[a-z][a-z0-9+-]+", (ask or "").lower()):
        if w not in _WEB_STOP and not w.isdigit() and w not in words:
            words.append(w)
    return " ".join(words[:5])


def web_preload(ask: str, fetch=None, now=None) -> str:
    if not flag("KREW_HANDS_WEB_PRELOAD", "web_preload") or not _WEB_ASK.search(ask or "") or _DOCS_ASK.search(ask or ""):
        return ""
    q = web_query(ask)
    if not q:
        return ""
    import json as _json
    import time as _time
    import urllib.parse
    import urllib.request
    url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode({
        "query": q, "optionalWords": q, "tags": "story", "hitsPerPage": 12,
        "numericFilters": f"created_at_i>{int((now or _time.time()) - 7 * 86400)}",
        "attributesToRetrieve": "title,url,points,num_comments,created_at"})
    try:
        if fetch is None:
            with urllib.request.urlopen(url, timeout=8) as r:
                data = _json.loads(r.read().decode("utf-8"))
        else:
            data = fetch(url)
    except Exception:  # noqa: BLE001 - no preload; the helper still has web_search
        return ""
    hits = sorted(data.get("hits") or [], key=lambda h: -(h.get("points") or 0))[:10]
    if not hits:
        return ""
    rows = "\n".join(f"- {h.get('title')} ({h.get('points') or 0} points, {h.get('num_comments') or 0} comments, "
                     f"{str(h.get('created_at') or '')[:10]}) {h.get('url') or ''}" for h in hits)
    return ("This week's Hacker News stories for this job are ALREADY fetched (web_search is degraded krew-wide). "
            "Answer from them - name the story behind each point. At most ONE web_extract of a story URL if you must; "
            "no web_search.\n" + rows + "\n")


def prompt_for(ask: str) -> str:
    return think_marker(ask) + (
        "You are Crissa's hands. The mouth already heard him. Do the job with "
        "the krew tools you have, then stop.\n"
        "Reply with one to three spoken sentences of what you found. No markdown. "
        "No TTS.\n"
        "Board counts: one team_board list. The first line is the tally — that is "
        "the number. Do not hunt sqlite, a shell, a URL, or the browser for a "
        "second count.\n"
        "Time questions: team_board list with the window in words (last two hours).\n"

        # a board question — hands ran a list and Crissa got a tally back (98 open / 121 done).
        # No ruling of hers landed on a card all day. A ruling is a write.
        "Ruling a card — the job names a card id (t_ and eight characters) with her ruling "
        "(try, park, later <date>, hands, pass, or her words): one team_board call, action "
        "answer, id the card, text her words exactly. It replies Landed on that id. Say "
        "Landed and the id back. A list is never the answer to a ruling; if the reply is "
        "not Landed, say exactly what it said.\n"
        "Reading a card, its body or its thread: one team_board call, action show, id the card. "
        "That reply IS the thread; say what it says.\n"

        "If you're asked to pick one word (pass/fail, yes/no) and the text you read doesn't contain it, say "
        "'the text I can see stops before the answer'. Never choose for it.\n"
        "Crissa's list: one team_list list.\n"
        "Glass: team_display / show_me. Never browser_*, never CDP, never invent "
        "a :port URL.\n"

        # to READ - the mouth speaks the helper's answer, so a helper that speaks says it twice.
        "Never speak: no team_speak or team_announce - the mouth says your answer. Put nothing on the "
        "glass or wall (show_me, team_display) unless the job asks to show or put something up.\n"
        "You have no terminal. Do not call terminal, do not install packages, "
        "do not sudo, do not spawn.\n"
        "Desktop only if the job is a native window: computer_use. Search: "
        "web_search. Memory: memory.\n"
        + (DOCS_LINE if docs_on() else "")
        + (LESSON_LINE if lessons_on() else "")
        + (SEARCH_LINE if search_hints_on() else "")
        + (screen_line() if cua_route_on() else "")
        + screen_errand_line(ask)
        + "When two tool calls have already answered the job, stop and speak.\n"
        + (DISPLAY_STEPS if is_display_ask(ask) and not is_card_read(ask) else "")
        + docs_preload(ask)
        + web_preload(ask)
        + f"\nJob: {ask.strip()}"
    )



# board" worked, then a re-run's helper reached for browser_vision / browser_navigate in its own
# headless browser, invented an aliyuncs URL, put the wall on the wrong page and hit the step cap
# twice. The prompt already said "Never browser_*"; a prompt does not hold. A display job now runs
# with the `team` toolset alone (team_display, show_me, ...), so browser_* is not there to call.
# Other jobs keep the full kit (e6818238 kept Crissa's browser on purpose). Kill KREW_HANDS_DISPLAY_ONLY=0.
DISPLAY_TOOLSETS = os.environ.get("KREW_HANDS_DISPLAY_TOOLSETS", "team")
_DISPLAY_ASK = re.compile(
    r"\b(show|put|pull|bring|throw|flip|switch)\b.{0,24}\b(up|me|on|to)\b"
    r"|\bdisplay\b|\b(on|onto|to) the (glass|wall|screen|display)\b|\bshow_me\b|\bteam_display\b",
    re.I,
)



# "tell me what it says", and the display pattern caught it - 28 of the 32 jobs dressed as display jobs

# the `team` kit (team_board is in it); they are not given the display steps. Kill: KREW_HANDS_CARD_READ=0.
_CARD_READ = re.compile(
    r"\bt_[0-9a-f]{8}\b|\b(?:thread|comments?|body|reason|ruling|decide queue|tally|how many)\b"

    # display" was dressed as a display job for the noun "display"; so was "check the numpy build in the
    # display/wall environment ... then fix it". Filing, checking and fixing are not putting a screen up.
    r"|\b(?:create|file|cut|open|write)\b[^.]{0,30}\b(?:card|ticket)\b|\b(?:check|fix|debug|diagnose)\b", re.I)


def is_card_read(ask: str) -> bool:
    if os.environ.get("KREW_HANDS_CARD_READ", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(_CARD_READ.search(ask or ""))


def is_display_ask(ask: str) -> bool:
    """A job whose whole point is to put something on the glass."""
    if os.environ.get("KREW_HANDS_DISPLAY_ONLY", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(_DISPLAY_ASK.search(ask or ""))



# so picking its tools before launch costs nothing - no cache to break, no round trip - where tooling up
# mid-run costs steps a four-step run does not have. The full kit is 53 tools / 44,469 B on every call;
# team + file + skill_view + web is 31 / 18,613 B and covered the tools actually called in 96.8 % of 1,997
# stored runs (scripts/hands-kit-backtest.py). What it leaves out is the browser (15 tools), session
# search, memory, vision, video, clarify and the two skill-writing tools; an ask that names one of those
# jobs - or presence, which the hands read through the browser - keeps the full kit.

# the default is ON; KREW_HANDS_LEAN_DEFAULT=0 is the way back to the whole CLI kit. The card-read path is
# team_board show - in `team`, which every kit carries - and team_board now takes the read words the hands
# kept inventing. Every run logs the kit it wore (~/.hermes/logs/hands-kit.jsonl).
LEAN_TOOLSETS = os.environ.get("KREW_HANDS_LEAN_TOOLSETS", "team,file,skill_read,web")
_FULL_ASK = re.compile(
    r"\b(?:browse|browser|website|web ?site|youtube|click|native window|desktop|screen ?shot|take a shot|"
    r"camera|photo|image|picture|video|remember|memory|recall|what did (?:i|he|we) say|last time|"
    r"earlier session|past session|session_id|presence|in the (?:room|house)|who(?:'s| is) (?:here|home|in|present)|"
    r"is (?:he|she|the user) (?:here|home|present|in))\b", re.I)


def lean_default_on() -> bool:
    return os.environ.get("KREW_HANDS_LEAN_DEFAULT", "1").strip().lower() not in ("0", "false", "no", "off")



# so it wore the lean kit - no computer_use - and the helper listed the ZOS Brave's tabs with team_display instead.
# With her private screen up, a job about HER screen, a window or an app on it goes to the full kit (computer_use is
# in it only while lib/crissa_her_screen's check passes). Switch KREW_HANDS_CUA_ROUTE=1 (OFF by default; the drill's
# after-arm turns it on so the night measures it).
_CUA_ASK = re.compile(r"\b(?:your (?:own |private )?screen|on her screen|windows? (?:that are |are )?open|open windows?|"
                      r"computer.use|capture (?:of )?your|native (?:app|window)|xfce|bot desktop)\b", re.I)


def cua_route_on() -> bool:
    return flag("KREW_HANDS_CUA_ROUTE", "cua_route")


def wants_full_kit(ask: str) -> bool:
    return bool(_FULL_ASK.search(ask or "")) or (cua_route_on() and bool(_CUA_ASK.search(ask or "")))



# outcomes-*.md, then show_me a report" wore the display kit (`team` alone), so read_file and search_files did not exist
# and the run ended "no file-reading tool" - twice in one lap. The display kit keeps browser_* out; the lean kit has
# no browser either, so a display job that must read or summarise a file wears the lean kit. Kill KREW_HANDS_READ_SHOW=0.
_READ_THEN_SHOW = re.compile(r"\b(?:read|summari[sz]e|newest|latest file|outcomes|from the file)\b|\.(?:md|json|csv|txt)\b|"
                             r"(?:^|\s)(?:~|/data|/home)/", re.I)


def reads_then_shows(ask: str) -> bool:
    if _off("KREW_HANDS_READ_SHOW"):
        return False
    return bool(_READ_THEN_SHOW.search(ask or ""))


def kit_for(ask: str, *, lean: bool | None = None) -> str | None:
    """The `-t` list for this job, or None for the profile's whole CLI kit."""
    lean = lean_default_on() if lean is None else lean
    if is_display_ask(ask) and lean and reads_then_shows(ask):
        return LEAN_TOOLSETS
    if is_display_ask(ask) and not (lean and is_card_read(ask)):
        return DISPLAY_TOOLSETS          # a read dressed as a display job keeps `team` until lean is armed
    if lean and not wants_full_kit(ask):
        return LEAN_TOOLSETS
    return None



# hermes loads every toolset minus her config's `agent.disabled_toolsets` (terminal, code_execution), which it
# subtracts last - cron jobs and delegated children included. The morning's first ruling (an explicit allow list)
# stays as the SAFETY FALLBACK: if her config cannot be read or no longer blocks both toolsets, the job wears her
# named CLI list instead, so a lost config line never hands her a shell. A hands child never gets `hands` itself
# (KREW_HANDS_NESTED). Kill: KREW_HANDS_FULL_EXPLICIT=0 (back to no -t, unconditionally); KREW_HANDS_BLOCKLIST=0
# (back to the allow list always).
CRISSA_CONFIG = HERMES_DIR / "profiles" / "crissa" / "config.yaml"
FULL_TOOLSETS_FALLBACK = "browser,clarify,computer_use,file,memory,search,session_search,skills,team,video,vision,web"
NEVER_IN_HANDS = {"hands", "terminal", "code_execution", "delegation", "cronjob", "process", "todo"}
MUST_BE_BLOCKED = {"terminal", "code_execution"}


def _off(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() in ("0", "false", "no", "off")


def full_kit(config: Path | None = None) -> str | None:
    """None (no -t: every toolset minus her block list) when her config blocks a shell and a code runner;
    otherwise her named CLI list, or the pinned list when the config cannot be read."""
    if _off("KREW_HANDS_FULL_EXPLICIT"):
        return None
    cfg: dict = {}
    try:
        import krew_yaml as yaml
        cfg = yaml.safe_load((config or CRISSA_CONFIG).read_text()) or {}
    except Exception:  # noqa: BLE001 - no yaml in this interpreter, or no config: the fallback
        cfg = {}
    blocked = {str(t).strip().lower() for t in (((cfg.get("agent") or {}).get("disabled_toolsets")) or [])}
    if not _off("KREW_HANDS_BLOCKLIST") and MUST_BE_BLOCKED <= blocked:
        return None
    names = [str(t) for t in ((cfg.get("platform_toolsets") or {}).get("cli") or [])]
    names = [n for n in names if n and n not in NEVER_IN_HANDS]
    return ",".join(names) if names else FULL_TOOLSETS_FALLBACK


def note_kit(ask: str, log: Path | None = None) -> None:
    """One line per run: the kit worn and the kit the lean default would pick. Never raises."""
    try:
        import json
        import time
        path = log or (HERMES_DIR / "logs" / "hands-kit.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": round(time.time(), 1), "worn": kit_for(ask) or "full", "lean_would": kit_for(ask, lean=True) or "full",
               "armed": lean_default_on(), "ask": " ".join((ask or "").split())[:160]}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001
        pass


def hermes_argv(ask: str, hermes: str | None = None) -> list[str]:
    bin_ = hermes or (str(HERMES) if HERMES.is_file() else "hermes")
    return [
        bin_,
        "-p",
        "crissa",
        "--cli",
        "--accept-hooks",
        "--yolo",
        "chat",
        *(["-t", kit] if (kit := final_kit(ask)) else []),
        "--oneshot",
        "--max-turns",
        str(MAX_TURNS),
        "--run-budget",
        str(max(30, RUN_BUDGET)),
        "-Q",
        "-q",
        prompt_for(ask),
    ]


FAILED = ("HANDS DID NOT FINISH - they ran out of steps. Tell him plainly it did not work. "
          "Do not say it is done, do not promise to follow up or to file anything.")



# already in the result she was handed at 07:58 and she answered with a confident cause and an offer to
# file a card; at 07:56 the helper's own forced summary said "Card's on the board" after calling the wrong
# tool, and she told him it was filed. So the result is built from the run's TOOL REPLIES, not from its
# summary (lib/hands_confirm, the session store read-only):
#   a run that stops short hands back ONE instruction and no partial findings at all;
#   every run ends with `confirmed:` lines - what a tool reply proves happened - or `confirmed: nothing`;
#   a summary that claims an effect no reply proves gets a NOT CONFIRMED line after it.
# The verdict lines go LAST (the plugin keeps the last 900 chars). Kill: KREW_HANDS_CONFIRM=0.
DID_NOT_FINISH = ("HANDS DID NOT FINISH. About this job say only: it did not work, the hands ran out of steps. "
                  "Report nothing they found. Do not say anything was filed, shown or done unless a confirmed: "
                  "line below says so. Do not promise to follow up or to file anything.")
SESSION_LINE = re.compile(r"(?m)^[ \t]*session_id:[ \t]*(\S+)[ \t]*$")
# The CLI checks `-t` names before its plugin thread has declared `skill_read`, so a lean run can print

# and skill_view works). It is the launcher's noise, never the job's finding.
KIT_NOISE = re.compile(r"(?m)^[ \t]*Warning: Unknown toolsets: (?:skill_read|docs)(?:, (?:skill_read|docs))*[ \t]*\n?")

KEEP = 880


def _cut(text: str, n: int, more: str) -> str:
    text = text or ""
    return text if len(text) <= n else f"{text[:n]} [cut at {n} of {len(text)} chars; {more}]"


def _confirm():
    """lib/hands_confirm, or None when the switch is off or the library is not beside this script."""
    if os.environ.get("KREW_HANDS_CONFIRM", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    lib = str(Path(__file__).resolve().parent.parent / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    try:
        import hands_confirm
        return hands_confirm
    except Exception:  # noqa: BLE001 - never a traceback in her ear: the old result stands
        return None



# OFF by default. On: KREW_HANDS_CLAIM_TRUTH=1, or the file ~/.hermes/hands-claim-truth.on (read on every run, so
# no restart - this script is spawned per call). KREW_HANDS_CLAIM_TRUTH=0 beats the file.
CLAIM_TRUTH_FILE = KH.hermes_home(passwd=True) / "hands-claim-truth.on"


def claim_truth() -> bool:
    raw = os.environ.get("KREW_HANDS_CLAIM_TRUTH", "").strip().lower()
    if raw:
        return raw in ("1", "true", "yes", "on")
    return CLAIM_TRUTH_FILE.exists()


def state_db() -> Path:
    home = os.environ.get("HERMES_HOME", "").strip()
    if home and (Path(home) / "state.db").is_file():
        return Path(home) / "state.db"
    return HERMES_DIR / "profiles" / "crissa" / "state.db"


def report(out: str, rc: int, *, db: Path | None = None, ask: str | None = None) -> str:
    """What the mouth is handed for a finished child: the summary, then what its tool replies prove."""
    hc = _confirm()
    if hc is None:
        return failed_line(out, rc) if rc != 0 else (_cut(out, 1200, "the helper's session has the rest") if out else f"hands rc={rc}")
    m = SESSION_LINE.search(out or "")
    text = KIT_NOISE.sub("", SESSION_LINE.sub("", out or "")).strip()
    facts = hc.run_facts(db or state_db(), m.group(1)) if m else []
    effects = hc.confirmed(facts)
    lines = hc.confirmed_lines(effects) or ["confirmed: nothing"]
    lines += hc.not_done_lines(hc.not_done(facts)) if hasattr(hc, "not_done") else []
    if rc != 0:
        return "\n".join([DID_NOT_FINISH, *lines])
    miss = hc.unconfirmed(text, effects)
    if miss:
        text = hc.strip_claims(text, miss)      # the false sentence itself never reaches her
        lines.append(hc.not_confirmed_line(miss))
    if claim_truth() and hasattr(hc, "false_success"):
        try:
            bad = set(hc.false_success(text, effects, facts, ask=ask))
        except TypeError:                     # an older hands_confirm without the ask
            bad = set(hc.false_success(text, effects, facts))
        if bad:
            text = " ".join(x for x in hc.sentences(text) if x not in bad).strip()
            lines.append(hc.truth_line(facts, effects))
    tail = "\n".join(lines)
    body = _cut(text, max(200, KEEP - len(tail) - 1), "the helper's session has the rest") if text else ""
    return f"{body}\n{tail}".strip()


def failed_line(out: str, rc: int) -> str:
    """What reaches Crissa when a run stops short: at most 500 chars of the helper's
    own summary, then the verdict (last, so a 900-char tail always carries it)."""
    got = " ".join((out or "").split())
    got = _cut(got, 500, "the helper's session has the rest") if got else "(no summary)"
    return f"What the hands got to (rc {rc}): {got}\n{FAILED}"



# script under the hermes tree and hermes appended that tree's AGENTS.md to the tool result ("Subdirectory
# context discovered") - 24,040 chars became 53,107, +15,719 prompt tokens in one step of a four-step run.
# Krew patch 120 gives hermes the switch (`--ignore-rules` would also drop her SOUL and the memory
# provider, which the hands need for "recall from memory"). Kill: KREW_HANDS_SUBDIR_HINTS=1.
def final_kit(ask: str) -> str | None:
    """The `-t` list the child wears: the job's kit, the switched extras, then the CUA guard."""
    return guard_cua(with_extras(kit_for(ask) or full_kit()))



# hermes points the cua-driver at her Bot Desktop while it runs, but if the screen died mid-run it would fall back
# to the process env - display :0, his desktop. So a child whose kit carries computer_use is born with her
# screen's DISPLAY/XAUTHORITY and no Wayland handle: a dead screen then fails the click, it never lands on his.
def pin_screen(env: dict, kit: str | None, *, env_file: Path | None = None) -> dict:
    if kit is not None and "computer_use" not in kit.split(","):
        return env
    if not private_screen_ready(env_file):
        return env          # the guard already took computer_use out of this kit
    lib = str(Path(__file__).resolve().parent.parent / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import crissa_her_screen       # one env builder for her hands and her isolation check
    return crissa_her_screen.screen_env(env, env_file or BOT_DESKTOP_ENV)


def child_env(base: dict | None = None, kit: str | None = "") -> dict:
    env = dict(base if base is not None else os.environ)
    if kit != "":
        env = pin_screen(env, kit)
    env[NESTED_FLAG] = "1"
    if docs_on():
        env["KREW_HANDS_DOCS"] = "1"      # the child's plugin registers krew_docs from its env
    if flag("KREW_PARALLEL_BOARD_READS", "parallel_board_reads"):
        env["KREW_PARALLEL_BOARD_READS"] = "1"   # team-crissa registers team_board show with hermes patch 200
    env.pop("HERMES_TUI_SKILLS", None)
    if env.get("KREW_HANDS_SUBDIR_HINTS", "0").strip().lower() not in ("1", "true", "yes", "on"):
        env["HERMES_SUBDIRECTORY_HINTS"] = "0"
    return env


def refuse_reason(ask: str, *, nested: str | None = None, mode: str | None = None) -> str | None:
    if not (ask or "").strip():
        return "hands: empty ask"
    flag = (nested if nested is not None else os.environ.get(NESTED_FLAG, "")).strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return "hands: already in a CLI run — do the job yourself"
    m = mode if mode is not None else gpu_mode()
    if m in ("render", "off"):
        return f"hands sit out {m} — ops is parked. Standard occupancy."
    if m == "beast":
        return "hands sit out Beast — ops is parked. Ask again in Standard."
    return None


def _lock_fd() -> int | None:
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return -1
    return fd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ask", nargs="+", help="the job, in plain words")
    args = ap.parse_args(argv)
    ask = " ".join(args.ask).strip()
    why = refuse_reason(ask)
    if why:
        print(why)
        return 2 if why.startswith("hands: empty") else 3
    fd = _lock_fd()
    if fd == -1:
        print("Hands are already busy. One job at a time.")
        return 5
    try:
        cmd = hermes_argv(ask)
        note_kit(ask)
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                env=child_env(kit=final_kit(ask)),
                cwd=str(Path.home()),
            )
        except subprocess.TimeoutExpired:
            print("Hands hit the time cap. Ask me to try a smaller piece.")
            return 4
        out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()

        # max_iterations after asking the helper for a summary, and that summary read as a
        # finished job - Crissa promised "I'll get back to you" and "filed it as a card for Ada"

        print(report(out, r.returncode, ask=ask))
        return min(r.returncode, 125) if r.returncode != 0 else 0
    finally:
        if fd is not None and fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
