import base64
import json

import httpx
import websockets

from tests.scenarios._helpers import (
    MOCK_LLM_RESPONSE,
    WS_URL,
    collect_events_until,
    generate_audio,
    logger,
    received_requests,
    send_audio_chunks,
    send_silence,
    start_test,
)


async def run_basic_pipeline_test() -> bool:
    checker = start_test(1, "Basic Pipeline", "basic")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")
        session = msg.get("session", {})
        checker.check(session.get("model") == "mock-llm", f"model is mock-llm (got {session.get('model')})")
        turn_detection = session.get("turn_detection", {})
        checker.check(
            turn_detection.get("threshold") == 0.6, f"VAD threshold is 0.6 (got {turn_detection.get('threshold')})"
        )
        checker.check(
            turn_detection.get("silence_duration_ms") == 350,
            f"silence_duration_ms is 350 (got {turn_detection.get('silence_duration_ms')})",
        )
        checker.check(
            turn_detection.get("prefix_padding_ms") == 300,
            f"prefix_padding_ms is 300 (got {turn_detection.get('prefix_padding_ms')})",
        )

        chunks_sent = await send_audio_chunks(ws, audio_pcm)
        logger.info(f"Sent {len(audio_pcm)} bytes of audio in {chunks_sent} chunks")

        await send_silence(ws)
        logger.info("Sent silence, waiting for response events...")

        events = await collect_events_until(ws, "response.done")

    event_types = [e["type"] for e in events]
    checker.check("input_audio_buffer.speech_started" in event_types, "speech_started received")
    checker.check("input_audio_buffer.speech_stopped" in event_types, "speech_stopped received")
    checker.check("input_audio_buffer.committed" in event_types, "buffer committed")
    checker.check(
        "conversation.item.input_audio_transcription.completed" in event_types,
        "transcription completed",
    )
    checker.check("conversation.item.added" in event_types, "conversation item created")
    checker.check("response.created" in event_types, "response created")
    checker.check("response.output_item.added" in event_types, "output item added")
    checker.check("response.content_part.added" in event_types, "content part added")
    checker.check("response.output_audio_transcript.delta" in event_types, "transcript deltas received")
    checker.check("response.output_audio.delta" in event_types, "audio deltas received")
    checker.check("response.output_audio.done" in event_types, "audio done")
    checker.check("response.output_audio_transcript.done" in event_types, "transcript done")
    checker.check("response.content_part.done" in event_types, "content part done")
    checker.check("response.output_item.done" in event_types, "output item done")
    checker.check("response.done" in event_types, "response done")
    checker.check_event_ordering(
        events,
        [
            "input_audio_buffer.speech_started",
            "input_audio_buffer.speech_stopped",
            "input_audio_buffer.committed",
            "conversation.item.added",
            "response.created",
            "response.output_item.added",
            "response.content_part.added",
            "response.output_audio_transcript.delta",
            "response.output_audio.delta",
            "response.output_audio.done",
            "response.output_audio_transcript.done",
            "response.content_part.done",
            "response.output_item.done",
            "response.done",
        ],
    )
    checker.check_event_id_uniqueness(events)
    checker.check_response_id_consistency(events)
    response_done_events = [e for e in events if e["type"] == "response.done"]
    if response_done_events:
        resp = response_done_events[0].get("response", {})
        resp_status = resp.get("status")
        checker.check(resp_status == "completed", f"response status is 'completed' (got '{resp_status}')")
        output = resp.get("output", [])
        checker.check(len(output) > 0, f"response.done includes output items ({len(output)} items)")
        if output:
            checker.check(
                output[0].get("role") == "assistant",
                f"output item role is 'assistant' (got {output[0].get('role')})",
            )
            checker.check(
                output[0].get("status") == "completed",
                f"output item status is 'completed' (got {output[0].get('status')})",
            )
    transcript_parts = [e.get("delta", "") for e in events if e["type"] == "response.output_audio_transcript.delta"]
    full_transcript = "".join(transcript_parts)
    checker.check(
        MOCK_LLM_RESPONSE.lower() in full_transcript.lower().strip(),
        f"response transcript matches mock LLM output (got {full_transcript!r})",
    )
    transcript_done = [e for e in events if e["type"] == "response.output_audio_transcript.done"]
    if transcript_done:
        done_transcript = transcript_done[0].get("transcript", "")
        checker.check(
            done_transcript.strip() == full_transcript.strip(),
            "transcript.done matches accumulated delta transcripts",
        )
    audio_deltas = [e for e in events if e["type"] == "response.output_audio.delta"]
    checker.check(len(audio_deltas) > 0, f"audio deltas received ({len(audio_deltas)} chunks)")
    total_audio_bytes = sum(len(base64.b64decode(e.get("delta", ""))) for e in audio_deltas)
    checker.check(total_audio_bytes > 1000, f"total audio data is non-trivial ({total_audio_bytes} bytes)")
    output_item_added = [e for e in events if e["type"] == "response.output_item.added"]
    if output_item_added:
        item_id = output_item_added[0].get("item", {}).get("id")
        item_events = [
            e
            for e in events
            if e.get("item_id") == item_id
            and e["type"]
            in {
                "response.output_audio_transcript.delta",
                "response.output_audio.delta",
                "response.output_audio.done",
                "response.output_audio_transcript.done",
                "response.content_part.added",
                "response.content_part.done",
                "response.output_item.done",
            }
        ]
        checker.check(len(item_events) > 0, f"response events reference correct item_id ({item_id})")
    checker.check(len(received_requests) > 0, "mock LLM received at least one request")
    if received_requests:
        req = received_requests[-1]
        checker.check("messages" in req, "request has messages field")
        checker.check(req.get("stream") is True, "request has stream=True")
        checker.check("model" in req, "request has model field")
        messages = req.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]
        checker.check(len(user_messages) > 0, "mock LLM received at least one user message")

    checker.dump_events_on_failure(events)
    return checker.passed
