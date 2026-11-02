"""The lifted code canary: krew's own code trees and no service envs by default, every machine value from
janitor.yaml `code_canary:`, the mode from KREW_CODE_CANARY else the file else report."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(tmp_path, yaml_text: str | None, **env) -> dict:
    cfg = tmp_path / "janitor.yaml"
    if yaml_text is not None:
        cfg.write_text(yaml_text)
    e = {k: v for k, v in os.environ.items() if not k.startswith("KREW_CODE_CANARY")}
    e.update(KREW_JANITOR_CONFIG=str(cfg), **env)
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import code_canary as c; print(json.dumps({"
            "'mode': c.mode(), 'envs': list(c.SERVICE_ENVS), 'root': c.ENVS_ROOT, 'ms': c.MS_UNIT, "
            "'code': {k: str(v) for k, v in c.CODE_TREES.items()}, 'hermes': str(c.HERMES_DIR), 'recipe': c.RECIPE}))")
    out = subprocess.run([sys.executable, "-c", code, str(ROOT / "janitor")], env=e, capture_output=True, text=True,
                         check=True)
    return json.loads(out.stdout)


def test_the_defaults_watch_krews_own_code_and_report(tmp_path):
    r = _read(tmp_path, None)
    assert r["mode"] == "report"
    assert r["envs"] == [] and r["root"] is None
    assert r["code"] == {"krew/janitor": str(ROOT / "janitor"), "krew/rails": str(ROOT / "rails")}
    assert r["ms"] == "krew-ms-nightly.service"


def test_the_machine_values_come_from_janitor_yaml(tmp_path):
    r = _read(tmp_path, "code_canary:\n  mode: act\n  hermes_dir: /h\n  envs_root: /e\n  service_envs: [vllm]\n"
                        "  code_trees: {mine/lib: /m/lib}\n  ms_unit: nightly.service\n  recipe: see the notes\n")
    assert r == {"mode": "act", "envs": ["vllm"], "root": "/e", "ms": "nightly.service", "code": {"mine/lib": "/m/lib"},
                 "hermes": "/h", "recipe": "see the notes"}


def test_the_env_overrides_the_file_and_junk_reads_report(tmp_path):
    assert _read(tmp_path, "code_canary:\n  mode: act\n", KREW_CODE_CANARY="off")["mode"] == "off"
    assert _read(tmp_path, "code_canary:\n  mode: loud\n")["mode"] == "report"


def test_the_shipped_example_parses_to_the_defaults(tmp_path):
    r = _read(tmp_path, (ROOT / "config" / "janitor.example.yaml").read_text())
    assert r["mode"] == "report" and r["envs"] == [] and set(r["code"]) == {"krew/janitor", "krew/rails"}


def test_the_cli_says_off(tmp_path):
    e = {**os.environ, "KREW_CODE_CANARY": "off", "KREW_JANITOR_CONFIG": str(tmp_path / "none.yaml")}
    out = subprocess.run([str(ROOT / "janitor" / "krew-code-canary.py"), "check"], env=e, capture_output=True,
                         text=True, timeout=30)
    assert out.returncode == 0 and "off" in out.stdout
