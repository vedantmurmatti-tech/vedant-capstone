"""Text-to-speech for Esmerelda's voice — Step 1 (see BUILD_LOG.md), hardened
against real-world latency/reliability problems found in production use (see
BUILD_LOG.md's "TTS reliability investigation" entry).

Provider: a public Gradio Space running Kokoro (an open-weight TTS
model), called through `gradio_client` rather than scraped as a webpage.
The canonical `hexgrad/Kokoro-TTS` Space has its Gradio API disabled
(`show_api: False` in its `/config`, confirmed directly — not assumed),
so there is nothing there to call. `Remsky/Kokoro-TTS-Zero` is a public
Space running the same Kokoro model with its API left enabled and
exposes the same voice set (including `af_sarah`), so it's used instead.
Swapping providers later only means changing the two constants below and
`_call_provider`'s body — nothing in routes.py or the frontend needs to
change, since both just deal in raw audio bytes.

Known, unavoidable limitation of this specific free public Space (see
BUILD_LOG.md): it runs on shared Hugging Face "ZeroGPU" hardware with a
per-IP/session quota that this project has genuinely hit multiple times
during ordinary testing, well before any unusual load. When that quota is
exhausted, every request fails with the Space's own real error message
("You have exceeded your ZeroGPU runs limit...") until the quota resets —
this is not a bug in this module, is not fixable by retrying, and no
amount of retry/backoff logic here changes the Space's own quota. This
module surfaces that condition clearly in its logs rather than masking it.
"""

import logging
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("esmerelda.tts")

# Same pattern as api/groq_agent.py's own load_dotenv() call — idempotent
# (python-dotenv never overrides an already-set env var), and this module
# reads its own env var independently rather than relying on another
# module having been imported first.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env")

_SPACE_ID = "Remsky/Kokoro-TTS-Zero"
_API_NAME = "/generate_speech_from_ui"
_DEFAULT_VOICE = "af_sarah"  # stable English female voice

# Bounds the underlying HTTP calls gradio_client makes (space discovery,
# config, the queue join/poll requests) — without this, a slow or
# unresponsive Space can hang a request indefinitely with no visible cause.
_HTTP_TIMEOUT_SECONDS = 30.0

# Bounds the *entire* wait for a generation result (queue time + actual
# inference), separate from the per-HTTP-call timeout above — a Space that
# accepts the job but never finishes it (e.g. stuck in its queue) would
# otherwise hang forever even with a bounded per-request HTTP timeout.
_RESULT_TIMEOUT_SECONDS = 60.0

# The gradio_client.Client for `_SPACE_ID` is expensive to construct — its
# __init__ alone makes several real HTTP round trips (resolving the Space
# id, fetching its /config, fetching its full API schema via
# /gradio_api/info). The original version of this module constructed a
# fresh Client on every single call to synthesize_speech(), which is what
# actually made EVERY request slow, not just the first — this is fixed by
# building it once and reusing it, guarded by a lock since FastAPI can run
# multiple requests concurrently (in threadpool threads, for this sync
# route).
_client_lock = threading.Lock()
_client = None


class TtsUnavailableError(Exception):
    """Raised whenever speech couldn't be generated, for any reason.

    Deliberately loses whatever provider-specific detail the failure
    actually had (a Gradio traceback, an HTTP error, a network timeout) —
    routes.py turns this into a generic 502 for the frontend, and nothing
    about how the TTS provider works or why it failed is exposed to
    callers. The detail is not lost, though — it's logged in full by this
    module before being collapsed here.
    """


