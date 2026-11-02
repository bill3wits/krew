"""Nothing from a dev box ships: no personal or household names, no keys, no house paths or LAN addresses.

Scans every file the repo carries (text files; a binary fails on its own). An exception is spelled out below
with the file, the exact text and why; the exact text is erased from that file's content before the patterns
run, so a pattern stays strict everywhere else.

The crew's profile names (Crissa, Lila, Ada, Bash, Steward, Vera, Scout, Judge) are product names and ship: SOULs,
skills, board assignees and plugin defaults key on them. Household and family names, personal data, house paths,
transcripts and keys do not.

Denied personal / household / voice / face-id tokens are matched by sha256 of their normalized form
(lowercase; spaces, underscores and hyphens stripped). Cleartext of those tokens never appears in this file.
Structural leaks (paths, keys, LAN, house-word) stay as regexes. Planted tests use made-up or structural bait.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from test_krew_packages import ROOT, repo_files


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _norm(s: str) -> str:
    return re.sub(r"[\s_-]+", "", s).lower()


# sha256(norm(token)) — cleartext denied names are not in this repository
DENIED_NAME_HASHES: dict[str, frozenset[str]] = {
    "private-name": frozenset({
        "60dc47fccb617b98d90ea36073914c00419ae2775ece08e2a65f6666990baa3a",
    }),
    "household-name": frozenset({
        "01621148306fc8fb7c2b95eeb5c37e375f90db53cf8313ea87c9c34c05b7e0e5",
        "17fcb111a7f1334b3607b32e9c4f4e0a8a01b11c4d60c9913bed25eb78e819c9",
        "fc881aa34d44660e1012dec26ccda0b469d6c8359e91dc674dab4c095b9fe832",
    }),
    "house-voice": frozenset({
        "35f85825b9016fcc7818aca22298dc88a92ba6e4f029366ff8acbb3f823152d4",
        "86da0abd3ee1d8d2342b3c0ede6103b2cbf5458043696ddecfb73cc2738000c0",
        "82d3042b941378bc994b8c2596fa69e4370bd2cb5400c4c043c996dc197fda7c",
        "52e7c82feaba0420470cf5b034eb6af39041ba7f4a18ca791f2cc1c6410b7b23",
        "f7e4cfe2c2d7e08db40c798a2b6c47c8937a3f35ea53c8b27a5095d1c83946db",
        "ff37a07c6115a24a3fc3b4cab4cfbac131854e21fd172732c29be2ef0c8a736c",
    }),
    "face-id-engine": frozenset({
        "198becaf9c45016fec5d9bcd2e8d748de6b44a26cd4cc35ea72b670e665dff79",
        "e52456723c4222578335ff00a90f9434673444bda2bc74bf2cf8e58e60c7d374",
        "598c3180f84c7fbfa40fe7a5048e2b9ad4483237761d79337c0a0b222a95c2b0",
        "88af042bfd95523ccc9be72c5b3cfa5b46211397e0e07e18017b2551c4fd9078",
    }),
    "house-only-name": frozenset({
        "444b759c5264422ea582403ae2083d2447fd226a2e40795968dd740e9202cb97",
    }),
}

SCRUB = (
    ("person-name", re.compile(r"(?<![a-z0-9])bill(?:3wits|s|\d+)?(?![a-z0-9])", re.I)),
    ("email", re.compile(r"(?<![\w.+-])\w[\w.+-]*@[\w-]+\.[a-z]{2,}", re.I)),
    ("dashboard-port", re.compile(r"127\.0\.0\.1:(8652|8642)")),
    ("house-path", re.compile(r"/home/agent|projects/house-rails|house-deliverables|/data/(house|envs|models)")),
    ("house-host", re.compile(r"\.home\.arpa|(?<![\d.])192\.168\.\d+\.\d+|(?<![\d.])10\.\d+\.\d+\.\d+(?![\d.])")),
    ("house-word", re.compile(r"(?<![a-z0-9_-])house[-_](?!keeping)|\bHOUSE_[A-Z]", re.I)),
    ("key", re.compile(r"(?<![A-Za-z0-9])(sk-[A-Za-z0-9_-]{16,}|xai-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
                       r"hf_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")),
)

# (file, exact text, why)
ALLOW = (
    ("README.md", "hermes plugins install bill3wits/krew/plugins/", "the install line needs the repository's real slug"),
    ("README.md", "git clone https://github.com/bill3wits/krew", "the clone line needs the repository's real slug"),
    ("plugins/crissa-wall/README.md", "hermes plugins install bill3wits/krew/plugins/crissa-wall --enable",
     "the install line needs the repository's real slug"),
    ("plugins/crissa-wall/wall_net.py", '"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"',
     "the RFC 1918 private ranges as kill-search's default LAN, not an address of any machine"),
    ("tests/test_pack_drift.py", '"api_key": "sk-local-decomposer"',
     "the reference install's own decomposer placeholder, kept as it is on purpose (never a key: a local server with no "
     "auth); the lifted pack-drift test classifies that exact config"),
    ("tests/test_scrub.py", None, "this file holds the structural patterns and name hashes"),
    *((f"crew/hindsight/plugin/hindsight/{f}", "192.168.1.10",
       "the vendored Hindsight plugin's own example of a remote Hindsight host (upstream's text, held to its pinned "
       "tree sha in crew/local-services.json), not an address of any machine") for f in ("README.md", "__init__.py",
                                                                                          "setup.py")),
)


# plugins/crissa-wall/ is krew's own plugin since 2026-10-06: every rule applies to it.
# The reference install's M&S / window lists, verbatim and commented out. Its own unit, env and file names are
# written with the prefix `ref` by scripts/krew-ms-sync.py (ref-conductor.service, ref-gpu-mode), so no pattern is
# relaxed there any more (2026-10-06); the tuple stays so a relaxation has one place to come back to.
REFERENCE = ("config/ms.example.yaml", "config/window-schedule.example.json", "config/restart-window.example.json",
             "config/fleet-bootcheck.example.json")
REFERENCE_RELAXED: set[str] = set()


def name_offences(rel: str, text: str, hash_sets: dict[str, frozenset[str]] = DENIED_NAME_HASHES) -> list[str]:
    """Catch denied tokens by hash of normalized form; never echo the cleartext match."""
    out: list[str] = []
    seen: set[tuple[int, str]] = set()

    def _hit(pos: int, cat: str) -> None:
        line = text.count("\n", 0, pos) + 1
        key = (line, cat)
        if key not in seen:
            seen.add(key)
            out.append(f"{rel}:{line}: {cat}: <denied>")

    words = list(re.finditer(r"[A-Za-z][A-Za-z0-9]*", text))
    for m in words:
        h = _sha(m.group(0).lower())
        for cat, hashes in hash_sets.items():
            if h in hashes:
                _hit(m.start(), cat)
    # adjacent pairs separated only by space / _ / - (multi-word private names)
    for a, b in zip(words, words[1:]):
        between = text[a.end():b.start()]
        if not re.fullmatch(r"[\s_-]+", between):
            continue
        h = _sha(_norm(text[a.start():b.end()]))
        for cat, hashes in hash_sets.items():
            if h in hashes:
                _hit(a.start(), cat)
    return out


def offences(rel: str, text: str) -> list[str]:
    for f, exact, _why in ALLOW:
        if f == rel:
            if exact is None:
                return []
            text = text.replace(exact, "")
    out = name_offences(rel, text)
    for name, pat in SCRUB:
        if rel in REFERENCE and name in REFERENCE_RELAXED:
            continue
        for m in pat.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append(f"{rel}:{line}: {name}: {m.group(0)!r}")
    return out


def test_nothing_from_the_dev_box_ships():
    bad = []
    for rel in repo_files():
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            bad.append(f"{rel}: binary file (krew ships text only)")
            continue
        bad += offences(rel, text)
    assert not bad, "\n".join(bad)


def test_planted_offences_are_caught():
    """Structural and public-slug bait only — no household or voice cleartext in the planted set."""
    planted = {
        "a.py": "PATH = '/home/agent/projects/house-rails/lib'\n",
        "b.md": "Ask Miraq, then Quorv.\nHOUSE_SECRET=1\n",  # made-up names + house-word
        "c.yaml": "api_key: sk-abcdefghijklmnopqrstuv\n",
        "d.txt": "host: crissa.home.arpa 192.168.1.20\n",
        "e.md": "talk to bills\n",
    }
    for rel, text in planted.items():
        assert offences(rel, text), rel


def test_denied_name_hash_checker_works():
    """Mechanism check with a made-up token whose hash we inject — cleartext deny-list stays out of git."""
    fake = "zorpblat"
    h = _sha(fake.lower())
    hits = name_offences("x.md", f"Ask {fake} today.\n", {"household-name": frozenset({h})})
    assert hits and "household-name" in hits[0]
    # multi-word
    phrase = "Zorp Blat"
    hp = _sha(_norm(phrase))
    hits2 = name_offences("y.md", f"See {phrase} now.\n", {"private-name": frozenset({hp})})
    assert hits2 and "private-name" in hits2[0]


CREW = ("Crissa", "Lila", "Ada", "Bash", "Steward", "Vera", "Scout", "Judge")


def test_crew_profile_names_are_allowed():
    text = "".join(f"You are **{n}**. Ask {n.lower()} on the board (assignee={n.lower()}).\n" for n in CREW)
    assert not offences("souls/x/SOUL.md", text)


def test_allow_entries_are_still_needed():
    """An exception whose text no longer appears is stale: drop it."""
    stale = []
    for f, exact, why in ALLOW:
        if exact is None:
            continue
        p = ROOT / f
        if p.exists() and exact not in p.read_text(encoding="utf-8"):
            stale.append(f"{f}: {exact!r}")
    assert not stale, "stale scrub exceptions:\n" + "\n".join(stale)
