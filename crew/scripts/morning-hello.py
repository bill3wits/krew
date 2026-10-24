#!/usr/bin/env python3
"""morning-hello — the daily brief moment (design-krew-face-ui.md F2).

Reads the person's brief spec, then for each card: fetch → show → speak —
the card enters the stage exactly as Zoey starts its sentence (speech is
spoken through the Wall's player, /v1/speak, and the brief waits
for each line on /v1/state, so sync is structural, not timed).





   "cards": ["weather", "headline", "house"]}
Cards gracefully skip when their source is unreachable. Local sources only:
wttr.in (geo-IP weather), SearXNG :8080 (headlines), face-shell /v1/house.
"""

from __future__ import annotations

import argparse
import os
import json
import re
import sys
import urllib.parse
import time
import urllib.error
import urllib.request
from pathlib import Path


def _local(port: int, path: str = "") -> str:
    """A local service URL: the reference install's port by default, each movable (KREW_PORT_<port>, KREW_LOCAL_HOST)."""
    host = os.environ.get("KREW_LOCAL_HOST") or "127.0.0.1"
    return f"http://{host}:{os.environ.get(f'KREW_PORT_{port}') or port}{path}"


FACE = _local(8088, "")
SEARX = _local(8080, "")
FACE_DOWN: list = []      # face-shell's first failure, said once
# her voice is the Wall's player (Crissa): krew never runs a Crissa script (2026-10-06)
ZOEYOS_PLAYER = os.environ.get("ZOEYOS_PLAYER", "http://127.0.0.1:8100").rstrip("/")
# how long one line may stay on the air before the brief goes on (seconds; 0 = wait as long as it takes)
LINE_WAIT_S = float(os.environ.get("KREW_MORNING_LINE_WAIT_S") or 120)
PEOPLE = Path(os.path.expanduser(os.environ.get("KREW_PEOPLE_DIR") or "~/.krew/people"))

DEFAULT_BRIEF = {
    "wake_hour": 7,
    "greeting": "Good morning, *{name}*.",
    "cards": ["weather", "headline", "house"],
}


