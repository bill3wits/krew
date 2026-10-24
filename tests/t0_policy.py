"""Test support for the lifted T0 tests: the shipped policy (config/tier0.example.yaml) with every var filled in
the crew's shape, written once per session, and a YAML reader that works in the Hermes venv (no PyYAML there).

The account the tests pretend to be is HOME (/home/crew, via KREW_REAL_HOME), so `~` in the policy and in the
recorded commands means the same folder; nothing under it is touched.
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "rails" / "tier0_gate.py"
EXAMPLE = ROOT / "config" / "tier0.example.yaml"
HOME = "/home/crew"
VARS = """vars:
  LIVE_TREE: ~/projects/crew-rails
  SOLO_TEST: test_solo_mode.py
  HERMES_TREE: ~/.hermes/hermes-agent
  HERMES_CLONE: ~/projects/hermes-agent
  KREW_HOME_DIR: ~/.krew
  PROJECTS: ~/projects
  DELIVERABLES: ~/Desktop/crew-deliverables
  MODEL_PORTS: [8000, 8005, 8001, 8003]
  BRAIN_PORTS: [8000, 8005, 8196]
  BRAIN_NAMES: [Atlas, atlas]
  GPU_TOOL: gpu-image
  DATA_STORES: [/stores/crew-security, /stores/crew-envs, /stores/crew-memory, /stores/hindsight, /stores/hf]
  PACKS_DIR: hermes-packs
  RESTORE_SCRIPTS: [scripts/sync-packs.sh, scripts/crew-config-restore.py]
  ID_STORE: crew-id
  PROMOTION_MARKERS: [promote-flags, adapters/latest]
  HAND_ONLY_TOOLS: [pose-mouth-harvest.py, pose-mouth-harvest-close.py]
  DASHBOARD_HOSTS: [krew.example.lan]
"""
REPOS = "  repos: [~/projects/crew-rails, ~/projects/crew-id, ~/.hermes/hermes-agent]\n"


class _Yaml:
    """safe_load / safe_dump: PyYAML when present, else ruamel.yaml (the Hermes venv carries it)."""

    def safe_load(self, text):
        try:
            import yaml
            return yaml.safe_load(text)
        except ImportError:
            from ruamel.yaml import YAML
            return YAML(typ="safe").load(text)

    def safe_dump(self, data, stream=None, **kw):
        try:
            import yaml
            return yaml.safe_dump(data, stream, **kw)
        except ImportError:
            from ruamel.yaml import YAML
            y = YAML(typ="safe")
            y.default_flow_style = False
            buf = io.StringIO()
            y.dump(data, buf)
            if stream is None:
                return buf.getvalue()
            stream.write(buf.getvalue())

    dump = safe_dump


yaml = _Yaml()


def filled(text: str) -> str:
    start = text.index("\nvars:\n") + 1
    end = text.index("\ntrusted_repos:")
    out = text[:start] + VARS + text[end:]
    swap = {"  repos: ": REPOS, "  own_tabs: ": "  own_tabs: ['http://127.0.0.1:8099/']\n",
            "  pack_paths: ": "  pack_paths: {skill: 'hermes-packs/skills/{skill}', profile: 'hermes-packs/distributions/{profile}'}\n"}
    lines = [next((v for k, v in swap.items() if ln.startswith(k)), ln) for ln in out.splitlines(keepends=True)]
    lines = [ln for ln in lines if not ln.startswith("                   # profile file's source")]
    return "".join(lines)


_DIR = Path(tempfile.mkdtemp(prefix="krew-t0-policy-"))
POLICY = _DIR / "tier0.yaml"
POLICY.write_text(filled(EXAMPLE.read_text(encoding="utf-8")), encoding="utf-8")
os.environ.setdefault("KREW_T0_TEST_POLICY", str(POLICY))


def _gate_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("krew_tier0_gate_for_tests", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._REAL_HOME = Path(HOME)
    return mod


def filled_doc() -> dict:
    """The test policy as the gate sees it: every {VAR} in a pattern, path list and reason filled."""
    g = _gate_module()
    doc = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    vars_ = {k: v for k, v in (doc.get("vars") or {}).items()}
    for r in doc.get("blocklist") or []:
        pat, _miss = g._fill_pattern(str(r["pattern"]), vars_)
        r["pattern"] = pat if pat is not None else r["pattern"]
        r["reason"] = g._fill_text(str(r.get("reason") or ""), vars_)
        for k in ("write_roots", "write_block", "write_except", "write_block_always"):
            if r.get(k):
                r[k], _ = g._fill_paths(r[k], vars_)
    return doc


def filled_rules() -> list:
    return filled_doc()["blocklist"]
