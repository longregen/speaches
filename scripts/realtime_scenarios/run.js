const lib = require('./lib');

const WS_URL = `${lib.BASE_URL}/v1/realtime?model=llm-default&transcription_model=deepdml/faster-whisper-large-v3-turbo-ct2`;
const QUESTION = lib.pcmToB64Chunks('long_question.pcm');
const BARGE = lib.pcmToB64Chunks('barge.pcm');
const COUGH = lib.pcmToB64Chunks('cough.pcm');

const SCENARIOS = ['A_full_drain', 'B_barge_during_streaming', 'C_barge_during_drain', 'D_cough_during_drain'];

async function runOne(name) {
  console.log(`\n========== ${name} ==========`);
  const sessionUpdate = {
    instructions: 'Respond in two short sentences only. Be vivid but very brief.',
    voice: 'af_heart',
    max_response_output_tokens: 60,
  };
  if (name === 'D_cough_during_drain') {
    // Wide barge_in_delay so the cough has room to be suppressed by speech_stopped.
    sessionUpdate.turn_detection = {
      type: 'server_vad', threshold: 0.8, prefix_padding_ms: 300,
      silence_duration_ms: 350, create_response: true,
      min_speech_duration_ms: 120, barge_in_delay_ms: 1500,
    };
  }

  const r = await lib.runPage(async (a) => {
    const { ws, events, state } = await window.__h.openWs(a.wsUrl);
    ws.send(JSON.stringify({ type: 'session.update', session: a.sessionUpdate }));
    const send = (c, p) => window.__h.sendChunks(ws, c, p);
    const silence = (ms, p) => window.__h.sendSilence(ws, a.silence, ms, p);
    const waitFor = (pred, to, label) => window.__h.waitFor(events, pred, to, label);
    const idle = window.__h.idle;
    const log = (m) => console.log(`[${a.name}] ${m}`);

    if (a.name === 'A_full_drain') {
      log('sending question'); await send(a.question); await silence(1500);
      const done = await waitFor(e => e.type === 'response.output_audio.done', 60000, 'audio.done');
      const ms = done.audio_duration_ms ?? 0;
      log(`audio_duration_ms=${ms}; idling ${ms + 2000}ms`);
      await idle(Math.max(2000, ms + 2000));
    } else if (a.name === 'B_barge_during_streaming') {
      log('sending question'); await send(a.question); await silence(700);
      await waitFor(e => e.type === 'response.created', 30000, 'response.created');
      await waitFor(e => e.type === 'response.output_audio.delta', 30000, 'first audio.delta');
      log('barging mid-stream'); await silence(200); await send(a.barge); await silence(800);
      try { await waitFor(e => e.type === 'response.done', 15000, 'response.done'); } catch (e) { log(e.message); }
      await silence(15000);
    } else if (a.name === 'C_barge_during_drain') {
      log('sending question'); await send(a.question); await silence(700);
      const done = await waitFor(e => e.type === 'response.done', 60000, 'response.done');
      const ms = done.response?.usage?.output_audio_duration_ms ?? 0;
      const into = Math.max(800, Math.floor(ms * 0.3));
      log(`waiting ${into}ms into drain before barge`);
      await silence(into); await send(a.barge); await silence(800); await silence(15000);
    } else if (a.name === 'D_cough_during_drain') {
      log('sending question'); await send(a.question); await silence(700);
      const done = await waitFor(e => e.type === 'response.output_audio.done', 60000, 'audio.done');
      const ms = done.audio_duration_ms ?? 0;
      log(`audio_duration_ms=${ms}; 800ms into drain before cough`);
      await silence(800); log('coughing'); await send(a.cough); await silence(700);
      await idle(Math.max(3000, ms - 1500 + 2500));
    }
    log(`closing ws (sessionId=${state.sessionId})`);
    ws.close(); await idle(500);
    return { sessionId: state.sessionId, eventCount: events.length };
  }, { wsUrl: WS_URL, question: QUESTION, barge: BARGE, cough: COUGH, silence: lib.SILENCE_200MS, name, sessionUpdate });

  console.log(`Result: sessionId=${r.sessionId}, events=${r.eventCount}`);
  return r.sessionId;
}

lib.runAll(SCENARIOS, runOne, 'sessions.json');
