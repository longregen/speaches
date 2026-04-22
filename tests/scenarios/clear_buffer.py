import json

import httpx
import websockets

from tests.scenarios._helpers import (
    WS_URL,
    collect_events_until,
    generate_audio,
    send_audio_chunks,
    start_test,
)


async def run_clear_buffer_test() -> bool:
    checker = start_test(9, "input_audio_buffer.clear", "clear-buffer")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")
        update_event = {
            "type": "session.update",
            "session": {"turn_detection": None},
        }
        await ws.send(json.dumps(update_event))
        await collect_events_until(ws, "session.updated", timeout_seconds=5)
        await send_audio_chunks(ws, audio_pcm[:9600])
        clear_event = {"type": "input_audio_buffer.clear", "event_id": "clear_1"}
        await ws.send(json.dumps(clear_event))

        clear_events = await collect_events_until(ws, "input_audio_buffer.cleared", timeout_seconds=5)
        clear_types = [e["type"] for e in clear_events]
        checker.check("input_audio_buffer.cleared" in clear_types, "input_audio_buffer.cleared received")
        commit_event = {"type": "input_audio_buffer.commit", "event_id": "commit_empty_1"}
        await ws.send(json.dumps(commit_event))

        error_events = await collect_events_until(ws, "error", timeout_seconds=5)
        error_types = [e["type"] for e in error_events]
        checker.check("error" in error_types, "error received for committing empty buffer after clear")

    return checker.passed