def _get_client():
    """Returns the shared gradio_client.Client, constructing it exactly
    once (across the process's lifetime) on first use.

    Requires HF_TOKEN (see backend/.env.example) — the Space's real
    ZeroGPU quota error ("You have exceeded your ZeroGPU runs limit...",
    see BUILD_LOG.md) is a genuine per-anonymous-session limit on this
    free public Space; authenticating with a Hugging Face account token
    gives that account's own, separate quota instead. If HF_TOKEN isn't
    set, this fails immediately with a clear configuration error rather
    than silently falling back to an unauthenticated client that would
    just hit the same quota error again.
    """
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is None:  # re-check: another thread may have won the race
            hf_token = os.environ.get("HF_TOKEN")
            # Diagnostic only — never the token itself, only whether one is
            # present, its first 3 characters (enough to confirm it's the
            # expected "hf_..." shape, not enough to reconstruct it), and
            # its length (confirms it wasn't truncated/misquoted in .env).
            logger.info(
                "[TTS] HF_TOKEN check: configured=%s, prefix=%s, length=%s",
                bool(hf_token),
                hf_token[:3] if hf_token else "(none)",
                len(hf_token) if hf_token else 0,
            )
            if not hf_token:
                logger.error("[TTS] HF_TOKEN is not configured in backend/.env")
                raise TtsUnavailableError("HF_TOKEN is not configured in backend/.env")

            from gradio_client import Client

            t0 = time.monotonic()
            logger.info("[TTS] Space client initializing (space=%s, authenticated=True)", _SPACE_ID)
            _client = Client(
                _SPACE_ID,
                httpx_kwargs={"timeout": _HTTP_TIMEOUT_SECONDS},
                verbose=False,
                token=hf_token,
            )
            logger.info("[TTS] Space client ready in %.2fs", time.monotonic() - t0)
        return _client


def synthesize_speech(text: str, voice: str = _DEFAULT_VOICE) -> bytes:
    """Turns `text` into WAV audio bytes using the configured Kokoro Space.

    Raises TtsUnavailableError on any failure (Space unreachable, API
    shape changed, quota exhausted, timeout, empty text, etc.) — callers
    never need to know why, but the exact reason is always logged here
    first.
    """
    text = text.strip()
    overall_start = time.monotonic()
    logger.info("[TTS] request started: %d chars, voice=%s", len(text), voice)

    if not text:
        logger.warning("[TTS] request rejected: empty text")
        raise TtsUnavailableError("No text was provided to speak.")

    try:
        from gradio_client import Client  # noqa: F401 - import-availability check only
    except ImportError as exc:
        logger.error("[TTS] gradio_client is not installed")
        raise TtsUnavailableError("The gradio_client package is not installed.") from exc

    # Deliberately outside the try/except below: a missing HF_TOKEN is a
    # configuration error, not a provider-call failure, and must surface
    # as its own distinct message (per instruction) rather than being
    # relabeled into the generic "voice provider unavailable" below.
    client = _get_client()

    try:
        space_start = time.monotonic()
        logger.info("[TTS] space request started (api_name=%s)", _API_NAME)
        job = client.submit(
            text=text,
            voice_names=[voice],
            speed=1.0,
            api_name=_API_NAME,
        )
        result = job.result(timeout=_RESULT_TIMEOUT_SECONDS)
        space_elapsed = time.monotonic() - space_start
        logger.info("[TTS] space request completed in %.2fs", space_elapsed)
    except Exception as exc:  # noqa: BLE001 - any provider failure collapses to one clean error
        elapsed = time.monotonic() - overall_start
        # Kokoro-TTS-Zero's own ZeroGPU quota error surfaces here as a
        # plain string inside the exception — flagged explicitly (not just
        # logged as a generic failure) since it's a real, recurring,
        # provider-side limit this project has hit repeatedly in testing,
        # not a bug in this code and not something retrying here fixes.
        if "ZeroGPU" in str(exc) or "zero-gpu" in str(exc).lower():
            logger.warning(
                "[TTS] request failed after %.2fs: Kokoro-TTS-Zero's shared ZeroGPU quota is exhausted "
                "(provider-side limit, not fixable by retrying): %s",
                elapsed, exc,
            )
        else:
            logger.warning("[TTS] request failed after %.2fs: %s: %s", elapsed, type(exc).__name__, exc)
        raise TtsUnavailableError("The voice provider is unavailable right now.") from exc

    # The Space returns (audio_filepath, plot_data, performance_summary) —
    # only the audio file is a locally-saved temp path here; everything
    # else is diagnostic output the Space's own UI uses and Esmerelda has
    # no use for.
    audio_path = result[0] if isinstance(result, (tuple, list)) else result
    try:
        audio_bytes = Path(audio_path).read_bytes()
    except OSError as exc:
        elapsed = time.monotonic() - overall_start
        logger.warning("[TTS] request failed after %.2fs: audio file unreadable: %s", elapsed, exc)
        raise TtsUnavailableError("The voice provider returned no usable audio.") from exc

    total_elapsed = time.monotonic() - overall_start
    logger.info(
        "[TTS] audio received: %d bytes; total generation time %.2fs",
        len(audio_bytes), total_elapsed,
    )
    return audio_bytes
