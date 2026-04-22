import json

import websockets

from tests.scenarios._helpers import WS_URL, collect_events_until, start_test


async def run_cancel_no_response_test() -> bool:
    checker = start_test(4, "Cancel with no active response", "cancel-no-resp")

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")
        cancel_event = {"type": "response.cancel", "event_id": "cancel_no_resp_1"}
        await ws.send(json.dumps(cancel_event))
        events = await collect_events_until(ws, "error", timeout_seconds=5)
        event_types = [e["type"] for e in events]
        checker.check("error" in event_types, "error event received for cancel with no response")

        error_events = [e for e in events if e["type"] == "error"]
        if error_events:
            error_msg = error_events[0].get("error", {}).get("message", "")
            checker.check(
                "No active response" in error_msg, f"error message mentions no active response: {error_msg!r}"
            )

    return checker.passed
