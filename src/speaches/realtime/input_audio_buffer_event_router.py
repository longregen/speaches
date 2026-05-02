import asyncio
import base64
from io import BytesIO
import logging
import time
from typing import TYPE_CHECKING
import uuid

import numpy as np

if TYPE_CHECKING:
    from numpy.typing import NDArray

from speaches.audio import Audio, audio_samples_from_file, resample_audio_data
from speaches.executors.shared.handler_protocol import TranscriptionRequest
from speaches.executors.shared.vad_types import VadOptions
from speaches.executors.silero_vad_v5 import get_speech_timestamps, to_ms_speech_timestamps
from speaches.inspect import emit as inspect_emit
from speaches.realtime.context import SessionContext
from speaches.realtime.event_router import EventRouter
from speaches.realtime.input_audio_buffer import (
    MS_SAMPLE_RATE,
    SAMPLE_RATE,
    InputAudioBuffer,
    InputAudioBufferTranscriber,
)
from speaches.realtime.utils import task_done_callback
from speaches.types.realtime import (
    ConversationState,
    InputAudioBufferAppendEvent,
    InputAudioBufferClearedEvent,
    InputAudioBufferClearEvent,
    InputAudioBufferCommitEvent,
    InputAudioBufferCommittedEvent,
    InputAudioBufferPartialTranscriptionEvent,
    InputAudioBufferSpeechStartedEvent,
    InputAudioBufferSpeechStoppedEvent,
    Response,
    TurnDetection,
    create_invalid_request_error,
    create_server_error,
)

MIN_AUDIO_BUFFER_DURATION_MS = 100

logger = logging.getLogger(__name__)

event_router = EventRouter()


def vad_detection_flow(
    input_audio_buffer: InputAudioBuffer, turn_detection: TurnDetection, ctx: SessionContext
) -> InputAudioBufferSpeechStartedEvent | InputAudioBufferSpeechStoppedEvent | None:
    if input_audio_buffer.vad_state.audio_end_ms is not None:
        # Speech stop already fired for this buffer; ignore further VAD on it.
        # This prevents duplicate speech_stopped events when the async handler
        # hasn't yet created a new buffer between audio appends.
        return None

    audio_window = input_audio_buffer.vad_data

    speech_timestamps = to_ms_speech_timestamps(
        get_speech_timestamps(
            audio_window,
            model_manager=ctx.vad_model_manager,
            model_id=ctx.vad_model_id,
            vad_options=VadOptions(
                threshold=turn_detection.threshold,
                min_silence_duration_ms=turn_detection.silence_duration_ms,
                speech_pad_ms=turn_detection.prefix_padding_ms,
                # Silero drops in-progress segments shorter than this from the returned
                # timestamps (see silero_vad_v5:278), so a brief noise spike never
                # escalates to a speech_started event.
                min_speech_duration_ms=turn_detection.min_speech_duration_ms,
            ),
        )
    )
    if len(speech_timestamps) > 1:
        logger.warning(f"More than one speech timestamp: {speech_timestamps}")

    speech_timestamp = speech_timestamps[-1] if len(speech_timestamps) > 0 else None

    # Inspector-only: second pass with min_speech_duration_ms=0 to see candidates
    # Silero's filter suppresses. Diff against the filtered pass to emit
    # vad:pending_start and vad:rejected_pending.
    if inspect_emit.has_subscribers():
        unfiltered = to_ms_speech_timestamps(
            get_speech_timestamps(
                audio_window,
                model_manager=ctx.vad_model_manager,
                model_id=ctx.vad_model_id,
                vad_options=VadOptions(
                    threshold=turn_detection.threshold,
                    min_silence_duration_ms=turn_detection.silence_duration_ms,
                    speech_pad_ms=turn_detection.prefix_padding_ms,
                    min_speech_duration_ms=0,
                ),
            )
        )
        vs = input_audio_buffer.vad_state
        has_filtered = speech_timestamp is not None
        has_unfiltered = len(unfiltered) > 0
        if has_unfiltered and not has_filtered and vs.audio_start_ms is None and vs.pending_start_ms is None:
            window_ms = len(audio_window) // MS_SAMPLE_RATE
            cand = unfiltered[-1]
            cand_start_ms = input_audio_buffer.duration_ms - window_ms + cand.start
            vs.pending_start_ms = cand_start_ms
            inspect_emit.emit(
                "vad",
                "pending_start",
                corr={"item_id": input_audio_buffer.id},
                audio_start_ms=cand_start_ms,
                min_speech_duration_ms=turn_detection.min_speech_duration_ms,
            )
        elif not has_unfiltered and vs.pending_start_ms is not None and vs.audio_start_ms is None:
            inspect_emit.emit(
                "vad",
                "rejected_pending",
                corr={"item_id": input_audio_buffer.id},
                audio_start_ms=vs.pending_start_ms,
                duration_ms=input_audio_buffer.duration_ms - vs.pending_start_ms,
                reason="below_min_speech_duration_ms",
            )
            vs.pending_start_ms = None
        elif has_filtered and vs.pending_start_ms is not None:
            vs.pending_start_ms = None

    if input_audio_buffer.vad_state.audio_start_ms is None:
        if speech_timestamp is None:
            return None
        input_audio_buffer.vad_state.audio_start_ms = (
            input_audio_buffer.duration_ms - len(audio_window) // MS_SAMPLE_RATE + speech_timestamp.start
        )
        return InputAudioBufferSpeechStartedEvent(
            item_id=input_audio_buffer.id,
            audio_start_ms=input_audio_buffer.vad_state.audio_start_ms,
        )

    elif speech_timestamp is None:
        input_audio_buffer.vad_state.audio_end_ms = input_audio_buffer.duration_ms
        return InputAudioBufferSpeechStoppedEvent(
            item_id=input_audio_buffer.id,
            audio_end_ms=input_audio_buffer.vad_state.audio_end_ms,
        )

    else:
        window_ms = len(audio_window) // MS_SAMPLE_RATE
        trailing_silence_ms = window_ms - speech_timestamp.end
        if trailing_silence_ms >= turn_detection.silence_duration_ms:
            input_audio_buffer.vad_state.audio_end_ms = input_audio_buffer.duration_ms - trailing_silence_ms
            return InputAudioBufferSpeechStoppedEvent(
                item_id=input_audio_buffer.id,
                audio_end_ms=input_audio_buffer.vad_state.audio_end_ms,
            )

    return None


