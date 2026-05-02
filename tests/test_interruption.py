import asyncio
import contextlib
from unittest.mock import MagicMock

import pytest

from speaches.realtime.conversation_event_router import Conversation
from speaches.realtime.input_audio_buffer import InputAudioBufferManager
from speaches.realtime.input_audio_buffer_event_router import (
    handle_input_audio_buffer_speech_stopped,
    handle_speech_started_interruption,
)
from speaches.realtime.pubsub import EventPubSub
from speaches.realtime.response_event_router import ResponseManager
from speaches.types.realtime import (
    ConversationItemContentAudio,
    ConversationItemContentInputText,
    ConversationItemMessage,
    ConversationState,
    InputAudioBufferSpeechStartedEvent,
    InputAudioBufferSpeechStoppedEvent,
)


def _drain_events(q: asyncio.Queue) -> list:
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


class FakeSessionContext:
    def __init__(self) -> None:
        self.pubsub = EventPubSub()
        self.conversation = Conversation(self.pubsub)
        self.state = ConversationState.IDLE
        self.session = MagicMock()
        self.session.turn_detection = None
        self.audio_buffers = InputAudioBufferManager(self.pubsub)
        self.response_manager = ResponseManager(completion_client=MagicMock(), pubsub=self.pubsub)
        self.tts_model_manager = MagicMock()
        self.stt_model_manager = MagicMock()
        self.partial_transcription_task = None
        self.partial_transcription_lock = asyncio.Lock()
        self.barge_in_task = None


def _make_speech_started_event(ctx: FakeSessionContext) -> InputAudioBufferSpeechStartedEvent:
    return InputAudioBufferSpeechStartedEvent(item_id=ctx.audio_buffers.current.id, audio_start_ms=0)


def _make_speech_stopped_event(ctx: FakeSessionContext, audio_end_ms: int = 1000) -> InputAudioBufferSpeechStoppedEvent:
    return InputAudioBufferSpeechStoppedEvent(item_id=ctx.audio_buffers.current.id, audio_end_ms=audio_end_ms)


async def _cancel_partial(ctx: FakeSessionContext) -> None:
    if ctx.partial_transcription_task is not None:
        ctx.partial_transcription_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ctx.partial_transcription_task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_state",
    [ConversationState.IDLE, ConversationState.PROCESSING],
    ids=["idle_to_listening", "processing_to_listening"],
)
async def test_speech_started_transitions_to_listening(start_state: ConversationState) -> None:
    ctx = FakeSessionContext()
    ctx.state = start_state

    event = _make_speech_started_event(ctx)
    handle_speech_started_interruption(ctx, event)
    await asyncio.sleep(0)

    assert ctx.state == ConversationState.LISTENING
    assert ctx.response_manager.active is None
    assert ctx.partial_transcription_task is not None
    await _cancel_partial(ctx)


@pytest.mark.asyncio
async def test_speech_started_listening_stays_listening() -> None:
    ctx = FakeSessionContext()
    ctx.state = ConversationState.LISTENING

    existing_task = asyncio.create_task(asyncio.sleep(10))
    ctx.partial_transcription_task = existing_task

    event = _make_speech_started_event(ctx)
    handle_speech_started_interruption(ctx, event)
    await asyncio.sleep(0)

    assert ctx.state == ConversationState.LISTENING
    assert existing_task.cancelled()
    assert ctx.partial_transcription_task is not None
    assert ctx.partial_transcription_task is not existing_task
    await _cancel_partial(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("barge_in_delay_ms", "expect_immediate_stop"),
    [(None, True), (100, False)],
    ids=["immediate_no_turn_detection", "delayed_with_turn_detection"],
)
async def test_speech_started_generating_barge_in(barge_in_delay_ms: int | None, expect_immediate_stop: bool) -> None:
    ctx = FakeSessionContext()
    ctx.state = ConversationState.GENERATING

    mock_response = MagicMock()
    mock_response.stop = MagicMock()
    ctx.response_manager._active = mock_response  # noqa: SLF001

    if barge_in_delay_ms is not None:
        td = MagicMock()
        td.barge_in_delay_ms = barge_in_delay_ms
        ctx.session.turn_detection = td

    event = _make_speech_started_event(ctx)
    handle_speech_started_interruption(ctx, event)
    await asyncio.sleep(0)

    if expect_immediate_stop:
        mock_response.stop.assert_called_once()
        assert ctx.barge_in_task is None
    else:
        mock_response.stop.assert_not_called()
        assert ctx.barge_in_task is not None
        await asyncio.sleep(barge_in_delay_ms / 1000 + 0.05)
        mock_response.stop.assert_called_once()

    assert ctx.state == ConversationState.LISTENING
    assert ctx.partial_transcription_task is not None
    await _cancel_partial(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "preexisting_task_attr",
    [None, "barge_in_task", "partial_transcription_task"],
    ids=[
        "listening_to_processing",
        "cancels_barge_in_task",
        "cancels_partial_transcription",
    ],
)
async def test_speech_stopped_from_listening(preexisting_task_attr: str | None) -> None:
    ctx = FakeSessionContext()
    ctx.state = ConversationState.LISTENING

    preexisting_task = None
    if preexisting_task_attr is not None:
        preexisting_task = asyncio.create_task(asyncio.sleep(10))
        setattr(ctx, preexisting_task_attr, preexisting_task)

    event = _make_speech_stopped_event(ctx)
    handle_input_audio_buffer_speech_stopped(ctx, event)
    await asyncio.sleep(0)

    assert ctx.state == ConversationState.PROCESSING
    assert ctx.partial_transcription_task is None
    if preexisting_task_attr == "barge_in_task":
        assert ctx.barge_in_task is None
        assert preexisting_task.cancelled()
    elif preexisting_task_attr == "partial_transcription_task":
        assert preexisting_task.cancelled()


