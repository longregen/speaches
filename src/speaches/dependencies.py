from functools import lru_cache
import io
import logging
from pathlib import Path
import subprocess
import time
from typing import Annotated, cast

import av.error
from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from faster_whisper.audio import decode_audio
from httpx import ASGITransport, AsyncClient
import numpy as np
from numpy import float32
from openai import AsyncOpenAI
from openai.resources.audio import AsyncSpeech, AsyncTranscriptions
from openai.resources.chat.completions import AsyncCompletions

from speaches.audio import Audio
from speaches.config import Config
from speaches.executors.shared.registry import ExecutorRegistry

logger = logging.getLogger(__name__)


# NOTE: `get_config` is called directly instead of using sub-dependencies so that these functions could be used outside of `FastAPI`
# WARN: Any new module that ends up calling this function directly (not through `FastAPI` dependency injection) should be patched in `tests/conftest.py`
@lru_cache
def get_config() -> Config:
    return Config()


async def get_config_async() -> Config:
    return get_config()


ConfigDependency = Annotated[Config, Depends(get_config_async)]


@lru_cache
def get_executor_registry() -> ExecutorRegistry:
    config = get_config()
    return ExecutorRegistry(config)


async def get_executor_registry_async() -> ExecutorRegistry:
    return get_executor_registry()


ExecutorRegistryDependency = Annotated[ExecutorRegistry, Depends(get_executor_registry_async)]


security = HTTPBearer(auto_error=False)


async def verify_api_key(
    config: ConfigDependency, credentials: HTTPAuthorizationCredentials | None = Depends(security)
) -> None:
    assert config.api_key is not None
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key required. Please provide an API key using the Authorization header with Bearer scheme.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if credentials.credentials != config.api_key.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key. The provided API key is incorrect.",
            headers={"WWW-Authenticate": "Bearer"},
        )


ApiKeyDependency = Depends(verify_api_key)


def _decode_audio_ffmpeg_fallback(audio_bytes: bytes, sampling_rate: int = 16000) -> "np.typing.NDArray[float32]":
    result = subprocess.run(
        [  # noqa: S607
            "ffmpeg",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(sampling_rate),
            "pipe:1",
            "-hide_banner",
            "-loglevel",
            "error",
        ],
        input=audio_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"ffmpeg fallback failed (rc={result.returncode}): {stderr}")
    audio_int16 = np.frombuffer(result.stdout, dtype=np.int16)
    return cast("np.typing.NDArray[float32]", audio_int16.astype(np.float32) / 32768.0)


def audio_file_dependency(
    file: Annotated[UploadFile, Form()],
) -> Audio:
    try:
        raw_bytes = file.file.read()
        if len(raw_bytes) == 0:
            raise HTTPException(
                status_code=400,
                detail="Failed to decode audio. The provided file is empty.",
            )
        magic = raw_bytes[:16].hex() if len(raw_bytes) >= 16 else raw_bytes.hex()
        logger.debug(
            f"Decoding audio file: {file.filename}, content_type: {file.content_type}, "
            f"size: {len(raw_bytes)} bytes, magic: {magic}"
        )
        start = time.perf_counter()

        if file.content_type in ("audio/pcm", "audio/raw"):
            logger.debug(f"Detected {file.content_type}, parsing as s16le monochannel")
            audio_int16 = np.frombuffer(raw_bytes, dtype=np.int16)
            audio_data = cast("np.typing.NDArray[float32]", audio_int16.astype(np.float32) / 32768.0)
        else:
            try:
                audio_data = cast(
                    "np.typing.NDArray[float32]",
                    decode_audio(io.BytesIO(raw_bytes), sampling_rate=16000),
                )
            except (av.error.InvalidDataError, av.error.ValueError) as pyav_err:
                logger.warning(
                    f"PyAV failed to decode audio (filename={file.filename}, content_type={file.content_type}, "
                    f"size={len(raw_bytes)}, magic={magic}): {pyav_err}. Trying ffmpeg fallback."
                )
                audio_data = _decode_audio_ffmpeg_fallback(raw_bytes, sampling_rate=16000)

        elapsed = time.perf_counter() - start
        audio = Audio(audio_data, sample_rate=16000, name=Path(file.filename).stem if file.filename else None)
        rtf = elapsed / audio.duration if audio.duration > 0 else float("inf")
        logger.debug(f"Decoded {audio.duration}s of audio in {elapsed:.5f}s (RTF: {rtf})")
        return audio
    except HTTPException:
        raise
    except av.error.InvalidDataError as e:
        raise HTTPException(
            status_code=415,
            detail="Failed to decode audio. The provided file type is not supported.",
        ) from e
    except av.error.ValueError as e:
        raise HTTPException(
            status_code=400,
            detail="Failed to decode audio. The provided file is likely empty.",
        ) from e
    except RuntimeError as e:
        if "ffmpeg fallback failed" in str(e):
            logger.exception(
                f"Both PyAV and ffmpeg fallback failed for file: {file.filename}, content_type: {file.content_type}"
            )
            raise HTTPException(
                status_code=415,
                detail="Failed to decode audio. The provided file type is not supported.",
            ) from e
        logger.exception(
            "Failed to decode audio. This is likely a bug. Please create an issue at https://github.com/speaches-ai/speaches/issues/new."
        )
        raise HTTPException(status_code=500, detail="Failed to decode audio.") from e
    except Exception as e:
        logger.exception(
            "Failed to decode audio. This is likely a bug. Please create an issue at https://github.com/speaches-ai/speaches/issues/new."
        )
        raise HTTPException(status_code=500, detail="Failed to decode audio.") from e


