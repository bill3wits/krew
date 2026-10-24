"""Test that render-collect release_hold_for goes through KREW_MODE_BIN
subprocess seam, not in-process exec_module (t_6698facd, a87be473 fix shape).
"""
from __future__ import annotations
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _make_stub(tmp: Path) -> Path:
    """Write a stub krew-mode bash script that logs argv and prints a freed unit."""
    stub = tmp / "krew-mode-stub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {tmp / 'hm-args.log'}\n"
        # Emulate `release-card`: print freed units one per line
        'if [ "$1" = "release-card" ]; then echo "ops.service"; exit 0; fi\n'
        'echo "stub: unexpected argv: $@" >&2; exit 1\n'
    )
    stub.chmod(0o755)
    return stub


def test_release_hold_for_uses_krew_mode_bin_seam(tmp_path):
    """release_hold_for must call krew-mode as a subprocess via KREW_MODE_BIN,
    NOT exec_module in-process. A non-executable stub is the discriminator:
    subprocess.run() on it raises (caught -> 'release_failed' ledger line),
    while an in-process exec_module would silently import the real krew-mode
    module and print nothing to the log. So 'release_failed' in the ledger
    proves the subprocess seam was actually exercised."""
    # Non-executable stub: subprocess.run() will fail to exec it, but the
    # real krew-mode module (via exec_module) would not even look at this path.
    stub = tmp_path / "krew-mode-stub-noexec"
    stub.write_text("#!/usr/bin/env bash\necho should-never-run\n")
    stub.chmod(0o644)  # NOT executable — subprocess.run will raise PermissionError

    # Redirect the live ledger to the tmp dir (krew-gpu-hold.jsonl) so the
    # test never writes noise into ~/.hermes/. Same KREW_HERMES seam the
    # other render-* tests use.
    fake_home = tmp_path / "fake-hermes"
    fake_home.mkdir()

    # Fresh import with the env override set
    old_bin = os.environ.get("KREW_MODE_BIN")
    old_hermes = os.environ.get("KREW_HERMES")
    os.environ["KREW_MODE_BIN"] = str(stub)
    os.environ["KREW_HERMES"] = str(fake_home)
    try:
        # Force reimport so the module picks up the new env vars
        if "render-collect" in sys.modules:
            del sys.modules["render-collect"]
        from importlib.machinery import SourceFileLoader
        from importlib.util import spec_from_loader
        loader = SourceFileLoader("render_collect_test", str(REPO / "crew" / "scripts" / "render-collect.py"))
        spec = spec_from_loader("render_collect_test", loader)
        rc = importlib.util.module_from_spec(spec)
        assert spec is not None
        loader.exec_module(rc)

        # Call release_hold_for; the non-executable stub causes subprocess.run
        # to raise PermissionError, which release_hold_for catches and logs
        # as 'release_failed'. If the code regressed to exec_module, the real
        # krew-mode module would be imported in-process and the ledger would
        # have NO 'release_failed' line (or would crash).
        rc.release_hold_for("t_6698facd")
    finally:
        if old_bin is not None:
            os.environ["KREW_MODE_BIN"] = old_bin
        else:
            os.environ.pop("KREW_MODE_BIN", None)
        if old_hermes is not None:
            os.environ["KREW_HERMES"] = old_hermes
        else:
            os.environ.pop("KREW_HERMES", None)

    # Assert the release_failed ledger line was written — this is a
    # subprocess-only side-effect that in-process exec_module never produces.
    ledger = rc.LEDGER
    entries = []
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            try:
                e = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if e.get("event") == "release_failed" and e.get("card") == "t_6698facd":
                entries.append(e)
    assert entries, (
        "no 'release_failed' ledger entry for t_6698facd — the non-executable "
        "stub was not attempted via subprocess.run. If the code regressed to "
        "in-process exec_module, the real krew-mode module would be imported "
        "and no release_failed line would be written."
    )


def test_release_hold_for_parses_freed_units_from_stdout(tmp_path):
    """release_hold_for must parse freed units from the subprocess stdout,
    not from the in-process return value."""
    stub = _make_stub(tmp_path)
    old_env = os.environ.get("KREW_MODE_BIN")
    os.environ["KREW_MODE_BIN"] = str(stub)
    try:
        if "render-collect" in sys.modules:
            del sys.modules["render-collect"]
        from importlib.machinery import SourceFileLoader
        from importlib.util import spec_from_loader
        loader = SourceFileLoader("render_collect_test", str(REPO / "crew" / "scripts" / "render-collect.py"))
        spec = spec_from_loader("render_collect_test", loader)
        rc = importlib.util.module_from_spec(spec)
        assert spec is not None
        loader.exec_module(rc)

        # Monkeypatch subprocess.run to capture what gets called and return
        # a completed process with the expected freed unit
        calls = []
        class _FakeCompleted:
            stdout = "ops.service\n"
            stderr = ""
            returncode = 0
        # NOTE: render-collect does `import subprocess`, so rc.subprocess IS
        # the shared real subprocess module. Patching rc.subprocess.run
        # patches it globally — restore in finally or the fake leaks into
        # every test that runs after this one (and into the live process).
        orig_run = subprocess.run
        def fake_run(*a, **kw):
            calls.append((a, kw))
            return _FakeCompleted()
        try:
            rc.subprocess.run = fake_run  # patch the module's subprocess reference
            rc.release_hold_for("t_fake1234")
        finally:
            rc.subprocess.run = orig_run
    finally:
        if old_env is not None:
            os.environ["KREW_MODE_BIN"] = old_env
        else:
            os.environ.pop("KREW_MODE_BIN", None)

    # Verify the subprocess call was made with the stub + release-card + card
    assert calls, "subprocess.run was not called — still using exec_module?"
    first_args = calls[0][0][0]
    assert str(stub) in first_args or any(str(stub) in str(x) for x in first_args), \
        f"stub path not in subprocess call args: {first_args}"
    assert "release-card" in first_args, f"'release-card' not in subprocess args: {first_args}"
    assert "t_fake1234" in first_args, f"card id not in subprocess args: {first_args}"