def test_vad_does_not_produce_duplicate_speech_stopped() -> None:
    from speaches.realtime.input_audio_buffer_event_router import vad_detection_flow

    ctx = FakeSessionContext()
    td = MagicMock()
    td.threshold = 0.5
    td.silence_duration_ms = 300
    td.prefix_padding_ms = 300
    ctx.session.turn_detection = td

    buf = ctx.audio_buffers.current

    buf.vad_state.audio_start_ms = 100
    buf.vad_state.audio_end_ms = 900

    result = vad_detection_flow(buf, td, ctx)
    assert result is None


@pytest.mark.asyncio
async def test_delayed_barge_in_cancelled_by_speech_stopped() -> None:
    ctx = FakeSessionContext()
    ctx.state = ConversationState.GENERATING

    mock_response = MagicMock()
    mock_response.stop = MagicMock()
    ctx.response_manager._active = mock_response  # noqa: SLF001

    td = MagicMock()
    td.barge_in_delay_ms = 500
    ctx.session.turn_detection = td

    started_event = _make_speech_started_event(ctx)
    handle_speech_started_interruption(ctx, started_event)
    assert ctx.barge_in_task is not None

    # speech_stopped before delay expires -> should cancel the barge-in
    stopped_event = _make_speech_stopped_event(ctx)
    handle_input_audio_buffer_speech_stopped(ctx, stopped_event)

    assert ctx.barge_in_task is None
    mock_response.stop.assert_not_called()
    assert ctx.state == ConversationState.PROCESSING


@pytest.mark.asyncio
async def test_delayed_barge_in_skips_if_response_changed() -> None:
    ctx = FakeSessionContext()
    ctx.state = ConversationState.GENERATING

    old_response = MagicMock()
    old_response.stop = MagicMock()
    ctx.response_manager._active = old_response  # noqa: SLF001

    td = MagicMock()
    td.barge_in_delay_ms = 50
    ctx.session.turn_detection = td

    event = _make_speech_started_event(ctx)
    handle_speech_started_interruption(ctx, event)
    assert ctx.barge_in_task is not None

    new_response = MagicMock()
    ctx.response_manager._active = new_response  # noqa: SLF001

    await asyncio.sleep(0.1)

    old_response.stop.assert_not_called()
    new_response.stop.assert_not_called()

    await _cancel_partial(ctx)


@pytest.mark.asyncio
async def test_second_speech_started_cancels_pending_barge_in() -> None:
    ctx = FakeSessionContext()
    ctx.state = ConversationState.GENERATING

    mock_response = MagicMock()
    mock_response.stop = MagicMock()
    ctx.response_manager._active = mock_response  # noqa: SLF001

    td = MagicMock()
    td.barge_in_delay_ms = 500
    ctx.session.turn_detection = td

    event1 = _make_speech_started_event(ctx)
    handle_speech_started_interruption(ctx, event1)
    first_barge_in = ctx.barge_in_task
    assert first_barge_in is not None

    event2 = _make_speech_started_event(ctx)
    handle_speech_started_interruption(ctx, event2)
    await asyncio.sleep(0)

    assert first_barge_in.cancelled()
    assert ctx.barge_in_task is None

    await _cancel_partial(ctx)


