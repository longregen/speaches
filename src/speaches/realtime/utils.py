import asyncio
from contextvars import Context
import logging
import random
import string
from typing import TYPE_CHECKING

from fastapi import WebSocket, WebSocketException, status

if TYPE_CHECKING:
    from speaches.config import Config

logger = logging.getLogger(__name__)


def generate_id_suffix() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=21))  # noqa: S311


def generate_event_id() -> str:
    return "event_" + generate_id_suffix()


def generate_conversation_id() -> str:
    return "conv_" + generate_id_suffix()


def generate_item_id() -> str:
    return "item_" + generate_id_suffix()


def generate_response_id() -> str:
    return "resp_" + generate_id_suffix()


def generate_session_id() -> str:
    return "sess_" + generate_id_suffix()


def generate_call_id() -> str:
    return "call_" + generate_id_suffix()


def task_done_callback(task: asyncio.Task, *, context: Context | None = None) -> None:  # noqa: ARG001
    try:
        task.result()
    except asyncio.CancelledError:
        logger.info(f"Task {task.get_name()} cancelled")
    except BaseException:  # TODO: should this be `Exception` instead?
        logger.exception(f"Task {task.get_name()} failed")


async def verify_websocket_api_key(
    websocket: WebSocket,
    config: "Config",
) -> None:
    if config.api_key is None:
        return

    api_key = websocket.query_params.get("api_key")

    if not api_key:
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            api_key = auth_header[7:]

    if not api_key:
        api_key = websocket.headers.get("x-api-key")

    if not api_key:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="API key required")

    if api_key != config.api_key.get_secret_value():
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid API key")
