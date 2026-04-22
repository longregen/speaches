"""Comprehensive e2e API test for speaches.

Run against a live speaches instance:
    python tests/e2e_api.py [base_url]

Default base_url: http://127.0.0.1:18000

Covers: health, models, TTS (multi-format), STT (multi-format, VAD),
translation, VAD timestamps, model load/unload, TTS->STT round-trip.

Used by both NixOS VM tests and standalone shell-based e2e scripts.
"""

import io
import sys

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18000"
TIMEOUT = 120.0
PASSED = 0
FAILED = 0


def check(desc: str, condition: bool) -> None:
    global PASSED, FAILED  # noqa: PLW0603
    if condition:
        PASSED += 1
        print(f"  PASS: {desc}")
    else:
        FAILED += 1
        print(f"  FAIL: {desc}")


def get_json(path: str) -> dict:
    r = httpx.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def post_audio_speech(body: dict) -> bytes:
    r = httpx.post(f"{BASE_URL}/v1/audio/speech", json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def post_form(path: str, data: dict | None = None, files: dict | None = None) -> httpx.Response:
    r = httpx.post(f"{BASE_URL}{path}", data=data, files=files, timeout=TIMEOUT)
    r.raise_for_status()
    return r


print("\n--- 1. Health & Diagnostics ---")

health = get_json("/health")
check("GET /health returns OK", health.get("message") == "OK")

ps_data = get_json("/api/ps")
check("GET /api/ps returns model list", "models" in ps_data)

print("\n--- 2. Model Management ---")

models_data = get_json("/v1/models")
model_count = len(models_data.get("data", []))
check(f"GET /v1/models returned {model_count} models", model_count >= 3)
for m in models_data["data"]:
    print(f"    - {m['id']}")

tts_models = get_json("/v1/models?task=text-to-speech")
check("TTS model filter works", len(tts_models.get("data", [])) >= 1)

stt_models = get_json("/v1/models?task=automatic-speech-recognition")
check("STT model filter returns >= 2", len(stt_models.get("data", [])) >= 2)

model_info = get_json("/v1/models/Systran/faster-whisper-base")
check("GET specific model info", "id" in model_info)

audio_models = get_json("/v1/audio/models")
check("GET /v1/audio/models", "models" in audio_models)

voices = get_json("/v1/audio/voices")
check(f"GET /v1/audio/voices returned {len(voices)} voices", len(voices) >= 1)

print("\n--- 3. TTS (Text-to-Speech) ---")

ORIGINAL_TEXT = "People assume that time is a strict progression of cause to effect"

for fmt in ("wav", "mp3", "flac", "opus"):
    audio = post_audio_speech({"model": "tts-1", "input": "Hello world", "voice": "af_bella", "response_format": fmt})
    check(f"TTS {fmt} format ({len(audio)} bytes)", len(audio) > 0)

main_wav = post_audio_speech({"model": "tts-1", "input": ORIGINAL_TEXT, "voice": "af_bella"})
check("TTS main audio generated", len(main_wav) > 0)

heart_wav = post_audio_speech({"model": "tts-1", "input": "Hello world", "voice": "af_heart"})
check("TTS with af_heart voice", len(heart_wav) > 0)

hd_wav = post_audio_speech({"model": "tts-1-hd", "input": "Hello world", "voice": "af_bella"})
check("TTS tts-1-hd alias", len(hd_wav) > 0)

print("\n--- 4. STT (Speech-to-Text) ---")

r = post_form(
    "/v1/audio/transcriptions",
    data={"model": "Systran/faster-whisper-base", "response_format": "json"},
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
stt_base = r.json()
transcribed = stt_base.get("text", "")
check(f"STT whisper-base: {transcribed[:60]}...", len(transcribed) > 0)
key_words = ["people", "assume", "time", "cause", "effect"]
matches = sum(1 for w in key_words if w in transcribed.lower())
check(f"Transcription quality: {matches}/{len(key_words)} key words", matches >= 3)

r = post_form(
    "/v1/audio/transcriptions",
    data={"model": "Systran/faster-whisper-tiny.en", "response_format": "json"},
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
stt_tiny = r.json()
check("STT whisper-tiny.en returned text", len(stt_tiny.get("text", "")) > 0)

r = post_form(
    "/v1/audio/transcriptions",
    data={
        "model": "Systran/faster-whisper-base",
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["word", "segment"],
    },
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
verbose = r.json()
check(f"Verbose JSON: {len(verbose.get('words', []))} words", len(verbose.get("words", [])) > 0)
check(
    f"Verbose JSON: {len(verbose.get('segments', []))} segments",
    len(verbose.get("segments", [])) > 0,
)

r = post_form(
    "/v1/audio/transcriptions",
    data={"model": "Systran/faster-whisper-base", "response_format": "srt"},
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
check("SRT output non-empty", len(r.text.strip()) > 0)

r = post_form(
    "/v1/audio/transcriptions",
    data={"model": "Systran/faster-whisper-base", "response_format": "vtt"},
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
check("VTT output has WEBVTT header", "WEBVTT" in r.text)

r = post_form(
    "/v1/audio/transcriptions",
    data={"model": "Systran/faster-whisper-base", "response_format": "text"},
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
check("Plain text output non-empty", len(r.text.strip()) > 0)

mp3_audio = post_audio_speech({"model": "tts-1", "input": "Hello world", "voice": "af_bella", "response_format": "mp3"})
r = post_form(
    "/v1/audio/transcriptions",
    data={"model": "Systran/faster-whisper-base", "response_format": "json"},
    files={"file": ("audio.mp3", io.BytesIO(mp3_audio), "audio/mpeg")},
)
stt_mp3 = r.json()
check("STT with MP3 input", len(stt_mp3.get("text", "")) > 0)

r = post_form(
    "/v1/audio/transcriptions",
    data={
        "model": "Systran/faster-whisper-base",
        "response_format": "json",
        "vad_filter": "true",
    },
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
stt_vad = r.json()
check("STT with VAD filter", len(stt_vad.get("text", "")) > 0)

print("\n--- 5. Translation ---")

r = post_form(
    "/v1/audio/translations",
    data={"model": "Systran/faster-whisper-base", "response_format": "json"},
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
translation = r.json()
check("Translation endpoint returned text", len(translation.get("text", "")) > 0)

print("\n--- 6. VAD (Voice Activity Detection) ---")

r = post_form(
    "/v1/audio/speech/timestamps",
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
vad_data = r.json()
check(f"VAD detected {len(vad_data)} segment(s)", len(vad_data) >= 1)

r = post_form(
    "/v1/audio/speech/timestamps",
    data={"model": "silero_vad_v6"},
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
vad_v6 = r.json()
check(f"VAD v6 detected {len(vad_v6)} segment(s)", len(vad_v6) >= 1)

r = post_form(
    "/v1/audio/speech/timestamps",
    data={"threshold": "0.5", "min_silence_duration_ms": "500", "speech_pad_ms": "100"},
    files={"file": ("main.wav", io.BytesIO(main_wav), "audio/wav")},
)
vad_custom = r.json()
check("VAD with custom params", isinstance(vad_custom, list))

print("\n--- 7. Model Load/Unload ---")

r = httpx.post(f"{BASE_URL}/api/ps/Systran/faster-whisper-tiny.en", timeout=TIMEOUT)
check(f"Model load endpoint (HTTP {r.status_code})", r.status_code in (201, 409))

ps_after = get_json("/api/ps")
has_tiny = any("tiny.en" in m for m in ps_after.get("models", []))
check("Loaded model visible in /api/ps", has_tiny)

r = httpx.delete(f"{BASE_URL}/api/ps/Systran/faster-whisper-tiny.en", timeout=TIMEOUT)
check(f"Model unload endpoint (HTTP {r.status_code})", r.status_code in (200, 409))

print("\n--- 8. TTS->STT Round-Trip ---")

pipeline_text = "The quick brown fox jumps over the lazy dog near the river bank"
pipeline_wav = post_audio_speech({"model": "tts-1", "input": pipeline_text, "voice": "af_bella"})

r = post_form(
    "/v1/audio/transcriptions",
    data={"model": "Systran/faster-whisper-base"},
    files={"file": ("pipeline.wav", io.BytesIO(pipeline_wav), "audio/wav")},
)
pipe_text = r.json().get("text", "").lower()
pipe_words = ["quick", "brown", "fox", "jumps", "lazy", "dog", "river"]
pipe_matches = sum(1 for w in pipe_words if w in pipe_text)
check(f"Round-trip whisper-base: {pipe_matches}/{len(pipe_words)} words", pipe_matches >= 4)

r = post_form(
    "/v1/audio/transcriptions",
    data={"model": "Systran/faster-whisper-tiny.en"},
    files={"file": ("pipeline.wav", io.BytesIO(pipeline_wav), "audio/wav")},
)
tiny_text = r.json().get("text", "").lower()
tiny_matches = sum(1 for w in pipe_words if w in tiny_text)
check(f"Round-trip whisper-tiny.en: {tiny_matches}/{len(pipe_words)} words", tiny_matches >= 3)

print(f"\n=== Results: {PASSED} passed, {FAILED} failed ===")
if FAILED > 0:
    print(f"{FAILED} test(s) failed!")
    sys.exit(1)
print("All e2e API tests passed!")
