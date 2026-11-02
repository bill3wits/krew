#!/usr/bin/env python3
"""Build or query the krew docs index (the one `krew_docs` searches), from a krew checkout.

    python3 scripts/krew-docs-index.py build            index every source in `docs.sources` (krew.yaml)
    python3 scripts/krew-docs-index.py status
    python3 scripts/krew-docs-index.py query "how do I add a toolset"

Same CLI as plugins/krew-docs/docs_index.py. Keyword search (SQLite FTS5) always works; for meaning search
as well, point `docs.embedder_venv` at a venv with `fastembed` (see README, "Docs index").
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins" / "krew-docs"))
sys.argv[0] = "docs_index.py"
runpy.run_path(str(Path(__file__).resolve().parent.parent / "plugins" / "krew-docs" / "docs_index.py"), run_name="__main__")
