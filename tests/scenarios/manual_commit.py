import json

import httpx
import websockets

from tests.scenarios._helpers import (
    WS_URL,
    collect_events_until,
    generate_audio,
    received_requests,
    send_audio_chunks,
    start_test,
)


async def run_manual_commit_test() -> bool:
    checker = start_test(11, "Manual commit + response.create (no VAD)", "manual-commit")

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
        await send_audio_chunks(ws, audio_pcm)
        commit_event = {"type": "input_audio_buffer.commit", "event_id": "manual_commit_1"}
        await ws.send(json.dumps(commit_event))

        commit_events = await collect_events_until(ws, "input_audio_buffer.committed", timeout_seconds=10)
        commit_types = [e["type"] for e in commit_events]
        checker.check("input_audio_buffer.committed" in commit_types, "buffer committed manually")
        transcription_events = await collect_events_until(
            ws, "conversation.item.input_audio_transcription.completed", timeout_seconds=30
        )
        t_types = [e["type"] for e in transcription_events]
        checker.check(
            "conversation.item.input_audio_transcription.completed" in t_types,
            "transcription completed after manual commit",
        )
        response_create = {"type": "response.create", "event_id": "manual_response_1"}
        await ws.send(json.dumps(response_create))

        response_events = await collect_events_until(ws, "response.done", timeout_seconds=60)
        response_types = [e["type"] for e in response_events]
        checker.check("response.created" in response_types, "response.created after manual trigger")
        checker.check("response.done" in response_types, "response.done after manual trigger")

        response_done = [e for e in response_events if e["type"] == "response.done"]
        if response_done:
            resp_status = response_done[0].get("response", {}).get("status")
            checker.check(resp_status == "completed", f"manual response completed (got {resp_status})")

        checker.check(len(received_requests) > 0, "mock LLM received request")

        all_events = commit_events + transcription_events + response_events
        checker.check_event_id_uniqueness(all_events)
        checker.dump_events_on_failure(all_events)

    return checker.passed
