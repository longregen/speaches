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


async def run_multi_round_test() -> bool:
    checker = start_test(10, "Multiple rounds on same connection", "multi-round")

    async with httpx.AsyncClient(timeout=30.0) as client:
        audio_pcm = await generate_audio(client)

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")

        response_ids = []

        for round_num in range(1, 3):
            logger.info(f"  [multi-round] Starting round {round_num}")
            received_requests.clear()

            await send_audio_chunks(ws, audio_pcm)
            await send_silence(ws)

            events = await collect_events_until(ws, "response.done", timeout_seconds=120)
            event_types = [e["type"] for e in events]

            checker.check(
                "response.done" in event_types,
                f"round {round_num}: response.done received",
            )

            response_done = [e for e in events if e["type"] == "response.done"]
            if response_done:
                resp = response_done[0].get("response", {})
                resp_status = resp.get("status")
                resp_id = resp.get("id")
                response_ids.append(resp_id)
                checker.check(
                    resp_status == "completed",
                    f"round {round_num}: response completed (got {resp_status})",
                )
            transcript_parts = [
                e.get("delta", "") for e in events if e["type"] == "response.output_audio_transcript.delta"
            ]
            full_transcript = "".join(transcript_parts)
            checker.check(
                MOCK_LLM_RESPONSE.lower() in full_transcript.lower().strip(),
                f"round {round_num}: transcript matches",
            )
            checker.check(
                len(received_requests) > 0,
                f"round {round_num}: mock LLM received request",
            )

            # In round 2, verify conversation history grew
            if round_num == 2 and received_requests:
                messages = received_requests[-1].get("messages", [])
                # Should have messages from both rounds (user + assistant from round 1, plus user from round 2)
                checker.check(
                    len(messages) >= 3,
                    f"round 2: LLM received conversation history ({len(messages)} messages)",
                )
        if len(response_ids) >= 2:
            checker.check(
                response_ids[0] != response_ids[1],
                f"different response IDs across rounds ({response_ids[0]} vs {response_ids[1]})",
            )

    return checker.passed
