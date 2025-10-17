# Standalone e2e test (no VM required)
{
  pkgs,
  speachesPackage,
  modelCache,
}:
pkgs.writeShellScriptBin "speaches-e2e-test" ''
  set -euo pipefail

  echo "=== Speaches E2E Test ==="

  export HF_HUB_CACHE="${modelCache}"
  export HF_HUB_OFFLINE=1

  TEST_DIR=$(mktemp -d)
  cd "$TEST_DIR"
  echo "Test directory: $TEST_DIR"

  echo "Starting server..."
  ${speachesPackage}/bin/speaches --host 127.0.0.1 --port 18000 &
  SERVER_PID=$!

  cleanup() {
    echo "Cleaning up..."
    kill $SERVER_PID 2>/dev/null || true
    wait $SERVER_PID 2>/dev/null || true
  }
  trap cleanup EXIT

  echo "Waiting for server..."
  for i in $(seq 1 120); do
    if ${pkgs.curl}/bin/curl -s http://127.0.0.1:18000/health >/dev/null 2>&1; then
      echo "Server ready!"
      break
    fi
    [ $i -eq 120 ] && { echo "Server timeout"; exit 1; }
    [ $((i % 10)) -eq 0 ] && echo "Waiting... ($i/120)"
    sleep 1
  done

  echo "Testing health..."
  ${pkgs.curl}/bin/curl -f http://127.0.0.1:18000/health

  echo "Testing models endpoint..."
  ${pkgs.curl}/bin/curl -s http://127.0.0.1:18000/v1/models | ${pkgs.jq}/bin/jq -e '.data'

  ORIGINAL_TEXT="People assume that time is a strict progression of cause to effect. But actually, from a nonlinear, non-subjective viewpoint, it is more like a big ball of wibbly wobbly, timey wimey stuff."

  echo "Testing TTS..."
  ${pkgs.curl}/bin/curl -s -X POST "http://127.0.0.1:18000/v1/audio/speech" \
    -H "Content-Type: application/json" \
    -d "{\"model\": \"tts-1\", \"input\": \"$ORIGINAL_TEXT\", \"voice\": \"af_bella\"}" \
    -o test.wav

  [ -s test.wav ] || { echo "TTS failed"; exit 1; }
  echo "TTS OK: $(${pkgs.coreutils}/bin/du -h test.wav | cut -f1)"

  echo "Testing STT..."
  TRANSCRIPTION=$(${pkgs.curl}/bin/curl -s -X POST "http://127.0.0.1:18000/v1/audio/transcriptions" \
    -F "file=@test.wav" \
    -F "model=Systran/faster-whisper-base")

  echo "Transcription: $TRANSCRIPTION"
  TRANSCRIBED_TEXT=$(echo "$TRANSCRIPTION" | ${pkgs.jq}/bin/jq -r '.text') || { echo "STT failed"; exit 1; }

  # Compare words: lowercase, strip punctuation, check 80% overlap
  normalize() { echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs '[:alnum:]' '\n' | sort -u; }
  ORIG_WORDS=$(normalize "$ORIGINAL_TEXT")
  TRANS_WORDS=$(normalize "$TRANSCRIBED_TEXT")
  ORIG_COUNT=$(echo "$ORIG_WORDS" | wc -l)
  MATCHED=$(comm -12 <(echo "$ORIG_WORDS") <(echo "$TRANS_WORDS"))
  MISSED=$(comm -23 <(echo "$ORIG_WORDS") <(echo "$TRANS_WORDS"))
  EXTRA=$(comm -13 <(echo "$ORIG_WORDS") <(echo "$TRANS_WORDS"))
  MATCH_COUNT=$(echo "$MATCHED" | wc -l)
  if [ "$ORIG_COUNT" -eq 0 ]; then
    echo "FAIL: no words in original text"; exit 1
  fi
  PCT=$((MATCH_COUNT * 100 / ORIG_COUNT))
  echo "Original:    $ORIGINAL_TEXT"
  echo "Transcribed: $TRANSCRIBED_TEXT"
  echo "Word match: $MATCH_COUNT/$ORIG_COUNT ($PCT%)"
  [ -n "$MISSED" ] && echo "Missed words: $MISSED" | tr '\n' ' ' && echo
  [ -n "$EXTRA" ] && echo "Extra words:  $EXTRA" | tr '\n' ' ' && echo
  if [ "$PCT" -lt 80 ]; then
    echo "FAIL: word match $PCT% < 80% threshold"; exit 1
  fi

  echo "=== E2E Test Passed ==="
''
