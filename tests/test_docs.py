"""krew-docs: a fresh install builds an index from its own sources; a shortened passage says so and names the read."""
from __future__ import annotations

import json
import re
import subprocess
import sys

from conftest import ROOT, submodule


def write_settings(home, src, extra=""):
    (home / "krew.yaml").write_text(f"docs:\n  sources:\n    notes: {src}\n  chars: 200\n{extra}")


def test_build_query_read_on_a_fresh_home(krew_home, tmp_path):
    src = tmp_path / "notes"
    src.mkdir()
    long_para = "The gateway restarts cleanly when the toolsets change. " * 30
    (src / "gateway.md").write_text(f"# Gateway\n\n## Restart\n\n{long_para}\n\n## Other\n\nunrelated words here\n")
    write_settings(krew_home, src)
    script = ROOT / "scripts" / "krew-docs-index.py"
    r = subprocess.run([sys.executable, str(script), "build"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    st = subprocess.run([sys.executable, str(script), "status"], capture_output=True, text=True)
    assert st.returncode == 0 and "notes" in st.stdout
    d = submodule("krew-docs", "docs")
    out = d.lookup("gateway restart toolsets")
    assert "gateway.md" in out
    assert "[shortened:" in out and '"action": "read"' in out
    call = json.loads(re.search(r"krew_docs (\{[^}]*\})\]", out).group(1))
    whole = d.read(call["ref"], call["part"])
    assert "(part" in whole and "whole)" in whole and long_para.split(". ")[0] in whole
    assert "[shortened:" not in whole


def test_nothing_on_record_is_said_plainly(krew_home, tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    (src / "a.md").write_text("# A\n\nalpha beta\n")
    write_settings(krew_home, src)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "krew-docs-index.py"), "build"], check=True,
                   capture_output=True)
    d = submodule("krew-docs", "docs")
    assert "nothing on record" in d.lookup("zebra quantum")


def test_suggest_goes_to_a_review_queue_not_the_index(krew_home):
    d = submodule("krew-docs", "docs")
    out = d.suggest("A guide", "https://example.org/guide", "explains toolsets", by="steward")
    assert "review" in out.lower()
    q = d.read_queue()
    assert q and q[-1]["where"] == "https://example.org/guide"
