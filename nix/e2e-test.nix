{
  pythonVersion,
  fullTest ? true,
}:
if fullTest
then ''
  import json
  import time

  machine.start()
  machine.wait_for_unit("speaches.service")
  machine.wait_for_open_port(18000)

  time.sleep(5)

  print("Testing health endpoint...")
  machine.succeed("curl -f http://127.0.0.1:18000/health")

  print("Testing model listing...")
  models_output = machine.succeed("curl -s http://127.0.0.1:18000/v1/models")
  models_data = json.loads(models_output)
  assert "data" in models_data

  machine.succeed("mkdir -p /tmp/test_results")

  original_text = "People assume that time is a strict progression of cause to effect. But actually, from a nonlinear, non-subjective viewpoint, it is more like a big ball of wibbly wobbly, timey wimey stuff."

  print("Testing TTS with Kokoro model...")
  machine.succeed(f"""
    curl -f -X POST "http://127.0.0.1:18000/v1/audio/speech" \
      -H "Content-Type: application/json" \
      -d '{{"model": "tts-1", "input": "{original_text}", "voice": "af_bella", "response_format": "mp3"}}' \
      -o /tmp/test_results/test_output.mp3
  """)

  machine.succeed("test -f /tmp/test_results/test_output.mp3")
  machine.succeed("test -s /tmp/test_results/test_output.mp3")

  print("Testing STT with Whisper base model...")
  machine.succeed("""
    curl -f -X POST "http://127.0.0.1:18000/v1/audio/transcriptions" \
      -F "file=@/tmp/test_results/test_output.mp3" \
      -F "model=Systran/faster-whisper-base" \
      -o /tmp/test_results/transcription.json
  """)

  machine.succeed("test -f /tmp/test_results/transcription.json")
  machine.succeed("test -s /tmp/test_results/transcription.json")
  transcription_output = machine.succeed("cat /tmp/test_results/transcription.json")
  print(f"Transcription response: {transcription_output}")
  transcription_data = json.loads(transcription_output)
  assert "text" in transcription_data

  transcribed_text = transcription_data['text']
  print("TTS->STT pipeline test passed with ${pythonVersion}!")
  print(f"Original text: {original_text}")
  print(f"Transcribed text: {transcribed_text}")

  assert len(transcribed_text) > 0
  assert any(word in transcribed_text.lower() for word in ["people", "assume", "viewpoint", "cause"])

  print("All tests passed successfully!")
''
else ''
  machine.start()
  machine.wait_for_unit("speaches.service")
  machine.wait_for_open_port(18000)
  print("Testing ${pythonVersion} package...")
  machine.succeed("curl -f http://127.0.0.1:18000/health")
  print("${pythonVersion} e2e test passed!")
''
