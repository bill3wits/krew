"""YAML for crew scripts: PyYAML when present, else Hermes's own reader (hermes_yaml), else ruamel.yaml directly.

The pinned Hermes venv ships ruamel.yaml, not PyYAML; crew scripts run with that venv (krew-crew-run), so
`import krew_yaml as yaml` gives them safe_load / safe_dump either way."""
from __future__ import annotations

import io

try:
    from yaml import YAMLError, safe_dump, safe_load  # noqa: F401
except ImportError:
    try:
        from hermes_yaml import safe_dump, safe_load  # noqa: F401
        from ruamel.yaml.error import YAMLError  # noqa: F401
    except ImportError:
        from ruamel.yaml import YAML
        from ruamel.yaml.error import YAMLError  # noqa: F401

        def safe_load(stream):
            if hasattr(stream, "read"):
                stream = stream.read()
            return YAML(typ="safe", pure=True).load(stream)

        def safe_dump(data, stream=None, **_kw):
            y = YAML(typ="safe", pure=True)
            y.default_flow_style = False
            buf = io.StringIO()
            y.dump(data, buf)
            if stream is None:
                return buf.getvalue()
            stream.write(buf.getvalue())
            return None
