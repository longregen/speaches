"""Shared helpers for realtime e2e scenarios.

Prefixed with underscore so pytest does not collect it as a test module.
"""

import asyncio
import base64
import json
import logging
from typing import Any

from fastapi import FastAPI
import httpx
from starlette.requests import Request
from starlette.responses import StreamingResponse
import uvicorn
import websockets

logger = logging.getLogger("e2e_realtime")

INPUT_TEXT = "Hello, how are you today?"
MOCK_LLM_RESPONSE = "I am doing great, thank you for asking!"
MOCK_LLM_SLOW_RESPONSE = (
    "This is a very long response that takes a while to stream out word by word "
    "so that we have time to interrupt it during generation. "
    "It keeps going and going with many words to fill up time. "
    "We need enough words here to ensure the streaming takes several seconds "
    "so the barge-in and cancel tests have time to fire their interrupts."
)

SPEACHES_PORT = 18000
MOCK_LLM_PORT = 18001
SPEACHES_URL = f"http://127.0.0.1:{SPEACHES_PORT}"
WS_BASE_URL = f"ws://127.0.0.1:{SPEACHES_PORT}/v1/realtime"
WS_URL = f"{WS_BASE_URL}?model=mock-llm&transcription_model=Systran/faster-whisper-base"

# Shared mutable state used by scenarios to control mock LLM behavior.
received_requests: list[dict[str, Any]] = []


class MockLLMState:
    use_slow_response = False
    use_dismiss_response = False


mock_app = FastAPI()


@mock_app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> StreamingResponse:
    body = await request.json()
    received_requests.append(body)
    logger.info(f"Mock LLM received request with {len(body.get('messages', []))} messages")

    if MockLLMState.use_dismiss_response:
        response_text = "*"
    elif MockLLMState.use_slow_response:
        response_text = MOCK_LLM_SLOW_RESPONSE
    else:
        response_text = MOCK_LLM_RESPONSE

    def _chunk(choices: list, usage: dict | None = None) -> str:
        data = {"id": "chatcmpl-mock", "object": "chat.completion.chunk", "model": "mock-llm", "choices": choices}
        if usage is not None:
            data["usage"] = usage
        return f"data: {json.dumps(data)}\n\n"

    async def generate():
        words = response_text.split()
        for i, word in enumerate(words):
            token = word + (" " if i < len(words) - 1 else "")
            delta = {"role": "assistant", "content": token} if i == 0 else {"content": token}
            yield _chunk([{"index": 0, "delta": delta, "finish_reason": None}])
            await asyncio.sleep(0.05 if MockLLMState.use_slow_response else 0.01)
        yield _chunk([{"index": 0, "delta": {}, "finish_reason": "stop"}])
        yield _chunk([], usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20})
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


def start_mock_llm() -> None:
    uvicorn.run(mock_app, host="127.0.0.1", port=MOCK_LLM_PORT, log_level="warning")


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


async def send_audio_chunks(ws: websockets.ClientConnection, audio_pcm: bytes, chunk_size: int = 9600) -> int:
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
    return chunks_sent


async def send_silence(ws: websockets.ClientConnection, duration_seconds: float = 3.5) -> None:
    chunk_size = 9600
    silence_samples = int(24000 * duration_seconds)
    silence_bytes = b"\x00" * (silence_samples * 2)
    for i in range(0, len(silence_bytes), chunk_size):
        chunk = silence_bytes[i : i + chunk_size]
        event = {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode("utf-8"),
        }
        await ws.send(json.dumps(event))
        await asyncio.sleep(0.01)


