#!/usr/bin/env python3
"""E2E test for the realtime WebSocket pipeline with a mock LLM.

This test verifies the full realtime voice pipeline:
1. Generate speech audio from known text using Kokoro TTS
2. Send audio through the realtime WebSocket
3. VAD detects speech, Whisper transcribes the audio
4. Mock LLM receives the transcription and returns a known response
5. TTS synthesizes the response audio
6. Verify all response events arrive correctly with proper ordering and field consistency

Additional test scenarios:
7. Barge-in: send speech during response generation to trigger interruption
8. response.cancel: explicitly cancel an active response
9. conversation.item.truncate: truncate assistant audio item
10. Cancel with no active response: verify error
11. session.update: update session configuration
12. conversation.item.create + response.create: manual item injection and response trigger
13. conversation.item.delete: delete a conversation item
14. input_audio_buffer.clear: clear the audio buffer
15. Multiple rounds on same connection: verify state isolation

Requires:
- Speaches server running with CHAT_COMPLETION_BASE_URL pointing to the mock LLM
- Kokoro TTS model available
- Whisper STT model available
- Silero VAD model available
"""

import asyncio
import logging
import sys
import threading
import time

import httpx

from tests.scenarios import SCENARIOS
from tests.scenarios._helpers import MOCK_LLM_PORT, start_mock_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("e2e_realtime")


async def run_all_tests() -> bool:
    results: dict[str, bool] = {}

    for name, test_fn in SCENARIOS:
        try:
            results[name] = await test_fn()
        except Exception:
            logger.exception(f"Test {name} raised an exception")
            results[name] = False

    logger.info("=" * 60)
    logger.info("RESULTS:")
    all_passed = True
    for name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        logger.info(f"  {name}: {status}")
        if not passed:
            all_passed = False

    passed_count = sum(1 for v in results.values() if v)
    total_count = len(results)
    logger.info(f"  Total: {passed_count}/{total_count} passed")
    logger.info("=" * 60)

    return all_passed


def main() -> None:
    logger.info(f"Starting mock LLM on port {MOCK_LLM_PORT}")
    mock_thread = threading.Thread(target=start_mock_llm, daemon=True)
    mock_thread.start()
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
    success = asyncio.run(run_all_tests())
    if success:
        logger.info("ALL TESTS PASSED")
    else:
        logger.error("SOME TESTS FAILED")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
