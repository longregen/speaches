import asyncio
from collections.abc import Generator, Iterable
import logging
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import StreamingResponse
from faster_whisper.transcribe import BatchedInferencePipeline, TranscriptionInfo
from huggingface_hub.utils._cache_manager import _scan_cached_repo
from huggingface_hub.errors import CorruptedCacheException

from speaches.api_types import (
    DEFAULT_TIMESTAMP_GRANULARITIES,
    TIMESTAMP_GRANULARITIES_COMBINATIONS,
    CreateTranscriptionResponseJson,
    CreateTranscriptionResponseVerboseJson,
    TimestampGranularities,
    TranscriptionSegment,
)
from speaches.dependencies import AudioFileDependency, ConfigDependency, WhisperModelManagerDependency
from speaches.executors.whisper import utils as whisper_utils
from speaches.hf_utils import get_model_card_data_from_cached_repo_info, get_model_repo_path, load_repo_model_card_data
from speaches.model_aliases import ModelId
from speaches.text_utils import segments_to_srt, segments_to_text, segments_to_vtt

logger = logging.getLogger(__name__)

router = APIRouter(tags=["automatic-speech-recognition"])

type ResponseFormat = Literal["text", "json", "verbose_json", "srt", "vtt"]

# https://platform.openai.com/docs/api-reference/audio/createTranscription#audio-createtranscription-response_format
DEFAULT_RESPONSE_FORMAT: ResponseFormat = "json"


def segments_to_response(
    segments: Iterable[TranscriptionSegment],
    transcription_info: TranscriptionInfo,
    response_format: ResponseFormat,
) -> Response:
    segments = list(segments)
    match response_format:
        case "text":
            return Response(segments_to_text(segments), media_type="text/plain")
        case "json":
            return Response(
                CreateTranscriptionResponseJson.from_segments(segments).model_dump_json(),
                media_type="application/json",
            )
        case "verbose_json":
            return Response(
                CreateTranscriptionResponseVerboseJson.from_segments(segments, transcription_info).model_dump_json(),
                media_type="application/json",
            )
        case "vtt":
            return Response(
                "".join(segments_to_vtt(segment, i) for i, segment in enumerate(segments)), media_type="text/vtt"
            )
        case "srt":
            return Response(
                "".join(segments_to_srt(segment, i) for i, segment in enumerate(segments)), media_type="text/plain"
            )


def format_as_sse(data: str) -> str:
    return f"data: {data}\n\n"


def segments_to_streaming_response(
    segments: Iterable[TranscriptionSegment],
    transcription_info: TranscriptionInfo,
    response_format: ResponseFormat,
) -> StreamingResponse:
    def segment_responses() -> Generator[str, None, None]:
        for i, segment in enumerate(segments):
            if response_format == "text":
                data = segment.text
            elif response_format == "json":
                data = CreateTranscriptionResponseJson.from_segments([segment]).model_dump_json()
            elif response_format == "verbose_json":
                data = CreateTranscriptionResponseVerboseJson.from_segment(
                    segment, transcription_info
                ).model_dump_json()
            elif response_format == "vtt":
                data = segments_to_vtt(segment, i)
            elif response_format == "srt":
                data = segments_to_srt(segment, i)
            yield format_as_sse(data)

    return StreamingResponse(segment_responses(), media_type="text/event-stream")


@router.post(
    "/v1/audio/translations",
    response_model=str | CreateTranscriptionResponseJson | CreateTranscriptionResponseVerboseJson,
)
def translate_file(
    config: ConfigDependency,
    model_manager: WhisperModelManagerDependency,
    audio: AudioFileDependency,
    model: Annotated[ModelId, Form()],
    prompt: Annotated[str | None, Form()] = None,
    response_format: Annotated[ResponseFormat, Form()] = DEFAULT_RESPONSE_FORMAT,
    temperature: Annotated[float, Form()] = 0.0,
    stream: Annotated[bool, Form()] = False,
    vad_filter: Annotated[bool, Form()] = False,
) -> Response | StreamingResponse:
    with model_manager.load_model(model) as whisper:
        whisper_model = BatchedInferencePipeline(model=whisper) if config.whisper.use_batched_mode else whisper
        segments, transcription_info = whisper_model.transcribe(
            audio,
            task="translate",
            initial_prompt=prompt,
            temperature=temperature,
            vad_filter=vad_filter,
        )
        segments = TranscriptionSegment.from_faster_whisper_segments(segments)

        if stream:
            return segments_to_streaming_response(segments, transcription_info, response_format)
        else:
            return segments_to_response(segments, transcription_info, response_format)