@pytest.mark.parametrize(
    ("has_active_response", "expect_stop_called", "expect_error"),
    [(False, False, True), (True, True, False)],
    ids=["no_active_response_emits_error", "with_active_response_stops_it"],
)
def test_response_cancel(has_active_response: bool, expect_stop_called: bool, expect_error: bool) -> None:
    from openai.types.beta.realtime import ResponseCancelEvent

    from speaches.realtime.response_event_router import handle_response_cancel_event

    ctx = FakeSessionContext()
    mock_response = None
    if has_active_response:
        mock_response = MagicMock()
        mock_response.stop = MagicMock()
        ctx.response_manager._active = mock_response  # noqa: SLF001
    else:
        assert ctx.response_manager.active is None

    q = ctx.pubsub.subscribe()
    event = ResponseCancelEvent(type="response.cancel", event_id="evt_cancel")
    handle_response_cancel_event(ctx, event)

    if expect_stop_called:
        assert mock_response is not None
        mock_response.stop.assert_called_once()
    if expect_error:
        events = _drain_events(q)
        error_events = [e for e in events if e.type == "error"]
        assert len(error_events) == 1


def _make_assistant_audio_item(item_id: str, transcript: str = "text") -> ConversationItemMessage:
    content = ConversationItemContentAudio(audio="data", transcript=transcript)
    return ConversationItemMessage(id=item_id, role="assistant", content=[content], status="completed")


@pytest.mark.parametrize(
    ("setup_item", "item_id", "content_index", "expected_substring"),
    [
        (None, "nonexistent", 0, "does not exist"),
        ("user", "item_user_1", 0, "not an assistant message"),
        ("assistant", "item_a1", 5, "out of range"),
    ],
    ids=[
        "nonexistent_item",
        "non_assistant_message",
        "content_index_out_of_range",
    ],
)
def test_truncate_errors(setup_item: str | None, item_id: str, content_index: int, expected_substring: str) -> None:
    from openai.types.beta.realtime import ConversationItemTruncateEvent

    from speaches.realtime.conversation_event_router import handle_conversation_item_truncate_event

    ctx = FakeSessionContext()

    if setup_item == "user":
        item = ConversationItemMessage(
            id=item_id,
            role="user",
            content=[ConversationItemContentInputText(text="user text")],
            status="completed",
        )
        ctx.conversation.create_item(item)
    elif setup_item == "assistant":
        ctx.conversation.create_item(_make_assistant_audio_item(item_id))

    q = ctx.pubsub.subscribe()
    event = ConversationItemTruncateEvent(
        type="conversation.item.truncate",
        event_id="evt_trunc_err",
        item_id=item_id,
        content_index=content_index,
        audio_end_ms=500,
    )
    handle_conversation_item_truncate_event(ctx, event)

    events = _drain_events(q)
    error_events = [e for e in events if e.type == "error"]
    assert len(error_events) == 1
    assert expected_substring in error_events[0].error.message


def test_truncate_assistant_audio_message() -> None:
    from openai.types.beta.realtime import ConversationItemTruncateEvent

    from speaches.realtime.conversation_event_router import handle_conversation_item_truncate_event

    ctx = FakeSessionContext()

    original_transcript = "Hello, I am a long response that should be truncated at some point"
    content = ConversationItemContentAudio(audio="base64data", transcript=original_transcript)
    item = ConversationItemMessage(
        id="item_assistant_1",
        role="assistant",
        content=[content],
        status="completed",
    )
    ctx.conversation.create_item(item)

    q = ctx.pubsub.subscribe()

    event = ConversationItemTruncateEvent(
        type="conversation.item.truncate",
        event_id="evt_4",
        item_id="item_assistant_1",
        content_index=0,
        audio_end_ms=500,
    )
    handle_conversation_item_truncate_event(ctx, event)

    events = _drain_events(q)
    truncated_events = [e for e in events if e.type == "conversation.item.truncated"]
    assert len(truncated_events) == 1
    assert truncated_events[0].item_id == "item_assistant_1"
    assert truncated_events[0].audio_end_ms == 500

    assert len(content.transcript) < len(original_transcript)
    assert content.transcript == original_transcript[: len(content.transcript)]
