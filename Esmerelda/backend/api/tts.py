"""Text-to-speech for Esmerelda's voice — Step 1 (see BUILD_LOG.md).

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
"""

import logging
from pathlib import Path

logger = logging.getLogger("esmerelda.tts")

_SPACE_ID = "Remsky/Kokoro-TTS-Zero"
_API_NAME = "/generate_speech_from_ui"
_DEFAULT_VOICE = "af_sarah"  # stable English female voice


class TtsUnavailableError(Exception):
    """Raised whenever speech couldn't be generated, for any reason.

    Deliberately loses whatever provider-specific detail the failure
    actually had (a Gradio traceback, an HTTP error, a network timeout) —
    routes.py turns this into a generic 502 for the frontend, and nothing
    about how the TTS provider works or why it failed is exposed to
    callers.
    """


def synthesize_speech(text: str, voice: str = _DEFAULT_VOICE) -> bytes:
    """Turns `text` into WAV audio bytes using the configured Kokoro Space.

    Raises TtsUnavailableError on any failure (Space unreachable, API
    shape changed, empty text, etc.) — callers never need to know why.
    """
    text = text.strip()
    if not text:
        raise TtsUnavailableError("No text was provided to speak.")

    try:
        from gradio_client import Client
    except ImportError as exc:
        raise TtsUnavailableError("The gradio_client package is not installed.") from exc

    try:
        client = Client(_SPACE_ID)
        result = client.predict(
            text=text,
            voice_names=[voice],
            speed=1.0,
            api_name=_API_NAME,
        )
    except Exception as exc:  # noqa: BLE001 - any provider failure collapses to one clean error
        logger.warning("Kokoro TTS request failed: %s", exc)
        raise TtsUnavailableError("The voice provider is unavailable right now.") from exc

    # The Space returns (audio_filepath, plot_data, performance_summary) —
    # only the audio file is a locally-saved temp path here; everything
    # else is diagnostic output the Space's own UI uses and Esmerelda has
    # no use for.
    audio_path = result[0] if isinstance(result, (tuple, list)) else result
    try:
        return Path(audio_path).read_bytes()
    except OSError as exc:
        raise TtsUnavailableError("The voice provider returned no usable audio.") from exc
