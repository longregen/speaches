from __future__ import annotations

import asyncio
import base64
import concurrent.futures
from contextlib import contextmanager
import logging
import threading
import time
from typing import TYPE_CHECKING

import numpy as np
import openai
from openai.types.beta.realtime.response_create_event import Response as OAIResponseConfig
from pydantic import BaseModel

from speaches import text_utils
from speaches.executors.shared.handler_protocol import SpeechHandler, SpeechRequest
from speaches.realtime.chat_utils import (
    create_completion_params,
    items_to_chat_messages,
)
from speaches.realtime.event_router import EventRouter
from speaches.realtime.session_event_router import unsupported_field_error
from speaches.realtime.utils import generate_response_id, task_done_callback
from speaches.text_utils import PhraseChunker
from speaches.types.realtime import (
    ConversationItemContentAudio,
    ConversationItemContentText,
    ConversationItemFunctionCall,
    ConversationItemMessage,
    ConversationState,
    RealtimeResponse,
    Response,
    ResponseAudioDeltaEvent,
    ResponseAudioDoneEvent,
    ResponseAudioTranscriptDeltaEvent,
    ResponseAudioTranscriptDoneEvent,
    ResponseCancelEvent,
    ResponseContentPartAddedEvent,
    ResponseContentPartDoneEvent,
    ResponseCreatedEvent,
    ResponseCreateEvent,
    ResponseDoneEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseTextDeltaEvent,
    ResponseTextDoneEvent,
    ResponseToolProgressEvent,
    ServerConversationItem,
    Tool,
    create_server_error,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from openai.resources.chat import AsyncCompletions
    from openai.types.chat import ChatCompletionChunk

    from speaches.audio import Audio
    from speaches.realtime.context import SessionContext
    from speaches.realtime.conversation_event_router import Conversation
    from speaches.realtime.pubsub import EventPubSub
logger = logging.getLogger(__name__)

event_router = EventRouter()

_RESPONSE_EXCLUDE_FIELDS = frozenset({"conversation", "input", "output_audio_format", "metadata"})
_tts_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tts")


def _split_heard_unheard(phrases_delivered: list[tuple[str, float, int]], played_ms: int) -> tuple[str, str]:
    cumulative_ms = 0
    heard_parts: list[str] = []
    unheard_parts: list[str] = []
    for text, _, audio_ms in phrases_delivered:
        if cumulative_ms + audio_ms <= played_ms or cumulative_ms < played_ms:
            heard_parts.append(text)
        else:
            unheard_parts.append(text)
        cumulative_ms += audio_ms
    return " ".join(heard_parts), " ".join(unheard_parts)


def _inject_unheard_context(conversation: Conversation, unheard: str) -> None:
    if not unheard:
        return
    from speaches.types.realtime import ConversationItemContentInputText, ConversationItemMessage

    ctx_msg = f'[system: your response was interrupted by the user. The following was NOT heard: "{unheard}"]'
    dev_item = ConversationItemMessage(
        role="user",
        content=[ConversationItemContentInputText(text=ctx_msg, type="input_text")],
        status="completed",
    )
    conversation.create_item(dev_item)


async def _emit_turn_end_at_deadline(ctx: SessionContext) -> None:
    from speaches.inspect import emit as inspect_emit

    try:
        deadline = ctx.tts_drain_deadline_wall or 0.0
        delay = deadline - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        inspect_emit.emit(
            "turn",
            "turn_end",
            corr={"turn_id": ctx.tts_drain_turn_id},
            role="assistant",
            status="completed",
            audio_duration_ms=ctx.tts_drain_audio_duration_ms,
        )
        inspect_emit.set_turn_id(None)
    finally:
        ctx.tts_drain_task = None
        ctx.tts_drain_deadline_wall = None
        ctx.tts_drain_first_audio_wall_unix = None
        ctx.tts_drain_turn_id = None
        ctx.tts_drain_audio_duration_ms = None
        ctx.tts_drain_phrases_delivered = None
        if ctx.state == ConversationState.GENERATING:
            ctx.state = ConversationState.IDLE


def _build_response_update(event_response: object) -> dict:
    update: dict = {}
    for field_name in OAIResponseConfig.model_fields:
        if field_name in _RESPONSE_EXCLUDE_FIELDS:
            continue
        value = getattr(event_response, field_name)
        if value is None:
            continue
        if field_name == "tools":
            value = [Tool.model_validate(t.model_dump()) for t in value]
        update[field_name] = value
    return update


class ChoiceDeltaAudio(BaseModel):
    id: str | None = None
    transcript: str | None = None
    data: str | None = None
    expires_at: int | None = None


class ResponseHandler:
    def __init__(
        self,
        *,
        completion_client: AsyncCompletions,
        tts_model_manager: SpeechHandler,
        model: str,
        speech_model: str,
        configuration: Response,
        conversation: Conversation,
        pubsub: EventPubSub,
        no_response_token: str | None = None,
        audio_direct_prompt: str | None = None,
    ) -> None:
        self.id = generate_response_id()
        self.completion_client = completion_client
        self.tts_model_manager = tts_model_manager
        self.model = model  # NOTE: `Response` doesn't have a `model` field
        self.speech_model = speech_model
        self.configuration = configuration
        self.conversation = conversation
        self.pubsub = pubsub
        self.no_response_token = no_response_token
        self.audio_direct_prompt = audio_direct_prompt
        self.response = RealtimeResponse(
            id=self.id,
            status="incomplete",
            output=[],
            modalities=configuration.modalities,
        )
        self.task: asyncio.Task[None] | None = None
        self._cancelled = False
        self.dismissed = False
        self.pre_response_item_id: str | None = None
        self.audio_duration_ms: int = 0
        self._first_audio_wall: float | None = None  # time.perf_counter, monotonic
        self._first_audio_wall_unix: float | None = None  # time.time, for absolute drain deadline
        self._phrases_delivered: list[tuple[str, float, int]] = []

    @contextmanager
    def add_output_item[T: ServerConversationItem](self, item: T) -> Generator[T, None, None]:
        self.response.output.append(item)
        self.pubsub.publish_nowait(ResponseOutputItemAddedEvent(response_id=self.id, item=item))
        try:
            yield item
        finally:
            if self._cancelled:
                item.status = "incomplete"
            else:
                item.status = "completed"
            self.pubsub.publish_nowait(ResponseOutputItemDoneEvent(response_id=self.id, item=item))

    @contextmanager
    def add_item_content[T: ConversationItemContentText | ConversationItemContentAudio](
        self, item: ConversationItemMessage, content: T
    ) -> Generator[T, None, None]:
        item.content.append(content)
        self.pubsub.publish_nowait(
            ResponseContentPartAddedEvent(response_id=self.id, item_id=item.id, part=content.to_part())  # ty: ignore[invalid-argument-type]
        )
        yield content
        self.pubsub.publish_nowait(
            ResponseContentPartDoneEvent(response_id=self.id, item_id=item.id, part=content.to_part())  # ty: ignore[invalid-argument-type]
        )

    async def conversation_item_message_text_handler(self, chunk_stream: AsyncGenerator[ChatCompletionChunk]) -> None:
        from speaches.inspect import emit as inspect_emit

        with self.add_output_item(ConversationItemMessage(role="assistant", status="incomplete", content=[])) as item:
            self.conversation.create_item(item)

            with self.add_item_content(item, ConversationItemContentText(text="")) as content:
                async for chunk in chunk_stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]

                    if choice.delta.content is not None:
                        content.text += choice.delta.content
                        inspect_emit.emit(
                            "llm",
                            "chunk",
                            delta=choice.delta.content,
                            text_so_far_len=len(content.text),
                        )
                        self.pubsub.publish_nowait(
                            ResponseTextDeltaEvent(item_id=item.id, response_id=self.id, delta=choice.delta.content)
                        )

                self.pubsub.publish_nowait(
                    ResponseTextDoneEvent(item_id=item.id, response_id=self.id, text=content.text)
                )

    async def conversation_item_message_audio_handler(self, chunk_stream: AsyncGenerator[ChatCompletionChunk]) -> None:
        from speaches.inspect import emit as inspect_emit

        with self.add_output_item(ConversationItemMessage(role="assistant", status="incomplete", content=[])) as item:
            self.conversation.create_item(item)

            with self.add_item_content(item, ConversationItemContentAudio(audio="", transcript="")) as content:
                sentence_chunker = PhraseChunker()

                async def process_text_stream() -> None:
                    try:
                        async for chunk in chunk_stream:
                            if not chunk.choices:
                                continue
                            choice = chunk.choices[0]

                            audio = getattr(choice.delta, "audio", None)
                            if audio is not None:
                                assert isinstance(audio, dict), chunk
                                parsed_audio = ChoiceDeltaAudio(**audio)
                                if parsed_audio.transcript is not None:
                                    content.transcript += parsed_audio.transcript
                                    inspect_emit.emit(
                                        "llm",
                                        "chunk",
                                        delta=parsed_audio.transcript,
                                        text_so_far_len=len(content.transcript),
                                        source="native_audio_transcript",
                                    )
                                    self.pubsub.publish_nowait(
                                        ResponseAudioTranscriptDeltaEvent(
                                            item_id=item.id, response_id=self.id, delta=parsed_audio.transcript
                                        )
                                    )
                                if parsed_audio.data is not None:
                                    self.pubsub.publish_nowait(
                                        ResponseAudioDeltaEvent(
                                            item_id=item.id, response_id=self.id, delta=parsed_audio.data
                                        )
                                    )
                                continue

                            if choice.delta.content is not None:
                                content.transcript += choice.delta.content
                                inspect_emit.emit(
                                    "llm",
                                    "chunk",
                                    delta=choice.delta.content,
                                    text_so_far_len=len(content.transcript),
                                )
                                self.pubsub.publish_nowait(
                                    ResponseAudioTranscriptDeltaEvent(
                                        item_id=item.id, response_id=self.id, delta=choice.delta.content
                                    )
                                )
                                sentence_chunker.add_token(choice.delta.content)
                    finally:
                        sentence_chunker.close()
                        inspect_emit.emit("llm", "done", elapsed_ms=int((time.perf_counter() - self._llm_t_req) * 1000))

                async def process_tts_stream() -> None:
                    phrase_idx = 0
                    async for sentence in sentence_chunker:
                        sentence_clean = text_utils.clean_for_tts(sentence)
                        if not sentence_clean:
                            continue
                        phrase_id = f"{self.id}:{phrase_idx}"
                        phrase_idx += 1
                        inspect_emit.set_phrase_id(phrase_id)
                        inspect_emit.emit(
                            "response",
                            "phrase_boundary",
                            phrase_id=phrase_id,
                            text=sentence_clean,
                            reason="sentence_end",
                        )
                        request = SpeechRequest(
                            model=self.speech_model,
                            voice=self.configuration.voice,
                            text=sentence_clean,
                            speed=1.0,
                        )
                        inspect_emit.emit(
                            "tts_req",
                            "phrase_sent",
                            phrase_id=phrase_id,
                            model=self.speech_model,
                            voice=self.configuration.voice,
                            executor=type(self.tts_model_manager).__name__,
                            text=sentence_clean,
                            text_raw=sentence,
                        )
                        chunk_idx = 0
                        phrase_audio_ms = 0
                        phrase_t0 = time.perf_counter()
                        tts_err: str | None = None
                        try:
                            async for audio in self._stream_tts_chunks(request):
                                audio.resample(24000)
                                audio_bytes = audio.as_bytes()
                                audio_samples = len(audio_bytes) // 2
                                ms_audio = (audio_samples * 1000) // 24000
                                self.audio_duration_ms += ms_audio
                                phrase_audio_ms += ms_audio
                                # Capture first-chunk wall times up front so the
                                # audio_level emit below can re-stamp to the
                                # predicted playback wall (Kokoro outpaces realtime
                                # by ~10-50x; the chunks render at first_audio_wall +
                                # cumulative_ms, so the sparkline must too).
                                if chunk_idx == 0 and self._first_audio_wall is None:
                                    self._first_audio_wall = time.perf_counter()
                                    self._first_audio_wall_unix = time.time()
                                playback_wall: float | None = None
                                if self._first_audio_wall_unix is not None:
                                    # Sample lands at the midpoint of this chunk's
                                    # playback window.
                                    playback_wall = (
                                        self._first_audio_wall_unix + (self.audio_duration_ms - ms_audio / 2) / 1000
                                    )
                                from speaches.inspect import registry as _reg

                                _sid = inspect_emit.session_id_ctx.get()
                                if _sid:
                                    _c = _reg.get_ctx(_sid)
                                    if _c is not None and _c.audio_store is not None:
                                        _c.audio_store.append_tts_out(audio_bytes)
                                if audio_samples > 0:
                                    pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                                    rms = float(np.sqrt(np.mean(pcm * pcm))) if pcm.size else 0.0
                                    inspect_emit.emit(
                                        "audio_level",
                                        "sample",
                                        ts_wall_override=playback_wall,
                                        channel="tts_out",
                                        rms=rms,
                                        window_ms=ms_audio,
                                    )
                                if chunk_idx == 0:
                                    inspect_emit.emit(
                                        "tts_chunk",
                                        "chunk",
                                        phrase_id=phrase_id,
                                        chunk_idx=0,
                                        pcm_samples=audio_samples,
                                        ms_audio=ms_audio,
                                        cumulative_ms=phrase_audio_ms,
                                        first_chunk_ms=int((self._first_audio_wall - phrase_t0) * 1000),
                                    )
                                else:
                                    inspect_emit.emit(
                                        "tts_chunk",
                                        "chunk",
                                        phrase_id=phrase_id,
                                        chunk_idx=chunk_idx,
                                        pcm_samples=audio_samples,
                                        ms_audio=ms_audio,
                                        cumulative_ms=phrase_audio_ms,
                                    )
                                chunk_idx += 1
                                audio_data = base64.b64encode(audio_bytes).decode("utf-8")
                                self.pubsub.publish_nowait(
                                    ResponseAudioDeltaEvent(item_id=item.id, response_id=self.id, delta=audio_data)
                                )
                        except Exception as e:
                            tts_err = str(e)
                            inspect_emit.emit(
                                "tts_req",
                                "error",
                                phrase_id=phrase_id,
                                voice=self.configuration.voice,
                                model=self.speech_model,
                                error=tts_err,
                            )
                            raise
                        finally:
                            if tts_err is None:
                                inspect_emit.emit(
                                    "tts_req",
                                    "phrase_rendered",
                                    phrase_id=phrase_id,
                                    ms_audio=phrase_audio_ms,
                                    chunks=chunk_idx,
                                    total_ms=int((time.perf_counter() - phrase_t0) * 1000),
                                )
                                self._phrases_delivered.append((sentence_clean, phrase_t0, phrase_audio_ms))
                            inspect_emit.set_phrase_id(None)

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(process_text_stream())
                    tg.create_task(process_tts_stream())

                self.pubsub.publish_nowait(
                    ResponseAudioDoneEvent(
                        item_id=item.id, response_id=self.id, audio_duration_ms=self.audio_duration_ms
                    )
                )
                self.pubsub.publish_nowait(
                    ResponseAudioTranscriptDoneEvent(
                        item_id=item.id, response_id=self.id, transcript=content.transcript
                    )
                )

    async def conversation_item_function_call_handler(self, chunk_stream: AsyncGenerator[ChatCompletionChunk]) -> None:
        async for chunk in chunk_stream:
            if chunk.choices:
                break
        else:
            return

        assert len(chunk.choices) == 1, chunk
        choice = chunk.choices[0]
        assert choice.delta.tool_calls is not None and len(choice.delta.tool_calls) == 1, chunk
        tool_call = choice.delta.tool_calls[0]
        assert (
            tool_call.id is not None
            and tool_call.function is not None
            and tool_call.function.name is not None
            and tool_call.function.arguments is not None
        ), chunk
        item = ConversationItemFunctionCall(
            status="incomplete",
            call_id=tool_call.id,
            name=tool_call.function.name,
            arguments=tool_call.function.arguments,
        )
        assert item.call_id is not None and item.arguments is not None and item.name is not None, item

        with self.add_output_item(item):
            self.conversation.create_item(item)

            async for chunk in chunk_stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]

                if choice.delta.tool_calls is not None:
                    assert len(choice.delta.tool_calls) == 1, chunk
                    tool_call = choice.delta.tool_calls[0]
                    assert tool_call.function is not None and tool_call.function.arguments is not None, chunk
                    self.pubsub.publish_nowait(
                        ResponseFunctionCallArgumentsDeltaEvent(
                            item_id=item.id,
                            response_id=self.id,
                            call_id=item.call_id,
                            delta=tool_call.function.arguments,
                        )
                    )
                    item.arguments += tool_call.function.arguments

            self.pubsub.publish_nowait(
                ResponseFunctionCallArgumentsDoneEvent(
                    name=item.name, arguments=item.arguments, call_id=item.call_id, item_id=item.id, response_id=self.id
                )
            )

    async def _stream_tts_chunks(self, request: SpeechRequest) -> AsyncGenerator[Audio, None]:
        loop = asyncio.get_running_loop()
        # Backpressure: when the WebSocket consumer is slow, the queue fills and the
        # producer thread blocks on put_nowait, throttling TTS generation.
        q: asyncio.Queue[Audio | BaseException | None] = asyncio.Queue(maxsize=4)
        stop_event = threading.Event()

        def _produce() -> None:
            chunks_produced = 0
            # NOTE (unverified hypothesis): if the event loop closes while the thread is
            # still producing, call_soon_threadsafe raises RuntimeError and we drop the
            # remaining chunks with a warning log. Graceful on shutdown; not a reproduced
            # bug, but worth revisiting if users report truncated TTS during session teardown.
            try:
                for chunk in self.tts_model_manager.handle_speech_request(request):
                    if stop_event.is_set():
                        logger.debug("TTS producer: stop signal received after %d chunks", chunks_produced)
                        break
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, chunk)
                        chunks_produced += 1
                    except asyncio.QueueFull:
                        logger.warning("TTS producer: queue full (maxsize=%d), dropping chunk", q.maxsize)
                    except RuntimeError:
                        logger.warning("TTS producer: event loop closed, aborting after %d chunks", chunks_produced)
                        break
            except Exception as e:
                logger.exception("TTS producer failed after %d chunks", chunks_produced)
                try:
                    loop.call_soon_threadsafe(q.put_nowait, e)
                except RuntimeError:
                    logger.warning("TTS producer: cannot propagate error, event loop closed")
            finally:
                try:
                    loop.call_soon_threadsafe(q.put_nowait, None)
                except RuntimeError:
                    logger.warning("TTS producer: cannot signal completion, event loop closed")

        loop.run_in_executor(_tts_executor, _produce)
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            stop_event.set()

    def _check_dismissed(self) -> None:
        if not self.no_response_token:
            return
        last_item = next(reversed(self.conversation.items.values()), None)
        if not isinstance(last_item, ConversationItemMessage) or last_item.role != "assistant":
            return
        for content in last_item.content:
            transcript = getattr(content, "transcript", None) or getattr(content, "text", None) or ""
            if transcript.strip() == self.no_response_token:
                self.dismissed = True
                logger.info(f"Response dismissed: LLM returned no-response token {self.no_response_token!r}")
                return

    async def generate_response(self) -> None:
        from speaches.inspect import emit as inspect_emit

        self.pre_response_item_id = next(reversed(list(self.conversation.items)), None)
        t_req = time.perf_counter()
        self._llm_t_req = t_req
        messages = items_to_chat_messages(list(self.conversation.items.values()), self.audio_direct_prompt)
        inspect_emit.emit(
            "llm",
            "request",
            model=self.model,
            messages=[dict(m) for m in messages],
        )
        try:
            completion_params = create_completion_params(self.model, messages, self.configuration)
            raw_chunk_stream = await self.completion_client.create(**completion_params)

            # Upstream emits agentic tool progress as a top-level `tool_progress`
            # field on chunks with empty `choices`. The OpenAI client preserves
            # unknown fields in `chunk.model_extra`. Each emit is cumulative
            # (full historical-tools list), so we dedup per-tool/lifecycle to
            # avoid flooding the inspector `tool` lane.
            tool_state: dict[str, dict[str, bool]] = {}

            async def _forward_tool_progress(stream):  # type: ignore[no-untyped-def]
                async for ch in stream:
                    extra = getattr(ch, "model_extra", None) or {}
                    progress = extra.get("tool_progress")
                    if progress:
                        try:
                            self.pubsub.publish_nowait(
                                ResponseToolProgressEvent(
                                    response_id=self.id,
                                    tools=list(progress),
                                )
                            )
                        except Exception:  # pragma: no cover — never block the stream
                            logger.exception("failed to publish tool_progress")
                        for tool in progress:
                            try:
                                name = tool.get("name") if isinstance(tool, dict) else None
                                if not name:
                                    continue
                                status = tool.get("status")
                                result = tool.get("result")
                                error = tool.get("error")
                                summary = tool.get("summary")
                                summarizing = tool.get("summarizing")
                                args = tool.get("args")
                                state = tool_state.setdefault(
                                    name,
                                    {
                                        "use_token": False,
                                        "result": False,
                                        "start_summary": False,
                                        "summary": False,
                                    },
                                )
                                if not state["use_token"]:
                                    state["use_token"] = True
                                    inspect_emit.emit(
                                        "tool",
                                        "use_token",
                                        name=name,
                                        args=args,
                                    )
                                if not state["result"] and (status == "done" or result is not None or error):
                                    state["result"] = True
                                    inspect_emit.emit(
                                        "tool",
                                        "result",
                                        name=name,
                                        result=result,
                                        error=bool(error),
                                    )
                                if not state["start_summary"] and summarizing:
                                    state["start_summary"] = True
                                    inspect_emit.emit(
                                        "tool",
                                        "start_summary",
                                        name=name,
                                    )
                                if not state["summary"] and summary:
                                    state["summary"] = True
                                    inspect_emit.emit(
                                        "tool",
                                        "summary",
                                        name=name,
                                        summary=summary,
                                    )
                            except Exception:  # pragma: no cover
                                logger.exception("failed to emit tool inspector event")
                    yield ch

            chunk_stream = _forward_tool_progress(raw_chunk_stream)

            first: ChatCompletionChunk | None = None
            async for chunk in chunk_stream:
                if chunk.choices:
                    first = chunk
                    break
            if first is None:
                inspect_emit.emit("llm", "done", elapsed_ms=int((time.perf_counter() - t_req) * 1000))
                return

            ttft_ms = int((time.perf_counter() - t_req) * 1000)
            inspect_emit.emit("llm", "first_token", ttft_ms=ttft_ms, elapsed_ms=ttft_ms)

            is_tool_call = first.choices[0].delta.tool_calls is not None

            async def merge_chunks_and_chunk_stream(
                *chunks: ChatCompletionChunk, chunk_stream: openai.AsyncStream[ChatCompletionChunk]
            ) -> AsyncGenerator[ChatCompletionChunk]:
                for chunk in chunks:
                    yield chunk
                async for chunk in chunk_stream:
                    yield chunk

            if is_tool_call:
                await self.conversation_item_function_call_handler(
                    merge_chunks_and_chunk_stream(first, chunk_stream=chunk_stream)
                )
                inspect_emit.emit("llm", "done", elapsed_ms=int((time.perf_counter() - t_req) * 1000))
            else:
                if self.configuration.modalities == ["text"]:
                    handler = self.conversation_item_message_text_handler
                else:
                    handler = self.conversation_item_message_audio_handler
                await handler(merge_chunks_and_chunk_stream(first, chunk_stream=chunk_stream))
                self._check_dismissed()
                if self.configuration.modalities == ["text"]:
                    inspect_emit.emit("llm", "done", elapsed_ms=int((time.perf_counter() - t_req) * 1000))
        except asyncio.CancelledError:
            inspect_emit.emit(
                "llm",
                "cancelled",
                elapsed_ms=int((time.perf_counter() - t_req) * 1000),
            )
            raise
        except openai.APIError as e:
            logger.exception("Error while generating response")
            inspect_emit.emit(
                "llm",
                "error",
                error=f"{type(e).__name__}: {e.message}",
                elapsed_ms=int((time.perf_counter() - t_req) * 1000),
            )
            self.pubsub.publish_nowait(create_server_error(message=f"{type(e).__name__}: {e.message}"))
            raise

    def start(self) -> None:
        assert self.task is None
        self.task = asyncio.create_task(self.generate_response())
        self.task.add_done_callback(task_done_callback)

    def estimate_heard_text(self) -> tuple[str, str]:
        if not self._phrases_delivered or self._first_audio_wall is None:
            full = "".join(t for t, _, _ in self._phrases_delivered)
            return "", full
        played_ms = int((time.perf_counter() - self._first_audio_wall) * 1000)
        return _split_heard_unheard(self._phrases_delivered, played_ms)

    def played_ms_so_far(self) -> int:
        if self._first_audio_wall is None:
            return 0
        return int((time.perf_counter() - self._first_audio_wall) * 1000)

    def stop(self) -> None:
        self._cancelled = True
        if self.task is not None and not self.task.done():
            self.task.cancel()


