"""The trail's security scan reads hermes's real version from the tag, and a
hermes-agent advisory is dropped as superseded only when the live tree meets
its published fix (the User 2026-10-01, security fold t_54e04fb3).

The venv's hermes-agent dist reads 0.0.0 (the tree's pyproject.toml
placeholder), so the raw OSV query at 0.0.0 returns every hermes-agent
advisory ever published. Five 09-26 cards closed as false positives for
exactly this. The fold's ruling: read the tag (git describe) and judge each
finding against it; hermes-agent fixes come by an upstream hermes update,
never a crew git verb in the live tree.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_defaults_only  # noqa: E402,F401  (the reference profile for the session)
_SRC = Path(os.environ.get("MS_TRAIL_UNDER_TEST") or ROOT / "ms" / "scripts" / "ms-trail-run.py")
_SPEC = importlib.util.spec_from_file_location("ms_trail_live_version", _SRC)
_MS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MS)

sys.path.insert(0, str(ROOT / "ms" / "lib"))
from hermes_env_pins import vtuple as _vt  # noqa: E402  (the lib the trail itself uses)

# A real live-tree tag (v0.21.4+canary.20260928T071354Z-19-g35272ce28b, the
# 10-01 tree) — the canary/local tail must not break the comparison.
LIVE = "0.21.4+canary.20260928T071354Z-19-g35272ce28b"


def _mk(tmp_path, name: str, *, tag: str | None = "v0.21.4", git: bool = True) -> Path:
    t = tmp_path / name
    t.mkdir(parents=True)
    (t / "pyproject.toml").write_text('name = "hermes-agent"\nversion = "0.0.0"\n')
    if git:
        (t / ".git").mkdir()
    if tag:  # the fixture tree's "tag" (absent = no names)
        (t / "tag").write_text(tag)
    return t


def _fake_describe(captured: dict):
    tree = Path(str(captured["tree"])).resolve()

    def _run(cmd, *a, **k):
        # match on the "-C <tree>" pair as the real runner passes it
        if len(cmd) >= 2 and cmd[0] == "git" and cmd[1] in ("-C",) and Path(cmd[2]).resolve() == tree:
            tagfile = tree / "tag"
            if tagfile.exists():
                return (0, tagfile.read_text().strip(), "")
            return (128, "", "no tag")  # git describe: no names
        return (127, "", "not a fake")
    return _run


# ---------------------------------------------------------------- 0.0.0 read

def test_live_version_comes_from_the_tag_not_pyproject(tmp_path, monkeypatch):
    t = _mk(tmp_path, "tree", tag="v0.21.4+canary.20260928T071354Z-19-g35272ce28b")
    monkeypatch.setattr(_MS, "run", _fake_describe({"tree": t}))
    v = _MS._hermes_live_version(t)
    assert v.startswith("0.21.4+canary"), (
        f"tag is the version, not pyproject's 0.0.0 — got {v!r}")
    assert v != "0.0.0", "the 0.0.0 placeholder must not survive the read"


def test_live_version_empty_when_no_tag_and_not_a_guess(tmp_path, monkeypatch):
    # tag=None: _mk's default tag="v0.21.4" would write a tag file and git
    # describe would succeed — the point here is a tree that HAS .git but
    # carries no names, so git describe fails and the read stays "".
    t = _mk(tmp_path, "notag", tag=None)
    monkeypatch.setattr(_MS, "run", _fake_describe({"tree": t}))
    assert _MS._hermes_live_version(t) == ""  # (0,1,0) read, not (0,0,0)
    # no .git at all -> empty too
    b = tmp_path / "bare"; b.mkdir(); (b / "pyproject.toml").write_text("version = \"0.0.0\"")
    assert _MS._hermes_live_version(b) == ""


def test_components_shape_for_the_scanner(tmp_path, monkeypatch):
    t = _mk(tmp_path, "tree")
    monkeypatch.setattr(_MS, "run", _fake_describe({"tree": t}))
    monkeypatch.setattr(_MS, "_hermes_live_version", lambda *a, **k: "0.21.4")
    comps = _MS.hermes_real_version_components()
    assert comps == [{"package": "hermes-agent", "version": "0.21.4",
                      "ecosystem": "PyPI", "source": "live-tree"}]
    monkeypatch.setattr(_MS, "_hermes_live_version", lambda *a, **k: "")
    assert _MS.hermes_real_version_components() == []  # no tag -> no component


# ------------------------------------------------- superseded-by-the-live-tag

def _finding(vid, fixed):
    return {"package": "hermes-agent", "version": "0.0.0", "vuln_id": vid,
            "severity": "MODERATE", "fixed_versions": fixed}


def test_superseded_when_live_meets_a_published_fix():
    # The five false positives the 09-26 fold closed: fixes at 0.15/0.16/0.11.
    for vid, fixed in [("GHSA-4pqm", ["0.16.0"]), ("GHSA-238w", ["0.15.0"]),
                       ("GHSA-99f9", ["0.16.0"]), ("GHSA-pgp4", ["0.15.0"]),
                       ("GHSA-wm96", ["0.11.0"])]:
        assert _MS._advisory_applies(_finding(vid, fixed), LIVE, "0.0.0") is False, (
            f"{vid} fixed in <= {LIVE} — superseded")
        # and the gate is the live tag, not the 0.0.0 scan line:
        assert _MS._advisory_applies(_finding(vid, fixed), "0.0.0", "0.0.0") is True


def test_applies_when_no_fix_published_yet():
    # The two no-fix advisories (GHSA-pmqc feishu webhook body, GHSA-xq8w
    # _compress_context): judged separately against the live code, not here.
    assert _MS._advisory_applies(_finding("GHSA-pmqc", []), LIVE, "0.0.0") is True
    assert _MS._advisory_applies(_finding("GHSA-pmqc", None), LIVE, "0.0.0") is True


def test_applies_when_fix_is_ahead_of_the_live_tree():
    # 0.22.x > 0.21.4: not yet carried by the live tree.
    assert _MS._advisory_applies(_finding("GHSA-next", ["0.22.0"]), LIVE, "0.0.0") is True


def test_version_read_failure_keeps_everything():
    # live "" (tag unreadable) must hide no finding — the 0.0.0 reading stays.
    for fixed in (["0.16.0"], []):
        assert _MS._advisory_applies(_finding("GHSA-x", fixed), "", "0.0.0") is True
        assert _MS._advisory_applies(_finding("GHSA-x", fixed), "", None) is True


def test_non_hermes_agent_rows_pass_through_untouched():
    f = {"package": "httpx2", "version": "2.13.1", "vuln_id": "GHSA-7mj9"}
    assert _MS._advisory_applies(f, LIVE, "0.0.0") is True
    assert _MS._advisory_applies(f, "", None) is True


def test_vtuple_ignores_the_canary_tail():
    assert _vt(LIVE) >= _vt("0.16.0")
    assert _vt(LIVE) >= _vt("0.21.0")
    assert not (_vt(LIVE) >= _vt("0.22.0"))
    assert _vt("0.0.0") == (0, 0, 0)  # the placeholder that met nothing