async def collect_events_until(
    ws: websockets.ClientConnection,
    stop: str | set[str],
    timeout_seconds: float = 120,
    *,
    fail_on_timeout: bool = True,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    stops = {stop} if isinstance(stop, str) else stop
    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                event = json.loads(await ws.recv())
                events.append(event)
                if event["type"] in stops:
                    break
    except TimeoutError:
        if fail_on_timeout:
            event_types = [e["type"] for e in events]
            raise TimeoutError(
                f"Timed out after {timeout_seconds}s waiting for {stop!r}. Got {len(events)} events: {event_types}"
            ) from None
        logger.warning(f"Timed out waiting for {stop}")
    return events


collect_events_until_any = collect_events_until


async def drain_events(ws: websockets.ClientConnection, duration: float = 0.5) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(duration):
            while True:
                raw = await ws.recv()
                events.append(json.loads(raw))
    except TimeoutError:
        pass
    return events


class TestChecker:
    def __init__(self, name: str) -> None:
        self.name = name
        self.passed = True
        self.checks_run = 0
        self.failures: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        self.checks_run += 1
        if condition:
            logger.info(f"  [{self.name}] PASS: {message}")
        else:
            logger.error(f"  [{self.name}] FAIL: {message}")
            self.passed = False
            self.failures.append(message)

    def fail(self, message: str) -> None:
        self.check(condition=False, message=message)

    def ok(self, message: str) -> None:
        self.check(condition=True, message=message)

    def check_event_id_uniqueness(self, events: list[dict[str, Any]]) -> None:
        event_ids = [e.get("event_id") for e in events if e.get("event_id")]
        unique_ids = set(event_ids)
        self.check(
            len(event_ids) == len(unique_ids),
            f"all event_ids are unique ({len(event_ids)} total, {len(unique_ids)} unique)",
        )

    def check_response_id_consistency(
        self, events: list[dict[str, Any]], expected_response_id: str | None = None
    ) -> None:
        response_event_types = {
            "response.created",
            "response.done",
            "response.output_item.added",
            "response.output_item.done",
            "response.content_part.added",
            "response.content_part.done",
            "response.output_audio_transcript.delta",
            "response.output_audio_transcript.done",
            "response.output_audio.delta",
            "response.output_audio.done",
            "response.output_text.delta",
            "response.output_text.done",
        }
        response_events = [e for e in events if e["type"] in response_event_types]
        if not response_events:
            return

        if expected_response_id is None:
            created = [e for e in response_events if e["type"] == "response.created"]
            if created:
                expected_response_id = created[0].get("response", {}).get("id")

        if expected_response_id:
            for e in response_events:
                rid = e.get("response_id") or e.get("response", {}).get("id")
                if rid and rid != expected_response_id:
                    self.fail(f"response_id mismatch in {e['type']}: got {rid}, expected {expected_response_id}")
                    return
            self.ok(f"all response events have consistent response_id ({expected_response_id})")

    def check_event_ordering(self, events: list[dict[str, Any]], expected_order: list[str]) -> None:
        event_types = [e["type"] for e in events]
        idx = 0
        for expected in expected_order:
            found = False
            while idx < len(event_types):
                if event_types[idx] == expected:
                    found = True
                    idx += 1
                    break
                idx += 1
            if not found:
                self.fail(f"event ordering: expected {expected!r} but not found after position {idx}")
                return
        self.ok(f"event ordering matches expected sequence ({len(expected_order)} events)")

    def dump_events_on_failure(self, events: list[dict[str, Any]]) -> None:
        if not self.passed:
            logger.error(f"  [{self.name}] Event dump for debugging:")
            for i, e in enumerate(events):
                etype = e.get("type", "unknown")
                summary = {
                    k: v
                    for k, v in e.items()
                    if k not in ("audio", "delta")
                    or etype not in ("input_audio_buffer.append", "response.output_audio.delta")
                }
                if "delta" in e and etype == "response.output_audio.delta":
                    summary["delta"] = f"<{len(e['delta'])} chars of base64>"
                logger.error(f"    [{i}] {json.dumps(summary, default=str)[:200]}")


def start_test(num: int, title: str, checker_id: str) -> TestChecker:
    received_requests.clear()
    sep = "=" * 60
    logger.info(sep)
    logger.info(f"TEST {num}: {title}")
    logger.info(sep)
    return TestChecker(checker_id)
