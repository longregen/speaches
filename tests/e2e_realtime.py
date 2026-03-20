#!/usr/bin/env python3
"""E2E test for the realtime WebSocket pipeline with a mock LLM.

This test verifies the full realtime voice pipeline:
1. Generate speech audio from known text using Kokoro TTS
2. Send audio through the realtime WebSocket
3. VAD detects speech, Whisper transcribes the audio
4. Mock LLM receives the transcription and returns a known response
5. TTS synthesizes the response audio
6. Verify all response events arrive correctly

Requires:
- Speaches server running with CHAT_COMPLETION_BASE_URL pointing to the mock LLM
- Kokoro TTS model available
- Whisper STT model available
- Silero VAD model available
"""

import asyncio
import base64
import json
import logging
import sys
import threading
import time
from typing import Any

from fastapi import FastAPI
import httpx
from starlette.requests import Request
from starlette.responses import StreamingResponse
import uvicorn
import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("e2e_realtime")

INPUT_TEXT = "Hello, how are you today?"
MOCK_LLM_RESPONSE = "I am doing great, thank you for asking!"

SPEACHES_PORT = 18000
MOCK_LLM_PORT = 18001
SPEACHES_URL = f"http://127.0.0.1:{SPEACHES_PORT}"

# ---- Mock LLM Server ----

received_requests: list[dict[str, Any]] = []

mock_app = FastAPI()


