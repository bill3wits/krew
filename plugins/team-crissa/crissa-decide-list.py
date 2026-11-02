#!/usr/bin/env python3
"""crissa-decide-list — put the Decide-with-User queue on the document sheet.

The queue is board state, not a second list: cards that are `blocked` with

decide"); Crissa shepherds when they go up, and owns taking them down.

WHY THE DOC SHEET AND NOT THE WALL: the wall's right side (dial, seat markers,
where the speaker is) may never be covered. The doc surface floats in its own
8.5x11 window on top, auto-opens when written, and closes itself when cleared
or when its TTL runs out. `display/voiceonly-doc.html` already carries the
hover-× and Escape, both of which POST a real clear — so a User who wants it
gone can always get rid of it himself.

PRIVACY IS THE GATE. krew-mode is `user_auth_only`: speak_to is the
authenticated user, `speak_to_others: false`. His board business is his. So
this shows ONLY when the room holds exactly him. Any second body — guest,
unknown, uncertain — and the sheet does not go up, and comes down if it is up.
That is the same stance crissa-needs already takes ("Guest / silent: do not
mention the board").

Commands:
    show     gate, render, POST, then VERIFY the glass and report what it says
    clear    take it down now
    status   what would happen, and why — writes nothing
    watch    hold the sheet only while AUTH holds; clear on user exit

Exit 0 = the glass is in the state this command intended. Exit 1 = it is not.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
import krew_hermes as KH  # noqa: E402  where Hermes lives


def _local(port: int, path: str = "") -> str:
    """A local service URL: the reference install's port by default, each movable (KREW_PORT_<port>, KREW_LOCAL_HOST)."""
    host = os.environ.get("KREW_LOCAL_HOST") or "127.0.0.1"
    return f"http://{host}:{os.environ.get(f'KREW_PORT_{port}') or port}{path}"


VO = os.environ.get("CRISSA_PLAYER", "http://127.0.0.1:8100").rstrip("/")   # the Wall's player (Crissa)
ME = (os.environ.get("KREW_PERSON") or "user").strip().lower()        # whose board the sheet is
# the presence rail's live-camera block (faces, locked, age_s): KREW_PRESENCE_CAMERA_KEY names it
CAMERA_KEY = os.environ.get("KREW_PRESENCE_CAMERA_KEY") or "camera"
PRESENCE = VO + "/v1/rail/presence/v1/presence"   # her presence feed, through the player's rail (Crissa)
KREW_MODE = KH.hermes_home() / "krew-mode.json"
BOARDS = KH.hermes_home() / "kanban/boards"
HERMES = Path.home() / ".local/bin/hermes"
LOG = KH.hermes_home() / "logs/crissa-decide-sheet.log"
CONSENT = KH.hermes_home() / "crissa-decide-consent.json"
CONSENT_TTL = 900     # his yes covers this sitting, not the rest of the day

DOC_TITLE = "Needs you"   # fallback only — show() uses the card title
DOC_TTL = 900          # 15 min. The sheet is a prompt, not wallpaper.
MAX_ITEMS = 6          # more than this is a board problem, not a sheet problem


# --------------------------------------------------------------------------
# room
# --------------------------------------------------------------------------

def auth_user() -> str:
    try:
        return str(json.loads(KREW_MODE.read_text())["auth"]["user_id"]).lower()
    except Exception:
        return ME


def gate(p: dict, *, me: str = ME) -> tuple[str, str, int]:
    """(state, why, headcount) from a presence object. Unclear is always a no.

    Privacy follows AUTH, not mill occupancy. Busy-on drops AUTH and silences
    mill, so headcount can still name him while he is in the doorway — that is
    the form-from-hidden race on a fast walk-in. ``user_auth`` and ``room_busy``
    are the storm signal; occupancy is the guest/alone check after AUTH holds.

    Three states, because "not alone" is not the same refusal as "not here":

      clear  alone, confirmed, AUTH held — put it up
      ask    AUTH holds but he has company — HE decides, out loud
      no     nobody to ask, or we cannot trust who is in the room

    ``ask`` exists because a flat refusal makes the wrong call for him:
    company in the room is not automatically company he is hiding the board
    from, and that judgement is his. The default still stays closed — ``ask``
    never shows on its own, it only unlocks his spoken yes.
    """
    me = (me or ME).strip().lower()
    buf = p.get(CAMERA_KEY) if isinstance(p.get(CAMERA_KEY), dict) else {}
    locked = str(buf.get("locked") or "").strip().lower()
    faces = buf.get("faces") if isinstance(buf.get("faces"), list) else []
    try:
        age = float(buf["age_s"]) if buf.get("age_s") is not None else None
    except (TypeError, ValueError):
        age = None
    live_me = locked == me or any(
        str((f or {}).get("name") or "").strip().lower() == me for f in faces
    )
    pending = bool(buf.get("pending_look") or p.get("f2f") and buf.get("pending_look"))
    # One camera cannot see the room. "User alone" is an occupancy guess.
    # Ask-or-no: visor User → spoken yes; no User lock → no.
    if not live_me and not pending:
        return "no", "live camera does not show User — sheet stays private", 0
    return "ask", "live camera has User; one spoken yes (cannot see the rest of the room)", 1