# HACK: Since Form() doesn't support `alias`, we need to use a workaround.
async def get_timestamp_granularities(request: Request) -> TimestampGranularities:
    form = await request.form()
    if form.get("timestamp_granularities[]") is None:
        return DEFAULT_TIMESTAMP_GRANULARITIES
    timestamp_granularities = form.getlist("timestamp_granularities[]")
    assert timestamp_granularities in TIMESTAMP_GRANULARITIES_COMBINATIONS, (
        f"{timestamp_granularities} is not a valid value for `timestamp_granularities[]`."
    )
    return timestamp_granularities


# https://platform.openai.com/docs/api-reference/audio/createTranscription
# https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L8915
@router.post(
    "/v1/audio/transcriptions",
    response_model=str | CreateTranscriptionResponseJson | CreateTranscriptionResponseVerboseJson,
)
def transcribe_file(
    config: ConfigDependency,
    model_manager: WhisperModelManagerDependency,
    request: Request,
    audio: AudioFileDependency,
    model: Annotated[ModelId, Form()],
    language: Annotated[str | None, Form()] = None,
    prompt: Annotated[str | None, Form()] = None,
    response_format: Annotated[ResponseFormat, Form()] = DEFAULT_RESPONSE_FORMAT,
    temperature: Annotated[float, Form()] = 0.0,
    timestamp_granularities: Annotated[
        TimestampGranularities,
        # WARN: `alias` doesn't actually work.
        Form(alias="timestamp_granularities[]"),
    ] = ["segment"],
    stream: Annotated[bool, Form()] = False,
    hotwords: Annotated[str | None, Form()] = None,
    vad_filter: Annotated[bool, Form()] = False,
) -> Response | StreamingResponse:
    timestamp_granularities = asyncio.run(get_timestamp_granularities(request))
    if timestamp_granularities != DEFAULT_TIMESTAMP_GRANULARITIES and response_format != "verbose_json":
        logger.warning(
            "It only makes sense to provide `timestamp_granularities[]` when `response_format` is set to `verbose_json`. See https://platform.openai.com/docs/api-reference/audio/createTranscription#audio-createtranscription-timestamp_granularities."
        )

    model_repo_path = get_model_repo_path(model)
    if model_repo_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model}' is not installed locally. You can download the model using `POST /v1/models`",
        )
    # Try to scan the cached repo, but fall back to direct README loading if corrupted
    try:
        cached_repo_info = _scan_cached_repo(model_repo_path)
        model_card_data = get_model_card_data_from_cached_repo_info(cached_repo_info)
    except CorruptedCacheException as e:
        logger.warning(f"Cache appears corrupted for {model}: {e}. Falling back to direct README loading.")
        # Try to load the model card directly from the README file
        readme_paths = list(model_repo_path.glob("snapshots/*/README.md"))
        if readme_paths:
            model_card_data = load_repo_model_card_data(readme_paths[0])
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Model cache is corrupted and no README.md found for '{model}'",
            )
    
    if model_card_data is None:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load model card data for '{model}'",
        )
    if whisper_utils.hf_model_filter.passes_filter(model_card_data):
        with model_manager.load_model(model) as whisper:
            whisper_model = BatchedInferencePipeline(model=whisper) if config.whisper.use_batched_mode else whisper
            segments, transcription_info = whisper_model.transcribe(
                audio,
                task="transcribe",
                language=language,
                initial_prompt=prompt,
                word_timestamps="word" in timestamp_granularities,
                temperature=temperature,
                vad_filter=vad_filter,
                hotwords=hotwords,
            )
            segments = TranscriptionSegment.from_faster_whisper_segments(segments)

            if stream:
                return segments_to_streaming_response(segments, transcription_info, response_format)
            else:
                return segments_to_response(segments, transcription_info, response_format)
    else:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model}' is not supported. If you think this is a mistake, please open an issue.",
        )