@event_router.register("input_audio_buffer.append")
async def handle_input_audio_buffer_append(ctx: SessionContext, event: InputAudioBufferAppendEvent) -> None:
    audio_chunk = audio_samples_from_file(BytesIO(base64.b64decode(event.audio)), 24000)
    audio_chunk = resample_audio_data(audio_chunk, 24000, 16000)
    input_audio_buffer = ctx.audio_buffers.current
    input_audio_buffer.append(audio_chunk)

    if inspect_emit.get_turn_id() is None:
        # Reset stale VAD state carried over from a previous turn (e.g. barge-in
        # attempt during a response that completed before the barge-in fired).
        vs = input_audio_buffer.vad_state
        if vs.audio_start_ms is not None:
            logger.debug(
                f"Resetting stale VAD state on new turn: audio_start_ms={vs.audio_start_ms}, "
                f"audio_end_ms={vs.audio_end_ms}"
            )
            vs.audio_start_ms = None
            vs.audio_end_ms = None
            vs.pending_start_ms = None
        tid = uuid.uuid4().hex
        inspect_emit.set_turn_id(tid)
        inspect_emit.set_item_id(input_audio_buffer.id)
        inspect_emit.emit("turn", "turn_start", role="user")
    if len(audio_chunk) > 0:
        if ctx.audio_store is not None:
            ctx.audio_store.append_mic_in(np.asarray(audio_chunk, dtype=np.float32))
        rms = float(np.sqrt(np.mean(np.asarray(audio_chunk, dtype=np.float32) ** 2)))
        inspect_emit.emit(
            "audio_level",
            "sample",
            channel="mic_in",
            rms=rms,
            window_ms=int(len(audio_chunk) * 1000 / SAMPLE_RATE),
        )

    if ctx.session.turn_detection is not None:
        vad_event = vad_detection_flow(input_audio_buffer, ctx.session.turn_detection, ctx)
        if vad_event is not None:
            if isinstance(vad_event, InputAudioBufferSpeechStartedEvent):
                _handle_speech_started(ctx, vad_event, input_audio_buffer)
            else:
                ctx.pubsub.publish_nowait(vad_event)
            if isinstance(vad_event, InputAudioBufferSpeechStoppedEvent):
                item_id = vad_event.item_id
                audio_start = input_audio_buffer.vad_state.audio_start_ms
                speech_ms = vad_event.audio_end_ms - (
                    audio_start if audio_start is not None else vad_event.audio_end_ms
                )
                inspect_emit.emit(
                    "vad",
                    "stopped",
                    corr={"item_id": item_id},
                    audio_end_ms=vad_event.audio_end_ms,
                    speech_ms=max(0, speech_ms),
                )
                if (
                    ctx.state == ConversationState.GENERATING
                    and ctx.barge_in_task is not None
                    and not ctx.barge_in_task.done()
                ):
                    logger.info("Skipping commit: suppressed barge-in false start during GENERATING")
                else:
                    ctx.audio_buffers.rotate()
                    await commit_and_transcribe(ctx, item_id)


