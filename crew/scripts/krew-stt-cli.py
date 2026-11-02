#!/usr/bin/env python3
"""krew-stt-cli: a Hermes `stt.providers.<name>.command` that transcribes one audio file through the Crissa ASR.

    python3 krew-stt-cli.py <audio file>        -> the transcript on stdout (empty when nothing was heard)

The reference install's front-door config named a transcription script that no longer existed (its STT engine was
retired), so a voice note sent to her profile had no ear at all. This is the replacement, written for krew (nothing
to extract): the file is decoded to 16 kHz mono 16-bit PCM (the `wave` module for a WAV, ffmpeg for anything else)
and POSTed raw to the Crissa voice module's ASR (`POST /stt`, X-Sample-Rate), whose JSON `text` is printed.

    CRISSA_ASR          the ASR's address (default http://127.0.0.1:8096; the port table's `asr`)
    KREW_STT_TIMEOUT_S  how long one transcription may take (default 60)

Exit 0 with the text; exit 2 when the file cannot be decoded; exit 3 when the ASR did not answer (stderr says which).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
import wave
from pathlib import Path

RATE = 16000


def pcm_of(path: Path) -> bytes:
    """16 kHz mono int16 PCM of the file: WAV through `wave` when it already has that shape, else ffmpeg."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == RATE:
                return w.readframes(w.getnframes())
    except (wave.Error, EOFError, OSError):
        pass
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("not a 16 kHz mono 16-bit WAV and ffmpeg is not on PATH")
    r = subprocess.run([ff, "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                       capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg could not decode it: " + (r.stderr.decode("utf-8", "replace").strip()[-200:] or "no output"))
    return r.stdout


def transcribe(pcm: bytes, base: str | None = None, timeout: float | None = None) -> str:
    base = (base or os.environ.get("CRISSA_ASR") or "http://127.0.0.1:8096").rstrip("/")
    timeout = float(timeout if timeout is not None else os.environ.get("KREW_STT_TIMEOUT_S") or 60)
    req = urllib.request.Request(base + "/stt", data=pcm, method="POST",
                                 headers={"Content-Type": "application/octet-stream", "X-Sample-Rate": str(RATE)})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8", "replace") or "{}")
    return str(body.get("text") or "").strip()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = Path(argv[0])
    try:
        pcm = pcm_of(path)
    except Exception as e:  # noqa: BLE001
        print(f"krew-stt-cli: cannot read {path}: {e}", file=sys.stderr)
        return 2
    try:
        text = transcribe(pcm)
    except Exception as e:  # noqa: BLE001
        print(f"krew-stt-cli: the ASR did not answer ({type(e).__name__}: {e}); is the Crissa voice module's ASR up?",
              file=sys.stderr)
        return 3
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
