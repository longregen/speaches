import json

import httpx
import websockets

from tests.scenarios._helpers import (
    WS_URL,
    collect_events_until,
    generate_audio,
    logger,
    send_audio_chunks,
    send_silence,
    start_test,
)


async def run_truncate_test() -> bool:
    checker = start_test(3, "conversation.item.truncate", "truncate")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")

        await send_audio_chunks(ws, audio_pcm)
        await send_silence(ws)
        events = await collect_events_until(ws, "response.done")
        event_types = [e["type"] for e in events]
        checker.check("response.done" in event_types, "got response.done")
        output_item_added = [e for e in events if e["type"] == "response.output_item.added"]
        if output_item_added:
            item_id = output_item_added[0].get("item", {}).get("id")
            checker.check(item_id is not None, f"got output item id: {item_id}")
            truncate_event = {
                "type": "conversation.item.truncate",
                "event_id": "truncate_test_1",
                "item_id": item_id,
                "content_index": 0,
                "audio_end_ms": 500,
            }
            await ws.send(json.dumps(truncate_event))
            logger.info(f"Sent conversation.item.truncate for item {item_id}")
            truncate_events = await collect_events_until(ws, "conversation.item.truncated", timeout_seconds=5)
            truncate_types = [e["type"] for e in truncate_events]

            checker.check(
                "conversation.item.truncated" in truncate_types,
                "conversation.item.truncated received",
            )

            truncated = [e for e in truncate_events if e["type"] == "conversation.item.truncated"]
            if truncated:
                checker.check(
                    truncated[0].get("item_id") == item_id,
                    "truncated event has correct item_id",
                )
                checker.check(
                    truncated[0].get("audio_end_ms") == 500,
                    "truncated event has correct audio_end_ms",
                )
                checker.check(
                    truncated[0].get("content_index") == 0,
                    "truncated event has correct content_index",
                )
            bad_truncate = {
                "type": "conversation.item.truncate",
                "event_id": "truncate_bad_1",
                "item_id": "nonexistent_item_id",
                "content_index": 0,
                "audio_end_ms": 100,
            }
            await ws.send(json.dumps(bad_truncate))
            error_events = await collect_events_until(ws, "error", timeout_seconds=5)
            error_types = [e["type"] for e in error_events]
            checker.check("error" in error_types, "error received for truncating non-existent item")
        else:
            checker.passed = False
            logger.error("  [truncate] FAIL: no output item found to truncate")

    checker.dump_events_on_failure(events)
    return checker.passed
