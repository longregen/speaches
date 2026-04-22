from __future__ import annotations

import time
from typing import TYPE_CHECKING

from speaches.types.inspect import SessionMeta

if TYPE_CHECKING:
    from speaches.inspect.relay import InspectorRelay
    from speaches.realtime.context import SessionContext


_SESSIONS: dict[str, tuple[SessionContext, InspectorRelay, float]] = {}


def register(ctx: SessionContext, relay: InspectorRelay) -> None:
    _SESSIONS[ctx.session.id] = (ctx, relay, time.time())


def unregister(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)


def get_ctx(session_id: str) -> SessionContext | None:
    entry = _SESSIONS.get(session_id)
    return entry[0] if entry else None


def get_relay(session_id: str) -> InspectorRelay | None:
    entry = _SESSIONS.get(session_id)
    return entry[1] if entry else None


def list_meta() -> list[SessionMeta]:
    out: list[SessionMeta] = []
    for ctx, relay, created_at in _SESSIONS.values():
        out.append(
            SessionMeta(
                id=ctx.session.id,
                created_at=created_at,
                model=ctx.session.model,
                state=ctx.state.value,
                turn_count=relay.turn_count,
                last_event_ts=relay.last_event_ts,
            )
        )
    return out
