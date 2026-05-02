from __future__ import annotations

import contextlib
import json as _json
import logging
from pathlib import Path
import struct
from typing import TYPE_CHECKING, Annotated, cast

import anyio
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from fastapi.responses import StreamingResponse

from speaches.dependencies import ConfigDependency, ExecutorRegistryDependency  # noqa: TC001
from speaches.inspect import registry
from speaches.realtime.utils import verify_websocket_api_key
from speaches.types.inspect import SessionHistoryEntry, SessionMeta

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from speaches.config import Config
    from speaches.inspect.audio_store import Channel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["inspect"])


def _session_dir(config: Config) -> Path:
    return Path(config.inspect_session_dir).expanduser()


@router.get("/v1/inspect/sessions/models")
def inspect_models(executor_registry: ExecutorRegistryDependency) -> Response:
    models = []
    for executor in executor_registry.all_executors():
        models.extend(m.model_dump() for m in executor.model_registry.list_local_models())
    return Response(content=_json.dumps({"data": models, "object": "list"}), media_type="application/json")


@router.get("/v1/inspect/sessions", response_model=list[SessionMeta])
async def list_sessions() -> list[SessionMeta]:
    return registry.list_meta()


@router.get("/v1/inspect/sessions/history", response_model=list[SessionHistoryEntry])
async def list_history(config: ConfigDependency) -> list[SessionHistoryEntry]:
    sd = _session_dir(config)
    if not sd.exists():
        return []
    out: list[SessionHistoryEntry] = []
    for p in sd.iterdir():
        if p.is_file() and p.suffix == ".ndjson":
            st = p.stat()
            out.append(SessionHistoryEntry(id=p.stem, size_bytes=st.st_size, mtime=st.st_mtime))
    out.sort(key=lambda e: e.mtime, reverse=True)
    return out


@router.get("/v1/inspect/sessions/history/{sid}")
async def get_history(sid: str, config: ConfigDependency) -> Response:
    path = _session_dir(config) / f"{sid}.ndjson"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="session not found")

    async def stream() -> AsyncGenerator[bytes, None]:
        async with await anyio.open_file(path, "rb") as f:
            while True:
                chunk = await f.read(65536)
                if not chunk:
                    return
                yield chunk

    return StreamingResponse(stream(), media_type="application/x-ndjson")


_SAMPLE_RATES: dict[str, int] = {"mic_in": 16000, "tts_out": 24000}


def _wav_header(num_samples: int, sample_rate: int) -> bytes:
    byte_rate = sample_rate * 2
    block_align = 2
    data_bytes = num_samples * 2
    return (
        b"RIFF"
        + struct.pack("<I", 36 + data_bytes)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, block_align, 16)
        + b"data"
        + struct.pack("<I", data_bytes)
    )


@router.get("/v1/inspect/sessions/{sid}/audio")
async def get_audio(
    sid: str,
    config: ConfigDependency,
    channel: Annotated[str, Query(pattern="^(mic_in|tts_out)$")],
    from_ms: int = 0,
    to_ms: int = 0,
) -> Response:
    if channel not in _SAMPLE_RATES:
        raise HTTPException(status_code=400, detail="invalid channel")
    sr = _SAMPLE_RATES[channel]

    ctx = registry.get_ctx(sid)
    pcm: bytes
    audio_store = getattr(ctx, "audio_store", None) if ctx is not None else None
    if audio_store is not None:
        pcm = audio_store.slice(cast("Channel", channel), from_ms, to_ms)
    else:
        raw = _session_dir(config) / f"{sid}.audio_{channel}.raw"
        if not raw.is_file():
            raise HTTPException(status_code=404, detail="no audio for session")
        # Read offset from sidecar so session-relative times map to recording positions
        offset_ms = 0
        sidecar = _session_dir(config) / f"{sid}.audio.json"
        if sidecar.is_file():
            try:
                import json

                meta = json.loads(sidecar.read_text())
                offset_ms = meta.get("tracks", {}).get(channel, {}).get("offset_ms", 0)
            except (OSError, json.JSONDecodeError, KeyError):
                pass
        adj_from = max(0, from_ms - offset_ms)
        adj_to = max(0, to_ms - offset_ms) if to_ms > 0 else 0
        async with await anyio.open_file(raw, "rb") as f:
            all_pcm = await f.read()
        if adj_to <= 0:
            pcm = all_pcm[adj_from * sr * 2 // 1000 :]
        else:
            start = adj_from * sr * 2 // 1000
            end = adj_to * sr * 2 // 1000
            pcm = all_pcm[start:end]

    body = _wav_header(len(pcm) // 2, sr) + pcm
    return Response(content=body, media_type="audio/wav")


@router.websocket("/v1/inspect/{sid}/stream")
async def inspect_stream(websocket: WebSocket, sid: str, config: ConfigDependency) -> None:
    try:
        await verify_websocket_api_key(websocket, config)
    except WebSocketException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()

    relay = registry.get_relay(sid)
    if relay is None:
        path = _session_dir(config) / f"{sid}.ndjson"
        if path.is_file():
            async with await anyio.open_file(path, "rb") as f:
                buf = bytearray()
                while True:
                    chunk = await f.read(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
            for line in bytes(buf).splitlines():
                if line:
                    try:
                        await websocket.send_bytes(line)
                    except WebSocketDisconnect:
                        break
                    except Exception:
                        logger.exception("inspector replay send failed for sid=%s", sid)
                        break
        await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        return

    try:
        async for line in relay.subscribe():
            await websocket.send_bytes(line)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("inspector live stream failed for sid=%s", sid)
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()
