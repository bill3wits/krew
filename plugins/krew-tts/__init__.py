"""The Crissa voice tiers as a native streaming TTS provider (`crissa`).

Hermes ships the hard parts already: SentenceChunker cuts LLM token deltas into
clauses (and strips <think> even when split across deltas), and the gateway
consumer feeds them to a StreamingTTSProvider. All that was missing was a
provider that speaks the local voice. This is that ~30 lines.

Time-to-first-word goes from (full generation + full synthesis) to ~one clause.

Lifted from the reference install's streaming-voice plugin (2026-10-09). The voice is whichever Crissa tier answers
first, in the pack's own order: turbo (Chatterbox-Turbo, a GPU) 8186 -> audio8 8187 -> pocket (Pocket TTS, the CPU)
8188 -> kokoro 8189. turbo and pocket speak the /stream contract (chunked int16 PCM, X-Sample-Rate); audio8 and kokoro
answer /v1/audio/speech with the whole line as PCM. Addresses: KREW_TTS_URL (one address, no fallback),
CRISSA_PORT_<TIER> (the pack's port table variables), or `tts.tiers` in $KREW_HOME/krew.yaml. Pin with
tts.streaming.provider = crissa; the voice is tts.crissa.voice (default crissa).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Iterator

from . import krew_settings as S

logger = logging.getLogger(__name__)

PROVIDER = "crissa"
# the pack's port table (lib/crissa_ports.py DEFAULTS), in the voice tiers' order
TIERS = (("turbo", 8186, "stream"), ("audio8", 8187, "speech"), ("pocket", 8188, "stream"), ("kokoro", 8189, "speech"))
VOICE = os.environ.get("KREW_TTS_VOICE", "crissa")
TIMEOUT = float(os.environ.get("KREW_TTS_TIMEOUT", "120"))
# One call is one clause off the chunker. A runaway paragraph (a
# reply that never hit a sentence end, a pasted table) must not become a
# 30-second synthesis: clip at a sentence end. 0 = no clip.
SPEECH_MAX_CHARS = int(os.environ.get("KREW_TTS_SPEECH_MAX_CHARS", "400"))


def tier_urls(env: dict | None = None, cfg: dict | None = None) -> list:
    """[(tier, base url, contract)] in order. KREW_TTS_URL alone = one address, contract `stream`."""
    env = os.environ if env is None else env
    one = str(env.get("KREW_TTS_URL") or "").strip().rstrip("/")
    if one:
        return [("url", one, str(env.get("KREW_TTS_CONTRACT") or "stream"))]
    cfg = S.load("tts") if cfg is None else (cfg or {})
    tiers = cfg.get("tiers") if isinstance(cfg.get("tiers"), list) else None
    out = []
    for name, port, contract in TIERS:
        if tiers is not None and name not in tiers:
            continue
        pinned = str(env.get("CRISSA_PORT_" + name.upper()) or "").strip()
        try:
            p = int(pinned) if pinned else port + int(str(env.get("CRISSA_PORT_OFFSET") or "0").strip() or 0)
        except ValueError:
            p = port
        urls = cfg.get("urls") if isinstance(cfg.get("urls"), dict) else {}
        base = str(urls.get(name) or "").strip().rstrip("/") or f"http://127.0.0.1:{p}"
        out.append((name, base, contract))
    return out


def clip_at_sentence(text: str, max_chars: int) -> str:
    """The line up to its last sentence end before max_chars (0 = whole). The reference install stripped
    unspeakable symbols here through a module of its own; the Wall's player does that for every line it speaks,
    and a line the framework speaks itself is clipped only."""
    t = (text or "").strip()
    if not max_chars or len(t) <= max_chars:
        return t
    head = t[:max_chars]
    for mark in (". ", "! ", "? ", "; "):
        i = head.rfind(mark)
        if i > max_chars // 3:
            return head[: i + 1].strip()
    return head.rstrip() + "."


def _alive(base: str) -> bool:
    import urllib.error
    import urllib.request
    for probe in (f"{base}/health", base + "/"):
        try:
            with urllib.request.urlopen(probe, timeout=2):
                return True
        except urllib.error.HTTPError:
            return True  # answered = process is alive
        except OSError:
            continue
    return False


def first_live(tiers: list) -> tuple:
    for t in tiers:
        if _alive(t[1]):
            return t
    return ()


def _register_streaming() -> bool:
    try:
        from tools.tts_streaming import StreamingTTSProvider, register
    except Exception as exc:  # pragma: no cover - version drift
        logger.warning("krew-tts: streaming seam unavailable (%s)", exc)
        return False

    @register(PROVIDER)
    class CrissaStreamer(StreamingTTSProvider):
        """Raw int16 LE mono PCM straight off the tier's chunked /stream (or the whole line from /v1/audio/speech).

        Sample rate is FIXED at class level, not negotiated: the consumer opens its playback device from
        ``sample_rate`` before it calls ``stream()``. Every Crissa tier emits 24 kHz (X-Sample-Rate says so);
        override with KREW_TTS_SAMPLE_RATE if that ever changes.
        """

        sample_rate = int(os.environ.get("KREW_TTS_SAMPLE_RATE", "24000"))
        channels = 1
        sample_width = 2

        @staticmethod
        def available() -> bool:
            return bool(first_live(tier_urls()))

        def stream(self, text: str) -> Iterator[bytes]:
            import http.client
            from urllib.parse import urlparse

            text = clip_at_sentence(text or "", SPEECH_MAX_CHARS)
            tier = first_live(tier_urls())
            if not tier:
                raise RuntimeError("no Crissa voice tier answers (turbo, audio8, pocket, kokoro)")
            name, base, contract = tier
            voice = (self.section or {}).get("voice") or VOICE
            u = urlparse(base)
            if contract == "speech":
                path, payload = "/v1/audio/speech", json.dumps({"input": text, "voice": voice, "response_format": "pcm"})
            else:
                path, payload = "/stream", json.dumps({"text": text, "voice": voice})
            conn = http.client.HTTPConnection(u.hostname or "127.0.0.1", u.port or 80, timeout=TIMEOUT)
            try:
                conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                if resp.status != 200:
                    raise RuntimeError(f"{name} {path} {resp.status}")
                # X-Sample-Rate is deliberately NOT honoured here: the consumer has
                # already opened its sink from cls.sample_rate. A tier at another rate is said once.
                sr = resp.getheader("X-Sample-Rate")
                if sr and sr.isdigit() and int(sr) != self.sample_rate:
                    logger.warning(
                        "krew-tts: %s says %s Hz but the provider is pinned to %s Hz; "
                        "set KREW_TTS_SAMPLE_RATE=%s to fix playback pitch",
                        name, sr, self.sample_rate, sr,
                    )
                while True:
                    try:
                        chunk = resp.read(4096)
                    except http.client.IncompleteRead as e:
                        if e.partial:
                            yield e.partial
                        break
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    # PATCH 1: make `streaming.provider: auto` find the local voice, and find it FIRST.
    #
    # Upstream ships _PROVIDER_PRIORITY = ["elevenlabs","gemini","openai","xai"]
    # and `auto` walks ONLY that list. A local provider is not in it, so on a machine with no
    # cloud keys `auto` walks all four, finds nothing, returns None, and the
    # dispatcher silently drops to the sync per-sentence path. That looks
    # exactly like "streaming is broken" with no error anywhere.
    #
    # Front, not back: this machine must never reach a cloud TTS. A previous
    # fallthrough sent audio to a cloud TTS before anyone noticed.
    # Local-first here means `auto` can no longer leak speech off-box.
    #
    # Mutated IN PLACE so references already bound by other importers see it.
    try:
        from tools import tts_streaming as _ts

        prio = getattr(_ts, "_PROVIDER_PRIORITY", None)
        if isinstance(prio, list) and PROVIDER not in prio:
            prio.insert(0, PROVIDER)
            logger.info("krew-tts: %s prepended to streaming priority %s", PROVIDER, prio)
    except Exception as exc:  # pragma: no cover - version drift
        logger.warning("krew-tts: could not adjust provider priority (%s)", exc)

    # PATCH 2: export the class at module level.
    #
    # CrissaStreamer is defined inside this function and only reachable through
    # the @register decorator, so `from krew_tts import CrissaStreamer`
    # raises AttributeError, which reads as "the plugin is broken" when it is
    # perfectly fine. Publish it so tests and probes can import it directly.
    globals()["CrissaStreamer"] = CrissaStreamer

    return True


def _install_resolution_probe() -> None:
    """PATCH 3: make a streaming fallback LOUD instead of silent.

    ``resolve_streaming_provider`` returning None is a legitimate code path
    (sync per-sentence still speaks), so nothing logs it. The cost is that a
    misconfigured pin, a down voice, or the `auto` trap above are all invisible:
    speech still comes out, just without streaming, and you cannot tell which
    path a turn took. Wrap it to say so once per outcome change.
    """
    try:
        from tools import tts_streaming as _ts
    except Exception:
        return
    if getattr(_ts.resolve_streaming_provider, "_krew_probe", False):
        return  # already wrapped (plugin re-registered)

    _orig = _ts.resolve_streaming_provider
    _last = {}

    def _wrapped(tts_config, preferred=None):
        inst = _orig(tts_config, preferred)
        pinned = str(((tts_config or {}).get("streaming") or {}).get("provider") or "")
        got = type(inst).__name__ if inst is not None else None
        if _last.get("got") != got or _last.get("pinned") != pinned:
            _last["got"], _last["pinned"] = got, pinned
            if inst is None:
                logger.warning(
                    "krew-tts: NO streaming provider for pin=%r; falling back to "
                    "sync per-sentence. If pin is 'auto', that is the upstream priority "
                    "list; pin '%s' explicitly. If pin is '%s', check that a voice tier is up.",
                    pinned or "(unset)", PROVIDER, PROVIDER,
                )
            else:
                logger.info("krew-tts: streaming via %s (pin=%r)", got, pinned or "(unset)")
        return inst

    _wrapped._krew_probe = True
    _ts.resolve_streaming_provider = _wrapped


def register(ctx):
    if _register_streaming():
        _install_resolution_probe()
        logger.info("krew-tts: '%s' streaming provider registered (pin via tts.streaming.provider: %s)",
                    PROVIDER, PROVIDER)