class ResponseManager:
    def __init__(
        self,
        *,
        completion_client: AsyncCompletions,
        pubsub: EventPubSub,
    ) -> None:
        self._completion_client = completion_client
        self._pubsub = pubsub
        self._active: ResponseHandler | None = None

    @property
    def is_active(self) -> bool:
        return self._active is not None

    @property
    def active(self) -> ResponseHandler | None:
        return self._active

    def cancel_active(self) -> None:
        if self._active is not None:
            self._active.stop()
            self._active = None

    async def create_and_run(
        self,
        *,
        ctx: SessionContext,
        model: str,
        configuration: Response,
        conversation: Conversation,
    ) -> None:
        from speaches.inspect import emit as inspect_emit

        self.cancel_active()
        use_audio_direct = ctx.session.audio_direct_to_llm
        effective_model = ctx.session.audio_direct_model if use_audio_direct else model
        effective_client = ctx.upstream_completion_client if use_audio_direct else self._completion_client
        handler = ResponseHandler(
            completion_client=effective_client,
            tts_model_manager=ctx.tts_model_manager,
            model=effective_model,
            speech_model=ctx.session.speech_model,
            configuration=configuration,
            conversation=conversation,
            pubsub=self._pubsub,
            no_response_token=ctx.session.no_response_token,
            audio_direct_prompt=ctx.session.audio_direct_prompt if ctx.session.audio_direct_to_llm else None,
        )
        self._active = handler
        self._pubsub.publish_nowait(ResponseCreatedEvent(response=handler.response))
        ctx.state = ConversationState.GENERATING

        inspect_emit.set_response_id(handler.id)
        inspect_emit.emit("response", "plan_start", trigger="create_and_run", model=effective_model)
        inspect_emit.emit("turn", "turn_start", role="assistant")

        handler.start()
        assert handler.task is not None
        status: str = "completed"
        err: str | None = None
        try:
            await handler.task
        except asyncio.CancelledError:
            logger.info(f"Response {handler.id} was cancelled")
            handler.response.status = "cancelled"
            status = "cancelled"
            heard, unheard = handler.estimate_heard_text()
            if heard or unheard:
                _inject_unheard_context(conversation, unheard)
                if unheard:
                    inspect_emit.emit("turn", "bargein_context", heard=heard, unheard=unheard)
            self._pubsub.publish_nowait(ResponseDoneEvent(response=handler.response))
        except Exception as e:
            logger.exception(f"Response {handler.id} failed")
            handler.response.status = "failed"
            status = "failed"
            err = str(e)
            self._pubsub.publish_nowait(ResponseDoneEvent(response=handler.response))
        else:
            handler.response.status = "completed"
            self._pubsub.publish_nowait(ResponseDoneEvent(response=handler.response))
            if handler.dismissed:
                for item in handler.response.output:
                    conversation.delete_item(item.id)
                if handler.pre_response_item_id:
                    conversation.delete_item(handler.pre_response_item_id)
        finally:
            done_payload: dict = {"status": status}
            if handler.audio_duration_ms:
                done_payload["total_audio_ms"] = handler.audio_duration_ms
            if err is not None:
                done_payload["error"] = err

            if status == "cancelled":
                done_payload["played_ms"] = handler.played_ms_so_far()
                inspect_emit.emit("response", "cancelled", **done_payload)
                inspect_emit.emit(
                    "turn",
                    "turn_end",
                    role="assistant",
                    status="cancelled",
                    played_ms=handler.played_ms_so_far(),
                )
                inspect_emit.set_turn_id(None)
            else:
                inspect_emit.emit("response", "done", **done_payload)
                if (
                    status == "completed"
                    and handler.audio_duration_ms > 0
                    and handler._first_audio_wall_unix is not None  # noqa: SLF001
                    and not handler.dismissed
                ):
                    # Defer turn_end to the predicted end of TTS playback.
                    first_audio_wall_unix = handler._first_audio_wall_unix  # noqa: SLF001
                    deadline = first_audio_wall_unix + handler.audio_duration_ms / 1000
                    captured_turn_id = inspect_emit.get_turn_id()
                    ctx.tts_drain_deadline_wall = deadline
                    ctx.tts_drain_first_audio_wall_unix = first_audio_wall_unix
                    ctx.tts_drain_turn_id = captured_turn_id
                    ctx.tts_drain_audio_duration_ms = handler.audio_duration_ms
                    ctx.tts_drain_phrases_delivered = list(handler._phrases_delivered)  # noqa: SLF001
                    ctx.tts_drain_task = asyncio.create_task(_emit_turn_end_at_deadline(ctx), name="tts_drain")
                    ctx.tts_drain_task.add_done_callback(task_done_callback)
                else:
                    inspect_emit.emit("turn", "turn_end", role="assistant", status=status)
                    inspect_emit.set_turn_id(None)

            inspect_emit.set_response_id(None)
            inspect_emit.set_item_id(None)

            if self._active is handler:
                self._active = None
                if ctx.state == ConversationState.GENERATING and ctx.tts_drain_task is None:
                    ctx.state = ConversationState.IDLE


