from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
import time
from typing import TYPE_CHECKING

import numpy as np
import openai.types.audio
from openai.types.realtime.conversation_item_input_audio_transcription_completed_event import (
    UsageTranscriptTextUsageDuration,
)
from pydantic import BaseModel

from speaches.audio import Audio
from speaches.executors.shared.handler_protocol import TranscriptionHandler, TranscriptionRequest
from speaches.executors.shared.vad_types import VadOptions
from speaches.realtime.utils import generate_item_id, task_done_callback
from speaches.types.realtime import (
    ConversationItemContentInputAudio,
    ConversationItemInputAudioTranscriptionCompletedEvent,
    ConversationItemMessage,
    ServerEvent,
    Session,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from speaches.realtime.conversation_event_router import Conversation
    from speaches.realtime.pubsub import EventPubSub

SAMPLE_RATE = 16000
MS_SAMPLE_RATE = 16
MAX_VAD_WINDOW_SIZE_SAMPLES = 3000 * MS_SAMPLE_RATE
MAX_BUFFER_SIZE_SAMPLES = 30 * 60 * SAMPLE_RATE  # 30 minutes
_INITIAL_CAPACITY = 3200  # 200ms at 16kHz, ~12.5 KiB

# Duration-aware avg_logprob gate. Short audio is the highest-risk territory
# for whisper noise hallucinations (cough -> 'Bye.', breath -> 'Hmm.'); for
# clips at or under FULL_MS we apply the configured base threshold as-is.
# Once the VAD-held speech extends past FULL_MS, real-speech becomes more
# likely with each additional second, so we relax the threshold linearly
# toward LOOSE_FLOOR. At or above OFF_MS the gate is disabled outright.
_AVG_LOGPROB_GATE_FULL_MS = 1500
_AVG_LOGPROB_GATE_OFF_MS = 5000
_AVG_LOGPROB_GATE_LOOSE_FLOOR = -3.0

logger = logging.getLogger(__name__)


def _effective_avg_logprob_threshold(base: float | None, duration_ms: int) -> float | None:
    if base is None:
        return None
    if duration_ms <= _AVG_LOGPROB_GATE_FULL_MS:
        return base
    if duration_ms >= _AVG_LOGPROB_GATE_OFF_MS:
        return None
    frac = (duration_ms - _AVG_LOGPROB_GATE_FULL_MS) / (_AVG_LOGPROB_GATE_OFF_MS - _AVG_LOGPROB_GATE_FULL_MS)
    return base + frac * (_AVG_LOGPROB_GATE_LOOSE_FLOOR - base)


class VadState(BaseModel):
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None
    # Inspector-only: true once a pending (min_speech=0) candidate has been signalled
    # for the current speech attempt, so repeated `pending_start` events aren't emitted.
    pending_start_ms: int | None = None
    # TODO: consider keeping track of what was the last audio timestamp that was processed.


class InputAudioBuffer:
    def __init__(self, pubsub: EventPubSub) -> None:
        self.id = generate_item_id()
        self._buffer: NDArray[np.float32] = np.empty(_INITIAL_CAPACITY, dtype=np.float32)
        self._size: int = 0
        self.vad_state = VadState()
        self.pubsub = pubsub
        self._vad_ring: NDArray[np.float32] = np.zeros(2 * MAX_VAD_WINDOW_SIZE_SAMPLES, dtype=np.float32)
        self._vad_ring_pos: int = 0
        self._vad_ring_filled: int = 0

    @property
    def data(self) -> NDArray[np.float32]:
        return self._buffer[: self._size]

    @property
    def vad_data(self) -> NDArray[np.float32]:
        if self._vad_ring_filled < MAX_VAD_WINDOW_SIZE_SAMPLES:
            return self._vad_ring[: self._vad_ring_filled]
        start = self._vad_ring_pos
        return self._vad_ring[start : start + MAX_VAD_WINDOW_SIZE_SAMPLES]

    @property
    def size(self) -> int:
        return self._size

    @property
    def duration(self) -> float:
        return self._size / SAMPLE_RATE

    @property
    def duration_ms(self) -> int:
        return self._size // MS_SAMPLE_RATE

    def append(self, audio_chunk: NDArray[np.float32]) -> None:
        chunk_len = len(audio_chunk)
        if self._size + chunk_len > MAX_BUFFER_SIZE_SAMPLES:
            logger.warning(
                f"Audio buffer size limit reached ({MAX_BUFFER_SIZE_SAMPLES} samples), dropping {chunk_len} new samples"
            )
            return
        required = self._size + chunk_len
        if required > len(self._buffer):
            new_capacity = max(required, len(self._buffer) * 2)
            new_buffer: NDArray[np.float32] = np.empty(new_capacity, dtype=np.float32)
            new_buffer[: self._size] = self._buffer[: self._size]
            self._buffer = new_buffer
        self._buffer[self._size : required] = audio_chunk
        self._size = required
        self._vad_ring_append(audio_chunk)

    def _vad_ring_append(self, audio_chunk: NDArray[np.float32]) -> None:
        n = len(audio_chunk)
        cap = MAX_VAD_WINDOW_SIZE_SAMPLES
        if n >= cap:
            tail = audio_chunk[-cap:]
            self._vad_ring[:cap] = tail
            self._vad_ring[cap : 2 * cap] = tail
            self._vad_ring_pos = 0
            self._vad_ring_filled = cap
            return
        pos = self._vad_ring_pos
        end = pos + n
        if end <= cap:
            self._vad_ring[pos:end] = audio_chunk
            self._vad_ring[pos + cap : end + cap] = audio_chunk
            new_pos = end % cap
        else:
            first = cap - pos
            self._vad_ring[pos:cap] = audio_chunk[:first]
            self._vad_ring[pos + cap : 2 * cap] = audio_chunk[:first]
            wrap = n - first
            self._vad_ring[:wrap] = audio_chunk[first:]
            self._vad_ring[cap : cap + wrap] = audio_chunk[first:]
            new_pos = wrap
        self._vad_ring_pos = new_pos
        self._vad_ring_filled = min(self._vad_ring_filled + n, cap)

    def consolidate(self) -> None:
        pass

    # TODO: come up with a better name for data_w_vad_applied
    @property
    def data_w_vad_applied(self) -> NDArray[np.float32]:
        if self.vad_state.audio_start_ms is None:
            return self.data
        else:
            assert self.vad_state.audio_end_ms is not None
            return self.data[
                self.vad_state.audio_start_ms * MS_SAMPLE_RATE : self.vad_state.audio_end_ms * MS_SAMPLE_RATE
            ]


class InputAudioBufferManager:
    def __init__(self, pubsub: EventPubSub) -> None:
        self._pubsub = pubsub
        initial = InputAudioBuffer(pubsub)
        self._buffers: OrderedDict[str, InputAudioBuffer] = OrderedDict({initial.id: initial})

    @property
    def current(self) -> InputAudioBuffer:
        buffer_id = next(reversed(self._buffers))
        return self._buffers[buffer_id]

    def get(self, buffer_id: str) -> InputAudioBuffer:
        return self._buffers[buffer_id]

    def rotate(self) -> InputAudioBuffer:
        new_buffer = InputAudioBuffer(self._pubsub)
        self._buffers[new_buffer.id] = new_buffer
        return new_buffer

    def clear_current(self) -> InputAudioBuffer:
        self._buffers.popitem()
        return self.rotate()


class InputAudioBufferTranscriber:
    def __init__(
        self,
        *,
        pubsub: EventPubSub,
        stt_model_manager: TranscriptionHandler,
        input_audio_buffer: InputAudioBuffer,
        session: Session,
        conversation: Conversation,
    ) -> None:
        self.pubsub = pubsub
        self.stt_model_manager = stt_model_manager
        self.input_audio_buffer = input_audio_buffer
        self.session = session
        self.conversation = conversation

        self.task: asyncio.Task[None] | None = None
        self.events = asyncio.Queue[ServerEvent]()

    async def _handler(self) -> None:
        from speaches.inspect import emit as inspect_emit  # avoid import cycle at module load

        audio = Audio(self.input_audio_buffer.data_w_vad_applied, sample_rate=SAMPLE_RATE)
        # Request word timestamps only when an inspector is attached. Adds ~10-20%
        # to whisper latency on bigger models; not worth the cost otherwise.
        granularities: list = ["segment"]
        if inspect_emit.has_subscribers():
            granularities = ["segment", "word"]
        request = TranscriptionRequest(
            audio=audio,
            model=self.session.input_audio_transcription.model,
            language=self.session.input_audio_transcription.language,
            response_format="verbose_json",
            speech_segments=[],
            vad_options=VadOptions(min_silence_duration_ms=160, max_speech_duration_s=30),
            timestamp_granularities=granularities,
        )
        start = time.perf_counter()
        try:
            result = await asyncio.to_thread(self.stt_model_manager.handle_non_streaming_transcription_request, request)
        except Exception as exc:
            elapsed = time.perf_counter() - start
            inspect_emit.emit(
                "stt",
                "failed",
                error=str(exc),
                elapsed_ms=int(elapsed * 1000),
            )
            raise
        elapsed = time.perf_counter() - start

        # Per-segment whisper signals. Stats are emitted on every accepted turn
        # too so operators can read the inspector to pick thresholds.
        avg_no_speech: float | None = None
        min_no_speech: float | None = None
        max_no_speech: float | None = None
        avg_logprob: float | None = None
        min_logprob: float | None = None
        max_logprob: float | None = None
        avg_compression: float | None = None
        min_compression: float | None = None
        max_compression: float | None = None
        nsp_threshold = self.session.no_speech_prob_threshold
        logprob_threshold = self.session.avg_logprob_threshold
        audio_duration_ms = int(len(self.input_audio_buffer.data_w_vad_applied) * 1000 / SAMPLE_RATE)
        effective_logprob_threshold = _effective_avg_logprob_threshold(logprob_threshold, audio_duration_ms)
        if isinstance(result, openai.types.audio.TranscriptionVerbose):
            transcript = result.text
            if result.segments:
                probs = [s.no_speech_prob for s in result.segments]
                avg_no_speech = sum(probs) / len(probs)
                min_no_speech = min(probs)
                max_no_speech = max(probs)
                lps = [s.avg_logprob for s in result.segments]
                avg_logprob = sum(lps) / len(lps)
                min_logprob = min(lps)
                max_logprob = max(lps)
                crs = [s.compression_ratio for s in result.segments]
                avg_compression = sum(crs) / len(crs)
                min_compression = min(crs)
                max_compression = max(crs)
            nsp_fail = nsp_threshold is not None and avg_no_speech is not None and avg_no_speech > nsp_threshold
            logprob_fail = (
                effective_logprob_threshold is not None
                and avg_logprob is not None
                and avg_logprob < effective_logprob_threshold
            )
            if nsp_fail or logprob_fail:
                reason = "no_speech_prob" if nsp_fail else "avg_logprob"
                logger.info(
                    f"Noise gate ({reason}): discarding audio "
                    f"(avg_no_speech_prob={avg_no_speech} thr={nsp_threshold}, "
                    f"avg_logprob={avg_logprob} thr={effective_logprob_threshold} (base={logprob_threshold}, dur={audio_duration_ms}ms), "
                    f"transcript={transcript!r}, elapsed={elapsed:.2f}s)"
                )
                inspect_emit.emit(
                    "stt",
                    "rejected_noise",
                    text=transcript,
                    reason=reason,
                    avg_no_speech_prob=avg_no_speech,
                    min_no_speech_prob=min_no_speech,
                    max_no_speech_prob=max_no_speech,
                    no_speech_prob_threshold=nsp_threshold,
                    avg_logprob=avg_logprob,
                    min_logprob=min_logprob,
                    max_logprob=max_logprob,
                    avg_logprob_threshold=logprob_threshold,
                    effective_avg_logprob_threshold=effective_logprob_threshold,
                    audio_duration_ms=audio_duration_ms,
                    avg_compression_ratio=avg_compression,
                    min_compression_ratio=min_compression,
                    max_compression_ratio=max_compression,
                    elapsed_ms=int(elapsed * 1000),
                )
                self.pubsub.publish_nowait(
                    ConversationItemInputAudioTranscriptionCompletedEvent(
                        item_id=self.input_audio_buffer.id,
                        transcript="",
                        usage=UsageTranscriptTextUsageDuration(
                            seconds=self.input_audio_buffer.duration,
                            type="duration",
                        ),
                    )
                )
                return
        elif isinstance(result, tuple):
            transcript = result[0]
        else:
            transcript = result.text

        logger.debug(f"Transcription completed in {elapsed:.2f}s: {transcript!r}")

        if not transcript.strip():
            logger.info(f"Empty transcript: discarding audio (duration={self.input_audio_buffer.duration:.2f}s)")
            inspect_emit.emit(
                "stt",
                "rejected_empty",
                duration_ms=int(self.input_audio_buffer.duration * 1000),
                elapsed_ms=int(elapsed * 1000),
            )
            self.pubsub.publish_nowait(
                ConversationItemInputAudioTranscriptionCompletedEvent(
                    item_id=self.input_audio_buffer.id,
                    transcript="",
                    usage=UsageTranscriptTextUsageDuration(
                        seconds=self.input_audio_buffer.duration,
                        type="duration",
                    ),
                )
            )
            return

        content_item = ConversationItemContentInputAudio(transcript=transcript, type="input_audio")
        item = ConversationItemMessage(
            id=self.input_audio_buffer.id,
            role="user",
            content=[content_item],
            status="completed",
        )
        self.conversation.create_item(item)
        if item.id not in self.conversation.items:
            logger.warning(
                f"Item '{item.id}' was not added to conversation (likely duplicate), skipping transcription event"
            )
            return
        audio_start_ms = self.input_audio_buffer.vad_state.audio_start_ms
        audio_end_ms = self.input_audio_buffer.vad_state.audio_end_ms
        word_detail: list[dict] = []
        if isinstance(result, openai.types.audio.TranscriptionVerbose) and result.words:
            base_ms = audio_start_ms or 0
            word_detail.extend(
                {
                    "w": w.word,
                    "start_ms": int(base_ms + w.start * 1000),
                    "end_ms": int(base_ms + w.end * 1000),
                }
                for w in result.words
            )
        inspect_emit.emit(
            "stt",
            "final",
            text=transcript,
            words=word_detail or len(transcript.split()),
            audio_start_ms=audio_start_ms,
            audio_end_ms=audio_end_ms,
            elapsed_ms=int(elapsed * 1000),
            avg_no_speech_prob=avg_no_speech,
            min_no_speech_prob=min_no_speech,
            max_no_speech_prob=max_no_speech,
            no_speech_prob_threshold=nsp_threshold,
            avg_logprob=avg_logprob,
            min_logprob=min_logprob,
            max_logprob=max_logprob,
            avg_logprob_threshold=logprob_threshold,
            effective_avg_logprob_threshold=effective_logprob_threshold,
            audio_duration_ms=audio_duration_ms,
            avg_compression_ratio=avg_compression,
            min_compression_ratio=min_compression,
            max_compression_ratio=max_compression,
        )
        inspect_emit.emit("turn", "user_committed", item_id=item.id)
        self.pubsub.publish_nowait(
            ConversationItemInputAudioTranscriptionCompletedEvent(
                item_id=item.id,
                transcript=transcript,
                usage=UsageTranscriptTextUsageDuration(
                    seconds=self.input_audio_buffer.duration,
                    type="duration",
                ),
            )
        )

    async def _audio_direct_handler(self) -> None:
        import base64
        import io
        import wave

        from speaches.inspect import emit as inspect_emit

        start = time.perf_counter()
        audio_data = self.input_audio_buffer.data_w_vad_applied
        pcm16 = (np.clip(audio_data, -1.0, 1.0) * 32767).astype("<i2").tobytes()

        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm16)
        audio_b64 = base64.b64encode(wav_buf.getvalue()).decode("ascii")

        elapsed = time.perf_counter() - start
        audio_start_ms = self.input_audio_buffer.vad_state.audio_start_ms
        audio_end_ms = self.input_audio_buffer.vad_state.audio_end_ms
        duration_ms = len(audio_data) * 1000 // SAMPLE_RATE

        inspect_emit.emit(
            "stt",
            "audio_direct",
            audio_start_ms=audio_start_ms,
            audio_end_ms=audio_end_ms,
            duration_ms=duration_ms,
            audio_b64_len=len(audio_b64),
            elapsed_ms=int(elapsed * 1000),
        )

        content_item = ConversationItemContentInputAudio(
            transcript=None,
            audio=audio_b64,
        )
        item = ConversationItemMessage(
            id=self.input_audio_buffer.id,
            role="user",
            content=[content_item],
            status="completed",
        )
        self.conversation.create_item(item)
        if item.id not in self.conversation.items:
            logger.warning(f"Item '{item.id}' was not added to conversation (likely duplicate), skipping")
            return
        inspect_emit.emit("turn", "user_committed", item_id=item.id)
        self.pubsub.publish_nowait(
            ConversationItemInputAudioTranscriptionCompletedEvent(
                item_id=item.id,
                transcript="[audio direct to LLM]",
                usage=UsageTranscriptTextUsageDuration(
                    seconds=self.input_audio_buffer.duration,
                    type="duration",
                ),
            )
        )

    # TODO: add timeout parameter
    def start(self) -> None:
        assert self.task is None
        td = self.session.turn_detection
        create_response = td is None or td.create_response
        use_direct = self.session.audio_direct_to_llm and create_response
        handler = self._audio_direct_handler if use_direct else self._handler
        self.task = asyncio.create_task(handler())
        self.task.add_done_callback(task_done_callback)