def room() -> tuple[str, str, int]:
    """(may_show, why). Conservative: anything unclear is a no."""
    try:
        with urllib.request.urlopen(PRESENCE, timeout=4) as r:
            p = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            why = (json.loads(e.read() or b"{}") or {}).get("why") or e.reason
        except ValueError:
            why = e.reason
        return "no", f"presence through the player said {e.code}: {why} — not showing the board", 0
    except Exception as e:
        return "no", f"Crissa not installed (no player at {VO}: {type(e).__name__}) — not showing his board", 0
    return gate(p, me=auth_user())


def grant_consent(head: int) -> None:
    """Record his spoken yes, with the room it was given for.

    Consent is for THIS room. If another body arrives it lapses — he agreed to
    show it in front of the people who were there when he said yes, not in
    front of whoever walks in next.
    """
    try:
        CONSENT.write_text(json.dumps({"at": int(time.time()), "headcount": head}))
    except Exception:
        pass


def consent_holds(head: int) -> bool:
    try:
        c = json.loads(CONSENT.read_text())
    except Exception:
        return False
    if int(time.time()) - int(c.get("at") or 0) > CONSENT_TTL:
        return False
    # No occupancy count. Consent is this sitting (TTL), not a headcount.
    return True


def drop_consent() -> None:
    try:
        CONSENT.unlink()
    except Exception:
        pass


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------

def current_board_db() -> Path | None:
    """The board `hermes kanban` is pointed at (the ● row)."""
    try:
        out = subprocess.run([str(HERMES), "kanban", "boards"], capture_output=True,
                             text=True, timeout=90).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if line.strip().startswith("●"):
            parts = line.replace("●", "", 1).split()
            if parts:
                db = BOARDS / parts[0] / "kanban.db"
                return db if db.is_file() else None
    return None


