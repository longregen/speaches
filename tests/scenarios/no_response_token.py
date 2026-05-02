import json

import httpx
import websockets

from tests.scenarios._helpers import (
    WS_URL,
    MockLLMState,
    collect_events_until,
    generate_audio,
    send_audio_chunks,
    send_silence,
    start_test,
)


async def run_no_response_token_test() -> bool:
    checker = start_test(12, "No-response token (message clearing)", "no-response")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    MockLLMState.use_dismiss_response = True

    try:
        async with websockets.connect(WS_URL) as ws:
            msg = json.loads(await ws.recv())
            checker.check(msg["type"] == "session.created", "session.created received")
            session = msg.get("session", {})
            checker.check(
                session.get("no_response_token") == "*",
                f"no_response_token defaults to '*' (got {session.get('no_response_token')!r})",
            )
            await send_audio_chunks(ws, audio_pcm)
            await send_silence(ws)
            events = await collect_events_until(ws, "response.done", timeout_seconds=120)
            event_types = [e["type"] for e in events]
            checker.check("input_audio_buffer.speech_started" in event_types, "speech_started received")
            checker.check("input_audio_buffer.speech_stopped" in event_types, "speech_stopped received")
            checker.check("input_audio_buffer.committed" in event_types, "buffer committed")
            checker.check("response.created" in event_types, "response.created received")
            checker.check("response.done" in event_types, "response.done received")
            response_done = [e for e in events if e["type"] == "response.done"]
            if response_done:
                resp_status = response_done[0].get("response", {}).get("status")
                checker.check(resp_status == "completed", f"response status is 'completed' (got {resp_status})")
            transcript_parts = [
                e.get("delta", "") for e in events if e["type"] == "response.output_audio_transcript.delta"
            ]
            full_transcript = "".join(transcript_parts)
            checker.check(
                full_transcript.strip() == "*",
                f"transcript is the no-response token '*' (got {full_transcript!r})",
            )
            audio_deltas = [e for e in events if e["type"] == "response.output_audio.delta"]
            checker.check(
                len(audio_deltas) == 0,
                f"no audio deltas generated for dismissed response ({len(audio_deltas)} found)",
            )

            delete_events = await collect_events_until(
                ws, "conversation.item.deleted", timeout_seconds=10, fail_on_timeout=False
            )
            deleted_items = [e for e in delete_events if e["type"] == "conversation.item.deleted"]
            checker.check(
                len(deleted_items) >= 1,
                f"conversation items deleted after dismiss ({len(deleted_items)} deletions)",
            )
            if len(deleted_items) >= 2:
                checker.check(
                    deleted_items[0].get("item_id") != deleted_items[1].get("item_id"),
                    "two different items deleted (assistant + user input)",
                )

            all_events = events + delete_events
            checker.check_event_id_uniqueness(all_events)
            checker.dump_events_on_failure(all_events)
    finally:
        MockLLMState.use_dismiss_response = False

    return checker.passed