@event_router.register("input_audio_buffer.commit")
async def handle_input_audio_buffer_commit(ctx: SessionContext, _event: InputAudioBufferCommitEvent) -> None:
    input_audio_buffer = ctx.audio_buffers.current
    if input_audio_buffer.duration_ms < MIN_AUDIO_BUFFER_DURATION_MS:
        ctx.pubsub.publish_nowait(
            create_invalid_request_error(
                message=f"Error committing input audio buffer: buffer too small. Expected at least {MIN_AUDIO_BUFFER_DURATION_MS}ms of audio, but buffer only has {input_audio_buffer.duration_ms}.00ms of audio."
            )
        )
    else:
        item_id = input_audio_buffer.id
        ctx.audio_buffers.rotate()
        await commit_and_transcribe(ctx, item_id)


@event_router.register("input_audio_buffer.clear")
def handle_input_audio_buffer_clear(ctx: SessionContext, _event: InputAudioBufferClearEvent) -> None:
    ctx.audio_buffers.clear_current()
    ctx.pubsub.publish_nowait(InputAudioBufferClearedEvent())


async def _partial_transcription_loop(ctx: SessionContext, input_audio_buffer: InputAudioBuffer, item_id: str) -> None:
    interval = 0.5
    min_samples = 8000  # 500ms of audio at 16kHz
    min_new_samples = 4000  # 250ms minimum new audio before re-transcribing
    last_snapshot_size = 0
    cached_snapshot: NDArray[np.float32] | None = None
    while True:
        await asyncio.sleep(interval)
        if input_audio_buffer.size < min_samples:
            continue
        if input_audio_buffer.size - last_snapshot_size < min_new_samples:
            continue
        if ctx.partial_transcription_lock.locked():
            continue
        async with ctx.partial_transcription_lock:
            current_size = input_audio_buffer.size
            if cached_snapshot is None:
                audio_snapshot = input_audio_buffer.data.copy()
            else:
                new_samples = input_audio_buffer.data[last_snapshot_size:current_size].copy()
                audio_snapshot = np.concatenate([cached_snapshot, new_samples])
            cached_snapshot = audio_snapshot
            last_snapshot_size = current_size
            audio = Audio(audio_snapshot, sample_rate=SAMPLE_RATE)
            request = TranscriptionRequest(
                audio=audio,
                model=ctx.session.input_audio_transcription.model,
                language=ctx.session.input_audio_transcription.language,
                response_format="text",
                speech_segments=[],
                vad_options=VadOptions(min_silence_duration_ms=160, max_speech_duration_s=30),
                timestamp_granularities=["segment"],
            )
            try:
                result = await asyncio.to_thread(
                    ctx.stt_model_manager.handle_non_streaming_transcription_request, request
                )
                transcript = result[0] if isinstance(result, tuple) else result.text
                if transcript.strip():
                    inspect_emit.emit("stt", "partial", text=transcript)
                    ctx.pubsub.publish_nowait(
                        InputAudioBufferPartialTranscriptionEvent(item_id=item_id, transcript=transcript)
                    )
            except Exception:
                logger.exception("Partial transcription failed")


