# Realtime scenario harness

Playwright-driven end-to-end tests for the `/v1/realtime` WebSocket. Each
runner scripts a session (audio in, events out), writes the resulting
session ids to a JSON file, and the NDJSON log for each session can be
pulled from the server afterwards for offline analysis.

## Files

```
lib.js                    Shared env config + browser harness injected into every scenario
run.js                    A/B/C/D   barge-in & drain in default conversation mode
run_extra.js              E-N       barge timing edges, multi-turn, false-start paths
run_modes.js              O-R       audio-direct vs STT+LLM against a multimodal model
run_stt.js                A/B/D     same scenarios forced to audio_direct_to_llm=false
run_transcription.js      T_*       intent=transcription: asserts real STT + no LLM
snap_inspector.js         Screenshots the inspector UI for each captured session
fetch_ndjson.sh           Pulls /v1/inspect/sessions/history/<sid> for every session id
analyze.py                Per-scenario timeline + verdict for A-D
audio/                    PCM16 mono 24kHz fixtures (long_question, barge, cough)
```

## Scenarios

| ID  | What it exercises |
|-----|-------------------|
| `A_full_drain` | Question -> response -> drain runs to completion. `turn_end` lands ~`audio_duration_ms` after `response.done`. |
| `B_barge_during_streaming` | Barge mid-stream. `turn_end` is `truncated` with non-zero `played_ms`. |
| `C_barge_during_drain` | Barge after `response.done`, before drain timer fires. Truncates via drain-cancel. |
| `D_cough_during_drain` | Brief noise during drain -> `bargein_pending` then `bargein_cancelled` (`reason=speech_stopped`); drain still completes. |
| `E_` .. `N_` | See `run_extra.js` header. |
| `O-R` | Same flows under `audio_direct_to_llm` on/off for comparison. |
| `T_transcribe_basic` | `intent=transcription`: STT completes, zero `response.*` events. |
| `T_transcribe_with_audio_direct_flag` | Same, plus `audio_direct_to_llm=true` -- the server-side guard at `input_audio_buffer.start` must still route to real STT (transcript must not be the `"[audio direct to LLM]"` placeholder). |

## Usage

```bash
# 1. Install Playwright once.
cd scripts/realtime_scenarios
npm install

# 2. Drive scenarios against a local server.
SPEACHES_URL=ws://127.0.0.1:1327 \
CHROMIUM_PATH=$(which chromium) \
OUT_DIR=/tmp/realtime_scenarios \
node run.js

# Or pick one.
node run.js B_barge_during_streaming

# 3. Pull the NDJSONs (all sessions_*.json by default).
SPEACHES_HTTP=http://127.0.0.1:1327 OUT_DIR=/tmp/realtime_scenarios \
  ./fetch_ndjson.sh

# 4. Analyze the A-D set.
./analyze.py --dir /tmp/realtime_scenarios
```

`run_transcription.js` exits non-zero if the transcript is empty, is the
`[audio direct to LLM]` placeholder, or if any `response.*` events show up
in the stream -- the three failure modes that would mean transcription
mode regressed.

## Audio assets

PCM16 mono 24 kHz. Regenerate via the bundled TTS if you want different
prompts:

```bash
curl -s http://127.0.0.1:1327/v1/audio/speech \
  -H 'content-type: application/json' \
  -d '{"model":"hexgrad/Kokoro-82M","voice":"af_heart","input":"...","response_format":"pcm"}' \
  > audio/long_question.pcm
```

## Notes

- Uploads are paced at 2x realtime (100 ms wall per 200 ms chunk) so suites
  finish fast while VAD still treats the stream as continuous. Drain-window
  waits use plain `setTimeout` (`idle()`) since wall time must advance.
- `D_cough_during_drain` and the `I/N` false-start scenarios widen
  `barge_in_delay_ms` to 1500 so the short cough has room to be suppressed
  by `speech_stopped`.
- The chromium arg `--disable-features=LocalNetworkAccessChecks,...` is
  needed for Chromium 147+ to allow `ws://localhost` from `about:blank`.
