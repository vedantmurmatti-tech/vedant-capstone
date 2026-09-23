"""Speech-to-text for Esmerelda's Orb voice input — Step 2 (see BUILD_LOG.md).

Provider: Groq's hosted Whisper transcription endpoint
(`audio.transcriptions.create`, model="whisper-large-v3-turbo"), reused
via the *exact same* `GROQ_API_KEY` and singleton `AsyncGroq` client
api/groq_agent.py already constructs for the chat agent (its
`_get_client()`) — not a second client, a second API key, or a new
provider. Groq is already a required, configured dependency of this
project (see .env.example); its transcription endpoint needs no new
account, no new credential, and is fast enough for a short voice-input
clip. This is deliberately the smallest appropriate implementation, per
instruction to prefer an existing/free service already in the project
before adding anything new.
"""

import logging
import time

import groq

from .groq_agent import GroqUnavailableError
from .groq_agent import _get_client as _get_groq_client

logger = logging.getLogger("esmerelda.stt")

_MODEL = "whisper-large-v3-turbo"

# A real recording — even a one-word "hello" — is comfortably larger than
# this in any browser-produced audio/webm container (headers alone are a
# few hundred bytes). Anything smaller is treated as an empty/invalid
# recording and rejected before ever calling Groq.
_MIN_AUDIO_BYTES = 256


class SttUnavailableError(Exception):
    """Raised whenever a recording couldn't be transcribed, for any
    reason (empty recording, Groq not configured, network failure,
    unsupported audio, any other provider error). Deliberately loses
    provider-specific detail — routes.py turns this into a generic 502
    for the frontend — but the exact reason is always logged here first.
    """


async def transcribe_speech(audio_bytes: bytes, filename: str, content_type: str | None) -> str:
    """Transcribes `audio_bytes` (a whole recorded clip, e.g. audio/webm)
    to text via Groq's Whisper endpoint. Raises SttUnavailableError on
    any failure — callers never need to know why, but the exact reason
    is always logged here first.
    """
    overall_start = time.monotonic()
    logger.info(
        "[STT] request started: %d bytes, filename=%s, content_type=%s",
        len(audio_bytes), filename, content_type,
    )

    if len(audio_bytes) < _MIN_AUDIO_BYTES:
        logger.warning("[STT] request rejected: recording too small/empty (%d bytes)", len(audio_bytes))
        raise SttUnavailableError("The recording was empty or too short to transcribe.")

    try:
        client = _get_groq_client()
    except GroqUnavailableError as exc:
        logger.error("[STT] Groq is not configured: %s", exc)
        raise SttUnavailableError(str(exc)) from exc

    try:
        logger.info("[STT] Groq request started (model=%s)", _MODEL)
        space_start = time.monotonic()
        transcription = await client.audio.transcriptions.create(
            model=_MODEL,
            file=(filename or "recording.webm", audio_bytes, content_type or "application/octet-stream"),
            response_format="json",
        )
        space_elapsed = time.monotonic() - space_start
        logger.info("[STT] Groq request completed in %.2fs", space_elapsed)
    except (groq.BadRequestError, groq.UnprocessableEntityError) as exc:
        elapsed = time.monotonic() - overall_start
        logger.warning("[STT] request failed after %.2fs: unsupported/invalid audio: %s", elapsed, exc)
        raise SttUnavailableError("That recording's audio format wasn't understood.") from exc
    except (groq.APIConnectionError, groq.APITimeoutError) as exc:
        elapsed = time.monotonic() - overall_start
        logger.warning("[STT] request failed after %.2fs: network failure reaching Groq: %s", elapsed, exc)
        raise SttUnavailableError("Couldn't reach the transcription service.") from exc
    except Exception as exc:  # noqa: BLE001 - any other provider failure collapses to one clean error
        elapsed = time.monotonic() - overall_start
        logger.warning("[STT] request failed after %.2fs: %s: %s", elapsed, type(exc).__name__, exc)
        raise SttUnavailableError("Transcription failed.") from exc

    text = (transcription.text or "").strip()
    total_elapsed = time.monotonic() - overall_start
    logger.info("[STT] transcription received: %d chars; total time %.2fs", len(text), total_elapsed)
    return text
