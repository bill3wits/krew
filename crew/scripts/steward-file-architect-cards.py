#!/usr/bin/env python3
"""File Ada/Steward cards from note Proposed cards / Action items. No scout kanban."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from architect_lib import (  # noqa: E402
    CARD_ASSIGNEE,
    INBOX,
    MS_DIR,
    all_ops_action_ids,
    append_jsonl,
    load_jsonl,
    plan_filings,
    today,
)

HERMES = Path.home() / ".local" / "bin" / "hermes"
# A try needs a green, and a green needs a queue. Until 2026-09-12 these
# cards said "Decompose only if User/Zoey greens a try" and sat on Ada with
# nobody's name on them; the needs monitor never woke Zoey (it read titles)
# and the sweep escalated with a title it also could not read. Now the card
# is BLOCKED needs_input at filing: that is the Decide queue Zoey reads, her
# answer (try | park | later) unblocks it, and scripts/kanban-loop.py turns
# "try" into a triage card the decomposer consumes. 0 = old behaviour.
GREEN_GATE = os.environ.get("ARCHITECT_GREEN_GATE", "1") not in ("0", "false", "no")
GREEN_KINDS = ("team-improve", "architect-try")
GREEN_QUESTION = (
    "Green this try? Answer with one word first: try (a triage card is cut and "
    "decomposed), park (archived, note why), or later <date>. Software call: "
    "Crissa decides; the User is not asked."
)
# `## 3. Action items` too: Ada numbers her Sunday note's sections, and on 09-13 the
# numbered heading hid all three ops-action lines — Steward never got one.
HEAD = re.compile(r"^#{2,3}\s+(?:\d+[.)]\s*)?(proposed cards|action items)", re.I)
# House law: every kanban call is `hermes -p <profile>`. "" = the bare CLI.
PROFILE = os.environ.get("ARCHITECT_FILER_PROFILE", "ada").strip()
# One idea, one card (2026-09-17): the same idea was filed on up to 7 nights and
# by three routes (research, team-eval's nnt-agreed, a role note). A gated kind
# is keyed on its first field, and a key this board has EVER seen — open, done
# or archived (a Park) — is not filed again; the night is logged in SEEN.
# ARCHITECT_IDEMP_SLUG=0 = the old whole-title key and no look-up.
IDEMP_SLUG = os.environ.get("ARCHITECT_IDEMP_SLUG", "1") not in ("0", "false", "no")
SEEN = MS_DIR / "improvements" / "seen.jsonl"
# Scout's own `team-improve:` lines wait for the team vote (2026-09-19): the research job
# filed them at 20:22, Zoey ruled at 20:29, the vote finished at 20:32 — vLLM 0.29 ranked
# 5th of 12 with nothing agreed and became the night's biggest thread. From the research
# note they file only once rank-<day>.json exists (team-eval-run.sh runs this filer right
# after the ranker), and the card carries the vote + the KPI the voters named.
# TEAM_IMPROVE_WAITS_FOR_VOTE=0 = file at once, as before.
WAIT_FOR_VOTE = os.environ.get("TEAM_IMPROVE_WAITS_FOR_VOTE", "1") not in ("0", "false", "no")
RANK_DIR = MS_DIR / "improvements"
KANBAN_HOME = Path.home() / ".hermes" / "kanban"
FAILED: list[str] = []   # a gate that did not close; main() exits 3


def _ledger():
    """lib/idea_ledger (self-loop step 2), or None: the ledger must never be why a card is not filed."""
    try:
        lib = str(Path(__file__).resolve().parents[1] / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import idea_ledger
        return idea_ledger
    except Exception as e:  # noqa: BLE001
        print(f"idea ledger unavailable: {e}")
        return None


def ledger_path(beside: Path | None = None) -> Path:
    """ideas.jsonl beside the filer's own ledgers (resolved at call time, so a test that points
    SEEN / PROPOSALS away points this away too)."""
    return (beside or SEEN).with_name("ideas.jsonl")


def ledger_event(key: str, event: str, note: Path | str, data: dict | None = None, text: str = "",
                 beside: Path | None = None) -> None:
    L = _ledger()
    if L is None or not key:
        return
    try:
        L.record([{"key": key, "event": event, "kind": "filer", "text": text, **({"data": data} if data else {})}],
                 today().isoformat(), str(note), path=ledger_path(beside))
    except Exception as e:  # noqa: BLE001
        print(f"idea ledger: {e}")


def hermes_argv(*args: str, hermes: Path | None = None) -> list[str]:
    return [str(hermes or HERMES), *(["-p", PROFILE] if PROFILE else []), "kanban", *args]


def slug_key(kind: str, title: str) -> str:
    first = re.split(r"\s+[—–-]{1,2}\s+", title.strip(), maxsplit=1)[0]
    first = re.sub(r"[^a-z0-9./]+", "-", first.lower()).strip("-")[:80]
    return f"{kind}:{first}" if first else ""


def seen_in_ledger(key: str) -> tuple[str, str] | None:
    """The first filing of this key in SEEN — it outlives a purged card."""
    for row in load_jsonl(SEEN):
        if row.get("key") == key and row.get("status") == "filed" and row.get("task"):
            return (str(row["task"]), "filed earlier (not on the board)")
    return None


def title_head(kind: str, title: str) -> str:
    return f"{kind}: " + re.split(r"\s+[—–-]{1,2}\s+", title.strip(), maxsplit=1)[0].lower()


def seen_on_board(key: str, head: str = "") -> tuple[str, str] | None:
    """(task id, status) of any card on the fleet's current board with this
    idempotency key — archived included, which hermes' own check skips. `head`
    (kind + first field) also finds the cards filed under the old hashed key."""
    try:
        slug = (KANBAN_HOME / "current").read_text().strip()
        db = KANBAN_HOME / "boards" / slug / "kanban.db"
        if not slug or not db.is_file():
            return None
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT id, status FROM tasks WHERE idempotency_key = ? "
                              "ORDER BY created_at DESC LIMIT 1", (key,)).fetchone()
            if not row and head:
                row = con.execute("SELECT id, status FROM tasks WHERE lower(substr(title, 1, ?)) = ? "
                                  "AND created_by = 'steward-architect' ORDER BY created_at DESC LIMIT 1",
                                  (len(head), head)).fetchone()
        finally:
            con.close()
        return (str(row[0]), str(row[1])) if row else None
    except (OSError, sqlite3.Error):
        return None
# A "nothing tonight" line is not a card: the filer files none of them and Zoey is not asked.
# ARCHITECT_SKIP_NONE=0 = file every line as before.
SKIP_NONE = os.environ.get("ARCHITECT_SKIP_NONE", "1") not in ("0", "false", "no")
NONE_ITEM = re.compile(r"^\W*(none|nothing|no (cards?|try|tries|candidates?))\b", re.I)
ITEM = re.compile(
    r"(architect-try|architect-watch|team-improve|team-source|team-source-drop|ops-action|nnt-agreed)\s*:\s*(.+)$",
    re.I,
)
# `nnt-agreed: <repo> — <why>` (Zoey's ruling, or Steward after two roles agree)
# is filed as a team-improve card (green queue) AND appended to the top
# improvements list. the User 2026-09-12: NNT = the week's top GitHub repos; Scout
# picks, the team debates the next day, agreed items go on the list.
NNT_AGREED = re.compile(r"^\s*[-*]?\s*nnt-agreed\s*:\s*(.+)$", re.I | re.M)
PROPOSALS = MS_DIR / "improvements" / "proposals.jsonl"
ASSIGNEE = CARD_ASSIGNEE
ACTIONS = MS_DIR / "improvements" / "actions.jsonl"


def parse_cards(text: str) -> list[tuple[str, str]]:
    in_sec = False
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        if HEAD.search(line):
            in_sec = True
            continue
        if in_sec and line.startswith("#"):
            break
        if not in_sec:
            continue
        raw = line.strip().lstrip("-*` ").replace("`", "")
        m = ITEM.search(raw)
        if m:
            kind = m.group(1).lower()
            title = m.group(2).strip().strip("`")[:160]
            if SKIP_NONE and NONE_ITEM.match(title):
                continue            # "architect-try: none this cycle" is a note, not a card (t_cfc678aa, 09-18)
            found.append((kind, title))
    return found[:5]


_CREATED = re.compile(r"\b(t_[0-9a-f]{8})\b")


def created_id(stdout: str) -> str:
    """`hermes kanban create` prints 'Created t_xxxxxxxx (...)'."""
    m = _CREATED.search(stdout or "")
    return m.group(1) if m else ""


def _vote_key(item: str) -> str:
    return re.sub(r"\s+", " ", item.split(" — ")[0].replace("`", "").strip()).lower()


def rank_rows(day: str, improve_dir: Path | None = None) -> list[dict] | None:
    """Tonight's team vote (team-eval-rank.py), or None when it has not run yet."""
    p = (improve_dir or RANK_DIR) / f"rank-{day}.json"
    try:
        return [r for r in (json.loads(p.read_text()).get("rows") or []) if isinstance(r, dict)]
    except (OSError, ValueError, AttributeError):
        return None


