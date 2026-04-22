import json

import websockets

from tests.scenarios._helpers import WS_URL, collect_events_until, start_test


async def run_session_update_test() -> bool:
    checker = start_test(6, "session.update", "session-update")

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")
        original_session = msg.get("session", {})
        update_event = {
            "type": "session.update",
            "event_id": "session_update_1",
            "session": {
                "instructions": "You are a helpful test assistant.",
                "temperature": 0.5,
            },
        }
        await ws.send(json.dumps(update_event))

        events = await collect_events_until(ws, "session.updated", timeout_seconds=5)
        event_types = [e["type"] for e in events]
        checker.check("session.updated" in event_types, "session.updated received")

        updated_events = [e for e in events if e["type"] == "session.updated"]
        if updated_events:
            updated_session = updated_events[0].get("session", {})
            checker.check(
                updated_session.get("instructions") == "You are a helpful test assistant.",
                f"instructions updated (got {updated_session.get('instructions')!r})",
            )
            checker.check(
                updated_session.get("temperature") == 0.5,
                f"temperature updated to 0.5 (got {updated_session.get('temperature')})",
            )
            checker.check(
                updated_session.get("model") == original_session.get("model"),
                "model preserved after session update",
            )
            checker.check(
                updated_session.get("voice") == original_session.get("voice"),
                "voice preserved after session update",
            )
        update_vad = {
            "type": "session.update",
            "event_id": "session_update_2",
            "session": {
                "turn_detection": {
                    "threshold": 0.8,
                    "silence_duration_ms": 500,
                },
            },
        }
        await ws.send(json.dumps(update_vad))

        events2 = await collect_events_until(ws, "session.updated", timeout_seconds=5)
        updated_events2 = [e for e in events2 if e["type"] == "session.updated"]
        if updated_events2:
            td = updated_events2[0].get("session", {}).get("turn_detection", {})
            checker.check(td.get("threshold") == 0.8, f"VAD threshold updated to 0.8 (got {td.get('threshold')})")
            checker.check(
                td.get("silence_duration_ms") == 500,
                f"silence_duration_ms updated to 500 (got {td.get('silence_duration_ms')})",
            )
            checker.check(
                td.get("create_response") is True,
                f"create_response preserved (got {td.get('create_response')})",
            )
            checker.check(
                td.get("prefix_padding_ms") == 300,
                f"prefix_padding_ms preserved (got {td.get('prefix_padding_ms')})",
            )

    return checker.passed
