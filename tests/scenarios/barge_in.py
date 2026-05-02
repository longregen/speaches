import asyncio
import json
from typing import Any

import httpx
import websockets

from tests.scenarios._helpers import (
    WS_URL,
    MockLLMState,
    generate_audio,
    logger,
    send_audio_chunks,
    send_silence,
    start_test,
)


async def run_barge_in_test() -> bool:
    checker = start_test(5, "Barge-in", "barge-in")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    MockLLMState.use_slow_response = True

    try:
        async with websockets.connect(WS_URL) as ws:
            msg = json.loads(await ws.recv())
            checker.check(msg["type"] == "session.created", "session.created received")
            await send_audio_chunks(ws, audio_pcm)
            await send_silence(ws)
            events: list[dict[str, Any]] = []
            audio_delta_count = 0
            got_response_created = False
            try:
                async with asyncio.timeout(60):
                    while True:
                        raw = await ws.recv()
                        event = json.loads(raw)
                        events.append(event)
                        if event["type"] == "response.created":
                            got_response_created = True
                        if event["type"] == "response.output_audio.delta":
                            audio_delta_count += 1
                            if audio_delta_count >= 3:
                                break
                        if event["type"] == "response.done":
                            break
            except TimeoutError:
                pass

            checker.check(got_response_created, "response.created received")
            checker.check(audio_delta_count >= 1, f"got audio deltas during first response ({audio_delta_count})")
            logger.info("Sending speech audio to trigger barge-in...")
            await send_audio_chunks(ws, audio_pcm)
            await send_silence(ws)
            barge_in_events: list[dict[str, Any]] = []
            response_done_count = 0
            try:
                async with asyncio.timeout(120):
                    while True:
                        raw = await ws.recv()
                        event = json.loads(raw)
                        barge_in_events.append(event)
                        if event["type"] == "response.done":
                            response_done_count += 1
                            if response_done_count >= 2:
                                break
                            resp_status = event.get("response", {}).get("status")
                            if resp_status == "completed":
                                break
            except TimeoutError:
                pass

            all_events = events + barge_in_events
            all_types = [e["type"] for e in all_events]

            response_done_events = [e for e in all_events if e["type"] == "response.done"]

            if len(response_done_events) >= 2:
                first_status = response_done_events[0].get("response", {}).get("status")
                second_status = response_done_events[1].get("response", {}).get("status")
                checker.check(
                    first_status == "cancelled",
                    f"first response was cancelled by barge-in (got {first_status})",
                )
                checker.check(
                    second_status == "completed",
                    f"second response completed after barge-in (got {second_status})",
                )
                first_id = response_done_events[0].get("response", {}).get("id")
                second_id = response_done_events[1].get("response", {}).get("id")
                checker.check(
                    first_id != second_id,
                    f"barge-in created a new response (ids: {first_id} vs {second_id})",
                )
            elif len(response_done_events) == 1:
                checker.check(
                    "input_audio_buffer.speech_started" in all_types,
                    "speech_started detected during barge-in attempt",
                )
                logger.info("  [barge-in] NOTE: first response completed before barge-in could interrupt")
            else:
                checker.fail("expected at least one response.done event")

            checker.check_event_id_uniqueness(all_events)
            checker.dump_events_on_failure(all_events)
    finally:
        MockLLMState.use_slow_response = False

    return checker.passed
