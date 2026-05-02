const lib = require('./lib');

const MODEL = process.env.MODEL || 'gemma-4-e4b-audio';
const WS_URL = `${lib.BASE_URL}/v1/realtime?model=${encodeURIComponent(MODEL)}&transcription_model=deepdml/faster-whisper-large-v3-turbo-ct2`;
const QUESTION = lib.pcmToB64Chunks('long_question.pcm');
const BARGE = lib.pcmToB64Chunks('barge.pcm');

const SCENARIOS = {
  O_full_drain_audio_direct: { mode: 'audio_direct', flow: 'full_drain' },
  P_barge_audio_direct:      { mode: 'audio_direct', flow: 'barge_stream' },
  Q_full_drain_stt_llm:      { mode: 'stt_llm',      flow: 'full_drain' },
  R_barge_stt_llm:           { mode: 'stt_llm',      flow: 'barge_stream' },
};

async function runOne(id) {
  const sc = SCENARIOS[id];
  console.log(`\n========== ${id} (mode=${sc.mode}, flow=${sc.flow}) ==========`);
  const sessionUpdate = {
    instructions: 'Respond in two short sentences only. Be vivid but very brief.',
    voice: 'af_heart',
    max_response_output_tokens: 60,
    audio_direct_to_llm: sc.mode === 'audio_direct',
    audio_direct_model: MODEL,
    input_audio_transcription: { model: 'deepdml/faster-whisper-large-v3-turbo-ct2' },
  };

  const r = await lib.runPage(async (a) => {
    const { ws, events, state } = await window.__h.openWs(a.wsUrl);
    ws.send(JSON.stringify({ type: 'session.update', session: a.sessionUpdate }));
    const send = (c, p) => window.__h.sendChunks(ws, c, p);
    const silence = (ms, p) => window.__h.sendSilence(ws, a.silence, ms, p);
    const waitFor = (pred, to, label) => window.__h.waitFor(events, pred, to, label);
    const idle = window.__h.idle;
    const log = (m) => console.log(`[${a.id}] ${m}`);

    if (a.flow === 'full_drain') {
      log('sending question'); await send(a.question); await silence(1500);
      const done = await waitFor(e => e.type === 'response.output_audio.done', 60000, 'audio.done');
      const ms = done.audio_duration_ms ?? 0;
      log(`audio_duration_ms=${ms}; idling ${ms + 2500}ms`);
      await idle(ms + 2500);
    } else if (a.flow === 'barge_stream') {
      log('sending question'); await send(a.question); await silence(700);
      await waitFor(e => e.type === 'response.created', 30000, 'response.created');
      await waitFor(e => e.type === 'response.output_audio.delta', 30000, 'audio.delta');
      log('barging'); await silence(200); await send(a.barge); await silence(800);
      try { await waitFor(e => e.type === 'response.done', 15000, 'response.done'); } catch (e) { log(e.message); }
      await silence(15000);
    }
    log(`closing ws (sessionId=${state.sessionId})`);
    ws.close(); await idle(500);
    return { sessionId: state.sessionId, eventCount: events.length };
  }, { wsUrl: WS_URL, question: QUESTION, barge: BARGE, silence: lib.SILENCE_200MS, id, flow: sc.flow, sessionUpdate });

  console.log(`Result: sessionId=${r.sessionId}, events=${r.eventCount}`);
  return r.sessionId;
}

lib.runAll(Object.keys(SCENARIOS), runOne, 'sessions_modes.json');
