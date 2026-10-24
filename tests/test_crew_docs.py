"""Docs index night + memory (lane C, group 3): the dream night's files, the nightly index step, the places map +
watch, Zoey's docs lookup and review queue. Every run here is against a temp root; nothing reaches a real store."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import ROOT

CREW = ROOT / "crew"
S = CREW / "scripts"


def _env(tmp: Path, **kw) -> dict:
    env = dict(os.environ, HOME=str(tmp), KREW_MEMORY_ROOT=str(tmp / "mem"), PYTHONPATH=str(CREW / "lib"),
               HINDSIGHT_API_URL="http://127.0.0.1:9", HINDSIGHT_URL="http://127.0.0.1:9")
    env.update({k: str(v) for k, v in kw.items()})
    return env


def _run(tmp, *argv, **kw):
    return subprocess.run([sys.executable, *map(str, argv)], capture_output=True, text=True, env=_env(tmp, **kw),
                          timeout=60)


def _lib(name: str, path: Path):
    sys.path.insert(0, str(CREW / "lib"))
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_memory_day_files_and_bullets_say_when_cut(tmp_path):
    long = "word " * 80
    assert _run(tmp_path, S / "krew_memory.py", "append", "--kind", "system", "--text", long).returncode == 0
    day = next((tmp_path / "mem" / "day").glob("*.jsonl"))
    assert json.loads(day.read_text().splitlines()[0])["text"] == long          # kept whole
    out = _run(tmp_path, S / "krew_memory.py", "today").stdout
    assert "[cut at 160 of" in out and str(day.parent) in out                   # coach: where the rest is
    for cmd in ("roll-day", "roll-3day", "workspace"):
        r = _run(tmp_path, S / "krew_memory.py", cmd)
        assert r.returncode == 0, r.stderr
    assert (tmp_path / "mem" / "rolling" / "3day.md").is_file()


def test_dream_night_dry_runs_without_hindsight(tmp_path):
    _run(tmp_path, S / "krew_memory.py", "append", "--kind", "system", "--text", "boiler serviced")
    r = _run(tmp_path, S / "krew_memory.py", "dream-night", "--dry", KREW_DREAM_PEOPLE="Ana and Joe")
    assert r.returncode in (0, 5), r.stderr
    rep = json.loads(r.stdout)
    assert "steps" in rep
    assert not list(Path(tmp_path).glob("data*"))


def test_reflect_asks_about_the_configured_people(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_MEMORY_ROOT", str(tmp_path / "mem"))
    monkeypatch.setenv("KREW_DREAM_PEOPLE", "Ana and Joe")
    m = _lib("krew_memory_t", S / "krew_memory.py")
    q = m.hindsight_reflect_day("2026-01-02", dry=True)["query"]
    assert "Ana and Joe" in q


def test_krew_dream_rollup_dry(tmp_path):
    (tmp_path / ".hermes").mkdir()
    r = _run(tmp_path, S / "krew-dream.py", "--rollup", "3", "--dry")
    assert r.returncode == 0, r.stderr
    assert "door" not in r.stdout.lower()


def test_nightly_index_off_by_default_and_builds_with_krew_docs(tmp_path):
    r = _run(tmp_path, S / "nightly-krew-index.py", "--dry")
    assert r.returncode == 0 and "skipped" in r.stdout
    fake = tmp_path / "docs_index.py"
    fake.write_text("")
    r = _run(tmp_path, S / "nightly-krew-index.py", "--dry", KREW_NIGHTLY_INDEX="1", KREW_DOCS_INDEX_SCRIPT=fake)
    assert r.returncode == 0, r.stderr
    assert f"{fake} build" in r.stdout and "--source" not in r.stdout and "CPUQuota=300%" in r.stdout
    r = _run(tmp_path, S / "nightly-krew-index.py", "--dry", KREW_NIGHTLY_INDEX="1", KREW_DOCS_INDEX_SCRIPT=fake,
             KREW_INDEX_SOURCES="docs,places", KREW_INDEX_CPU_QUOTA="150%")
    assert "--source docs,places" in r.stdout and "CPUQuota=150%" in r.stdout


def _places_cfg(tmp: Path) -> Path:
    (tmp / "refs" / "products" / "kettle").mkdir(parents=True)
    (tmp / "refs" / "products" / "kettle" / "SOURCES.md").write_text("maker's site, 3 photos\n")
    (tmp / "refs" / "products" / "kettle" / "a.jpg").write_bytes(b"x")
    cfg = json.loads((CREW / "config" / "krew-places.json").read_text())
    cfg["map_file"] = str(tmp / "places" / "PLACES.md")
    cfg["places"] = [dict(cfg["places"][0], root=str(tmp / "refs"))]
    cfg["watch"]["off_file"] = str(tmp / "off")
    p = tmp / "places.json"
    p.write_text(json.dumps(cfg))
    return p


def test_places_map_is_written_from_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("KREW_PLACES_CONFIG", str(_places_cfg(tmp_path)))
    m = _lib("krew_places_t", CREW / "lib" / "krew_places.py")
    rows = m.walk()
    out = m.write_map(rows)
    text = Path(out).read_text()
    assert "kettle" in text and "maker's site" in text


def test_places_watch_once_rewrites_the_map_then_builds_places(tmp_path):
    cfgp = _places_cfg(tmp_path)
    fake = tmp_path / "docs_index.py"
    fake.write_text(f"import sys, pathlib; pathlib.Path({str(tmp_path / 'argv')!r}).write_text(' '.join(sys.argv[1:]))\n")
    r = _run(tmp_path, S / "krew-places-watch.py", "once", KREW_PLACES_CONFIG=cfgp, KREW_DOCS_INDEX_SCRIPT=fake)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "argv").read_text() == "build --source places"
    assert "kettle" in (tmp_path / "places" / "PLACES.md").read_text()


def test_zoey_docs_lookup_marks_cuts_and_reads_every_source_by_default(tmp_path, monkeypatch):
    cfgp = tmp_path / "zd.json"
    cfgp.write_text(json.dumps({"sources": [], "n": 5, "chars": 40, "timeout_s": 5, "queue": str(tmp_path / "q.jsonl")}))
    monkeypatch.setenv("KREW_ZOEY_DOCS_CONFIG", str(cfgp))
    m = _lib("zoey_docs_t", CREW / "lib" / "zoey_docs.py")
    hits = [{"source": "notes", "ref": "a.md#1", "text": "x " * 100}, {"source": "skills", "ref": "s.md", "text": "short"}]

    class R:
        returncode, stderr, stdout = 0, "", json.dumps(hits)
    out = m.lookup("how", runner=lambda *a, **k: R())
    assert "notes:a.md#1" in out and "skills:s.md" in out
    assert "[cut at 40 of" in out and "read --ref a.md#1" in out


def test_review_approves_into_the_references_folder(tmp_path, monkeypatch):
    cfgp = tmp_path / "zd.json"
    cfgp.write_text(json.dumps({"queue": str(tmp_path / "q.jsonl"), "approved_dir": str(tmp_path / "refs")}))
    monkeypatch.setenv("KREW_ZOEY_DOCS_CONFIG", str(cfgp))
    m = _lib("zoey_docs_t2", CREW / "lib" / "zoey_docs.py")
    m.suggest("Kettle manual", "https://example.org/kettle.pdf", "descaling steps")
    rid = m.read_queue(tmp_path / "q.jsonl")[0]["id"]
    r = _run(tmp_path, S / "zoey-docs-review.py", "approve", rid, "--no-index", KREW_ZOEY_DOCS_CONFIG=cfgp)
    assert r.returncode == 0, r.stderr
    assert list((tmp_path / "refs").glob("*.md"))


def test_hindsight_unit_is_opt_in_and_env_example_has_no_secret():
    unit = (CREW / "units" / "krew-hindsight.service").read_text()
    assert "# krew: opt-in" in unit and "upgrade-0.10.2.sh" in unit
    env = (CREW / "hindsight" / "hindsight.env.example").read_text()
    assert "CHANGE-ME" in env


def test_installer_never_enables_an_opt_in_unit(tmp_path):
    r = subprocess.run([sys.executable, str(S / "krew-crew-install.py"), "--krew-home", str(tmp_path / "k"),
                        "--hermes-home", str(tmp_path / "h"), "--dry-run"], capture_output=True, text=True,
                       env=dict(os.environ, HOME=str(tmp_path)), timeout=60)
    assert r.returncode == 0, r.stderr
    runs = next(line for line in r.stdout.splitlines() if line.startswith("Units a straight clone runs"))
    assert "krew-places-watch.service" in runs and "krew-hindsight.service" not in runs
    assert "krew-hindsight.service" in next(line for line in r.stdout.splitlines() if line.startswith("Opt-in"))


def test_coach_dont_cap_in_memory_and_docs():
    mem = (S / "krew_memory.py").read_text()
    for pat in (r'\(text or ""\)\[:1200\]', r"\{t\[:160\]\}", r"\[:4000\]\)", r"\{t\[:200\]\}", r"\)\[:12000\]"):
        assert not re.search(pat, mem), pat
    assert not re.search(r'\.strip\(\)\[: int\(cfg\["chars"\]\)\]', (CREW / "lib" / "zoey_docs.py").read_text())
