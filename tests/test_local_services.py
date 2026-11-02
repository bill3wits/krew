"""krew's local services (the User 2026-11-01: "EVERYthing we run is local"): the long memory (vendored Hindsight plugin,
hindsight-api, the embedding model, the pgvector store) and web search (SearXNG), as krew-install --memory / --search
and init's defaults install them. Every external effect goes through a recorder: no pip, no download, no systemctl."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
import tarfile
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import krew_install_local as L  # noqa: E402
import krew_install_init as I  # noqa: E402

PINS = L.pins()


class Rec:
    """A stand-in for subprocess.run: records argv; answers the model fetch and the pg0 lookup like the real venv."""

    def __init__(self, hf: Path | None = None, rc: int = 0):
        self.calls, self.hf, self.rc = [], hf, rc

    def __call__(self, cmd, env=None, cwd=None):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        out = ""
        if "-c" in cmd and "snapshot_download" in cmd[cmd.index("-c") + 1]:
            a = json.loads(cmd[-1])
            d = Path(env["HF_HOME"]) / "hub" / ("models--" + a["repo"].replace("/", "--")) / "snapshots" / a["revision"]
            for f in a["allow"]:
                (d / f).parent.mkdir(parents=True, exist_ok=True)
                (d / f).write_text("x")
            out = json.dumps({"dir": str(d), "bad": {}})
        elif "-c" in cmd and "import pg0" in cmd[cmd.index("-c") + 1]:
            out = "/venv/lib/python3.14/site-packages/pg0/bin/pg0"
        return types.SimpleNamespace(returncode=self.rc, stdout=out, stderr="")

    def ran(self, *words) -> list:
        return [c for c in self.calls if all(w in c for w in words)]


def homes(tmp_path):
    hh, kh = tmp_path / "hermes", tmp_path / "krew"
    for p in ("", "profiles/ada", "profiles/bash"):
        (hh / p).mkdir(parents=True, exist_ok=True)
        (hh / p / "config.yaml").write_text("model:\n  default: crew\nweb:\n  backend: firecrawl\n  extract_char_limit: 8000\n"
                                            if p == "profiles/ada" else "model:\n  default: crew\n")
    return hh, kh


def say_to(lines):
    return lambda s: lines.append(str(s))


# ---- the pins ---------------------------------------------------------------------------------------------------

def test_the_vendored_plugin_is_the_pinned_tree_and_carries_its_licence():
    pp = PINS["hindsight"]["plugin"]
    src = ROOT / pp["dir"]
    assert L.tree_sha(src) == pp["tree_sha256"]
    assert "MIT License" in (src / "LICENSE").read_text() and (src / "plugin.yaml").is_file()
    assert not (src / "tests").exists()                 # dropped, and the pin says so
    assert pp["dropped"] == ["tests/", "docs/banner.png"] and not (src / "docs").exists()   # krew ships text only


def test_every_pin_is_exact_and_every_unit_ships():
    hs, s, e = PINS["hindsight"], PINS["searxng"], PINS["embedding"]
    assert "hindsight-api==0.10.2" in hs["requirements"] and any(r.startswith("sqlalchemy") and "<2.1" in r
                                                                  for r in hs["requirements"])
    assert hs["torch"].startswith("torch==") and hs["torch_index"].endswith("/whl/cpu")
    assert len(s["commit"]) == 40 and len(s["tarball_sha256"]) == 64 and len(s["tree_sha256"]) == 64
    assert len(e["revision"]) == 40 and e["dim"] == 384 and e["prefixes"] == {"query": "query: ", "passage": "passage: "}
    assert all(len(h) == 64 for h in e["onnx"].values())
    for u in PINS["units"]:
        text = (ROOT / "crew" / "units" / u).read_text()
        assert "# krew: opt-in" in text                 # krew-crew-install writes them, never enables them unnamed
        assert "%h/.krew" in text and "{" not in text.split("[Unit]")[1].replace("${", "")


def test_loopback_everywhere():
    db = (ROOT / "crew" / "units" / "krew-memory-db.service").read_text()
    assert "listen_addresses=127.0.0.1" in db and "Type=forking" in db and "PIDFile=" in db
    hs = (ROOT / "crew" / "units" / "krew-hindsight.service").read_text()
    assert "Requires=krew-memory-db.service" in hs and "MemoryHigh" not in hs.split("[Service]")[1]
    assert 'bind_address: "127.0.0.1"' in L.SETTINGS and "limiter: false" in L.SETTINGS and "json" in L.SETTINGS


def test_tree_sha_is_order_and_cache_blind(tmp_path):
    a = tmp_path / "a"
    (a / "d").mkdir(parents=True)
    (a / "x.py").write_text("1")
    (a / "d" / "y.py").write_text("2")
    first = L.tree_sha(a)
    (a / "__pycache__").mkdir()
    (a / "__pycache__" / "x.pyc").write_text("junk")
    (a / "LICENSE").write_text("MIT")
    assert L.tree_sha(a) == first
    (a / "x.py").write_text("changed")
    assert L.tree_sha(a) != first


def test_expand_reads_xdg_then_the_default(tmp_path):
    assert L.expand("${XDG_DATA_HOME:-~/.local/share}/crissa-models/hf", {"XDG_DATA_HOME": str(tmp_path)}) == \
        tmp_path / "crissa-models" / "hf"
    assert L.expand("${XDG_DATA_HOME:-~/.local/share}/m", {}) == Path("~/.local/share/m").expanduser()


# ---- memory -----------------------------------------------------------------------------------------------------

def test_memory_installs_everything_locally_and_a_scratch_install_starts_nothing(tmp_path):
    hh, kh = homes(tmp_path)
    (hh / ".env").write_text("XAI_API_KEY=sekret\n")
    xdg = tmp_path / "xdg"
    rec, lines = Rec(), []
    ok, why = L.memory(hh, kh, say_to(lines), brain={"base_url": "http://127.0.0.1:8005/v1", "model": "crew", "key_env": ""},
                       unit_dir=kh / "systemd-user", scratch=True, run=rec, runtime_python="/hermes/venv/bin/python",
                       env={"XDG_DATA_HOME": str(xdg)})
    assert ok, why
    # the plugin: the vendored copy, its pip pin into Hermes's runtime, never `hermes plugins install`
    assert L.tree_sha(hh / "plugins" / "hindsight") == PINS["hindsight"]["plugin"]["tree_sha256"]
    assert not rec.ran("plugins", "install")
    assert rec.ran("/hermes/venv/bin/python", "hindsight-client==0.10.2")
    cfg = json.loads((hh / "hindsight" / "config.json").read_text())
    assert cfg["api_url"] == f"http://127.0.0.1:{PINS['hindsight']['port']}" and cfg["auto_recall"] is False
    # the API venv: CPU torch FIRST from its own index, then hindsight-api
    pips = [c for c in rec.calls if "install" in c and str(kh / "hindsight" / ".venv" / "bin" / "python") in c]
    assert "torch==2.14.0" in pips[0] and PINS["hindsight"]["torch_index"] in pips[0]
    assert "hindsight-api==0.10.2" in pips[1]
    # the model, once, into the shared HF home
    snap = L.snapshot_dir(xdg / "crissa-models" / "hf", PINS["embedding"]["repo"], PINS["embedding"]["revision"])
    assert (snap / "model.safetensors").exists()
    env = L.read_env(kh / "hindsight" / "hindsight.env")
    assert env["HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL"] == str(snap)          # a path: the pinned revision, offline
    assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1" and env["HF_HOME"] == str(snap.parents[3])
    assert env["HINDSIGHT_API_HOST"] == "127.0.0.1" and env["HINDSIGHT_API_RERANKER_PROVIDER"] == "rrf"
    assert env["HINDSIGHT_API_LLM_BASE_URL"] == "http://127.0.0.1:8005/v1" and env["HINDSIGHT_API_LLM_API_KEY"] == "local"
    assert "enable_thinking" in env["HINDSIGHT_API_LLM_EXTRA_BODY"]
    db = L.read_env(kh / "hindsight" / "db.env")
    assert env["HINDSIGHT_API_DATABASE_URL"] == (f"postgresql://hindsight:{db['PG0_PASSWORD']}@127.0.0.1:"
                                                 f"{PINS['vector_db']['port']}/hindsight")
    for f in ("hindsight.env", "db.env"):
        assert stat.S_IMODE((kh / "hindsight" / f).stat().st_mode) == 0o600
    assert db["PG0_PASSWORD"] not in "\n".join(lines)                        # never printed
    # units rendered for this krew home; a scratch install runs no systemctl at all
    unit = (kh / "systemd-user" / "krew-hindsight.service").read_text()
    assert str(kh) in unit and "%h/.krew" not in unit
    assert not rec.ran("systemctl")
    assert any("scratch  not run: systemctl --user enable --now krew-memory-db.service krew-hindsight.service" in s
               for s in lines)


def test_memory_rerun_keeps_the_password_and_the_venv(tmp_path):
    hh, kh = homes(tmp_path)
    kw = dict(brain={"base_url": "http://127.0.0.1:8005/v1", "model": "crew", "key_env": ""}, unit_dir=kh / "u",
              scratch=True, runtime_python=None, env={"XDG_DATA_HOME": str(tmp_path / "x")})
    assert L.memory(hh, kh, lambda s: None, run=Rec(), **kw)[0]
    pw = L.read_env(kh / "hindsight" / "db.env")["PG0_PASSWORD"]
    (kh / "hindsight" / ".venv" / "bin").mkdir(parents=True)
    (kh / "hindsight" / ".venv" / "bin" / "python").write_text("")
    rec = Rec()
    assert L.memory(hh, kh, lambda s: None, run=rec, **kw)[0]
    assert L.read_env(kh / "hindsight" / "db.env")["PG0_PASSWORD"] == pw
    assert not [c for c in rec.calls if "venv" in c[1:2]] and not rec.ran("hindsight-api==0.10.2")


def test_memory_refuses_a_tampered_plugin_copy(tmp_path):
    hh, kh = homes(tmp_path)
    p = json.loads(L.PINS.read_text())
    p["hindsight"]["plugin"]["tree_sha256"] = "0" * 64
    pin = tmp_path / "pins.json"
    pin.write_text(json.dumps(p))
    rec = Rec()
    ok, why = L.memory(hh, kh, lambda s: None, brain={"base_url": "x", "model": "m", "key_env": ""}, unit_dir=kh / "u",
                       scratch=True, run=rec, pin_path=pin)
    assert not ok and "does not match its pin" in why[0]
    assert not (hh / "plugins" / "hindsight").exists() and not rec.calls


def test_an_own_install_enables_and_starts_the_units(tmp_path, monkeypatch):
    hh, kh = homes(tmp_path)
    own = tmp_path / "home" / ".config" / "systemd" / "user"
    monkeypatch.setattr(L, "USER_UNITS", own)
    rec = Rec()
    ok, _ = L.memory(hh, kh, lambda s: None, brain={"base_url": "http://127.0.0.1:8005/v1", "model": "crew", "key_env": ""},
                     unit_dir=own, scratch=False, run=rec, env={"XDG_DATA_HOME": str(tmp_path / "x")})
    assert ok
    assert rec.ran("systemctl", "--user", "daemon-reload")
    assert rec.ran("systemctl", "--user", "enable", "--now", "krew-memory-db.service", "krew-hindsight.service")


def test_units_written_anywhere_else_are_never_loaded_even_on_own_homes(tmp_path):
    """HOME=<scratch> makes the scratch homes look like the own ones: the unit folder decides as well."""
    hh, kh = homes(tmp_path)
    rec, lines = Rec(), []
    ok, _ = L.memory(hh, kh, say_to(lines), brain={"base_url": "http://127.0.0.1:8005/v1", "model": "crew", "key_env": ""},
                     unit_dir=tmp_path / "units", scratch=False, run=rec, env={"XDG_DATA_HOME": str(tmp_path / "x")})
    assert ok and not rec.ran("systemctl")
    assert any("not your systemd folder: not run: systemctl --user enable --now" in s for s in lines)


def test_a_cloud_brain_takes_its_key_and_no_thinking_switch(tmp_path):
    (tmp_path / ".env").write_text("XAI_API_KEY=sekret\n")
    v = L.llm_values({"base_url": "https://api.x.ai/v1", "model": "grok-4.7", "key_env": "XAI_API_KEY"}, tmp_path)
    assert v["HINDSIGHT_API_LLM_API_KEY"] == "sekret" and "HINDSIGHT_API_LLM_EXTRA_BODY" not in v


def test_the_kits_embedding_lock_wins(tmp_path):
    lock = tmp_path / PINS["embedding"]["kit_lock"]
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({"repo": "intfloat/multilingual-e5-small", "revision": "f" * 40, "dim": 384}))
    lines = []
    e = L.embedding_pin(PINS, tmp_path, say_to(lines))
    assert e["revision"] == "f" * 40 and any("the kit's wins" in s for s in lines)
    assert L.embedding_pin(PINS, None, lambda s: None)["revision"] == PINS["embedding"]["revision"]


# ---- search -----------------------------------------------------------------------------------------------------

def fake_archive(tmp_path) -> tuple[bytes, Path]:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, body in (("searxng-abc/searx/webapp.py", b"# webapp\n"), ("searxng-abc/requirements.txt", b"flask\n")):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            t.addfile(info, io.BytesIO(body))
    data = buf.getvalue()
    p = json.loads(L.PINS.read_text())
    import hashlib
    p["searxng"]["tarball_sha256"] = hashlib.sha256(data).hexdigest()
    p["searxng"]["tree_sha256"] = ""
    pin = tmp_path / "pins.json"
    pin.write_text(json.dumps(p))
    return data, pin


class Opener:
    def __init__(self, data):
        self.data, self.urls = data, []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        return io.BytesIO(self.data)


def test_search_fetches_the_pinned_commit_and_points_every_profile_at_it(tmp_path):
    hh, kh = homes(tmp_path)
    data, pin = fake_archive(tmp_path)
    op, rec, lines = Opener(data), Rec(), []
    ok, why = L.search(hh, kh, say_to(lines), unit_dir=kh / "systemd-user", scratch=True, run=rec, opener=op, pin_path=pin)
    assert ok, why
    assert op.urls == [PINS["searxng"]["url"]]
    src = kh / "searxng" / "src"
    assert (src / "searx" / "webapp.py").is_file() and (src / ".krew-commit").read_text().strip() == PINS["searxng"]["commit"]
    assert PINS["searxng"]["commit"] in (src / "searx" / "version_frozen.py").read_text()
    assert rec.ran("-r", str(src / "requirements.txt"))
    st = (kh / "searxng" / "settings.yml")
    assert stat.S_IMODE(st.stat().st_mode) == 0o600 and 'bind_address: "127.0.0.1"' in st.read_text()
    assert "secret_key: \"" in st.read_text() and "{secret}" not in st.read_text()
    url = f"http://127.0.0.1:{PINS['searxng']['port']}"
    for home in (hh, hh / "profiles" / "ada", hh / "profiles" / "bash"):
        cfg = (home / "config.yaml").read_text()
        assert "search_backend: searxng" in cfg and "keyless_fallback: false" in cfg and "keyless_rescue: false" in cfg
        assert L.read_env(home / ".env")["SEARXNG_URL"] == url
    ada = (hh / "profiles" / "ada" / "config.yaml").read_text()
    assert "extract_char_limit: 8000" in ada and ada.count("web:") == 1       # her other web keys are kept
    assert not rec.ran("systemctl")
    # a second run fetches nothing and changes nothing
    op2 = Opener(b"")
    before = {p: p.read_text() for p in hh.rglob("config.yaml")}
    assert L.search(hh, kh, lambda s: None, unit_dir=kh / "systemd-user", scratch=True, run=Rec(), opener=op2,
                    pin_path=pin)[0]
    assert op2.urls == [] and before == {p: p.read_text() for p in hh.rglob("config.yaml")}


def test_search_refuses_an_archive_that_is_not_the_pin(tmp_path):
    hh, kh = homes(tmp_path)
    data, pin = fake_archive(tmp_path)
    ok, why = L.search(hh, kh, lambda s: None, unit_dir=kh / "u", scratch=True, run=Rec(), opener=Opener(data + b"x"),
                       pin_path=pin)
    assert not ok and "does not match its pin" in why[0]
    assert not (kh / "searxng" / "src").exists()
    assert "search_backend" not in (hh / "config.yaml").read_text()


def test_no_fetch_switch_refuses_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv(L.NO_FETCH, "1")
    with pytest.raises(RuntimeError):
        L.fetch("https://example.invalid/x", tmp_path / "x")


def test_web_block_adds_or_merges():
    assert "web:\n" in L.web_block("model: {}\n") and "search_backend: searxng" in L.web_block("model: {}\n")
    t = "a: 1\nweb:\n  backend: firecrawl\n  search_backend: tavily\nb: 2\n"
    out = L.web_block(t)
    assert "search_backend: searxng" in out and "tavily" not in out and "backend: firecrawl" in out
    assert out.endswith("b: 2\n") and L.web_block(out) == out


# ---- init, the panel, All OS Off -------------------------------------------------------------------------------

def test_init_installs_memory_and_search_unless_told_not_to():
    ns = types.SimpleNamespace
    assert I.memory_search(ns()) == (True, True)
    assert I.memory_search(ns(no_memory=True, no_search=True)) == (False, False)
    assert I.memory_search(ns(no_memory=True, memory=True, no_search=True)) == (True, False)


def test_the_panel_names_the_three_units_so_all_os_off_and_on_cover_them():
    spec = importlib.util.spec_from_file_location("krewtest_tiles_local", ROOT / "panel" / "krew-panel-tiles.py")
    T = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "panel"))
    spec.loader.exec_module(T)
    units = {t["unit"] for t in T.manifest({})["tiles"] if t.get("unit")}
    assert set(PINS["units"]) <= units


def test_a_test_never_reaches_a_real_download_pip_or_systemctl(tmp_path, monkeypatch):
    monkeypatch.delenv(L.REAL_SWITCH, raising=False)
    assert L.under_test()
    with pytest.raises(RuntimeError, match="under a test"):
        L.default_run(["systemctl", "--user", "daemon-reload"])
    with pytest.raises(RuntimeError, match="under a test"):
        L.fetch("https://example.invalid/x.tar.gz", tmp_path / "x")
    hh, kh = homes(tmp_path)
    lines = []
    ok, _ = L.memory(hh, kh, say_to(lines), brain={"base_url": "http://127.0.0.1:1/v1", "model": "m", "key_env": ""},
                     unit_dir=kh / "u", scratch=False, runtime_python="/py")
    assert ok and (hh / "plugins" / "hindsight").is_dir() and not (kh / "hindsight" / ".venv").exists()
    assert any("under a test" in s for s in lines)
    ok, _ = L.search(hh, kh, say_to(lines), unit_dir=kh / "u", scratch=False)
    assert ok and not (kh / "searxng").exists() and "search_backend" not in (hh / "config.yaml").read_text()


# ---- the docs index on the same model -----------------------------------------------------------------------------

def _docs_index():
    from conftest import submodule
    return submodule("krew-docs", "docs_index")


def test_the_docs_index_pins_the_same_model_as_the_install():
    D = _docs_index()
    e = PINS["embedding"]
    assert (D.E5["repo"], D.E5["revision"], D.E5["hf_home"]) == (e["repo"], e["revision"], e["hf_home"])
    assert (D.E5["query"], D.E5["passage"]) == (e["prefixes"]["query"], e["prefixes"]["passage"]) and D.DIM == e["dim"]


def test_one_copy_on_disk_in_either_hf_layout(tmp_path, krew_home, monkeypatch):
    D = _docs_index()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    hf = tmp_path / "xdg" / "crissa-models" / "hf"
    tail = Path("models--intfloat--multilingual-e5-small") / "snapshots" / PINS["embedding"]["revision"]
    assert D.e5_snapshot(D.cfg()) == hf / "hub" / tail                      # nothing yet: the standard place
    (hf / tail / "onnx").mkdir(parents=True)
    (hf / tail / "onnx" / "model.onnx").write_text("x")
    assert D.e5_snapshot(D.cfg()) == hf / tail                              # a flat cache (HF_HUB_CACHE=<HF_HOME>)
    need = tuple(PINS["embedding"]["allow"]) + tuple(PINS["embedding"]["onnx"])
    assert L.snapshot_dir(hf, PINS["embedding"]["repo"], PINS["embedding"]["revision"], need) == hf / "hub" / tail
    for f in need:
        (hf / tail / f).parent.mkdir(parents=True, exist_ok=True)
        (hf / tail / f).write_text("x")
    assert L.snapshot_dir(hf, PINS["embedding"]["repo"], PINS["embedding"]["revision"], need) == hf / tail


def test_a_model_change_re_embeds_everything(tmp_path, krew_home, monkeypatch):
    D = _docs_index()
    src = tmp_path / "notes"
    src.mkdir()
    (src / "a.md").write_text("# A\n\n" + "Alpha beta gamma delta epsilon, a sentence long enough to index. " * 8 + "\n")
    (krew_home / "krew.yaml").write_text(f"docs:\n  sources:\n    notes: {src}\n")

    class Fake:
        def embed(self, texts, batch_size=64):
            for t in texts:
                assert t.startswith(("passage: ", "query: "))
                yield [1.0] + [0.0] * (D.DIM - 1)
    monkeypatch.setitem(D._EMB, "m", Fake())
    con = D.connect()
    with con:
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('model', 'BAAI/bge-small-en-v1.5')")
    D.build(con=con, progress=None)
    con.close()
    con = D.connect()
    D.build(con=con, progress=None)                                          # same model: nothing re-embedded
    con.close()
    con = D.connect()
    with con:
        con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('model', 'BAAI/bge-small-en-v1.5')")
    st = D.build(con=con, progress=None)
    assert st["model_change"].startswith("BAAI/bge-small-en-v1.5 -> intfloat/multilingual-e5-small@")
    assert st["embedded"] >= 1 and st["skipped"] == 0, json.dumps(st)
    assert D.status(con)["model"] == D.cfg()["model"]


def test_crissas_knowledge_index_gets_krews_manifest_and_loses_it_with_the_tiles(tmp_path, krew_home, monkeypatch):
    import krew_install_panel_tiles as PT
    state = tmp_path / "crissa"
    (state / "panel.d").mkdir(parents=True)
    f = PT.knowledge(state, krew_home, lambda s: None)
    body = json.loads(f.read_text())
    assert f == state / "knowledge.d" / "krew.json" and body["id"] == "krew"
    assert body["model"] == PINS["embedding"]["repo"] == PT.KNOWLEDGE_MODEL
    assert body["folders"]["built"] == str(krew_home / "built") and body["index_db"].endswith("docs-index/index.db")
    assert Path(body["folders"]["skills"]).is_dir() and Path(body["folders"]["docs"]).is_dir()
    spec = importlib.util.spec_from_file_location("krewtest_tiles_rm", ROOT / "panel" / "krew-panel-tiles.py")
    T = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "panel"))
    spec.loader.exec_module(T)
    monkeypatch.setenv("CRISSA_HOME", str(state))
    T.write({})
    assert T.remove({})["removed"] and not f.exists()


def test_the_docs_index_names_the_model_the_kit_looks_for():
    D = _docs_index()
    assert D.cfg()["model"].startswith(PINS["embedding"]["repo"] + "@")
