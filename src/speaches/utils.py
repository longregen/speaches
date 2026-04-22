import base64
from datetime import UTC, datetime
import io
import logging
import os
from typing import Any
import uuid

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


class CudaOutOfMemoryError(Exception):
    def __init__(self, audio_duration: float | None = None) -> None:
        self.audio_duration = audio_duration
        super().__init__(
            "GPU ran out of memory while processing audio"
            + (f" ({audio_duration:.1f}s)" if audio_duration is not None else "")
        )


class APIProxyError(Exception):
    """Exception for structured, actionable API or proxy errors.

    Args:
        message: Human-readable error message.
        hint: Short actionable hint for the user.
        suggestions: List of actionable suggestions for the user.
        status_code: HTTP status code (default 500).
        debug: Optional debug info (stack trace, request ID, etc.).
        error_id: Unique error ID for traceability.
        timestamp: When the error occurred (ISO 8601, UTC).

    """

    def __init__(
        self,
        message: str,
        hint: str | None = None,
        suggestions: list[str] | None = None,
        status_code: int = 500,
        debug: Any = None,
        error_id: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        self.message = message
        self.hint = hint
        self.suggestions = suggestions or []
        self.status_code = status_code
        self.debug = debug
        self.error_id = error_id or uuid.uuid4().hex
        self.timestamp = timestamp or datetime.now(UTC).isoformat()


def format_api_proxy_error(exc: "APIProxyError", context: str = "") -> str:
    debug_mode = os.environ.get("SPEACHES_LOG_LEVEL", "").lower() == "debug"
    user_message = f"An error occurred: {exc.message} (Error ID: {exc.error_id}). Please try again or contact support."
    suggestions = exc.suggestions or [
        "Double-check your input data and file format (e.g., ensure audio files are WAV/MP3 and not corrupted).",
        "Verify your API key and endpoint configuration in the settings.",
        "Check your internet/network connection.",
        "If the error persists, restart the application or server.",
        "Contact support with the error ID and debug info if available.",
    ]
    debug_info = (
        f"Debug: {exc.debug}\nContext: {context}\nTimestamp: {exc.timestamp}" if debug_mode and exc.debug else ""
    )
    return f"[ERROR] {user_message}\nSuggestions: {', '.join(suggestions)}" + (f"\n{debug_info}" if debug_info else "")


# TODO: maybe add length validation. gte 2s lte 10s
def parse_data_url_to_audio(data_url: str) -> np.typing.NDArray[np.float32]:
    if not data_url.startswith("data:"):
        msg = f"Invalid data URL format: {data_url[:50]}..."
        raise ValueError(msg)

    try:
        header, encoded = data_url.split(",", 1)
        audio_bytes = base64.b64decode(encoded)

        mime_type = header.split(":")[1].split(";")[0]

        if mime_type in ("audio/pcm", "audio/raw"):
            audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
            audio_data = audio_int16.astype(np.float32) / 32768.0
        else:
            audio_data, _sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")

            if len(audio_data.shape) > 1:
                audio_data = audio_data.mean(axis=1)

        return audio_data  # pyrefly: ignore[bad-return]
    except Exception as e:
        logger.exception("Failed to parse data URL to audio")
        msg = f"Failed to parse audio data URL: {e}"
        raise ValueError(msg) from e