def vote_line(title: str, rows: list[dict]) -> tuple[str, str]:
    """(one-line vote, kpi) for a filed item from the night's rank rows."""
    key = _vote_key(title)
    hit = next((r for r in rows if _vote_key(str(r.get("item") or "")) == key), None)
    if hit is None:
        hit = next((r for r in rows if len(key) >= 12 and (_vote_key(str(r.get("item") or "")).startswith(key[:40])
                                                             or key.startswith(_vote_key(str(r.get("item") or ""))[:40]))), None)
    if hit is None:
        return (f"team vote: not scored ({len(rows)} items ranked)", "")
    sc = ", ".join(f"{k} {v}" for k, v in (hit.get("scores") or {}).items())
    top = " — TOP (agreed)" if hit.get("top") else ""
    return (f"team vote: rank {hit.get('rank')} of {len(rows)}, sum {hit.get('sum')} ({sc}){top}", str(hit.get("kpi") or ""))


def block_for_green(tid: str, *, hermes: Path = HERMES, vote: str = "") -> bool:
    r = subprocess.run(
        # reason BEFORE --kind: a trailing nargs=* positional after the flag is
        # "unrecognized arguments" — no gate card was ever blocked (2026-09-17:
        # t_ce76cd74 and t_f10d0e20 both spawned a worker; one was "completed").
        hermes_argv("block", tid, GREEN_QUESTION + (f" [{vote}]" if vote else ""), "--kind", "needs_input", hermes=hermes),
        check=False, capture_output=True, text=True,
    )
    ok = r.returncode == 0
    print("green gate", tid, "blocked needs_input" if ok else f"block failed: {(r.stderr or r.stdout)[:120]}")
    if not ok:
        # Fail closed: a "Do not implement" card left ready spawns a worker
        # (16 of 16 did, 09-14..17). Take it off the board and say so.
        a = subprocess.run(hermes_argv("archive", tid, hermes=hermes), check=False, capture_output=True, text=True)
        print("green gate", tid, "archived instead" if a.returncode == 0 else "ARCHIVE FAILED TOO — card is live")
        FAILED.append(tid)
    return ok


