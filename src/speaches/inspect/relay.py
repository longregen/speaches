from __future__ import annotations

import asyncio
import contextlib
import json  # noqa: F401
import logging
import time  # noqa: F401
from typing import TYPE_CHECKING

from speaches.types.inspect import ERR_KINDS, InspectorEvent

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

logger = logging.getLogger(__name__)

_FANOUT_MAX = 2048


class InspectorRelay:
    def __init__(self, session_id: str, session_dir: Path) -> None:
        self.session_id = session_id
        self.session_dir = session_dir
        self._buffer: list[bytes] = []
        self._subscribers: list[asyncio.Queue[bytes]] = []
        self._seq = 0
        self.turn_count = 0
        self.last_event_ts: float | None = None
        self._ndjson_path = session_dir / f"{session_id}.ndjson"
        self._ndjson_fh = None
        self._dropped_count = 0

        # Per-session correlation IDs (shared across all tasks for this session).
        # ContextVars can't be used here because asyncio.create_task copies the
        # context at spawn time, so mutations in one task don't propagate.
        self.turn_id: str | None = None
        self.item_id: str | None = None
        self.response_id: str | None = None
        self.phrase_id: str | None = None
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            self._ndjson_fh = self._ndjson_path.open("ab")
        except OSError:
            logger.exception("Failed to open inspector ndjson at %s", self._ndjson_path)

    def next_seq(self) -> int:
        s = self._seq
        self._seq += 1
        return s

    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def publish(self, event: InspectorEvent) -> None:
        if event.lane == "turn" and event.kind == "turn_end":
            self.turn_count += 1
        self.last_event_ts = event.ts_wall
        line = event.model_dump_json().encode() + b"\n"
        self._buffer.append(line)
        if self._ndjson_fh is not None:
            try:
                self._ndjson_fh.write(line)
            except OSError:
                logger.exception("Failed to write inspector ndjson")
        for q in list(self._subscribers):
            self._enqueue(q, line)
        if event.lane != "error" and event.kind in ERR_KINDS:
            self._publish_error_mirror(event)

    def _publish_error_mirror(self, origin: InspectorEvent) -> None:
        mirror = InspectorEvent(
            session_id=origin.session_id,
            seq=self.next_seq(),
            ts_mono_ns=origin.ts_mono_ns,
            ts_wall=origin.ts_wall,
            lane="error",
            kind="raised",
            corr=origin.corr,
            span_id=origin.span_id,
            payload={
                "lane": origin.lane,
                "origin_seq": origin.seq,
                "origin_kind": origin.kind,
                "error": str(origin.payload.get("error") or origin.payload.get("reason") or origin.kind),
                "severity": "error",
            },
        )
        line = mirror.model_dump_json().encode() + b"\n"
        self._buffer.append(line)
        if self._ndjson_fh is not None:
            try:
                self._ndjson_fh.write(line)
            except OSError:
                logger.exception("Failed to write inspector ndjson")
        for q in list(self._subscribers):
            self._enqueue(q, line)

    def _enqueue(self, q: asyncio.Queue[bytes], line: bytes) -> None:
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()
                q.put_nowait(line)
                self._dropped_count += 1
            except Exception:  # noqa: BLE001
                self._dropped_count += 1

    async def subscribe(self) -> AsyncGenerator[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=_FANOUT_MAX)
        snapshot = list(self._buffer)
        for line in snapshot:
            q.put_nowait(line)
        self._subscribers.append(q)
        try:
            while True:
                line = await q.get()
                yield line
        finally:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(q)

    def close(self) -> None:
        if self._ndjson_fh is not None:
            try:
                self._ndjson_fh.flush()
                self._ndjson_fh.close()
            except OSError:
                logger.exception("Failed to close inspector ndjson")
            self._ndjson_fh = None
