import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketException,
    status,
)
from openai import AsyncOpenAI

from speaches.dependencies import (
    ConfigDependency,
    ExecutorRegistryDependency,
)
from speaches.inspect import registry as inspect_registry
from speaches.inspect.audio_store import AudioStore
from speaches.inspect.emit import emit as inspect_emit
from speaches.inspect.emit import session_id_ctx
from speaches.inspect.relay import InspectorRelay
from speaches.realtime.context import SessionContext
from speaches.realtime.conversation_event_router import event_router as conversation_event_router
from speaches.realtime.event_router import EventRouter
from speaches.realtime.input_audio_buffer_event_router import (
    event_router as input_audio_buffer_event_router,
)
from speaches.realtime.message_manager import WsServerMessageManager
from speaches.realtime.response_event_router import event_router as response_event_router
from speaches.realtime.session import OPENAI_REALTIME_SESSION_DURATION_SECONDS, create_session_object_configuration
from speaches.realtime.session_event_router import event_router as session_event_router
from speaches.realtime.utils import verify_websocket_api_key
from speaches.types.inspect import DEDICATED_LANE_EVENT_TYPES
from speaches.types.realtime import CLIENT_EVENT_TYPES, Event, SessionCreatedEvent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

event_router = EventRouter()
event_router.include_router(conversation_event_router)
event_router.include_router(input_audio_buffer_event_router)
event_router.include_router(response_event_router)
event_router.include_router(session_event_router)


async def _safe_dispatch(ctx: SessionContext, event: Event) -> None:
    try:
        await event_router.dispatch(ctx, event)
    except Exception:
        logger.exception(f"Failed to handle {event.type} event")


async def event_listener(ctx: SessionContext) -> None:
    try:
        async with asyncio.TaskGroup() as tg:
            async for event in ctx.pubsub.poll():
                tg.create_task(_safe_dispatch(ctx, event))
    except asyncio.CancelledError:
        logger.info("Event listener task cancelled")
        raise
    finally:
        logger.info("Event listener task finished")


async def inspect_pubsub_mirror(ctx: SessionContext) -> None:
    if ctx.inspector is None:
        return
    try:
        async for event in ctx.pubsub.poll():
            etype = getattr(event, "type", None)
            if etype is None or etype in DEDICATED_LANE_EVENT_TYPES:
                continue
            direction = "in" if etype in CLIENT_EVENT_TYPES else "out"
            try:
                body = event.model_dump_json()
            except Exception:  # noqa: BLE001
                body = ""
            inspect_emit("wire", direction, event_type=etype, bytes=len(body))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("inspect_pubsub_mirror failed")


@router.websocket("/v1/realtime")
async def realtime(
    ws: WebSocket,
    model: str,
    config: ConfigDependency,
    executor_registry: ExecutorRegistryDependency,
    intent: str = "conversation",
    language: str | None = None,
    transcription_model: str | None = None,
    instructions: str | None = None,
) -> None:
    """OpenAI Realtime API compatible WebSocket endpoint.

    According to OpenAI Realtime API specification:
    - 'model' parameter is the conversation model (e.g., gpt-4o-realtime-preview)
    - 'transcription_model' parameter is for input_audio_transcription.model
    - 'intent' parameter controls session behavior (conversation vs transcription)

    References:
    - https://platform.openai.com/docs/guides/realtime/overview
    - https://platform.openai.com/docs/api-reference/realtime-server-events/session/update

    """
    # Manually verify WebSocket authentication before accepting connection
    try:
        await verify_websocket_api_key(ws, config)
    except WebSocketException:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication failed")
        return

    await ws.accept()
    logger.info(f"Accepted websocket connection with intent: {intent}")

    if config.loopback_host_url is not None:
        loopback_base_url = f"{config.loopback_host_url}/v1"
    else:
        host = "127.0.0.1" if config.host in ("0.0.0.0", "::") else config.host
        loopback_base_url = f"http://{host}:{config.port}/v1"
    completion_client = AsyncOpenAI(
        base_url=loopback_base_url,
        api_key=config.api_key.get_secret_value() if config.api_key else "cant-be-empty",
        max_retries=0,
    ).chat.completions

    session = create_session_object_configuration(
        model, intent, language, transcription_model, config.default_realtime_stt_model
    )
    if instructions is not None:
        session.instructions = instructions
    ctx = SessionContext(
        executor_registry=executor_registry,
        completion_client=completion_client,
        vad_model_manager=executor_registry.vad.model_manager,
        vad_model_id=executor_registry.vad_model_id,
        session=session,
    )
    session_id_ctx.set(ctx.session.id)
    session_dir = Path(config.inspect_session_dir).expanduser()  # noqa: ASYNC240
    ctx.inspector = InspectorRelay(ctx.session.id, session_dir)
    ctx.audio_store = AudioStore(ctx.session.id, session_dir)
    inspect_registry.register(ctx, ctx.inspector)
    message_manager = WsServerMessageManager(ctx.pubsub)
    mm_task: asyncio.Task[None] | None = None
    try:
        async with asyncio.TaskGroup() as tg:
            event_listener_task = tg.create_task(event_listener(ctx), name="event_listener")
            mirror_task = tg.create_task(inspect_pubsub_mirror(ctx), name="inspect_pubsub_mirror")
            async with asyncio.timeout(OPENAI_REALTIME_SESSION_DURATION_SECONDS):
                mm_task = asyncio.create_task(message_manager.run(ws))
                await message_manager.ready.wait()
                ctx.pubsub.publish_nowait(SessionCreatedEvent(session=ctx.session))
                await mm_task
            event_listener_task.cancel()
            mirror_task.cancel()
    finally:
        if mm_task is not None and not mm_task.done():
            mm_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await mm_task
        if ctx.barge_in_task is not None and not ctx.barge_in_task.done():
            ctx.barge_in_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ctx.barge_in_task
        if ctx.partial_transcription_task is not None and not ctx.partial_transcription_task.done():
            ctx.partial_transcription_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ctx.partial_transcription_task
        active = ctx.response_manager.active
        if active is not None and active.task is not None and not active.task.done():
            ctx.response_manager.cancel_active()
            with contextlib.suppress(asyncio.CancelledError):
                await active.task
        if ctx.inspector is not None:
            ctx.inspector.close()
        if ctx.audio_store is not None:
            ctx.audio_store.close()
        inspect_registry.unregister(ctx.session.id)
        logger.info(f"Finished handling '{ctx.session.id}' session")
