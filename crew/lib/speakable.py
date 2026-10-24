"""Spoken-text hygiene for every house mouth.

User 2026-08-06: announcer and anyone in speech mode must not read symbols
or long IDs aloud. Tables/code/URLs stay on the wall; speech is prose.

  "task t8732a088 done"  →  "task done"   (keys dropped, no tails)
  bare UUID / sha        →  dropped
  pipes, fences, URLs    →  stripped or "a link"

Import from scripts (krew-speak, krew-keeper) and plugins; rail-voice
ear_bridge can load the same file by path.
"""
from __future__ import annotations

import re
from typing import Match

# Tokens that look like machine IDs (hex runs, UUIDs, board card ids t+hex, etc.)
_RE_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_RE_SHA = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
# t8732a088, card-ab12cd, task_9f3a2c1b — letter prefix optional
_RE_TASKISH = re.compile(
    r"\b(?:task|card|ticket|issue|commit|sha|id)[\s#:_\-]*"
    r"([A-Za-z]{0,3}[0-9A-Fa-f]{6,})\b",
    re.I,
)
_RE_HEX_TOKEN = re.compile(r"\b[A-Za-z]{0,2}[0-9A-Fa-f]{6,}\b")
_RE_LONG_ALNUM = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{12,}\b")
_RE_PATH = re.compile(r"(?:^|[\s\"'(])(/[\w./+~@%-]{6,})")
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")


def _tail3(tok: str) -> str:
    alnum = re.sub(r"[^A-Za-z0-9]", "", tok or "")
    if not alnum:
        return ""
    # Speak last 3 as individual-ish syllables Cosy can clear: "a o eight"
    return alnum[-3:].lower()


def _id_phrase(tok: str, label: str = "an ID") -> str:
    """Drop machine keys entirely (User 2026-08-25). Do not speak tails."""
    return ""


def _hex_idify(m: Match[str]) -> str:
    tok = m.group(0)
    digits = sum(c.isdigit() for c in tok)
    # pure words / short numbers stay
    if digits < 2 and not re.search(r"[a-fA-F]", tok):
        return tok
    # short readable words like "room12" — keep if mostly letters and short
    if len(tok) < 6:
        return tok
    letters = sum(c.isalpha() for c in tok)
    if letters >= 4 and digits <= 2 and len(tok) <= 8:
        return tok
    # Bare hex/sha: do not say "an ID ending in e2b" (2026-08-25 PA lock-in).
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", tok):
        return ""
    return _id_phrase(tok, "an ID")


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
_THINK_OPEN = re.compile(r"<think>.*$", re.S | re.I)   # an opener with no close: nothing after it is speech
_THINK_TAGS = re.compile(r"</?think>", re.I)