def _handle_speech_started(
    ctx: SessionContext, event: InputAudioBufferSpeechStartedEvent, input_audio_buffer: InputAudioBuffer
) -> None:
    if ctx.backfill_task is not None and not ctx.backfill_task.done():
        ctx.backfill_task.cancel()
        ctx.backfill_task = None

    if ctx.barge_in_task is not None and not ctx.barge_in_task.done():
        ctx.barge_in_task.cancel()
        ctx.barge_in_task = None
        inspect_emit.emit("bargein", "bargein_cancelled", corr=ctx.barge_in_corr, reason="second_speech_started")
        ctx.barge_in_corr = None

    suppress_for_client = False
    drain_in_flight = ctx.tts_drain_task is not None and not ctx.tts_drain_task.done()
    if ctx.state == ConversationState.GENERATING and (ctx.response_manager.is_active or drain_in_flight):
        delay_ms = ctx.session.turn_detection.barge_in_delay_ms if ctx.session.turn_detection else 0
        response_to_cancel = ctx.response_manager.active

        # Snapshot a stable corr for the bargein_pending / bargein_fired / bargein_cancelled
        # triple so the inspector can pair them. Once response.done fires (drain path),
        # the relay's response_id is cleared, and a bargein_fired emitted from
        # _commit_barge_in would land with response_id=None — leaving the inspector's
        # ingestBargein with an unmatched pending band labelled 'pending · Nms'.
        captured_response_id: str | None = None
        if response_to_cancel is not None:
            captured_response_id = response_to_cancel.id
        elif drain_in_flight:
            # The drain task's response is the one being interrupted; we don't keep its
            # id around explicitly, but the turn_id is captured.
            captured_response_id = None
        captured_turn_id = ctx.tts_drain_turn_id if drain_in_flight else inspect_emit.get_turn_id()
        ctx.barge_in_corr = {"response_id": captured_response_id, "turn_id": captured_turn_id}

        def _commit_barge_in() -> None:
            from speaches.realtime.response_event_router import (
                _inject_unheard_context,
                _split_heard_unheard,
            )

            corr = ctx.barge_in_corr or {}

            if ctx.response_manager.active is response_to_cancel and response_to_cancel is not None:
                logger.info(f"Barge-in confirmed after {delay_ms}ms delay: cancelling active response")
                inspect_emit.emit("bargein", "bargein_fired", corr=corr, delay_ms=delay_ms)
                ctx.barge_in_corr = None
                ctx.pubsub.publish_nowait(event)
                ctx.state = ConversationState.LISTENING
                ctx.response_manager.cancel_active()
                return

            if ctx.tts_drain_task is not None and not ctx.tts_drain_task.done():
                # Snapshot drain state before cancellation clears it.
                first_audio_wall_unix = ctx.tts_drain_first_audio_wall_unix
                phrases_delivered = ctx.tts_drain_phrases_delivered or []
                audio_duration_ms = ctx.tts_drain_audio_duration_ms or 0
                turn_id = ctx.tts_drain_turn_id
                drain_task = ctx.tts_drain_task

                played_ms = (
                    int((time.time() - first_audio_wall_unix) * 1000)
                    if first_audio_wall_unix is not None
                    else audio_duration_ms
                )
                played_ms = max(0, min(played_ms, audio_duration_ms))
                heard, unheard = _split_heard_unheard(phrases_delivered, played_ms)

                logger.info(
                    f"Barge-in during TTS drain after {delay_ms}ms: cancelling "
                    f"drain (played_ms={played_ms}/{audio_duration_ms})"
                )
                # Close the bargein-triggered VAD speech segment with a synthetic
                # stopped before we transition: the buffer is about to rotate to a
                # new item, and VAD on the old buffer will never fire stopped on
                # its own. Without this, the speech band rendered for the bargein
                # window stays open and gets stretched to the last event time.
                inspect_emit.emit(
                    "vad",
                    "stopped",
                    corr={"item_id": event.item_id},
                    audio_end_ms=input_audio_buffer.duration_ms,
                    speech_ms=max(0, input_audio_buffer.duration_ms - event.audio_start_ms),
                    reason="bargein_truncated",
                )
                ctx.state = ConversationState.LISTENING
                if unheard:
                    _inject_unheard_context(ctx.conversation, unheard)
                    inspect_emit.emit("turn", "bargein_context", corr=corr, heard=heard, unheard=unheard)
                inspect_emit.emit("bargein", "bargein_fired", corr=corr, delay_ms=delay_ms)
                ctx.barge_in_corr = None
                inspect_emit.emit(
                    "turn",
                    "turn_end",
                    corr={"turn_id": turn_id},
                    role="assistant",
                    status="truncated",
                    played_ms=played_ms,
                    audio_duration_ms=audio_duration_ms,
                )
                inspect_emit.set_turn_id(None)
                ctx.pubsub.publish_nowait(event)
                # Cancellation triggers the drain task's finally → clears ctx.tts_drain_*.
                drain_task.cancel()

        suppress_for_client = True
        if delay_ms > 0:

            async def _delayed_barge_in() -> None:
                await asyncio.sleep(delay_ms / 1000)
                if ctx.state == ConversationState.GENERATING:
                    _commit_barge_in()

            inspect_emit.emit("bargein", "bargein_pending", corr=ctx.barge_in_corr, delay_ms=delay_ms)
            ctx.barge_in_task = asyncio.create_task(_delayed_barge_in(), name="barge_in_delay")
            ctx.barge_in_task.add_done_callback(task_done_callback)
        else:
            logger.info("Barge-in detected: committing immediately (no delay)")
            _commit_barge_in()

    if not suppress_for_client:
        ctx.pubsub.publish_nowait(event)
        ctx.state = ConversationState.LISTENING

    # Pass item_id explicitly: during drain, response.done has already cleared
    # relay.item_id, so the implicit relay-state lookup would land None and the
    # inspector wouldn't be able to pair this confirmed_start with its eventual
    # vad:stopped (timeline.js:ingestVad keys on item_id).
    inspect_emit.emit(
        "vad",
        "confirmed_start",
        corr={"item_id": event.item_id},
        audio_start_ms=event.audio_start_ms,
    )

    if ctx.partial_transcription_task is not None and not ctx.partial_transcription_task.done():
        ctx.partial_transcription_task.cancel()

    ctx.partial_transcription_task = asyncio.create_task(
        _partial_transcription_loop(ctx, input_audio_buffer, event.item_id),
        name="partial_transcription",
    )
    ctx.partial_transcription_task.add_done_callback(task_done_callback)