AudioFileDependency = Annotated[Audio, Depends(audio_file_dependency)]


@lru_cache
def get_completion_client() -> AsyncCompletions:
    config = get_config()
    oai_client = AsyncOpenAI(
        base_url=config.chat_completion_base_url,
        api_key=config.chat_completion_api_key.get_secret_value(),
        max_retries=0,
    )
    return oai_client.chat.completions


async def get_completion_client_async() -> AsyncCompletions:
    return get_completion_client()


CompletionClientDependency = Annotated[AsyncCompletions, Depends(get_completion_client_async)]


@lru_cache
def get_speech_client() -> AsyncSpeech:
    config = get_config()
    if config.loopback_host_url is None:
        # TODO: verify shared state — speech_router may not share model_manager with the main app
        from speaches.routers.speech import (
            router as speech_router,
        )

        speech_app = FastAPI()
        speech_app.include_router(speech_router)
        http_client = AsyncClient(
            transport=ASGITransport(speech_app),
            base_url="http://test/v1",
        )
    else:
        http_client = AsyncClient(
            base_url=f"{config.loopback_host_url}/v1",
        )
    oai_client = AsyncOpenAI(
        http_client=http_client,
        api_key=config.api_key.get_secret_value() if config.api_key else "cant-be-empty",
        max_retries=0,
        base_url=f"{config.loopback_host_url}/v1" if config.loopback_host_url else None,
    )
    return oai_client.audio.speech


def get_speech_client_async() -> AsyncSpeech:
    return get_speech_client()


SpeechClientDependency = Annotated[AsyncSpeech, Depends(get_speech_client_async)]


@lru_cache
def get_transcription_client() -> AsyncTranscriptions:
    config = get_config()
    if config.loopback_host_url is None:
        # TODO: verify shared state — stt_router may not share model_manager with the main app
        from speaches.routers.stt import (
            router as stt_router,
        )

        stt_app = FastAPI()
        stt_app.include_router(stt_router)
        http_client = AsyncClient(
            transport=ASGITransport(stt_app),
            base_url="http://test/v1",
        )
    else:
        http_client = AsyncClient(
            base_url=f"{config.loopback_host_url}/v1",
        )
    oai_client = AsyncOpenAI(
        http_client=http_client,
        api_key=config.api_key.get_secret_value() if config.api_key else "cant-be-empty",
        max_retries=0,
        base_url=f"{config.loopback_host_url}/v1" if config.loopback_host_url else None,
    )
    return oai_client.audio.transcriptions


async def get_transcription_client_async() -> AsyncTranscriptions:
    return get_transcription_client()


TranscriptionClientDependency = Annotated[AsyncTranscriptions, Depends(get_transcription_client_async)]
