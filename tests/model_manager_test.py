from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import anyio
import pytest

from speaches.config import Config

if TYPE_CHECKING:
    from httpx import AsyncClient

    from tests.conftest import AclientFactory

MODEL_ID = "Systran/faster-whisper-tiny.en"


async def _wait_for_model_count(aclient: AsyncClient, expected: int, seconds: float = 15.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        res = (await aclient.get("/api/ps")).json()
        if len(res["models"]) == expected:
            return
        await asyncio.sleep(0.25)
    res = (await aclient.get("/api/ps")).json()
    assert len(res["models"]) == expected, f"expected {expected} models, got {len(res['models'])} after {seconds}s"


@pytest.mark.parametrize("pull_model_without_cleanup", [MODEL_ID], indirect=True)
@pytest.mark.usefixtures("pull_model_without_cleanup")
@pytest.mark.asyncio
async def test_model_unloaded_after_ttl(aclient_factory: AclientFactory) -> None:
    ttl = 5
    config = Config(stt_model_ttl=ttl, enable_ui=False)
    async with aclient_factory(config) as aclient:
        await _wait_for_model_count(aclient, 0)
        await aclient.post(f"/api/ps/{MODEL_ID}")
        await _wait_for_model_count(aclient, 1)
        # Wait for TTL to expire, then poll for unload (threading.Timer can be delayed)
        await asyncio.sleep(ttl)
        await _wait_for_model_count(aclient, 0, seconds=15.0)


@pytest.mark.parametrize("pull_model_without_cleanup", [MODEL_ID], indirect=True)
@pytest.mark.usefixtures("pull_model_without_cleanup")
@pytest.mark.asyncio
async def test_ttl_resets_after_usage(aclient_factory: AclientFactory) -> None:
    ttl = 5
    config = Config(stt_model_ttl=ttl, enable_ui=False, vad_model_ttl=0)
    async with aclient_factory(config) as aclient:
        await aclient.post(f"/api/ps/{MODEL_ID}")
        await _wait_for_model_count(aclient, 1)
        await asyncio.sleep(ttl - 2)  # sleep for less than the ttl. The model should not be unloaded
        await _wait_for_model_count(aclient, 1)

        async with await anyio.open_file("audio.wav", "rb") as f:
            data = await f.read()
        await aclient.post(
            "/v1/audio/transcriptions",
            files={"file": ("audio.wav", data, "audio/wav")},
            data={"model": MODEL_ID},
        )
        await _wait_for_model_count(aclient, 1)
        await asyncio.sleep(ttl - 2)  # sleep for less than the ttl. The model should not be unloaded
        await _wait_for_model_count(aclient, 1)

        # Wait for TTL to expire after last usage, then poll for unload
        await asyncio.sleep(3)
        await _wait_for_model_count(aclient, 0, seconds=15.0)

        # test the model can be used again after being unloaded
        await aclient.post(
            "/v1/audio/transcriptions",
            files={"file": ("audio.wav", data, "audio/wav")},
            data={"model": MODEL_ID},
        )


@pytest.mark.parametrize("pull_model_without_cleanup", [MODEL_ID], indirect=True)
@pytest.mark.usefixtures("pull_model_without_cleanup")
@pytest.mark.asyncio
async def test_model_cant_be_unloaded_when_used(aclient_factory: AclientFactory) -> None:
    ttl = 0
    config = Config(stt_model_ttl=ttl, enable_ui=False, vad_model_ttl=ttl)
    async with aclient_factory(config) as aclient:
        async with await anyio.open_file("audio.wav", "rb") as f:
            data = await f.read()

        task = asyncio.create_task(
            aclient.post(
                "/v1/audio/transcriptions", files={"file": ("audio.wav", data, "audio/wav")}, data={"model": MODEL_ID}
            )
        )
        # Poll until the model appears in loaded models before attempting to unload it.
        # A fixed sleep is unreliable: VAD runs before Whisper loads, so the delay varies
        # based on hardware speed and whether models are cached.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10.0
        while loop.time() < deadline:
            if MODEL_ID in (await aclient.get("/api/ps")).json().get("models", []):
                break
            await asyncio.sleep(0.05)
        res = await aclient.delete(f"/api/ps/{MODEL_ID}")
        assert res.status_code == 409, res.text

        await task
        await _wait_for_model_count(aclient, 0)


@pytest.mark.parametrize("pull_model_without_cleanup", [MODEL_ID], indirect=True)
@pytest.mark.usefixtures("pull_model_without_cleanup")
@pytest.mark.asyncio
async def test_model_cant_be_loaded_twice(aclient_factory: AclientFactory) -> None:
    ttl = -1
    config = Config(stt_model_ttl=ttl, enable_ui=False, vad_model_ttl=0)
    async with aclient_factory(config) as aclient:
        res = await aclient.post(f"/api/ps/{MODEL_ID}")
        assert res.status_code == 201
        res = await aclient.post(f"/api/ps/{MODEL_ID}")
        assert res.status_code == 409
        await _wait_for_model_count(aclient, 1)


@pytest.mark.parametrize("pull_model_without_cleanup", [MODEL_ID], indirect=True)
@pytest.mark.usefixtures("pull_model_without_cleanup")
@pytest.mark.asyncio
async def test_model_is_unloaded_after_request_when_ttl_is_zero(aclient_factory: AclientFactory) -> None:
    ttl = 0
    config = Config(stt_model_ttl=ttl, enable_ui=False, vad_model_ttl=ttl)
    async with aclient_factory(config) as aclient:
        async with await anyio.open_file("audio.wav", "rb") as f:
            data = await f.read()
        await aclient.post(
            "/v1/audio/transcriptions",
            files={"file": ("audio.wav", data, "audio/wav")},
            data={"model": "Systran/faster-whisper-tiny.en"},
        )
        await _wait_for_model_count(aclient, 0)