@event_router.register("response.create")
async def handle_response_create_event(ctx: SessionContext, event: ResponseCreateEvent) -> None:
    configuration = Response(
        conversation="auto", input=list(ctx.conversation.items.values()), **ctx.session.model_dump()
    )
    if event.response is not None:
        if event.response.conversation is not None:
            ctx.pubsub.publish_nowait(unsupported_field_error("response.conversation"))
        if event.response.input is not None:
            ctx.pubsub.publish_nowait(unsupported_field_error("response.input"))
        if (
            event.response.audio is not None
            and event.response.audio.output is not None
            and event.response.audio.output.format is not None
        ):
            ctx.pubsub.publish_nowait(unsupported_field_error("response.output_audio_format"))
        if event.response.metadata is not None:
            ctx.pubsub.publish_nowait(unsupported_field_error("response.metadata"))

        configuration_update = _build_response_update(event.response)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Applying response configuration update: {configuration_update}")
            logger.debug(f"Response configuration before update: {configuration}")
        configuration = configuration.model_copy(update=configuration_update)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Response configuration after update: {configuration}")

    await ctx.response_manager.create_and_run(
        ctx=ctx,
        model=ctx.session.model,
        configuration=configuration,
        conversation=ctx.conversation,
    )


@event_router.register("response.cancel")
def handle_response_cancel_event(ctx: SessionContext, event: ResponseCancelEvent) -> None:
    if not ctx.response_manager.is_active:
        ctx.pubsub.publish_nowait(create_server_error("No active response to cancel.", event_id=event.event_id))
        return
    ctx.response_manager.cancel_active()