def queue(db: Path) -> list[dict]:
    """blocked + needs_input, longest-waiting first, with the question text.

    `kanban list` cannot do this: its rows drop block_kind and it has no kind
    filter, so the board file is the only place the queue actually exists.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:

        # list were widened that day; this — the third reader of the same queue, the one
        # that renders the screen she actually decides from — was missed, so a question
        # parked in `scheduled` stayed off the sheet. Same rule, same kill switch, and only
        # `needs_input`: a park without a question is the liveness gate's, not hers.
        parked = ("" if os.environ.get("KREW_CRISSA_SEES_PARKED", "1").strip().lower()
                  in ("0", "false", "no", "off")
                  else " OR (status='scheduled' AND block_kind='needs_input')")
        rows = conn.execute(
            "SELECT id, title, assignee FROM tasks "
            f"WHERE (status='blocked' AND block_kind='needs_input'){parked} "
            "ORDER BY created_at ASC"
        ).fetchall()
        items = []
        for r in rows:
            reason = ""
            ev = conn.execute(
                "SELECT payload FROM task_events WHERE task_id=? AND kind='blocked' "
                "ORDER BY id DESC LIMIT 1", (r["id"],)
            ).fetchone()
            if ev and ev["payload"]:
                try:
                    reason = str(json.loads(ev["payload"]).get("reason") or "")
                except Exception:
                    reason = ""
            items.append({"id": r["id"], "title": r["title"],
                          "assignee": r["assignee"], "question": reason})
        return items
    finally:
        conn.close()


def card_headline(it: dict) -> str:
    """The notify is a headline. Never a body dump without a title."""
    t = " ".join(str(it.get("title") or "").split())
    return (t[:80] if t else DOC_TITLE)


def sheet(items: list[dict]) -> str:
    """Popup body: markdown headlines (card titles), then the one ask.


    like it. Keep headlines. The 8.5x11 sheet is the method — disruptive,
    closeable (× / Escape / TTL).
    """
    out: list[str] = []
    for it in items[:MAX_ITEMS]:
        head = card_headline(it)
        out.append(f"## {head}")
        q = " ".join(str(it.get("question") or "").split())
        if q and q.casefold() != head.casefold():
            if len(q) > 240:
                q = q[:239].rstrip() + "…"
            out.append(q)
        out.append("")
    extra = len(items) - MAX_ITEMS
    if extra > 0:
        out.append(f"(+{extra} more waiting)")
        out.append("")
    out.append("Say the word and I'll put your answer back on the card.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# glass
# --------------------------------------------------------------------------

def post_screen(body: dict) -> tuple[bool, str]:
    req = urllib.request.Request(
        f"{VO}/v1/screen", method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return True, r.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return False, f"the player at {VO} said {e.code}"
    except Exception as e:
        return False, f"Crissa not installed (no player at {VO}: {type(e).__name__})"


def read_glass() -> dict:
    """What the Wall's card shows (the player's GET /v1/screen). No player: {"_no_player": why}."""
    try:
        with urllib.request.urlopen(f"{VO}/v1/screen", timeout=4) as r:
            out = json.loads(r.read())
        return (out.get("screen") or {}) if isinstance(out, dict) else {}
    except urllib.error.HTTPError:
        return {}
    except Exception as e:
        return {"_no_player": f"Crissa not installed (no player at {VO}: {type(e).__name__})"}


def clear(reason: str) -> int:
    """Take it down, then prove it is down. Never report an accepted POST."""
    ok, detail = post_screen({"clear": ["doc"]})
    if not ok:
        print(f"FAILED to clear the sheet: {detail}")
        return 1
    time.sleep(0.4)
    if read_glass().get("doc"):
        print("POST accepted but the sheet is STILL UP — treat it as up.")
        return 1
    print(f"sheet down ({reason})", flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} down: {reason}\n")
    except Exception:
        pass
    return 0


def show(consented: bool = False) -> int:
    state, why, head = room()
    if state == "no":
        drop_consent()
        if read_glass().get("doc"):
            print(f"not showing: {why} — and one was up, taking it down")
            return clear(why)
        print(f"not showing: {why}")
        return 1
    if state == "ask" and not consented:
        print(f"ASK FIRST: {why}.\n"
              f"Say it out loud to him — his board, his call. "
              f"If he says yes, come back with action='show_consented'. "
              f"If he says no or does not answer, leave it down and do not "
              f"mention it again this turn.")
        return 2
    if state == "ask":
        grant_consent(head)   # head is the EFFECTIVE occupancy (see room())

    db = current_board_db()
    if db is None:
        print("could not resolve the current board — nothing shown")
        return 1

    items = queue(db)
    if not items:
        if read_glass().get("doc"):
            return clear("queue empty")
        print("nothing needs him — sheet left down")
        return 0

    head = card_headline(items[0])
    ok, detail = post_screen({
        "doc": sheet(items),
        "doc_title": head,
        "doc_ttl": DOC_TTL,
    })
    if not ok:
        print(f"FAILED to write the sheet: {detail}")
        return 1

    # Report the glass, not the POST.
    time.sleep(0.4)
    live = read_glass()
    if not live.get("doc"):
        print("POST accepted but the sheet is NOT up — do not tell him it is.")
        return 1
    n = len(items)
    print(f"sheet UP with {n} question{'' if n == 1 else 's'} "
          f"(title={live.get('doc_title')!r}) — {why}")
    for it in items[:MAX_ITEMS]:
        print(f"  {it['id']}  {(it['question'] or it['title'])[:70]}")
    return 0


def status() -> int:
    state, why, head = room()
    db = current_board_db()
    items = queue(db) if db else []
    live = read_glass()
    if live.get("_no_player"):
        print(live["_no_player"])
    label = {"clear": "CLEAR to show", "ask": "ASK HIM FIRST", "no": "no"}[state]
    print(f"room:   {label} — {why}")
    print(f"board:  {db.parent.name if db else '(unresolved)'}")
    print(f"queue:  {len(items)} needs_input")
    print(f"glass:  {'UP' if live.get('doc') else 'down'}"
          f"{' — ' + repr(live.get('doc_title')) if live.get('doc') else ''}")
    for it in items[:MAX_ITEMS]:
        print(f"  {it['id']}  {(it['question'] or it['title'])[:70]}")
    return 0


def watch(interval: float = 5.0) -> int:
    """Hold the sheet only while he is alone and authed.

    user_exit drops AUTH, and the sheet must not outlive it — a list of his
    business sitting on the glass for whoever walks in next is the exact thing
    the privacy stance forbids. The × and the TTL are backstops; this is the
    one that runs.
    """
    print("watching — sheet comes down on AUTH-off, Busy, or a second body (Ctrl-C to stop)")
    while True:
        try:
            if read_glass().get("doc"):
                state, why, head = room()
                if state == "no":
                    drop_consent()
                    clear(why)
                elif state == "ask" and not consent_holds(head):
                    # Either he never consented, or someone new walked in and
                    # the yes he gave no longer covers this room.
                    drop_consent()
                    clear(why)
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nwatch stopped — sheet left as it is")
            return 0
        except Exception as e:
            print(f"watch tick failed ({type(e).__name__}) — holding")
            time.sleep(interval)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "show":
        return show()
    if cmd in ("show_consented", "show-consented"):
        return show(consented=True)
    if cmd == "clear":
        drop_consent()
        return clear("asked to")
    if cmd == "status":
        return status()
    if cmd == "watch":
        return watch()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