@event_router.register("input_audio_buffer.speech_started")
def handle_speech_started_interruption(ctx: SessionContext, event: InputAudioBufferSpeechStartedEvent) -> None:
    pass


@event_router.register("input_audio_buffer.speech_stopped")
def handle_input_audio_buffer_speech_stopped(ctx: SessionContext, _event: InputAudioBufferSpeechStoppedEvent) -> None:
    was_suppressed_barge_in = False
    if ctx.barge_in_task is not None and not ctx.barge_in_task.done():
        logger.info("Speech stopped before barge-in delay expired, cancelling barge-in")
        ctx.barge_in_task.cancel()
        ctx.barge_in_task = None
        inspect_emit.emit("bargein", "bargein_cancelled", corr=ctx.barge_in_corr, reason="speech_stopped")
        ctx.barge_in_corr = None
        if ctx.state == ConversationState.GENERATING:
            was_suppressed_barge_in = True

    if not was_suppressed_barge_in:
        ctx.state = ConversationState.PROCESSING

    if ctx.partial_transcription_task is not None and not ctx.partial_transcription_task.done():
        ctx.partial_transcription_task.cancel()
    ctx.partial_transcription_task = None


async def commit_and_transcribe(ctx: SessionContext, item_id: str) -> None:
    event = InputAudioBufferCommittedEvent(
        previous_item_id=next(reversed(list(ctx.conversation.items)), None),  # FIXME
        item_id=item_id,
    )
    ctx.pubsub.publish_nowait(event)

    input_audio_buffer = ctx.audio_buffers.get(item_id)

    vs = input_audio_buffer.vad_state
    if vs.audio_start_ms is not None and vs.audio_end_ms is not None:
        speech_ms = vs.audio_end_ms - vs.audio_start_ms
        if speech_ms < MIN_AUDIO_BUFFER_DURATION_MS:
            logger.info(f"Skipping transcription: speech too short ({speech_ms}ms < {MIN_AUDIO_BUFFER_DURATION_MS}ms)")
            inspect_emit.emit(
                "stt",
                "rejected_short",
                speech_ms=speech_ms,
                buffer_duration_ms=input_audio_buffer.duration_ms,
            )
            if ctx.state == ConversationState.PROCESSING:
                ctx.state = ConversationState.IDLE
            return

    transcriber = InputAudioBufferTranscriber(
        pubsub=ctx.pubsub,
        stt_model_manager=ctx.stt_model_manager,
        input_audio_buffer=input_audio_buffer,
        session=ctx.session,
        conversation=ctx.conversation,
    )
    transcriber.start()
    assert transcriber.task is not None
    try:
        await transcriber.task
    except Exception:
        logger.exception("Transcription failed")
        ctx.pubsub.publish_nowait(create_server_error(message="Transcription failed"))
        if ctx.state == ConversationState.PROCESSING:
            ctx.state = ConversationState.IDLE
        return

    if ctx.session.turn_detection is None or not ctx.session.turn_detection.create_response:
        if ctx.state == ConversationState.PROCESSING:
            ctx.state = ConversationState.IDLE
        return

    if item_id not in ctx.conversation.items:
        if ctx.state == ConversationState.PROCESSING:
            ctx.state = ConversationState.IDLE
        return

    await ctx.response_manager.create_and_run(
        ctx=ctx,
        model=ctx.session.model,
        configuration=Response(
            conversation="auto", input=list(ctx.conversation.items.values()), **ctx.session.model_dump()
        ),
        conversation=ctx.conversation,
    )

    if ctx.session.audio_direct_to_llm:
        _start_backfill(ctx)


