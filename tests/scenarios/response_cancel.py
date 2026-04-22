import asyncio
import json

import httpx
import websockets

from tests.scenarios._helpers import (
    WS_URL,
    MockLLMState,
    collect_events_until,
    generate_audio,
    logger,
    send_audio_chunks,
    send_silence,
    start_test,
)


async def run_response_cancel_test() -> bool:
    checker = start_test(2, "response.cancel", "cancel")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    MockLLMState.use_slow_response = True

    try:
        async with websockets.connect(WS_URL) as ws:
            msg = json.loads(await ws.recv())
            checker.check(msg["type"] == "session.created", "session.created received")

            await send_audio_chunks(ws, audio_pcm)
            await send_silence(ws)
            events = await collect_events_until(ws, "response.created", timeout_seconds=60)
            event_types = [e["type"] for e in events]
            checker.check("response.created" in event_types, "response.created received before cancel")
            pre_cancel_audio = []
            try:
                async with asyncio.timeout(5):
                    while True:
                        raw = await ws.recv()
                        event = json.loads(raw)
                        events.append(event)
                        if event["type"] == "response.output_audio.delta":
                            pre_cancel_audio.append(event)
                            if len(pre_cancel_audio) >= 2:
                                break
                        elif event["type"] == "response.done":
                            break
            except TimeoutError:
                pass

            checker.check(len(pre_cancel_audio) > 0, f"got audio deltas before cancel ({len(pre_cancel_audio)})")
            cancel_event = {"type": "response.cancel", "event_id": "cancel_test_1"}
            await ws.send(json.dumps(cancel_event))
            logger.info("Sent response.cancel")
            remaining = await collect_events_until(ws, "response.done", timeout_seconds=15)
            events.extend(remaining)

            event_types = [e["type"] for e in events]
            checker.check("response.done" in event_types, "response.done received after cancel")

            response_done_events = [e for e in events if e["type"] == "response.done"]
            if response_done_events:
                resp = response_done_events[0].get("response", {})
                resp_status = resp.get("status")
                checker.check(
                    resp_status == "cancelled",
                    f"response status is 'cancelled' (got '{resp_status}')",
                )
                output = resp.get("output", [])
                if output:
                    checker.check(
                        output[0].get("status") == "incomplete",
                        f"cancelled output item status is 'incomplete' (got {output[0].get('status')})",
                    )

            checker.check_event_id_uniqueness(events)
            checker.dump_events_on_failure(events)
    finally:
        MockLLMState.use_slow_response = False

    return checker.passed