def fetch(url: str, timeout: float = 8.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode()


def express(payload: dict) -> None:
    """A card or scene on face-shell's stage. Down: said once, and the brief carries on (she still speaks)."""
    req = urllib.request.Request(
        f"{FACE}/v1/express", json.dumps(payload).encode(),
        {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=4).read()
    except (OSError, ValueError) as e:
        if not FACE_DOWN:
            FACE_DOWN.append(str(e))
            print(f"  face-shell is not answering at {FACE} ({type(e).__name__}: {e}): "
                  "the cards are not shown on its stage; the brief carries on")


def _player(method: str, path: str, body: dict | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    """The Wall's player. (0, {"error": ...}) when nothing answers: Crissa is not installed or not running."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ZOEYOS_PLAYER + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8") or "{}")
            return r.status, out if isinstance(out, dict) else {}
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            out = {}
        return e.code, out if isinstance(out, dict) else {}
    except (OSError, ValueError) as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def _no_player() -> str:
    return f"Crissa not installed (no player at {ZOEYOS_PLAYER}) — morning hello not spoken"


def speak(text: str, dry: bool) -> None:
    """One line through the Wall's player (POST /v1/speak, queued), then wait until it is spoken: the next card
    enters as she starts the next sentence, so the sync stays structural."""
    if dry:
        print(f"  (would speak) {text}")
        return
    code, out = _player("POST", "/v1/speak", {"text": text, "by": "krew-morning", "queue": True})
    if code == 0:
        raise SystemExit(_no_player())
    if code >= 400:
        print(f"  the player did not take the line ({code}: {out.get('error') or out.get('why') or 'no reason'})")
        return
    t0 = time.monotonic()
    while True:
        code, st = _player("GET", "/v1/state", timeout=4.0)
        if code == 0:
            raise SystemExit(_no_player())
        if not st.get("on_air") and not st.get("say_pending"):
            return
        if LINE_WAIT_S and time.monotonic() - t0 > LINE_WAIT_S:
            print(f"  still on the air after {LINE_WAIT_S:.0f}s (KREW_MORNING_LINE_WAIT_S); going on")
            return
        time.sleep(0.3)


def card_weather():
    try:
        now = fetch("https://wttr.in/?format=%C+%t", 6).strip()
        later = fetch("https://wttr.in/?format=%c", 4).strip()
        cond = re.sub(r"\s+", " ", now).replace("+", "")
        spoken = re.sub(r"(\d+)\s*°F", r"\1 degrees", cond, flags=re.I)
        return ({"kind": "card", "title": "Weather", "body": cond,
                 "target_id": "brief-weather"},
                f"It's {spoken.lower()} right now.")
    except Exception:
        return None


def card_headline():
    try:
        raw = json.loads(fetch(
            f"{SEARX}/search?q={urllib.parse.quote('news headlines')}"
            "&categories=news&format=json", 8))
        results = raw.get("results") or []
        meta = re.compile(
            r"headlin|bulletin|top \d|breaking news|latest news|news from|"
            r"live updates?:?\s*$", re.I)
        # first result shaped like an actual story, not an aggregator page
        for r in results:
            t = re.sub(r"\s+", " ", r.get("title") or "").strip()
            if 40 <= len(t) <= 140 and not meta.search(t) and "-" not in t[:12]:
                return ({"kind": "card", "title": "Headline", "body": t,
                         "kv": {"via": (r.get("engine") or "news")},
                         "target_id": "brief-headline"},
                        f"In the news: {t}.")
        return None
    except Exception:
        return None


def card_house():
    try:
        h = json.loads(fetch(f"{FACE}/v1/house", 5))
        names = [p["name"] for p in (h.get("presence") or {}).get("people", [])
                 if p.get("certain")]
        ear = "listening" if h.get("ear_open") else "resting"
        body = f"Ear {ear} · everyone's lanes are green"
        if names:
            body = f"{', '.join(names)} home · " + body
        return ({"kind": "card", "title": "House", "body": body,
                 "target_id": "brief-house"},
                "The krew is green and I'm listening.")
    except Exception as e:
        print(f"  the home card is skipped: face-shell did not answer at {FACE} ({type(e).__name__})")
        return None


def _searx_first(query: str, meta_re: str = r"$^"):
    raw = json.loads(fetch(
        f"{SEARX}/search?q={urllib.parse.quote(query)}&format=json", 8))
    bad = re.compile(meta_re, re.I)
    for r in raw.get("results") or []:
        t = re.sub(r"\s+", " ", r.get("title") or "").strip()
        if 30 <= len(t) <= 140 and not bad.search(t):
            return t
    return None


def card_markets():
    try:
        t = _searx_first("S&P 500 Dow Nasdaq close today",
                         r"quote|watchlist|ticker|how to|what is")
        if not t:
            return None
        return ({"kind": "card", "title": "Markets", "body": t,
                 "target_id": "brief-markets"}, f"Markets: {t}.")
    except Exception:
        return None


def card_on_this_day():
    try:
        import datetime as _dt
        day = _dt.date.today().strftime("%B %-d")
        t = _searx_first(f"on this day {day} in history",
                         r"on this day|wikipedia|britannica|history\.com|events on")
        if not t:
            return None
        return ({"kind": "card", "title": "On this day", "body": t,
                 "target_id": "brief-otd"}, f"On this day: {t}.")
    except Exception:
        return None


# The menu — broad, generic, cool. Unwired kinds are listed in the interview
# as "coming" so choosing them is harmless (card just doesn't render yet).
CARD_SOURCES = {"weather": card_weather, "headline": card_headline,
                "house": card_house, "markets": card_markets,
                "on_this_day": card_on_this_day}
CARD_MENU = ["weather", "headline", "markets", "on_this_day", "house",
             "calendar (coming)", "commute (coming)", "word of the day (coming)"]


def load_brief(person: str) -> dict:
    p = PEOPLE / person / "brief.json"
    if p.exists():
        try:
            return {**DEFAULT_BRIEF, **json.loads(p.read_text())}
        except Exception:
            pass
    return dict(DEFAULT_BRIEF)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--person", default=os.environ.get("KREW_PERSON") or "user")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    if not args.dry and _player("GET", "/v1/state", timeout=4.0)[0] == 0:
        print(_no_player())
        sys.exit(2)
    brief = load_brief(args.person)
    greeting = brief["greeting"].replace("{name}", args.person.title())

    express({"kind": "scene", "mood": "morning", "moment": "morning_hello",
             "greeting": greeting})
    speak(re.sub(r"\*", "", greeting), args.dry)

    shown = 0
    for name in brief["cards"]:
        src = CARD_SOURCES.get(name)
        if not src:
            continue
        got = src()
        if not got:
            continue  # unreachable source = card simply doesn't appear
        widget, sentence = got
        widget["pin"] = True
        express(widget)          # card enters…
        speak(sentence, args.dry)  # …as she speaks it (blocking = sync)
        shown += 1

    speak("That's your morning. I'm here when you need me.", args.dry)
    express({"kind": "scene", "mood": "morning"})  # coffee recedes, mood stays
    print(f"morning-hello: {shown} cards for {args.person}")


if __name__ == "__main__":
    main()
