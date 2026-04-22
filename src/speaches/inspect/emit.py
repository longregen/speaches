from __future__ import annotations

from contextvars import ContextVar
import time
from typing import Any

from speaches.inspect import registry
from speaches.types.inspect import Corr, InspectorEvent, LaneId

# Only session_id is per-task. Everything else lives on the per-session relay
# so mutations are visible across sibling tasks within the same session.
session_id_ctx: ContextVar[str | None] = ContextVar("inspect_session_id", default=None)


def _current_span_id() -> str | None:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return format(ctx.span_id, "016x")
    except Exception:  # noqa: BLE001, S110
        pass
    return None


def emit(lane: LaneId, kind: str, corr: dict[str, Any] | None = None, **payload: Any) -> None:
    sid = session_id_ctx.get()
    if sid is None:
        return
    relay = registry.get_relay(sid)
    if relay is None:
        return
    data = {
        "turn_id": relay.turn_id,
        "item_id": relay.item_id,
        "response_id": relay.response_id,
        "phrase_id": relay.phrase_id,
    }
    if corr:
        data.update({k: v for k, v in corr.items() if k in data})
    event = InspectorEvent(
        session_id=sid,
        seq=relay.next_seq(),
        ts_mono_ns=time.perf_counter_ns(),
        ts_wall=time.time(),
        lane=lane,
        kind=kind,
        corr=Corr(**data),
        span_id=_current_span_id(),
        payload=payload,
    )
    relay.publish(event)


def has_subscribers() -> bool:
    sid = session_id_ctx.get()
    if sid is None:
        return False
    relay = registry.get_relay(sid)
    return relay is not None and relay.has_subscribers()


# Mutators: writes the ID on the session's relay. Reads pick up across tasks.
def set_turn_id(value: str | None) -> None:
    sid = session_id_ctx.get()
    if sid is None:
        return
    r = registry.get_relay(sid)
    if r is not None:
        r.turn_id = value


def set_item_id(value: str | None) -> None:
    sid = session_id_ctx.get()
    if sid is None:
        return
    r = registry.get_relay(sid)
    if r is not None:
        r.item_id = value


def set_response_id(value: str | None) -> None:
    sid = session_id_ctx.get()
    if sid is None:
        return
    r = registry.get_relay(sid)
    if r is not None:
        r.response_id = value


def set_phrase_id(value: str | None) -> None:
    sid = session_id_ctx.get()
    if sid is None:
        return
    r = registry.get_relay(sid)
    if r is not None:
        r.phrase_id = value


def get_turn_id() -> str | None:
    sid = session_id_ctx.get()
    if sid is None:
        return None
    r = registry.get_relay(sid)
    return r.turn_id if r else None