def nnt_agreed(text: str) -> list[str]:
    """Agreed picks anywhere in a note (not only under a heading)."""
    out: list[str] = []
    for m in NNT_AGREED.finditer(text or ""):
        t = m.group(1).strip().strip("`")[:200]
        if t and t not in out:
            out.append(t)
    return out


AGREED_KPI = re.compile(r"\bkpi\s*[:=]\s*([a-z][a-z0-9_]*)", re.I)


def kpi_baseline(title: str, kpis_path: Path | None = None) -> dict:
    """The KPI an agreed pick names (team-eval-rank writes `kpi=<id>`) and its value
    now, the baseline the outcome is measured against (the User 2026-09-17: tie
    improvements to the team's KPIs). {} when none is named."""
    m = AGREED_KPI.search(title or "")
    kid = m.group(1).lower() if m else ""
    if not kid or kid in ("none", "unnamed"):
        return {"kpi_id": kid or "unnamed"} if kid else {}
    kpis_path = kpis_path or (MS_DIR / "improvements" / "kpis.json")
    try:
        k = (json.loads(kpis_path.read_text()).get("kpis") or {}).get(kid) or {}
    except (OSError, json.JSONDecodeError):
        k = {}
    if not k:
        return {"kpi_id": kid, "kpi_known": False}
    return {"kpi_id": kid, "kpi_known": True, "direction": k.get("direction"), "unit": k.get("unit"),
            "baseline": k.get("current"), "baseline_ts": k.get("updated_at")}


