from __future__ import annotations

import asyncio
import json
import os
import struct
import urllib.request

import pytest

from tests import e2e_realtime as rt
from tests.scenarios._helpers import SPEACHES_PORT, SPEACHES_URL

WS_URL = f"ws://127.0.0.1:{SPEACHES_PORT}"


async def _poll_session_id() -> str:
    for _ in range(300):
        try:
            with urllib.request.urlopen(f"{SPEACHES_URL}/v1/inspect/sessions", timeout=1) as resp:
                data = json.load(resp)
            if data:
                return data[0]["id"]
        except Exception:
            pass
        await asyncio.sleep(0.05)
    raise RuntimeError("no inspector session appeared")


async def _drain_inspector(sid: str, stop_event: asyncio.Event) -> list[dict]:
    import websockets

    url = f"ws://127.0.0.1:{SPEACHES_PORT}/v1/inspect/{sid}/stream"
    events: list[dict] = []
    async with websockets.connect(url) as ws:
        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                continue
            except Exception:
                break
            if isinstance(raw, bytes):
                raw = raw.decode()
            for line in raw.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    return events


@pytest.mark.asyncio
async def test_inspector_lanes_and_correlation() -> None:
    if not os.environ.get("SPEACHES_INSPECT_SMOKE"):
        pytest.skip("set SPEACHES_INSPECT_SMOKE=1 and run with a live speaches + mock LLM on 18000/18001")

    sid = await _poll_session_id()
    stop = asyncio.Event()
    insp_task = asyncio.create_task(_drain_inspector(sid, stop))

    await asyncio.to_thread(asyncio.run, rt.run_basic_pipeline_test())

    await asyncio.sleep(1.5)
    stop.set()
    events = await insp_task

    lanes_seen = {e["lane"] for e in events}
    for lane in ("audio_level", "vad", "stt", "turn", "llm", "response", "tts_req", "tts_chunk"):
        assert lane in lanes_seen, f"missing events on lane {lane!r}"

    assert all(e["session_id"] == sid for e in events)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs), "seq is not monotonic"

    turn_ids = {e["corr"].get("turn_id") for e in events if e.get("corr")}
    turn_ids.discard(None)
    assert len(turn_ids) >= 1

    pbs = [e for e in events if e["lane"] == "response" and e["kind"] == "phrase_boundary"]
    psr = [e for e in events if e["lane"] == "tts_req" and e["kind"] == "phrase_sent"]
    assert len(pbs) == len(psr), f"phrase_boundary={len(pbs)} vs phrase_sent={len(psr)}"


@pytest.mark.asyncio
async def test_inspector_audio_wav() -> None:
    if not os.environ.get("SPEACHES_INSPECT_SMOKE"):
        pytest.skip("set SPEACHES_INSPECT_SMOKE=1 and run with a live speaches + mock LLM on 18000/18001")

    sid = await _poll_session_id()
    # Let the session accumulate some audio before we query.
    await asyncio.sleep(3.0)

    for channel, sample_rate in (("mic_in", 16000), ("tts_out", 24000)):
        url = f"{SPEACHES_URL}/v1/inspect/sessions/{sid}/audio?channel={channel}&from_ms=0&to_ms=1000"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = resp.read()
        assert data[:4] == b"RIFF", f"{channel}: not a WAV"
        assert data[8:12] == b"WAVE"
        sr = struct.unpack("<I", data[24:28])[0]
        assert sr == sample_rate, f"{channel}: got sample rate {sr}, expected {sample_rate}"
