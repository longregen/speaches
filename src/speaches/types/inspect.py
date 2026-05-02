from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LaneId = Literal[
    "audio_level",
    "vad",
    "stt",
    "turn",
    "bargein",
    "llm",
    "response",
    "tool",
    "tts_req",
    "tts_chunk",
    "wire",
    "error",
]

ERR_KINDS: frozenset[str] = frozenset(
    {
        "error",
        "raised",
        "dropped",
        "failed",
        "phrase_error",
        "bargein_missed",
    }
)

# Protocol event types that have a dedicated inspector lane and should NOT
# appear on the wire lane as duplicates.
DEDICATED_LANE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "response.output_audio.delta",
        "response.output_text.delta",
        "response.output_audio_transcript.delta",
        "response.output_audio.done",
        "response.output_audio_transcript.done",
        "input_audio_buffer.append",
        "input_audio_buffer.speech_started",
        "input_audio_buffer.speech_stopped",
        "input_audio_buffer.committed",
        "input_audio_buffer.partial_transcription",
        "response.output_item.added",
        "response.output_item.done",
        "response.content_part.added",
        "response.content_part.done",
        "conversation.item.added",
        "conversation.item.done",
        "conversation.item.input_audio_transcription.completed",
    }
)


class Corr(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_id: str | None = None
    item_id: str | None = None
    response_id: str | None = None
    phrase_id: str | None = None


class InspectorEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    seq: int
    ts_mono_ns: int
    ts_wall: float
    lane: LaneId
    kind: str
    corr: Corr = Field(default_factory=Corr)
    span_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionMeta(BaseModel):
    id: str
    created_at: float
    model: str
    state: str
    turn_count: int
    last_event_ts: float | None = None


class SessionHistoryEntry(BaseModel):
    id: str
    size_bytes: int
    mtime: float