def record_proposal(title: str, note: Path, *, path: Path | None = None) -> bool:
    """Top improvements list: one line per agreed pick, idempotent by title."""
    path = path or PROPOSALS   # resolved at call time so tests can point it away
    # Idempotent by the ITEM (the text before the first " — "): the agreed line also
    # carries the vote ("3 of 3 roles", later "4 of 4"), and a re-rank of the same
    # night appended the same idea twice (2026-09-17).
    item = title.split(" — ")[0].strip()
    try:
        existing = {str(json.loads(l).get("title") or "").split(" — ")[0].strip()
                    for l in path.read_text().splitlines() if l.strip()} if path.is_file() else set()
    except (OSError, json.JSONDecodeError):
        existing = set()
    if item in existing:
        return False
    rec = {"ts": today().isoformat(), "kind": "nnt-agreed", "title": title,
           "source": str(note), "status": "agreed", **kpi_baseline(title),
           "card_key": slug_key("team-improve", title)}
    append_jsonl(path, rec)
    L = _ledger()
    if L is not None:
        ledger_event(L.key_of("nnt-agreed", title), "agreed", note, {"proposal": rec}, title[:200], beside=path)
    return True


def file_from(note: Path, window: dict | None = None) -> int:
    """File a note's cards. `window` (improve-file, self-loop step 3): the team vote is the gate -
    a team-improve card comes ONLY from the night's agreed tier (`nnt-agreed`, top of the rank),
    only when the rank had a quorum of real voters, at most window["cap"] a night; Scout's own
    team-improve bullets are recorded in the ledger and never filed on their own."""
    if not note.is_file():
        return 0
    cards = parse_cards(note.read_text(encoding="utf-8", errors="replace"))
    existing = all_ops_action_ids(load_jsonl(ACTIONS))
    n = 0
    text = note.read_text(encoding="utf-8", errors="replace")
    if window is not None:
        dropped = [t for k, t in cards if k == "team-improve"]
        cards = [(k, t) for k, t in cards if k != "team-improve"]
        for t in dropped:
            print("not filed (not the agreed tier; the ledger has it)", t[:90])
    for agreed in nnt_agreed(text):
        if (window is None or window.get("quorum_ok")) and ("team-improve", agreed) not in cards:
            cards.append(("team-improve", agreed))
        if record_proposal(agreed, note):
            print("top improvements +", agreed)
    cards = [(k, t) for k, t in cards if k != "nnt-agreed"]
    from_research = note.name.startswith("team-research-")
    ranked = rank_rows(today().isoformat()) if (from_research and WAIT_FOR_VOTE) else None
    for rec in plan_filings(cards, existing):
        kind, title = rec["kind"], rec["title"]
        if rec["skip"]:
            print("skip existing", kind, rec.get("ops_action_id"), title)
            continue
        if window is not None and kind == "team-improve":
            if window["filed"] >= window["cap"]:
                print(f"cap {window['cap']} reached tonight — held", title[:90])
                ledger_event(ledger_key(kind, title, slug_key(kind, title)), "held", note, {"why": "cap"})
                continue
            kid = (AGREED_KPI.search(title).group(1).lower() if AGREED_KPI.search(title) else "")
            L = _ledger()
            if kid and L is not None and L.kpi_scope(kid) == "board":
                other = open_kpi_card(kid)
                if other:
                    print(f"refused: {kid} is board-wide and {other} is still open on it", title[:80])
                    ledger_event(ledger_key(kind, title, slug_key(kind, title)), "refused", note,
                                 {"why": f"board-wide KPI {kid} already open on {other}"})
                    continue
        vote = ""
        if from_research and WAIT_FOR_VOTE and kind == "team-improve":
            if ranked is None:
                print("held for the team vote", kind, title[:90])
                continue
            vote, vkpi = vote_line(title, ranked)
            base = title[:110].rstrip()
            rec["title"] = title = (f"{base} — kpi={vkpi}" if vkpi and not AGREED_KPI.search(base) else base) + f" — {vote}"
        gated = GREEN_GATE and kind in GREEN_KINDS
        if IDEMP_SLUG and kind != "ops-action" and slug_key(kind, title):
            rec["idemp"] = slug_key(kind, title)
            hit = seen_on_board(rec["idemp"], title_head(kind, title)) or seen_in_ledger(rec["idemp"])
            if hit:
                print("seen again", rec["idemp"], hit[0], hit[1])
                append_jsonl(SEEN, {"day": today().isoformat(), "key": rec["idemp"], "task": hit[0],
                                    "status": hit[1], "note": str(note)})
                ledger_event(ledger_key(kind, title, rec["idemp"]), "seen-again", note,
                             {"task": hit[0], "status": hit[1]})
                continue
        body = f"Filed from {note}. Crystal: that note.\nkind={kind}\n"
        if kind == "ops-action":
            body += f"ops_action_id={rec['ops_action_id']}\nSteward closes this. Ada does not implement.\n"
        elif gated:
            kb = kpi_baseline(title)
            _L = _ledger()
            if kb.get("kpi_id") and _L is not None and _L.kpi_scope(kb["kpi_id"]) == "board":
                body += (f"kpi: {kb['kpi_id']} is BOARD-WIDE: filed as unmeasured (anything on the board moves it); "
                         "one open card per board-wide KPI.\n")
            elif kb.get("kpi_id"):
                body += (f"kpi: {kb['kpi_id']}"
                         + (f" ({kb.get('direction')}) baseline {kb.get('baseline')} {kb.get('unit') or ''} at filing"
                            if kb.get("kpi_known") else " (not in kpis.json — register it or it cannot be measured)")
                         + "\nscripts/improvement-outcomes.py measures it after this lands.\n")
            if kind == "team-improve":
                body += idea_memory(kind, title)
            body += ("gate: green\nDo not implement. Blocked needs_input for Crissa's green: "
                     "try -> kanban-loop cuts a triage card and the decomposer takes it; "
                     "park -> archived.\n")
        else:
            body += "Do not implement. Decompose only if User/Crissa greens a try.\n"
        r = subprocess.run(
            [
                *hermes_argv("create"),
                f"{kind}: {title}",
                "--assignee",
                rec["assignee"],
                "--created-by",
                "steward-architect",
                "--body",
                body,
                "--idempotency-key",
                rec["idemp"],
                "--workspace",
                f"dir:{INBOX}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        tid = created_id(r.stdout or "")
        if IDEMP_SLUG and kind != "ops-action":
            # The ideas ledger is this file, not the board: `kanban gc` trims a
            # finished card's events after 30 days and a purge takes the row.
            append_jsonl(SEEN, {"day": today().isoformat(), "key": rec["idemp"], "task": tid,
                                "status": "filed", "note": str(note)})
            ledger_event(ledger_key(kind, title, rec["idemp"]), "filed", note, {"task": tid, "status": "filed"})
        if gated and tid:
            block_for_green(tid, vote=vote)
        elif gated:
            print("green gate: no task id in create output; card left unblocked", kind, title)
            FAILED.append(f"{kind}: {title[:60]}")
        if kind == "ops-action":
            append_jsonl(
                ACTIONS,
                {
                    "ops_action_id": rec["ops_action_id"],
                    "title": title,
                    "status": "open",
                    "note": str(note),
                },
            )
            existing.add(rec["ops_action_id"])
        print("filed", kind, rec["assignee"], title)
        n += 1
        if window is not None and kind == "team-improve" and tid:
            window["filed"] += 1
    return n


def idea_memory(kind: str, title: str) -> str:
    """Self-loop step 4: what the house already knows about THIS idea, and the last verdicts,
    on the team-improve card Zoey rules (bounded; never her Decide prompt). '' when nothing."""
    L = _ledger()
    if L is None:
        return ""
    lines = []
    try:
        st = L.fold(L.load(ledger_path())).get(ledger_key(kind, title, slug_key(kind, title))) or {}
        if st.get("debated_days") or st.get("task"):
            d = sorted(st.get("debated_days") or [])
            lines.append(f"house memory: debated {len(d)} night(s)" + (f" ({d[0]}..{d[-1]})" if d else "")
                         + (f", best sum {st['best_sum']}" if st.get("best_sum") is not None else "")
                         + (f", filed before as {st['task']}" if st.get("task") else ""))
        les = RANK_DIR / f"lessons-{today().isoformat()}.md"
        if les.is_file():
            txt = les.read_text(encoding="utf-8")
            if "## Last verdicts" in txt:
                block = txt.split("## Last verdicts", 1)[1].split("\n## ", 1)[0]
                v = [l for l in block.splitlines() if l.startswith("- ")][:6]
                if v:
                    lines += ["last verdicts (lessons):", *v]
    except Exception as e:  # noqa: BLE001
        print(f"idea memory: {e}")
    return ("\n".join(lines) + "\n") if lines else ""


def open_kpi_card(kid: str) -> str:
    """An open card on the current board filed on this KPI (a gate or its try), or ''."""
    try:
        slug = (KANBAN_HOME / "current").read_text().strip()
        db = KANBAN_HOME / "boards" / slug / "kanban.db"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute(
                "SELECT id FROM tasks WHERE status NOT IN ('done', 'archived') "
                "AND (title LIKE 'team-improve:%' OR title LIKE 'try: team-improve:%') "
                "AND (lower(title) LIKE ? OR lower(COALESCE(body, '')) LIKE ?) "
                "ORDER BY created_at DESC LIMIT 1", (f"%kpi={kid}%", f"%kpi: {kid}%")).fetchone()
        finally:
            con.close()
        return str(row[0]) if row else ""
    except (OSError, sqlite3.Error):
        return ""


def cards_off() -> str:
    """The filer's own off switch (the User 09-28, Krew fence): the research, the notes, the
    vote and the outcomes keep running; only the cards stop. A file, because the filer runs
    from system AND user units and no one env reaches both."""
    if os.environ.get("KREW_ARCHITECT_CARDS", "1").strip().lower() in ("0", "false", "no", "off"):
        return "KREW_ARCHITECT_CARDS=0"
    flag = Path.home() / ".hermes" / "architect-cards.off"
    return str(flag) if flag.exists() else ""


def ledger_key(kind: str, title: str, idemp: str) -> str:
    """The ledger's key for a filing: a title whose first field IS a repo is that repo's key
    (the nnt-agreed path files `team-improve: owner/repo — ...`), else the idempotency key."""
    L = _ledger()
    return (L.repo_key(title) if L is not None else "") or idemp


def record_ideas(day: str, notes: list[Path]) -> int:
    """Self-loop step 2 (the User 09-28): recording is not filing. Every idea line of the
    night's notes, the night's vote, and the agreed proposals go to the ledger BEFORE the
    fence is read - under the Krew fence they stopped at 09-26, because cards_off() returned
    before file_from() ever reached record_proposal. Nothing here calls hermes.
    Kill: KREW_IDEA_LEDGER=0 (the ledger; the agreed proposals still record)."""
    L = _ledger()
    n = 0
    for note in notes:
        if not note.is_file() or note.name.startswith("ops-efficiency-"):
            continue
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for agreed in nnt_agreed(text):
            if record_proposal(agreed, note):
                print("top improvements +", agreed)
        if L is not None:
            try:
                n += L.record_note(note, day, path=ledger_path())
            except Exception as e:  # noqa: BLE001
                print(f"idea ledger: {note.name}: {e}")
    if L is not None:
        try:
            n += L.record_rank(RANK_DIR / f"rank-{day}.json", day, path=ledger_path())
        except Exception as e:  # noqa: BLE001
            print(f"idea ledger: rank: {e}")
    return n


def rank_quorum(day: str) -> tuple[bool, str]:
    """(quorum met, why) from the night's rank file: >= TEAM_EVAL_QUORUM real voters (the
    ranker's own rule). A named missing voice does not block a night that has three."""
    p = RANK_DIR / f"rank-{day}.json"
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return False, f"no rank-{day}.json (team-eval did not rank)"
    q = int(d.get("quorum") or os.environ.get("TEAM_EVAL_QUORUM", "3") or 3)
    voters = [v for v in (d.get("voters") or []) if v not in (d.get("discounted") or {})]
    miss = sorted(set(d.get("absent") or []) | set((d.get("discounted") or {}).keys()))
    why = f"{len(voters)} real voter(s) of quorum {q}" + (f"; missing: {', '.join(miss)}" if miss else "")
    return len(voters) >= q, why


def window_main(day: str) -> int:
    """improve-file (the window row after team-eval; self-loop step 3): the ONE filing path for
    the improvement round. Records the ledger first (fence or not), then files only the agreed
    tier under a quorum, cap IMPROVE_FILE_CAP (2) a night. Writes its own status line."""
    lib = str(Path(__file__).resolve().parents[1] / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "window" / "lib"))   # krew: job_status lives with the window
    import job_status
    prev = (today() - dt.timedelta(days=1)).isoformat()
    stack = [INBOX / f"architect-stack-{d}.md" for d in (prev, day)]
    stack = [p if p.is_file() else INBOX / "krew-reports" / p.name[len("architect-stack-"):-3] / p.name for p in stack]
    notes = [
        *stack,                                 # the stack job runs after this one: yesterday's is filed tonight
        INBOX / f"team-research-{day}.md",
        INBOX / f"team-eval-{day}.md",
        INBOX / f"team-sources-{day[:7]}.md",
        INBOX / f"team-sources-{day}.md",
    ]
    rec_n = record_ideas(day, notes)
    print(f"idea ledger: +{rec_n} event(s)")
    off = cards_off()
    if off:
        job_status.write("improve-file", day, "ok", f"fenced ({Path(off).name}): {rec_n} idea event(s) recorded, nothing filed")
        print(f"architect cards OFF ({off}): ideas recorded, nothing filed")
        return 0
    ok, why = rank_quorum(day)
    window = {"quorum_ok": ok, "cap": int(os.environ.get("IMPROVE_FILE_CAP", "2")), "filed": 0}
    print(f"team vote: {why} -> " + ("the agreed tier may be filed" if ok else "no team-improve card tonight"))
    total = sum(file_from(n, window) for n in notes)
    reason = f"{total} card(s) filed ({window['filed']} team-improve, cap {window['cap']}); vote: {why}"
    if FAILED:
        job_status.write("improve-file", day, "failed", f"{len(FAILED)} green gate(s) did not close; {reason}")
        print(f"FAILED: {len(FAILED)} green gate(s) did not close: {', '.join(FAILED)}", file=sys.stderr)
        return 3
    job_status.write("improve-file", day, "ok", reason)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--window" in argv:
        return window_main(today().isoformat())
    day = today().isoformat()
    if "--ops" in argv:
        # the Sunday ops-efficiency run: its own note only (improve-file skips ops notes)
        off = cards_off()
        if off:
            print(f"architect cards OFF ({off}): nothing filed")
            return 0
        n = file_from(INBOX / f"ops-efficiency-{day}.md")
        print(f"ops: {n} filed")
        return 3 if FAILED else 0
    notes = [
        INBOX / f"architect-stack-{day}.md",
        INBOX / f"team-research-{day}.md",
        INBOX / f"team-eval-{day}.md",
        *sorted(INBOX.glob(f"*-improvement-ideas-{day}.md")),
        *sorted((INBOX / "ideas").glob(f"*-improvement-ideas-{day}.md")),
        INBOX / f"team-sources-{day[:7]}.md",
        INBOX / f"team-sources-{day}.md",
        INBOX / f"ops-efficiency-{day}.md",
    ]
    print(f"idea ledger: +{record_ideas(day, notes)} event(s)")
    off = cards_off()
    if off:
        print(f"architect cards OFF ({off}): notes kept in {INBOX}, ideas recorded, nothing filed")
        return 0
    total = 0
    for note in notes:
        total += file_from(note)
    if total == 0:
        print("no proposed cards")
    if FAILED:
        print(f"FAILED: {len(FAILED)} green gate(s) did not close: {', '.join(FAILED)}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
