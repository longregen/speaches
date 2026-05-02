const lib = require('./lib');

const WS_URL = `${lib.BASE_URL}/v1/realtime?model=llm-default&transcription_model=deepdml/faster-whisper-large-v3-turbo-ct2`;
const QUESTION = lib.pcmToB64Chunks('long_question.pcm');
const BARGE = lib.pcmToB64Chunks('barge.pcm');
const COUGH = lib.pcmToB64Chunks('cough.pcm');

const SCENARIOS = [
  'E_barge_before_audio_delta',
  'F_cough_during_streaming',
  'G_two_full_drains',
  'H_barge_then_drain_followup',
  'I_cough_at_drain_start',
  'J_barge_near_end_of_drain',
  'K_two_barges_one_session',
  'L_long_silence_prelude',
  'M_rapid_three_turns',
  'N_cough_then_real_barge',
];

const WIDE_BARGE_SCENARIOS = new Set(['I_cough_at_drain_start', 'N_cough_then_real_barge']);

async function runOne(name) {
  console.log(`\n========== ${name} ==========`);
  const sessionUpdate = {
    instructions: 'Respond in two short sentences only. Be vivid but very brief.',
    voice: 'af_heart',
    max_response_output_tokens: 60,
  };
  if (WIDE_BARGE_SCENARIOS.has(name)) {
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

    async function askAndDrain(label) {
      log(`${label}: sending question`); await send(a.question); await silence(1500);
      const done = await waitFor(e => e.type === 'response.output_audio.done', 60000, `${label} audio.done`);
      const ms = done.audio_duration_ms ?? 0;
      log(`${label}: audio_duration_ms=${ms}; idling ${ms + 2000}ms`);
      await idle(Math.max(2000, ms + 2000));
    }

    switch (a.name) {
      case 'E_barge_before_audio_delta': {
        log('sending question'); await send(a.question); await silence(700);
        await waitFor(e => e.type === 'response.created', 30000, 'response.created');
        log('barging before any audio delta'); await send(a.barge); await silence(800);
        await silence(12000);
        break;
      }
      case 'F_cough_during_streaming': {
        log('sending question'); await send(a.question); await silence(700);
        await waitFor(e => e.type === 'response.output_audio.delta', 30000, 'first audio.delta');
        log('coughing mid-stream'); await silence(200); await send(a.cough); await silence(800);
        try { await waitFor(e => e.type === 'response.done', 15000, 'response.done'); } catch (e) { log(e.message); }
        await idle(4000);
        break;
      }
      case 'G_two_full_drains': {
        await askAndDrain('Q1'); await silence(500); await askAndDrain('Q2');
        break;
      }
      case 'H_barge_then_drain_followup': {
        log('Q1: sending question'); await send(a.question); await silence(700);
        await waitFor(e => e.type === 'response.output_audio.delta', 30000, 'Q1 first audio.delta');
        log('Q1: barging mid-stream'); await send(a.barge); await silence(800);
        try { await waitFor(e => e.type === 'response.done', 15000, 'Q1 response.done'); } catch (e) { log(e.message); }
        log('Q2: waiting for follow-up audio.done');
        const done = await waitFor(e => e.type === 'response.output_audio.done' && e.response_id !== undefined, 30000, 'Q2 audio.done');
        const ms = done.audio_duration_ms ?? 0;
        await idle(ms + 2500);
        break;
      }
      case 'I_cough_at_drain_start': {
        log('sending question'); await send(a.question); await silence(700);
        const done = await waitFor(e => e.type === 'response.output_audio.done', 60000, 'audio.done');
        const ms = done.audio_duration_ms ?? 0;
        log(`audio_duration_ms=${ms}; coughing within 50ms of drain start`);
        await silence(50); await send(a.cough); await silence(700);
        await idle(Math.max(3000, ms - 700 + 2500));
        break;
      }
      case 'J_barge_near_end_of_drain': {
        log('sending question'); await send(a.question); await silence(700);
        const done = await waitFor(e => e.type === 'response.done', 60000, 'response.done');
        const ms = done.response?.usage?.output_audio_duration_ms ?? 0;
        const wait = Math.floor(ms * 0.8);
        log(`waiting ${wait}ms (~80% of drain) before barge`);
        await silence(wait); await send(a.barge); await silence(800); await silence(12000);
        break;
      }
      case 'K_two_barges_one_session': {
        log('Q1: sending question'); await send(a.question); await silence(700);
        await waitFor(e => e.type === 'response.output_audio.delta', 30000, 'Q1 first audio.delta');
        log('Q1: barging'); await send(a.barge); await silence(800);
        try { await waitFor(e => e.type === 'response.done', 15000, 'Q1 response.done'); } catch (e) { log(e.message); }
        const before = events.length;
        log('Q2: waiting for follow-up audio.delta');
        await waitFor(e => e.type === 'response.output_audio.delta' && events.indexOf(e) >= before, 30000, 'Q2 first audio.delta');
        log('Q2: barging again'); await send(a.barge); await silence(800);
        try { await waitFor(e => e.type === 'response.done' && events.indexOf(e) >= before, 15000, 'Q2 response.done'); } catch (e) { log(e.message); }
        await silence(10000);
        break;
      }
      case 'L_long_silence_prelude': {
        log('sending 3s silence prelude'); await silence(3000);
        await askAndDrain('Q');
        break;
      }
      case 'M_rapid_three_turns': {
        await askAndDrain('Q1'); await silence(300);
        await askAndDrain('Q2'); await silence(300);
        await askAndDrain('Q3');
        break;
      }
      case 'N_cough_then_real_barge': {
        log('sending question'); await send(a.question); await silence(700);
        const done = await waitFor(e => e.type === 'response.output_audio.done', 60000, 'audio.done');
        const ms = done.audio_duration_ms ?? 0;
        log(`audio_duration_ms=${ms}; 500ms into drain before cough`);
        await silence(500); log('coughing'); await send(a.cough); await silence(700);
        log('now real barge inside same drain'); await send(a.barge); await silence(800);
        await silence(12000);
        break;
      }
    }
    log(`closing ws (sessionId=${state.sessionId})`);
    ws.close(); await idle(500);
    return { sessionId: state.sessionId, eventCount: events.length };
  }, { wsUrl: WS_URL, question: QUESTION, barge: BARGE, cough: COUGH, silence: lib.SILENCE_200MS, name, sessionUpdate });

  console.log(`Result: sessionId=${r.sessionId}, events=${r.eventCount}`);
  return r.sessionId;
}

lib.runAll(SCENARIOS, runOne, 'sessions_extra.json');
