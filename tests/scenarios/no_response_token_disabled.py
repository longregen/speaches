import json

import httpx
import websockets

from tests.scenarios._helpers import (
    WS_URL,
    MockLLMState,
    collect_events_until,
    drain_events,
    generate_audio,
    send_audio_chunks,
    send_silence,
    start_test,
)


async def run_no_response_token_disabled_test() -> bool:
    checker = start_test(13, "No-response token disabled via session.update", "no-response-disabled")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    MockLLMState.use_dismiss_response = True

    try:
        async with websockets.connect(WS_URL) as ws:
            msg = json.loads(await ws.recv())
            checker.check(msg["type"] == "session.created", "session.created received")
            update_event = {
                "type": "session.update",
                "session": {"no_response_token": None},
            }
            await ws.send(json.dumps(update_event))
            update_events = await collect_events_until(ws, "session.updated", timeout_seconds=5)
            updated = [e for e in update_events if e["type"] == "session.updated"]
            if updated:
                checker.check(
                    updated[0].get("session", {}).get("no_response_token") is None,
                    "no_response_token disabled",
                )

            await send_audio_chunks(ws, audio_pcm)
            await send_silence(ws)

            events = await collect_events_until(ws, "response.done", timeout_seconds=120)
            event_types = [e["type"] for e in events]

            checker.check("response.done" in event_types, "response.done received")
            transcript_parts = [
                e.get("delta", "") for e in events if e["type"] == "response.output_audio_transcript.delta"
            ]
            full_transcript = "".join(transcript_parts)
            checker.check(full_transcript.strip() == "*", f"transcript is '*' (got {full_transcript!r})")

            post_events = await drain_events(ws, duration=2.0)
            post_types = [e["type"] for e in post_events]
            deleted_count = sum(1 for t in post_types if t == "conversation.item.deleted")
            checker.check(
                deleted_count == 0,
                f"no conversation items deleted when feature disabled ({deleted_count} found)",
            )

            checker.dump_events_on_failure(events + post_events)
    finally:
        MockLLMState.use_dismiss_response = False

    return checker.passed
