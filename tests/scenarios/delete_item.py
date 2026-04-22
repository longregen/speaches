import json

import websockets

from tests.scenarios._helpers import WS_URL, collect_events_until, start_test


async def run_delete_item_test() -> bool:
    checker = start_test(8, "conversation.item.delete", "delete-item")

    async with websockets.connect(WS_URL) as ws:
        msg = json.loads(await ws.recv())
        checker.check(msg["type"] == "session.created", "session.created received")
        create_event = {
            "type": "conversation.item.create",
            "item": {
                "id": "test_item_to_delete",
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "This will be deleted"}],
            },
        }
        await ws.send(json.dumps(create_event))
        events = await collect_events_until(ws, "conversation.item.added", timeout_seconds=5)
        checker.check(
            any(e["type"] == "conversation.item.added" for e in events),
            "item created",
        )
        delete_event = {
            "type": "conversation.item.delete",
            "event_id": "delete_1",
            "item_id": "test_item_to_delete",
        }
        await ws.send(json.dumps(delete_event))
        delete_events = await collect_events_until(ws, "conversation.item.deleted", timeout_seconds=5)
        delete_types = [e["type"] for e in delete_events]
        checker.check("conversation.item.deleted" in delete_types, "conversation.item.deleted received")

        deleted = [e for e in delete_events if e["type"] == "conversation.item.deleted"]
        if deleted:
            checker.check(
                deleted[0].get("item_id") == "test_item_to_delete",
                "deleted event has correct item_id",
            )
        await ws.send(json.dumps(delete_event))
        error_events = await collect_events_until(ws, "error", timeout_seconds=5)
        error_types = [e["type"] for e in error_events]
        checker.check("error" in error_types, "error received for deleting non-existent item")

    return checker.passed
