from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ModelTask = Literal[
    "automatic-speech-recognition",
    "text-to-speech",
    "speaker-embedding",
    "voice-activity-detection",
    "speaker-diarization",
]

# https://platform.openai.com/docs/api-reference/audio/createSpeech#audio-createspeech-response_format
DEFAULT_SPEECH_RESPONSE_FORMAT = "mp3"

# https://platform.openai.com/docs/api-reference/audio/createSpeech#audio-createspeech-voice
# https://platform.openai.com/docs/guides/text-to-speech/voice-options
OPENAI_SUPPORTED_SPEECH_VOICE_NAMES = ("alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse")

# https://platform.openai.com/docs/guides/text-to-speech/supported-output-formats
type SpeechResponseFormat = Literal["pcm", "mp3", "wav", "flac", "opus", "aac"]
SUPPORTED_SPEECH_RESPONSE_FORMATS = ("pcm", "mp3", "wav", "flac", "opus", "aac")

MIN_SPEECH_SAMPLE_RATE = 8000
MAX_SPEECH_SAMPLE_RATE = 48000


# https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L11146
class Model(BaseModel):
    """There may be additional fields in the response that are specific to the model type."""

    id: str
    created: int = 0
    object: Literal["model"] = "model"
    owned_by: str
    language: list[str] | None = None
    """List of ISO 639-3 supported by the model. It's possible that the list will be empty. This field is not a part of the OpenAI API spec and is added for convenience."""

    task: ModelTask  # TODO: make a list?

    model_config = ConfigDict(extra="allow")


# https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L8730
class ListModelsResponse(BaseModel):
    data: list[Model]
    object: Literal["list"] = "list"


# https://github.com/openai/openai-openapi/blob/master/openapi.yaml#L10909
TimestampGranularities = list[Literal["segment", "word"]]


DEFAULT_TIMESTAMP_GRANULARITIES: TimestampGranularities = ["segment"]
TIMESTAMP_GRANULARITIES_COMBINATIONS: list[TimestampGranularities] = [
    [],  # should be treated as ["segment"]. https://platform.openai.com/docs/api-reference/audio/createTranscription#audio-createtranscription-timestamp_granularities
    ["segment"],
    ["word"],
    ["word", "segment"],
    ["segment", "word"],  # same as ["word", "segment"] but order is different
]


class EmbeddingObject(BaseModel):
    object: Literal["embedding"] = "embedding"
    index: Literal[0] = 0
    embedding: list[float]


class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class CreateEmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EmbeddingObject] = Field(..., min_length=1, max_length=1)
    model: str
    usage: EmbeddingUsage


# NOTE: I define these here because they aren't defined in the openai-python package. Once they are added there, we can remove these definitions.


class SpeechAudioDeltaEvent(BaseModel):
    type: Literal["speech.audio.delta"] = "speech.audio.delta"
    audio: str


class SpeechAudioTokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class SpeechAudioDoneEvent(BaseModel):
    type: Literal["speech.audio.done"] = "speech.audio.done"
    token_usage: SpeechAudioTokenUsage
