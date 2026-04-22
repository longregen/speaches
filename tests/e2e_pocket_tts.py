"""End-to-end test for Kyutai Pocket TTS: generate speech, then transcribe with Whisper.

Run against a live speaches instance:
    python tests/e2e_pocket_tts.py [base_url]

Default base_url: http://127.0.0.1:18000
"""

import sys
import time

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18000"
TTS_MODEL = "kyutai/pocket-tts-without-voice-cloning"
STT_MODEL = "Systran/faster-whisper-base"
VOICE = "alba"
DOCTOR_WHO_TEXT = "People assume that time is a strict progression of cause to effect, but actually from a non-linear, non-subjective viewpoint, it is more like a big ball of wibbly wobbly, timey wimey stuff."
KEY_WORDS = ["people", "time", "cause", "effect", "wibbly", "wobbly", "timey", "wimey"]


def test_model_available() -> None:
    res = httpx.get(f"{BASE_URL}/v1/models", timeout=10)
    res.raise_for_status()
    model_ids = [m["id"] for m in res.json()["data"]]
    assert TTS_MODEL in model_ids, f"{TTS_MODEL} not in available models: {model_ids}"
    print(f"  pocket-tts model available: {TTS_MODEL}")


def test_tts_basic() -> None:
    start = time.perf_counter()
    res = httpx.post(
        f"{BASE_URL}/v1/audio/speech",
        json={
            "model": TTS_MODEL,
            "voice": VOICE,
            "input": "Hello world, this is a quick test.",
            "response_format": "pcm",
        },
        timeout=60,
    )
    elapsed = time.perf_counter() - start
    res.raise_for_status()
    audio_bytes = len(res.content)
    audio_duration = audio_bytes / (2 * 24000)
    print(f"  {audio_bytes} bytes, {audio_duration:.2f}s audio in {elapsed:.3f}s")
    assert audio_bytes > 1000, f"Audio too short: {audio_bytes} bytes"


def test_tts_voices() -> None:
    for voice in ["alba", "marius", "cosette"]:
        res = httpx.post(
            f"{BASE_URL}/v1/audio/speech",
            json={"model": TTS_MODEL, "voice": voice, "input": "Testing this voice.", "response_format": "pcm"},
            timeout=60,
        )
        res.raise_for_status()
        assert len(res.content) > 500, f"Voice {voice} produced too little audio"
        print(f"  voice {voice}: {len(res.content)} bytes")


def test_tts_then_stt_round_trip() -> None:
    print(f"  TTS: generating speech for {len(DOCTOR_WHO_TEXT)} chars...")
    start = time.perf_counter()
    tts_res = httpx.post(
        f"{BASE_URL}/v1/audio/speech",
        json={"model": TTS_MODEL, "voice": VOICE, "input": DOCTOR_WHO_TEXT, "response_format": "wav"},
        timeout=120,
    )
    tts_elapsed = time.perf_counter() - start
    tts_res.raise_for_status()
    wav_bytes = tts_res.content
    assert len(wav_bytes) > 5000, f"WAV too short: {len(wav_bytes)} bytes"
    print(f"  TTS: {len(wav_bytes)} bytes in {tts_elapsed:.2f}s")

    print(f"  STT: transcribing with {STT_MODEL}...")
    start = time.perf_counter()
    stt_res = httpx.post(
        f"{BASE_URL}/v1/audio/transcriptions",
        files={"file": ("speech.wav", wav_bytes, "audio/wav")},
        data={"model": STT_MODEL, "response_format": "json"},
        timeout=120,
    )
    stt_elapsed = time.perf_counter() - start
    stt_res.raise_for_status()
    transcription = stt_res.json().get("text", "").lower()
    print(f'  STT: "{transcription[:100]}..." in {stt_elapsed:.2f}s')

    matches = sum(1 for w in KEY_WORDS if w in transcription)
    print(f"  Round-trip: {matches}/{len(KEY_WORDS)} key words matched")
    assert matches >= 4, f"Only {matches}/{len(KEY_WORDS)} key words matched in: {transcription}"


def main() -> None:
    tests = [test_model_available, test_tts_basic, test_tts_voices, test_tts_then_stt_round_trip]
    passed = 0
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            print(f"[RUN]  {name}")
            test()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