@mock_app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> StreamingResponse:
    body = await request.json()
    received_requests.append(body)
    logger.info(f"Mock LLM received request with {len(body.get('messages', []))} messages")

    async def generate():  # noqa: ANN202
        words = MOCK_LLM_RESPONSE.split()
        for i, word in enumerate(words):
            token = word + (" " if i < len(words) - 1 else "")
            chunk = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "model": "mock-llm",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": token} if i == 0 else {"content": token},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(0.01)

        # Final chunk with finish_reason
        chunk = {
            "id": "chatcmpl-mock",
            "object": "chat.completion.chunk",
            "model": "mock-llm",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"

        # Usage chunk (required because stream_options.include_usage=True)
        chunk = {
            "id": "chatcmpl-mock",
            "object": "chat.completion.chunk",
            "model": "mock-llm",
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def start_mock_llm() -> None:
    uvicorn.run(mock_app, host="127.0.0.1", port=MOCK_LLM_PORT, log_level="warning")


# ---- Test Logic ----


async def generate_audio(client: httpx.AsyncClient) -> bytes:
    logger.info(f"Generating audio from text: {INPUT_TEXT!r}")
    response = await client.post(
        f"{SPEACHES_URL}/v1/audio/speech",
        json={
            "model": "tts-1",
            "input": INPUT_TEXT,
            "voice": "af_heart",
            "response_format": "pcm",
            "sample_rate": 24000,
        },
    )
    response.raise_for_status()
    logger.info(f"Generated {len(response.content)} bytes of 24kHz 16-bit PCM audio")
    return response.content


async def run_test() -> bool:
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Generate audio from text using Kokoro TTS
        audio_pcm = await generate_audio(client)

    # Step 2: Connect to realtime WebSocket
    ws_url = f"ws://127.0.0.1:{SPEACHES_PORT}/v1/realtime?model=mock-llm"
    logger.info(f"Connecting to WebSocket: {ws_url}")

    async with websockets.connect(ws_url) as ws:
        # Wait for session.created
        msg = json.loads(await ws.recv())
        assert msg["type"] == "session.created", f"Expected session.created, got {msg['type']}"
        logger.info("Received session.created")

        # Step 3: Send audio chunks
        # 24kHz * 2 bytes/sample * 0.2s = 9600 bytes per chunk
        chunk_size = 9600
        chunks_sent = 0
        for i in range(0, len(audio_pcm), chunk_size):
            chunk = audio_pcm[i : i + chunk_size]
            event = {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("utf-8"),
            }
            await ws.send(json.dumps(event))
            chunks_sent += 1
            await asyncio.sleep(0.01)

        logger.info(f"Sent {len(audio_pcm)} bytes of audio in {chunks_sent} chunks")

        # Step 4: Send silence to trigger VAD speech_stopped
        # Need >550ms of silence (silence_duration_ms default)
        # Send 1.5 seconds of silence to be safe
        silence_samples = int(24000 * 1.5)
        silence_bytes = b"\x00" * (silence_samples * 2)
        for i in range(0, len(silence_bytes), chunk_size):
            chunk = silence_bytes[i : i + chunk_size]
            event = {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("utf-8"),
            }
            await ws.send(json.dumps(event))
            await asyncio.sleep(0.01)

        logger.info("Sent silence, waiting for response events...")

        # Step 5: Collect response events
        events: list[dict[str, Any]] = []
        transcript_parts: list[str] = []
        audio_deltas: list[str] = []
        got_response_done = False

        try:
            async with asyncio.timeout(120):
                while not got_response_done:
                    raw = await ws.recv()
                    event = json.loads(raw)
                    events.append(event)
                    event_type = event["type"]

                    if event_type == "response.audio_transcript.delta":
                        transcript_parts.append(event.get("delta", ""))
                    elif event_type == "response.audio.delta":
                        audio_deltas.append(event.get("delta", ""))
                    elif event_type == "response.done":
                        got_response_done = True
                    elif event_type == "error":
                        logger.error(f"Error event: {event}")
                        return False

                    logger.debug(f"Event: {event_type}")
        except TimeoutError:
            logger.warning("Timed out waiting for response.done")
            for e in events:
                logger.info(f"  {e['type']}")
            return False

    # Step 6: Verify results
    event_types = [e["type"] for e in events]
    logger.info(f"Received {len(events)} events: {event_types}")

    # Check expected event sequence
    checks_passed = True

    def check(condition: bool, message: str) -> None:
        nonlocal checks_passed
        if condition:
            logger.info(f"  PASS: {message}")
        else:
            logger.error(f"  FAIL: {message}")
            checks_passed = False

    logger.info("Verifying event sequence...")
    check("input_audio_buffer.speech_started" in event_types, "speech_started received")
    check("input_audio_buffer.speech_stopped" in event_types, "speech_stopped received")
    check("input_audio_buffer.committed" in event_types, "buffer committed")
    check(
        "conversation.item.input_audio_transcription.completed" in event_types,
        "transcription completed",
    )
    check("response.created" in event_types, "response created")
    check("response.output_item.added" in event_types, "output item added")
    check("response.audio_transcript.delta" in event_types, "transcript deltas received")
    check("response.audio.delta" in event_types, "audio deltas received")
    check("response.audio.done" in event_types, "audio done")
    check("response.audio_transcript.done" in event_types, "transcript done")
    check("response.done" in event_types, "response done")

    # Check mock LLM received the request
    logger.info("Verifying mock LLM received correct input...")
    check(len(received_requests) > 0, "mock LLM received at least one request")

    if received_requests:
        llm_request = received_requests[0]
        messages = llm_request.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]
        check(len(user_messages) > 0, "LLM request contains user message")

        if user_messages:
            transcribed_text = user_messages[-1].get("content", "")
            logger.info(f"  Original text: {INPUT_TEXT!r}")
            logger.info(f"  Transcribed text: {transcribed_text!r}")
            # Check that at least some key words were transcribed correctly
            key_words = ["hello", "how", "you", "today"]
            matched = sum(1 for w in key_words if w in transcribed_text.lower())
            check(matched >= 2, f"transcription quality ({matched}/{len(key_words)} key words matched)")

    # Check response transcript
    full_transcript = "".join(transcript_parts)
    logger.info(f"  Mock LLM response: {MOCK_LLM_RESPONSE!r}")
    logger.info(f"  Response transcript: {full_transcript!r}")
    check(
        MOCK_LLM_RESPONSE.lower() in full_transcript.lower().strip(),
        "response transcript matches mock LLM output",
    )

    # Check audio was generated
    check(len(audio_deltas) > 0, f"audio deltas received ({len(audio_deltas)} chunks)")

    return checks_passed


def main() -> None:
    # Start mock LLM in background thread
    logger.info(f"Starting mock LLM on port {MOCK_LLM_PORT}")
    mock_thread = threading.Thread(target=start_mock_llm, daemon=True)
    mock_thread.start()

    # Wait for mock LLM to be ready
    for attempt in range(20):
        try:
            resp = httpx.get(f"http://127.0.0.1:{MOCK_LLM_PORT}/docs")
            logger.info(f"Mock LLM is running (status {resp.status_code})")
            break
        except httpx.ConnectError:
            if attempt == 19:
                logger.warning("Failed to connect to mock LLM after 20 attempts")
                sys.exit(1)
            time.sleep(0.1)

    # Run test
    success = asyncio.run(run_test())
    if success:
        logger.info("ALL CHECKS PASSED")
    else:
        logger.error("SOME CHECKS FAILED")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