def _start_backfill(ctx: SessionContext) -> None:
    if ctx.backfill_task is not None and not ctx.backfill_task.done():
        return
    needs = [
        item
        for item in ctx.conversation.items.values()
        if item.type == "message"
        and item.content
        and len(item.content) == 1
        and item.content[0].type == "input_audio"
        and getattr(item.content[0], "audio", None)
        and not getattr(item.content[0], "transcript", None)
    ]
    if not needs:
        return
    ctx.backfill_task = asyncio.create_task(_backfill_transcripts(ctx, needs), name="backfill_transcripts")
    ctx.backfill_task.add_done_callback(task_done_callback)


async def _backfill_transcripts(ctx: SessionContext, items: list) -> None:
    import io
    import wave

    for item in items:
        if ctx.state != ConversationState.IDLE:
            logger.info("Backfill interrupted: state is no longer IDLE")
            return
        content = item.content[0]
        audio_b64 = content.audio
        if not audio_b64:
            continue
        try:
            wav_bytes = base64.b64decode(audio_b64)
            with io.BytesIO(wav_bytes) as buf, wave.open(buf, "rb") as wf:
                sr = wf.getframerate()
                frames = wf.readframes(wf.getnframes())
            pcm16 = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            if sr != SAMPLE_RATE:
                from speaches.audio import resample_audio_data

                pcm16 = resample_audio_data(pcm16, sr, SAMPLE_RATE)
            audio = Audio(pcm16, sample_rate=SAMPLE_RATE)
            request = TranscriptionRequest(
                audio=audio,
                model=ctx.session.input_audio_transcription.model,
                language=ctx.session.input_audio_transcription.language,
                response_format="text",
                speech_segments=[],
                vad_options=VadOptions(min_silence_duration_ms=160, max_speech_duration_s=30),
                timestamp_granularities=["segment"],
            )
            result = await asyncio.to_thread(ctx.stt_model_manager.handle_non_streaming_transcription_request, request)
            transcript = result[0] if isinstance(result, tuple) else result.text
            if transcript and transcript.strip():
                content.transcript = transcript.strip()
                inspect_emit.emit(
                    "stt",
                    "backfill",
                    item_id=item.id,
                    text=content.transcript,
                )
                logger.info(f"Backfilled transcript for {item.id}: {content.transcript!r}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"Backfill failed for item {item.id}")
