import asyncio
import io

import numpy as np
from openai import AsyncOpenAI
import pytest
import soundfile as sf

from speaches.executors.pocket_tts import POCKET_TTS_AVAILABLE, SAMPLE_RATE

pytestmark = pytest.mark.skipif(not POCKET_TTS_AVAILABLE, reason="pocket-tts is not installed")

POCKET_TTS_MODEL_ID = "kyutai/pocket-tts-without-voice-cloning"
VOICE_ID = "alba"
DEFAULT_INPUT = "Hello, this is a test of pocket TTS."


def _generate_speech(model_id: str, voice: str, text: str) -> list:
    from speaches.config import Config
    from speaches.executors.shared.handler_protocol import SpeechRequest
    from speaches.executors.shared.registry import ExecutorRegistry

    config = Config(tts_model_ttl=-1, enable_ui=False)
    registry = ExecutorRegistry(config)
    handler = registry.resolve_tts_model_manager(model_id)
    request = SpeechRequest(model=model_id, voice=voice, text=text, speed=1.0)
    return list(handler.handle_speech_request(request))


@pytest.mark.asyncio
async def test_pocket_tts_generate_audio() -> None:
    chunks = await asyncio.to_thread(_generate_speech, POCKET_TTS_MODEL_ID, VOICE_ID, DEFAULT_INPUT)
    assert len(chunks) > 0
    total_samples = sum(c.data.shape[0] for c in chunks)
    assert total_samples > 0
    for chunk in chunks:
        assert chunk.sample_rate == SAMPLE_RATE
        assert chunk.data.dtype == np.float32


@pytest.mark.asyncio
async def test_pocket_tts_openai_voice_fallback() -> None:
    chunks = await asyncio.to_thread(_generate_speech, POCKET_TTS_MODEL_ID, "alloy", DEFAULT_INPUT)
    assert len(chunks) > 0


@pytest.mark.asyncio
async def test_pocket_tts_different_voice() -> None:
    chunks = await asyncio.to_thread(_generate_speech, POCKET_TTS_MODEL_ID, "marius", DEFAULT_INPUT)
    assert len(chunks) > 0


@pytest.mark.asyncio
async def test_pocket_tts_wav_via_api(openai_client: AsyncOpenAI) -> None:
    res = await openai_client.audio.speech.create(
        model=POCKET_TTS_MODEL_ID,
        voice=VOICE_ID,
        input=DEFAULT_INPUT,
        response_format="wav",
    )
    audio_bytes = res.read()
    data, sample_rate = sf.read(io.BytesIO(audio_bytes))
    assert sample_rate == SAMPLE_RATE
    assert len(data) > 0
