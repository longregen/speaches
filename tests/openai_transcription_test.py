from pathlib import Path

from openai import AsyncOpenAI
import pytest

from speaches.routers.stt import RESPONSE_FORMATS


@pytest.mark.asyncio
@pytest.mark.requires_openai
@pytest.mark.parametrize("response_format", RESPONSE_FORMATS)
async def test_openai_supported_formats_for_non_whisper_models(
    actual_openai_client: AsyncOpenAI,
    response_format: str,
) -> None:
    file_path = Path("audio.wav")
    transcription_event_stream = await actual_openai_client.audio.transcriptions.create(  # pyrefly: ignore[no-matching-overload]
        file=file_path,
        model="gpt-4o-transcribe",
        response_format=response_format,
        stream=True,
    )
    async for event in transcription_event_stream:
        print(event)