def strip_thinking(text: str) -> str:
    """Reasoning never reaches the mouth. The agent runtime's chunker strips <think> on
    the streaming path; this is the belt for every other caller (PA lines,
    krew-speak, a reply pasted into a tool)."""
    t = _THINK_BLOCK.sub(" ", text or "")
    t = _THINK_OPEN.sub(" ", t)
    t = _THINK_TAGS.sub(" ", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()



# ---------------------------------------------------------------------------
# Times, money, dates (2026-10-06 voice bake-off). The port rule below used to
# eat clock times ("3:45 PM" -> "3 a port PM") and the symbol sweep dropped "$"
# ("$1,284.50" -> "1,284.50") and "/" in dates ("11/02/2026" -> "11 02 2026").
# They are lifted out before the sweep and put back afterwards: as written for
# display (wall, type, logs keep "3:45 PM"), as words for the mouth. Words are
# our own few lines, not num2words (LGPL), so the file ships anywhere.
# ---------------------------------------------------------------------------
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_SCALES = ((10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand"))
_ORD_IRREG = {"one": "first", "two": "second", "three": "third", "five": "fifth",
              "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
_MONTHS = ("January February March April May June July August September October "
           "November December").split()


def num_words(n: int) -> str:
    """12 -> 'twelve', 1284 -> 'one thousand two hundred eighty-four'."""
    n = int(n)
    if n < 0:
        return "minus " + num_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + ("-" + _ONES[o] if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return _ONES[h] + " hundred" + (" " + num_words(r) if r else "")
    for size, name in _SCALES:
        if n >= size:
            q, r = divmod(n, size)
            return num_words(q) + " " + name + (" " + num_words(r) if r else "")
    return str(n)


def ordinal_words(n: int) -> str:
    """2 -> 'second', 15 -> 'fifteenth', 21 -> 'twenty-first'."""
    w = num_words(n)
    head, sep, last = w.rpartition("-") if "-" in w.split(" ")[-1] else w.rpartition(" ")
    if last in _ORD_IRREG:
        last = _ORD_IRREG[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + sep + last


def year_words(y: int) -> str:
    """2026 -> 'twenty twenty-six', 2005 -> 'two thousand five', 1999 -> 'nineteen ninety-nine'."""
    y = int(y)
    if 2000 <= y <= 2009 or not 1100 <= y <= 2099:
        return num_words(y)
    hi, lo = divmod(y, 100)
    if lo == 0:
        return num_words(hi) + " hundred"
    return num_words(hi) + " " + ("oh " + _ONES[lo] if lo < 10 else num_words(lo))


def _decimal_words(raw: str) -> str:
    """'1,284.50' -> 'one thousand two hundred eighty-four point five zero'."""
    raw = raw.replace(",", "")
    whole, _, frac = raw.partition(".")
    out = num_words(int(whole or 0))
    if frac:
        out += " point " + " ".join(_ONES[int(c)] for c in frac)
    return out


# 3:45, 3:45 PM, 10:05:30 a.m., 15:30. Not a dotted host:22 and not a
# 3+ digit tail (:8005 stays a port). Minutes must be 00-59.
_RE_TIME = re.compile(
    r"(?<![\w.:])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)"
    r"(?:\s?([AaPp])\.?\s?[Mm]\b\.?)?"
)
_CUR = {"$": ("dollar", "dollars", "cent", "cents"), "€": ("euro", "euros", "cent", "cents"),
        "£": ("pound", "pounds", "penny", "pence")}
_SCALE_WORD = {"k": "thousand", "m": "million", "mm": "million", "b": "billion", "bn": "billion",
               "thousand": "thousand", "million": "million", "billion": "billion"}
_RE_MONEY = re.compile(
    r"([$€£])\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?"
    r"(?:\s?(thousand|million|billion|bn|mm|[kKmMbB])\b)?"
)
# US numeric dates with a year: 11/02/2026, 1/5/26. ISO: 2026-10-06.
_RE_DATE_US = re.compile(r"(?<![\w/])(1[0-2]|0?[1-9])/(3[01]|[12]\d|0?[1-9])/(\d{4}|\d{2})(?![\w/])")
_RE_DATE_ISO = re.compile(r"(?<![\w-])(\d{4})-(1[0-2]|0[1-9])-(3[01]|[12]\d|0[1-9])(?![\w-])")


def _say_time(m: Match[str]) -> str:
    h, mi, sec, ap = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    out = num_words(h)
    if mi == 0:
        out += "" if ap else " o'clock" if h <= 12 else " hundred"
    elif mi < 10:
        out += " oh " + _ONES[mi]
    else:
        out += " " + num_words(mi)
    if sec and int(sec):
        out += " and " + num_words(int(sec)) + " seconds"
    if ap:
        out += " a.m." if ap.lower() == "a" else " p.m."
    return out


def _say_money(m: Match[str]) -> str:
    sym, whole, cents, scale = m.group(1), m.group(2), m.group(3), m.group(4)
    one, many, sub1, subn = _CUR[sym]
    n = int(whole.replace(",", ""))
    if scale:
        amount = _decimal_words(whole + ("." + cents if cents else ""))
        return f"{amount} {_SCALE_WORD[scale.lower()]} {many}"
    c = int((cents or "0").ljust(2, "0"))
    if n == 0 and c:
        return f"{num_words(c)} {sub1 if c == 1 else subn}"
    out = f"{num_words(n)} {one if n == 1 else many}"
    if c:
        out += f" and {num_words(c)} {sub1 if c == 1 else subn}"
    return out


def _say_date(month: int, day: int, year: str) -> str:
    y = int(year)
    if len(year) == 2:
        y += 2000
    return f"{_MONTHS[month - 1]} {ordinal_words(day)}, {year_words(y)}"


# Placeholders made only of letters that are not hex digits, so the ID and
# hex sweeps below leave them alone.
_PH_DIGITS = "ghjkmnpqrs"


def _ph(i: int) -> str:
    return "zqxw" + "".join(_PH_DIGITS[int(c)] for c in str(i)) + "wxqz"


_RE_PH = re.compile(r"zqxw([%s]+)wxqz" % _PH_DIGITS)


def _lift_numbers(t: str) -> tuple[str, list[tuple[str, str]]]:
    """Swap times, money and dates for placeholders. Returns (text, [(written, spoken)])."""
    held: list[tuple[str, str]] = []

    def keep(written: str, spoken: str) -> str:
        held.append((written, spoken))
        return " " + _ph(len(held) - 1) + " "

    t = _RE_DATE_ISO.sub(lambda m: keep(m.group(0), _say_date(int(m.group(2)), int(m.group(3)), m.group(1))), t)
    t = _RE_DATE_US.sub(lambda m: keep(m.group(0), _say_date(int(m.group(1)), int(m.group(2)), m.group(3))), t)
    t = _RE_MONEY.sub(lambda m: keep(m.group(0), _say_money(m)), t)
    t = _RE_TIME.sub(lambda m: keep(m.group(0), _say_time(m)), t)
    return t, held


def _drop_numbers(t: str, held: list[tuple[str, str]], spoken: bool) -> str:
    def put(m: Match[str]) -> str:
        i = int("".join(str(_PH_DIGITS.index(c)) for c in m.group(1)))
        return held[i][1 if spoken else 0] if i < len(held) else ""
    t = _RE_PH.sub(put, t)
    t = re.sub(r"\s+([.,?!])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


_RE_PLAIN_NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(%|st|nd|rd|th)?(?![\w]|\.\d)")


def numbers_to_words(text: str) -> str:
    """Every number as words, for engines with no text front end of their own.

    Times, money and dates first (as for_speech does), then ordinals (15th),
    percentages, plain integers and decimals. A standalone 1100-2099 reads as a
    year. Digits glued to letters (GPT4, v2, x86) are left alone.
    """
    t, held = _lift_numbers(text or "")

    def plain(m: Match[str]) -> str:
        whole, frac, suf = m.group(1), m.group(2), m.group(3)
        if suf in ("st", "nd", "rd", "th") and not frac:
            return ordinal_words(int(whole.replace(",", "")))
        if not frac and not suf and "," not in whole and len(whole) == 4 and 1100 <= int(whole) <= 2099:
            return year_words(int(whole))
        w = _decimal_words(whole + (frac or ""))
        return w + (" percent" if suf == "%" else "")

    t = _RE_PLAIN_NUM.sub(plain, t)
    return _drop_numbers(t, held, spoken=True)



def strip_unspeakable(text: str, *, spoken: bool = False) -> str:
    """Drop / rewrite structures a mouth cannot say cleanly.

    Display owns tables, code, URLs, full IDs. Speech gets titles and
    summaries; machine keys are dropped (User 2026-08-25).

    Times, money and dates survive: as written by default (display), as words
    when spoken=True (the mouth).
    """
    t, held = _lift_numbers(text or "")
    # fenced code, closed OR unclosed — never speakable
    t = re.sub(r"```[\s\S]*?(?:```|$)", " ", t)
    # markdown table rows and |---| separator lines
    t = re.sub(r"^\s*\|.*\|.*$", " ", t, flags=re.M)
    t = re.sub(r"^\s*[|+:\- ]{4,}\s*$", " ", t, flags=re.M)
    # inline code keeps its words, loses the ticks
    t = re.sub(r"`([^`\n]*)`", r"\1", t)
    # URLs
    t = re.sub(r"(?:https?://|www\.)\S+", "a link", t)
    # emails
    t = re.sub(_RE_EMAIL, "an email", t)
    # absolute paths
    t = re.sub(_RE_PATH, lambda m: m.group(0)[0] + "a path" if m.group(0)[0] in " \t\"'(" else "a path", t)
    # bare filenames TTS spells (STEWARD-MS-REPORT.md, foo_bar.json)
    t = re.sub(
        r"\b[\w.-]+\.(?:md|json|jsonl|ya?ml|py|sh|txt|log|wav|html|toml|cfg|conf)\b",
        " a file ",
        t,
        flags=re.I,
    )
    # ports :8005, :7788 — "port eight thousand five" is long; say "a port"
    t = re.sub(r":\d{2,5}\b", " a port ", t)
    # bare board card ids t_abc123…
    t = re.sub(r"\bt_[0-9a-fA-F]{6,}\b", " ", t)
    # bullets / numbered items → plain sentence starts
    t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", t, flags=re.M)
    # headings & emphasis marks (after fences so we don't un-fence code)
    t = re.sub(r"[#*_~]+", " ", t)
    # em/en dashes & arrows — TTS often says "dash" or "greater than"
    t = t.replace("—", ", ").replace("–", ", ").replace("→", " to ").replace("⇒", " to ")
    t = t.replace("…", ". ").replace("...", ". ")

    # "task t8732a088" / "card: ab12cd" → drop the key (title/summary stay)
    def _taskish(m: Match[str]) -> str:
        kind = (m.group(0).split()[0] if m.group(0) else "task").lower()
        kind = re.sub(r"[^a-z]", "", kind) or "task"
        if kind in ("sha", "commit", "id"):
            kind = "an ID"
        elif kind in ("task", "card", "ticket", "issue"):
            kind = kind
        else:
            kind = "task"
        return _id_phrase(m.group(1), kind)

    t = _RE_TASKISH.sub(_taskish, t)
    # full UUIDs
    t = _RE_UUID.sub(lambda m: _id_phrase(m.group(0), "an ID"), t)
    # long hex / shas (after taskish so we don't double-rewrite)
    t = _RE_SHA.sub(_hex_idify, t)
    t = _RE_HEX_TOKEN.sub(_hex_idify, t)
    # long opaque tokens (mixed alnum 12+)
    t = _RE_LONG_ALNUM.sub(
        lambda m: _id_phrase(m.group(0), "an ID")
        if sum(c.isdigit() for c in m.group(0)) >= 2
        else m.group(0),
        t,
    )
    # collapse repeated ID phrases
    t = re.sub(r"(?:(?:an ID|task|card) ending in \w+[\s,]*){2,}", "an ID ", t, flags=re.I)
    t = re.sub(r"(?:an ID[\s-]*){2,}", "an ID ", t)

    # leftover symbols TTS spells letter-by-letter (Audio8 reads these aloud)
    t = t.replace("|", " ")
    t = t.replace("\\", " ")
    t = re.sub(r"[{}\[\]<>]", " ", t)
    t = re.sub(r"[@$%^&=+]+", " ", t)
    t = re.sub(r"[\"“”‘’]", " ", t)
    # colons as list glue ("Board: 34 tasks") → pause, not "colon"
    t = re.sub(r"\s*:\s*", ", ", t)
    # keep sentence punctuation . , ? ! ' -
    t = re.sub(r"[;]+", ",", t)
    t = re.sub(r"/{2,}", " ", t)
    t = re.sub(r"(?<!\w)/(?!\w)", " ", t)
    # strip any remaining non-prose junk (keeps letters, digits, basic punct)
    t = re.sub(r"[^\w\s.,?!'%-]", " ", t)

    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # collapse ", ," and trailing commas before period
    t = re.sub(r"(,\s*){2,}", ", ", t)
    t = re.sub(r"\s+,", ",", t)
    t = re.sub(r",\s*\.", ".", t)
    t = re.sub(r"\s+\.", ".", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _drop_numbers(t, held, spoken)


def clip_for_pa(text: str, max_chars: int = 160) -> str:
    """Hard cap for house PA — prefer sentence end, never trailing ellipsis glyph."""
    t = (text or "").strip()
    if not t:
        return t
    max_chars = max(40, min(int(max_chars), 280))
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    # prefer last sentence boundary
    for sep in (". ", "? ", "! "):
        i = cut.rfind(sep)
        if i >= 40:
            return cut[: i + 1].strip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;: ") + "."


# The Y in Zoey is the pin (the User 2026-09-08: "the y helps them"). Cosy
# reads "Zoe" as Zoo. Do not hyphenate to Zoh-ee — that was zooooeeeee.
# Phonetic respell is MOUTH-ONLY — logs, type, wall, memory keep "Zoey" / "Ada".
_RE_ZOEY = re.compile(r"\bZoey\b", re.I)
_RE_ZOE = re.compile(r"\bZoe\b", re.I)
# Ada: short-a wrong; "AY-duh" → "A Why-duh"; "Aeda" → "Aeeda".
# Mouth form: dictionary ā′də (/ˈeɪ.də/) — Cosy says "A-da". the User 2026-08-12.
_RE_ADA = re.compile(r"\bAda\b", re.I)
_ZOEY_SAY = "Zoey"
_ZOE_SAY = "Zoey"
_ADA_SAY = "ā′də"
# Reverse any leak (pipeline or model copy) so typed/logged text is the name.
_RESTORE_NAMES = (
    (re.compile(r"ā['′]d[əa]", re.I), "Ada"),
    (re.compile(r"\bAY-duh\b", re.I), "Ada"),
    (re.compile(r"\bA-da['′]?", re.I), "Ada"),
    (re.compile(r"\bZoh-ee\b", re.I), "Zoey"),
    (re.compile(r"\bZoh\b"), "Zoe"),
)


def apply_name_phonetics(text: str) -> str:
    """Respell house names Cosy mispronounces. Mouth only — never logs/type."""
    t = text or ""
    t = _RE_ZOEY.sub(_ZOEY_SAY, t)
    t = _RE_ZOE.sub(_ZOE_SAY, t)
    t = _RE_ADA.sub(_ADA_SAY, t)
    return t


def restore_name_spellings(text: str) -> str:
    """Undo mouth phonetics so display/logs show Ada / Zoey / Zoe."""
    t = text or ""
    for pat, name in _RESTORE_NAMES:
        t = pat.sub(name, t)
    return t



# Chatterbox Turbo native paralinguistic tags (house Manual / Turbo README).
# Known tags are allow-through for Turbo TTS. Unknown [word] tags are NOT
# silently dropped: left for TTS and listed so a caller can log them.
TURBO_NATIVE_TAGS = frozenset({"laugh", "chuckle", "cough"})
_RE_BRACKET_TAG = re.compile(r"\[([A-Za-z][\w-]*)\]")
# Placeholder survives strip_unspeakable's symbol purge (word-like only).
_TAG_PH = "zqxwtag{0}wxqz"


def classify_bracket_tags(text: str) -> tuple[list[str], list[str]]:
    """Return (known_names, unknown_names) for every [tag] in text (lowercased)."""
    known: list[str] = []
    unknown: list[str] = []
    for m in _RE_BRACKET_TAG.finditer(text or ""):
        name = m.group(1).lower()
        if name in TURBO_NATIVE_TAGS:
            known.append(name)
        else:
            unknown.append(name)
    return known, unknown


def strip_bracket_tags(text: str) -> tuple[str, list[str], list[str]]:
    """Remove every [tag] entirely (mouth / aligner / non-Turbo TTS).

    Returns (clean_text, known_names, unknown_names). The word inside the
    brackets is dropped too — otherwise strip_unspeakable's bracket purge
    leaves 'laugh' for the engine to speak aloud.
    """
    known, unknown = classify_bracket_tags(text or "")

    def repl(_m: Match[str]) -> str:
        return " "

    clean = _RE_BRACKET_TAG.sub(repl, text or "")
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\s+([.,!?])", r"\1", clean)
    return clean.strip(), known, unknown


def _lift_bracket_tags(text: str) -> tuple[str, list[str]]:
    """Swap [tag] for word-like placeholders so strip_unspeakable keeps them."""
    held: list[str] = []

    def repl(m: Match[str]) -> str:
        held.append(m.group(0))  # full "[laugh]"
        return " " + _TAG_PH.format(len(held) - 1) + " "

    return _RE_BRACKET_TAG.sub(repl, text or ""), held


def _drop_bracket_tags(text: str, held: list[str]) -> str:
    out = text or ""
    for i, tag in enumerate(held):
        out = out.replace(_TAG_PH.format(i), tag)
    return out


def for_mouth(text: str, *, empty_fallback: str = "", max_chars: int | None = None) -> str:
    """Aligner / mouth sheet: no bracket tags (known or unknown)."""
    clean, _known, _unknown = strip_bracket_tags(text or "")
    return for_speech(clean, empty_fallback=empty_fallback, max_chars=max_chars)


def for_turbo_tts(text: str, *, empty_fallback: str = "", max_chars: int | None = None) -> tuple[str, list[str], list[str]]:
    """Turbo TTS line: keep [tag] forms; strip everything else unspeakable.

    Returns (tts_text, known_names, unknown_names). Unknown tags stay in the
    TTS text (not silently dropped) so the engine can try them; callers should
    log unknown_names.
    """
    known, unknown = classify_bracket_tags(text or "")
    lifted, held = _lift_bracket_tags(text or "")
    t = strip_unspeakable(strip_thinking(lifted), spoken=True)
    t = _drop_bracket_tags(t, held)
    t = apply_name_phonetics(t)
    if not t.strip() and (text or "").strip():
        t = empty_fallback or "That part is better on a screen than out loud."
    if max_chars is not None:
        t = clip_for_pa(t, max_chars=max_chars)
    return t, known, unknown


def for_display(text: str) -> str:
    """Hygiene for logs, type, wall, memory — real names, no phonetics."""
    t = restore_name_spellings(text or "")
    return strip_unspeakable(t)


def title_for_pa(text: str, max_chars: int = 160) -> str:
    """Board card title for PA — keys stripped, no ID tails."""
    return for_speech(text or "", max_chars=max_chars)


def line_for_pa(title: str, summary: str = "", max_chars: int = 160) -> str:
    """Title plus optional short summary for PA (User 2026-08-25)."""
    title = (title or "").strip()
    summary = (summary or "").strip()
    if summary and summary.lower() not in title.lower():
        raw = f"{title}. {summary}" if title else summary
    else:
        raw = title
    return for_speech(raw, max_chars=max_chars)


def for_speech(
    text: str,
    *,
    empty_fallback: str = "",
    max_chars: int | None = None,
    keep_turbo_tags: bool = False,
) -> str:
    """Mouth pipeline: unspeakable strip + name phonetics + empty guard.

    Do not use this on replies that will be logged or typed. Use for_display
    there; krew-speak / Cosy apply phonetics at the last mile.

    max_chars: when set (PA announcer), clip_for_pa after hygiene so Audio8
    does not overflow or read mid-cut garbage.

    keep_turbo_tags: when True (Turbo TTS only), preserve [laugh]/[chuckle]/
    [cough] and any other [word] tag for the engine. Default False strips the
    whole tag so Cosy / Audio8 never speak the word "laugh". Prefer
    for_turbo_tts / for_mouth at the krew-speak seam.
    """
    raw = text or ""
    if keep_turbo_tags:
        t, _k, _u = for_turbo_tts(raw, empty_fallback=empty_fallback, max_chars=max_chars)
        return t
    clean, _k, _u = strip_bracket_tags(raw)
    t = strip_unspeakable(strip_thinking(clean), spoken=True)
    t = apply_name_phonetics(t)
    if not t and raw.strip():
        return empty_fallback or "That part is better on a screen than out loud."
    if max_chars is not None:
        t = clip_for_pa(t, max_chars=max_chars)
    return t
