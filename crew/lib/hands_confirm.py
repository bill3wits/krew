"""She says it was filed or done only when a tool reply says so (the User 2026-09-30).

09-30 07:56: Crissa's hands called the wrong tool, ran out of steps, and their forced summary read
"Card's on the board"; the mouth told him "Filed it as a research card with Ada". No card existed.
07:58: the hands' result ended "tell him plainly it did not work" and she answered with a confident
root cause and an offer to file a card. A rule in a prompt had already been tried and ignored, so
the check is here, against the run's own tool replies:

  facts      what each tool call of a run REPLIED (from the session store, read-only)
  confirmed  the replies that prove an effect: a card filed, a ruling landed, a comment, the list,
             a screen on the glass
  claims     what a sentence says happened
  unconfirmed  claims with no reply behind them

A claim needs the reply of the tool that does that thing. A reply that says "Nothing changed" or
"need a clip" confirms nothing, whatever the sentence after it says.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

CARD = r"t_[0-9a-f]{8}"
# a reply -> the effect it proves (kind, detail)
PROOFS: tuple[tuple[str, re.Pattern], ...] = (
    ("filed", re.compile(rf"^Filed for (?P<who>[\w-]+): .*?\((?P<id>{CARD})\)", re.S)),
    ("landed", re.compile(rf"^Landed on (?P<id>{CARD})")),
    ("commented", re.compile(rf"^Commented on (?P<id>{CARD})")),
    ("unblocked", re.compile(rf"^Unblocked (?P<id>{CARD})")),
    ("completed", re.compile(rf"^Completed (?P<id>{CARD})")),
    # team-crissa still words this reply with her old name until its own rename lands (2026-10-10): both are read
    ("listed", re.compile(r"^On (?:Crissa|Crissa)'s list\b")),
    ("list-done", re.compile(r"^Done: ")),
)
# The art tools read the glass back before they answer (team-crissa _art_to_glass: "on the glass: N lines" or "... did
# NOT land"); left out, a banner that was up read `confirmed: nothing` and she said it had not landed (X Spaces drill
# 2026-10-05).
DISPLAY_TOOLS = ("show_me", "team_display", "team_page", "team_express", "team_show", "team_banner", "team_glyph_art",
                 "team_wall_photo")
ART_TOOLS = ("team_banner", "team_glyph_art", "team_wall_photo")
REFUSAL = re.compile(r"NOTHING CHANGED|Nothing changed|Nothing filed|did not land|did NOT land|need a clip|empty ask|"
                     r"\bneeds (?:what|id|title|text)=|unknown action|Which screen\?|is one of:", re.I)
# a sentence -> the effect it claims
CLAIMS: tuple[tuple[str, re.Pattern, tuple[str, ...]], ...] = (
    ("filed", re.compile(
        r"\b(?:filed|created|opened|logged|raised|queued)\b[^.!?]{0,60}\b(?:card|ticket|task|board|ada|steward|bash|vera|lila|scout)\b"
        r"|\bput (?:it|that|this|a card|a ticket)\b[^.!?]{0,30}\bon the board\b"
        r"|\bcard(?:'s| is| was)? (?:now )?(?:on the board|filed|created)\b"
        r"|\bfiled (?:it|that|this|them|those)\b"
        r"|\b(?:it's|it is|that's) (?:filed|on the board)\b", re.I), ("filed",)),
    ("ruled", re.compile(r"\b(?:landed|ruled|answered|unblocked|closed|completed)\b[^.!?]{0,40}\b(?:card|t_[0-9a-f]{8}|ruling)\b"
                         r"|\bruling (?:is |has )?landed\b", re.I),
     ("landed", "unblocked", "completed", "commented")),
    ("shown", re.compile(r"\b(?:board|kanban|page|dashboard|report|sheet|screen|panel|it|that)(?:'s| is| are)\s+(?:now\s+)?"
                         r"(?:up|showing)\b[^.!?]{0,20}\b(?:screen|glass|wall)\b"
                         r"|\b(?:is|are|it's|that's)\s+(?:now\s+)?on (?:the )?(?:screen|glass|wall)\b"
                         r"|\b(?:put|pulled|brought)\b[^.!?]{0,30}\bon (?:the )?(?:screen|glass|wall)\b", re.I),
     ("shown",)),
    ("listed", re.compile(r"\b(?:added|put)\b[^.!?]{0,40}\b(?:my|your|the|crissa's) list\b|\b(?:it's|it is|that's) on (?:my|your) list\b", re.I),
     ("listed",)),
)
# A sentence about the future, a condition, an offer or a failure claims nothing.
NOT_A_CLAIM = re.compile(r"\b(?:rather than|instead of|i'll|i will|i can|i could|i would|i'd|we'll|want me to|shall i|should i|do you want|if |"
                         r"ask me|can't|cannot|couldn't|could not|didn't|did not|won't|not |never |no )", re.I)
SAY = {
    "filed": "no tool reply shows a card was filed",
    "ruled": "no tool reply shows a ruling landed on a card",
    "shown": "no tool reply shows anything went up on the glass",
    "listed": "no tool reply shows anything was put on the list",
}


def run_facts(db: Path, session_id: str) -> list[dict]:
    """Every tool call of one run with its reply, oldest first. [] when the store cannot be read."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute("select role, content, tool_calls, tool_call_id, tool_name from messages "
                           "where session_id=? order by id", (session_id,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    calls: dict[str, tuple[str, dict]] = {}
    facts: list[dict] = []
    for role, content, tool_calls, call_id, tool_name in rows:
        if role == "assistant" and tool_calls:
            try:
                parsed = json.loads(tool_calls)
            except (ValueError, TypeError):
                parsed = []
            for t in parsed if isinstance(parsed, list) else []:
                fn = (t or {}).get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {}
                for key in ((t or {}).get("id"), (t or {}).get("call_id")):
                    if key:
                        calls[key] = (fn.get("name") or "", args if isinstance(args, dict) else {})
        elif role == "tool":
            name, args = calls.get(call_id or "", (tool_name or "", {}))
            facts.append({"tool": name or tool_name or "", "args": args, "reply": content or ""})
    return facts


# A FILE WRITE IS AN EFFECT, AND A REFUSED ONE IS NOT (the User 2026-10-02). At 03:44 her hands wrote one file
# into her scratch folder and T0 refused one into a blocked env root; the result read `confirmed: nothing` for both,
# because write_file / patch answer in JSON and no PROOFS line reads JSON. Shapes measured over the bash,
# steward and crissa stores: {bytes_written, files_modified, resolved_path, verified} / {success, diff,
# files_modified} = written; any `error` = not written (it wins over `success`); the history's compact
# "[write_file] wrote to <path>" / "[patch] replace in <path>" = written.
WRITE_TOOLS = ("write_file", "patch")
SAVING_TOOLS = ("show_me", "team_outbox")
SAVED_REPLY = re.compile(r"\bSaved (\S+\.md) \+ (\S+\.html)\b|^confirmed: saved (\S+)", re.M)
COMPACT_WRITE = re.compile(r"^\[(?:write_file\] wrote to|patch\] replace in) (\S+)")


def write_outcome(fact: dict) -> tuple[str, str, str] | None:
    """('written' | 'refused', path, why) for a write_file / patch reply; None for any other tool."""
    if fact.get("tool") not in WRITE_TOOLS:
        return None
    reply = (fact.get("reply") or "").strip()
    args = fact.get("args") or {}
    path = str(args.get("path") or args.get("file_path") or "")
    m = COMPACT_WRITE.match(reply)
    if m:
        return "written", m.group(1), ""
    try:
        data = json.loads(reply)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return "refused", path, (reply[:120] or "no reply")
    if data.get("error"):
        return "refused", path, str(data["error"])
    mod = data.get("files_modified") or []
    if data.get("bytes_written") or mod or data.get("success") is True:
        return "written", str(data.get("resolved_path") or (mod[0] if mod else path)), ""
    return "refused", path, "the reply shows no write"


def _why(text: str) -> str:
    why = re.sub(r"^T0 blocked:\s*", "", text.strip())
    why = re.split(r" \(route:|\. ", why, maxsplit=1)[0]
    return why[:100]


def not_done(facts: list[dict]) -> list[dict]:
    """The writes a run TRIED that did not happen: [{tool, what, why}]."""
    out = []
    for f in facts:
        w = write_outcome(f)
        if w and w[0] == "refused":
            out.append({"tool": f.get("tool"), "what": w[1], "why": _why(w[2])})
    return out


def not_done_lines(items: list[dict]) -> list[str]:
    lines = []
    for i in items:
        line = f"not done: write {i['what'] or '(no path)'} - {i['why']}"
        if line not in lines:
            lines.append(line)
    return lines


def confirmed(facts: list[dict]) -> list[dict]:
    """The effects a run's tool replies prove: [{kind, id?, who?, what?, tool}]."""
    out: list[dict] = []
    for f in facts:
        reply = (f.get("reply") or "").strip()
        if not reply:
            continue
        w = write_outcome(f)
        if w is not None:
            if w[0] == "written":
                out.append({"kind": "written", "tool": f.get("tool"), "what": w[1]})
            continue
        # A REPORT OR AN OUTBOX DRAFT IS A WRITE (the User 2026-10-05, X Spaces drill): show_me report answers
        # "Saved <slug>.md + <slug>.html in <dir>" and team_outbox "confirmed: saved <path>"; both wrote files, and
        # reading them as no write handed her NOT DONE for a report that was on the glass and on disk.
        saved = [x for m in SAVED_REPLY.finditer(reply) for x in m.groups() if x] if f.get("tool") in SAVING_TOOLS else []
        for x in saved:
            out.append({"kind": "written", "tool": f.get("tool"), "what": x.rstrip(".")})
        if saved and f.get("tool") != "show_me":
            continue
        hit = False
        for kind, rx in PROOFS:
            m = rx.match(reply)
            if m:
                out.append({"kind": kind, "tool": f.get("tool"), **{k: v for k, v in m.groupdict().items() if v}})
                hit = True
                break
        if not hit and f.get("tool") in DISPLAY_TOOLS and not REFUSAL.search(reply[:400]):
            a = f.get("args") or {}
            what = a.get("what") or a.get("name") or (a.get("text") or f.get("tool") if f.get("tool") in ART_TOOLS else "")
            if str(what) not in ("shot", "tabs", ""):
                out.append({"kind": "shown", "tool": f.get("tool"), "what": str(what)[:60]})
    return out


def confirmed_lines(effects: list[dict]) -> list[str]:
    lines = []
    for e in effects:
        bits = [e["kind"]] + [str(e[k]) for k in ("id", "who", "what") if e.get(k)]
        line = "confirmed: " + " ".join(bits)
        if line not in lines:
            lines.append(line)
    return lines


def sentences(text: str) -> list[str]:
    body = re.sub(r"(?im)^\s*(?:confirmed|not confirmed):.*$", "", text or "")
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+|\s+[\u2014-]\s+", body) if x.strip()]


def claiming(text: str) -> list[tuple[str, str]]:
    """(kind, sentence) for every sentence that says an effect HAPPENED."""
    out = []
    for sent in sentences(text):
        if NOT_A_CLAIM.search(sent):
            continue
        out += [(kind, sent) for kind, rx, _ in CLAIMS if rx.search(sent)]
    return out


def claims(text: str) -> list[str]:
    """The kinds of effect a piece of speech says happened."""
    seen = [kind for kind, _ in claiming(text)]
    return [kind for kind, _, _ in CLAIMS if kind in seen]


def unconfirmed(text: str, effects: list[dict]) -> list[str]:
    """Claim kinds in `text` that no confirmed effect stands behind."""
    have = {e["kind"] for e in effects}
    return [kind for kind, rx, needs in CLAIMS if kind in claims(text) and not (have & set(needs))]


def strip_claims(text: str, kinds: list[str]) -> str:
    """`text` without the sentences that make an unconfirmed claim (a NOT CONFIRMED line was not enough
    on 09-30: handed a false summary and a warning, she repeated the summary)."""
    if not kinds:
        return text
    bad = {sent for kind, sent in claiming(text) if kind in kinds}
    return " ".join(x for x in sentences(text) if x not in bad).strip()


def effects_in(tool_replies: list[str]) -> list[dict]:
    """The mouth's side: effects named on `confirmed:` lines of the tool replies it was handed."""
    out = []
    for reply in tool_replies:
        for m in re.finditer(r"(?im)^\s*confirmed:\s*(\w[\w-]*)(?:\s+(\S+))?", reply or ""):
            out.append({"kind": m.group(1).lower(), "id": m.group(2) or ""})
    return out


def not_confirmed_line(kinds: list[str]) -> str:
    what = "; ".join(SAY[k] for k in kinds if k in SAY)
    return (f"NOT CONFIRMED: {what}. Do not say it happened. Say you could not confirm it."
            if what else "")


# SHE SAYS WHAT SHE TRIED, NEVER A SUCCESS NO REPLY SHOWS (the User 2026-10-02). At 03:43 her hands had no
# file tool, called only team_time, and answered "All three steps done. Step 1: file written, confirmed ok."
# - the result carried `confirmed: nothing` underneath, and the CLAIMS above have no word for a write or a
# bare "done". This drops those sentences and hands her one instruction: say what was tried, and that it
# did not happen. A write claim needs a `written` effect (and, when it names a file, that file); a bare
# success ("done", "succeeded", "confirmed ok") needs ANY effect. Behind KREW_HANDS_CLAIM_TRUTH (crissa-hands),
# OFF by default; the mouth's own claim check (rail-voice) does not read these kinds.
WRITE_CLAIM = re.compile(r"\b(?:file|files|write|writes|it|that)\b[^.!?]{0,30}\b(?:written|saved|landed|went through|succeeded|worked)\b"
                         r"|\b(?:wrote|saved)\b", re.I)
# A sentence that REPORTS what a reply or the board shows is not a claim about her own act (the User 2026-10-02):
# the drill's board count read "Three open. The first line says 3 open, and the rest are done." - the done COLUMN -
# and the run was handed NOT DONE for a read that worked. Only the success-with-nothing-written rule skips these;
# a write claim never does, and "All three steps done" (her own steps, 03:43) is still caught.
REPORTED = re.compile(r"\b(?:says|said|shows|showed|lists|listed|reads|according to|marked)\b"
                      r"|\b(?:rest|others?|cards?|tasks?|tickets?|items?|\d+)\s+(?:are|is|were|was)\s+(?:already\s+|all\s+)?"
                      r"(?:done|completed?|finished|closed)\b", re.I)
SUCCESS_CLAIM = re.compile(r"\b(?:done|completed?|finished|succeeded|worked|went through|confirmed ok|landed|all set)\b", re.I)
NAMED = re.compile(r"(?:~|/)?[\w.-]*/[\w./-]+|\b[\w-]+\.[a-z]{1,5}\b")


def _names_other_file(sent: str, written: list[str]) -> bool:
    names = [n.rstrip(".") for n in NAMED.findall(sent)]
    return bool(names) and not any(Path(w).name == Path(n).name for n in names for w in written if n)


# A COUNT IS A CLAIM TOO (t_03357f8d, the User 2026-10-02): "Both files written" passed with one write confirmed
# and one refused, because a write claim that names no file needed only SOME write. A number in the sentence
# ("both", "two", "all 3", "3 files") must not exceed the confirmed writes, and "all" / "every" / "each" fails as
# soon as any tried write was refused.
NUMBERS = {"both": 2, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
COUNT = re.compile(r"\b(both|two|three|four|five|six|seven|eight|nine|ten|\d+)\b(?:\s+(?:of\s+(?:the|them|my)\s+)?(?:the\s+)?"
                   r"(?:files?|writes?|of them|probes?))?", re.I)
EVERY = re.compile(r"\b(?:all|every|each)\b", re.I)


def _claims_more(sent: str, n_written: int, n_refused: int) -> bool:
    if n_refused and EVERY.search(sent):
        return True
    for m in COUNT.finditer(sent):
        word = m.group(1).lower()
        n = NUMBERS.get(word) or (int(word) if word.isdigit() else 0)
        if word.isdigit() and not m.group(0)[len(word):].strip():
            continue          # a bare digit ("bytes_written 2") is not a count of writes
        if n > n_written:
            return True
    return False


# A JOB THAT ONLY ASKED TO READ CANNOT FALSELY CLAIM AN ACT (the User 2026-10-03 rehearsal): "Nine cards are open on the
# board right now, with forty-one done." was stripped as a success claim and handed NOT DONE, and Crissa told him the
# board came back empty. The success-with-nothing-written rule exists for a job that ASKED for an action and then
# said it happened (03:43: "write the files" -> team_time only -> "All three steps done"); a read's success words
# describe the world. So that rule needs the ask to name an action; write claims are checked on every job.
ACTION_ASK = re.compile(
    r"\b(?:file|create|make|write|save|add|put|post|send|move|copy|delete|remove|rename|edit|change|set|update|fix|"
    r"restart|start|stop|close|archive|complete|mark|rule|answer|comment|unblock|approve|decline|assign|install|"
    r"run|turn (?:on|off)|switch|show me|bring up|open up|draft|record|register)\b", re.I)


def ask_is_action(ask: str | None) -> bool:
    """True when the job asked for something to be DONE (None = unknown: treat as an action, the old behaviour)."""
    return True if ask is None else bool(ACTION_ASK.search(ask))


def false_success(text: str, effects: list[dict], facts: list[dict] | None = None, ask: str | None = None) -> list[str]:
    """Sentences of `text` that say a write or a job succeeded with no tool reply behind them."""
    written = [e.get("what") or "" for e in effects if e.get("kind") == "written"]
    refused = len(not_done(facts or []))
    bad = []
    for sent in sentences(text):
        if NOT_A_CLAIM.search(sent):
            continue
        if WRITE_CLAIM.search(sent) and (not written or _names_other_file(sent, written)
                                         or _claims_more(sent, len(written), refused)):
            bad.append(sent)
        elif not effects and ask_is_action(ask) and SUCCESS_CLAIM.search(sent) and not REPORTED.search(sent):
            bad.append(sent)
    return bad


def tried(facts: list[dict]) -> str:
    """What the run tried, in a few words: each tool once, a write with its path and outcome."""
    seen: list[str] = []
    for f in facts:
        w = write_outcome(f)
        bit = f"{f.get('tool')} {w[1]} ({'written' if w[0] == 'written' else 'refused'})" if w else str(f.get("tool") or "")
        if bit and bit not in seen:
            seen.append(bit)
    return "; ".join(seen[:8]) if seen else "no tool at all"


def truth_line(facts: list[dict], effects: list[dict]) -> str:
    did = "no write tool was called" if not any(f.get("tool") in WRITE_TOOLS for f in facts) else "see the not done lines"
    what = "nothing it claims" if effects else "nothing at all"
    return (f"NOT DONE: the tool replies show {what} happened. Tell him what you tried and that it did not happen; "
            f"never say it is done. Tried: {tried(facts)} ({did}).")
