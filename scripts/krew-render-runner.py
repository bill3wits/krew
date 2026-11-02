#!/usr/bin/env python3
"""Work the render-order queue against your ComfyUI server (plan / run). Same CLI as
plugins/krew-render/render_runner.py; settings are `render:` in $KREW_HOME/krew.yaml."""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent / "plugins" / "krew-render"
sys.path.insert(0, str(HERE))
sys.argv[0] = "render_runner.py"
runpy.run_path(str(HERE / "render_runner.py"), run_name="__main__")
