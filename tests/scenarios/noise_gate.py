import asyncio
import json
from typing import Any

import httpx
import websockets

from tests.scenarios._helpers import (
    WS_URL,
    collect_events_until,
    drain_events,
    generate_audio,
    logger,
    received_requests,
    send_audio_chunks,
    send_silence,
    start_test,
)


async def run_noise_gate_test() -> bool:
    checker = start_test(14, "Noise gate (no_speech_prob_threshold)", "noise-gate")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    logger.info("  [noise-gate] Part A: threshold=0.0 should reject all audio")

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")
        session = msg.get("session", {})
        checker.check(
            session.get("no_speech_prob_threshold") is not None,
            f"no_speech_prob_threshold present in session (got {session.get('no_speech_prob_threshold')})",
        )
        update_event = {
            "type": "session.update",
            "session": {"no_speech_prob_threshold": 0.0},
        }
        await ws.send(json.dumps(update_event))
        update_events = await collect_events_until(ws, "session.updated", timeout_seconds=5)
        updated = [e for e in update_events if e["type"] == "session.updated"]
        if updated:
            checker.check(
                updated[0].get("session", {}).get("no_speech_prob_threshold") == 0.0,
                "no_speech_prob_threshold updated to 0.0",
            )

        # Send real speech audio - VAD will trigger, but noise gate should reject
        await send_audio_chunks(ws, audio_pcm)
        await send_silence(ws)

        # We should see speech_started and speech_stopped, but NO response
        # (because the transcription is discarded by noise gate before creating the item)
        events: list[dict[str, Any]] = []
        try:
            async with asyncio.timeout(15):
                while True:
                    raw = await ws.recv()
                    event = json.loads(raw)
                    events.append(event)
                    # If we see response.created, the noise gate didn't work
                    if event["type"] == "response.created":
                        break
                    # After committed, if noise gate works, nothing else should arrive.
                    # Give it a few more seconds for transcription to complete.
                    if event["type"] == "input_audio_buffer.committed":
                        more = await drain_events(ws, duration=10.0)
                        events.extend(more)
                        break
        except TimeoutError:
            pass

        event_types = [e["type"] for e in events]

        checker.check(
            "input_audio_buffer.speech_started" in event_types,
            "speech_started received (VAD still triggers)",
        )
        checker.check(
            "input_audio_buffer.committed" in event_types,
            "buffer committed (audio still committed)",
        )
        # The key check: no conversation item created, no response triggered
        checker.check(
            "conversation.item.added" not in event_types,
            "no conversation item created (noise gate rejected)",
        )
        checker.check(
            "response.created" not in event_types,
            "no response created (noise gate rejected)",
        )
        checker.check(
            len(received_requests) == 0,
            f"mock LLM received no requests ({len(received_requests)} found)",
        )
        checker.dump_events_on_failure(events)

    received_requests.clear()
    logger.info("  [noise-gate] Part B: threshold=None should let all audio through")

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "B: session.created received")
        update_event = {
            "type": "session.update",
            "session": {"no_speech_prob_threshold": None},
        }
        await ws.send(json.dumps(update_event))
        await collect_events_until(ws, "session.updated", timeout_seconds=5)

        # Send the same audio - should pass through to LLM
        await send_audio_chunks(ws, audio_pcm)
        await send_silence(ws)

        events = await collect_events_until(ws, "response.done", timeout_seconds=120)
        event_types = [e["type"] for e in events]

        checker.check(
            "conversation.item.added" in event_types,
            "B: conversation item created (gate disabled)",
        )
        checker.check(
            "response.done" in event_types,
            "B: response completed (gate disabled)",
        )
        checker.check(
            len(received_requests) > 0,
            "B: mock LLM received request (gate disabled)",
        )
        checker.dump_events_on_failure(events)

    return checker.passed
