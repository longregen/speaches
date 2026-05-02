"""End-to-end test for Kokoro PyTorch TTS on CUDA.

Run against a live speaches instance:
    python tests/e2e_kokoro_pytorch.py [base_url]

Default base_url: http://127.0.0.1:1327
"""

import sys
import time

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:1327"
MODEL_ID = "hexgrad/Kokoro-82M"
VOICE = "af_heart"
SHORT_TEXT = "Hello, this is a test of the Kokoro PyTorch TTS engine."
LONG_TEXT = (
    "The quick brown fox jumps over the lazy dog. "
    "How vexingly quick daft zebras jump! "
    "Pack my box with five dozen liquor jugs. "
    "The five boxing wizards jump quickly at dawn."
)


def test_model_available():
    res = httpx.get(f"{BASE_URL}/v1/models", timeout=10)
    res.raise_for_status()
    models = res.json()
    model_ids = [m["id"] for m in models["data"]]
    assert MODEL_ID in model_ids, f"{MODEL_ID} not in available models: {model_ids}"
    print(f"  model {MODEL_ID} is available")


def test_speech_basic():
    start = time.perf_counter()
    res = httpx.post(
        f"{BASE_URL}/v1/audio/speech",
        json={"model": MODEL_ID, "voice": VOICE, "input": SHORT_TEXT, "response_format": "pcm"},
        timeout=30,
    )
    elapsed = time.perf_counter() - start
    res.raise_for_status()
    audio_bytes = len(res.content)
    audio_duration = audio_bytes / (2 * 24000)
    rtf = audio_duration / elapsed
    print(f"  {audio_bytes} bytes, {audio_duration:.2f}s audio in {elapsed:.3f}s (RTF: {rtf:.1f}x)")
    assert audio_bytes > 1000, f"Audio too short: {audio_bytes} bytes"
    assert rtf > 10, f"RTF {rtf:.1f}x too low — likely running on CPU, not GPU"


def test_speech_long():
    start = time.perf_counter()
    res = httpx.post(
        f"{BASE_URL}/v1/audio/speech",
        json={"model": MODEL_ID, "voice": VOICE, "input": LONG_TEXT, "response_format": "pcm"},
        timeout=60,
    )
    elapsed = time.perf_counter() - start
    res.raise_for_status()
    audio_bytes = len(res.content)
    audio_duration = audio_bytes / (2 * 24000)
    rtf = audio_duration / elapsed
    print(f"  {audio_bytes} bytes, {audio_duration:.2f}s audio in {elapsed:.3f}s (RTF: {rtf:.1f}x)")
    assert audio_bytes > 5000, f"Audio too short: {audio_bytes} bytes"


def test_speech_voices():
    for voice in ["af_heart", "am_adam", "bf_alice"]:
        res = httpx.post(
            f"{BASE_URL}/v1/audio/speech",
            json={"model": MODEL_ID, "voice": voice, "input": "Testing voice.", "response_format": "pcm"},
            timeout=30,
        )
        res.raise_for_status()
        assert len(res.content) > 500, f"Voice {voice} produced too little audio"
        print(f"  voice {voice}: {len(res.content)} bytes")


def main():
    tests = [test_model_available, test_speech_basic, test_speech_long, test_speech_voices]
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
