import json

import websockets

from tests.scenarios._helpers import (
    WS_URL,
    collect_events_until,
    received_requests,
    start_test,
)


async def run_manual_conversation_test() -> bool:
    checker = start_test(7, "conversation.item.create + response.create", "manual-conv")

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")
        update_event = {
            "type": "session.update",
            "session": {
                "turn_detection": None,
            },
        }
        await ws.send(json.dumps(update_event))
        events = await collect_events_until(ws, "session.updated", timeout_seconds=5)
        updated = [e for e in events if e["type"] == "session.updated"]
        if updated:
            checker.check(
                updated[0].get("session", {}).get("turn_detection") is None,
                "turn_detection disabled",
            )
        create_event = {
            "type": "conversation.item.create",
            "event_id": "create_item_1",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "What is your name?"}],
            },
        }
        await ws.send(json.dumps(create_event))

        item_events = await collect_events_until(ws, "conversation.item.added", timeout_seconds=5)
        item_types = [e["type"] for e in item_events]
        checker.check("conversation.item.added" in item_types, "conversation.item.added received")

        created_events = [e for e in item_events if e["type"] == "conversation.item.added"]
        if created_events:
            created_item = created_events[0].get("item", {})
            checker.check(created_item.get("role") == "user", "created item has user role")
            checker.check(created_item.get("type") == "message", "created item is a message")
        response_create = {
            "type": "response.create",
            "event_id": "response_create_1",
        }
        await ws.send(json.dumps(response_create))
        response_events = await collect_events_until(ws, "response.done", timeout_seconds=60)
        response_types = [e["type"] for e in response_events]

        checker.check("response.created" in response_types, "response.created received")
        checker.check("response.done" in response_types, "response.done received")

        response_done = [e for e in response_events if e["type"] == "response.done"]
        if response_done:
            resp_status = response_done[0].get("response", {}).get("status")
            checker.check(resp_status == "completed", f"response completed (got {resp_status})")
        checker.check(len(received_requests) > 0, "mock LLM received a request")
        if received_requests:
            messages = received_requests[-1].get("messages", [])
            user_texts = [
                c.get("text", "") if isinstance(c, dict) else str(c)
                for m in messages
                if m.get("role") == "user"
                for c in (m.get("content") if isinstance(m.get("content"), list) else [m.get("content", "")])
            ]
            found_injected = any("What is your name?" in t for t in user_texts)
            checker.check(found_injected, f"mock LLM received injected user message (user texts: {user_texts})")

        checker.check_event_id_uniqueness(item_events + response_events)
        checker.dump_events_on_failure(item_events + response_events)

    return checker.passed
