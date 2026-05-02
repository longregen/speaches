const lib = require('./lib');

const STT_MODEL = 'deepdml/faster-whisper-large-v3-turbo-ct2';
const WS_URL = `${lib.BASE_URL}/v1/realtime?intent=transcription&model=${encodeURIComponent(STT_MODEL)}`;
const QUESTION = lib.pcmToB64Chunks('long_question.pcm');

const SCENARIOS = ['T_transcribe_basic', 'T_transcribe_with_audio_direct_flag'];

const PLACEHOLDER = '[audio direct to LLM]';

async function runOne(name) {
  console.log(`\n========== ${name} ==========`);
  const sessionUpdate = {
    input_audio_transcription: { model: STT_MODEL },
  };
  if (name === 'T_transcribe_with_audio_direct_flag') {
    // The server-side guard should ignore this in transcription-only mode and
    // still run real STT, yielding a real transcript rather than the placeholder.
    sessionUpdate.audio_direct_to_llm = true;
  }

  const r = await lib.runPage(async (a) => {
    const { ws, events, state } = await window.__h.openWs(a.wsUrl);
    ws.send(JSON.stringify({ type: 'session.update', session: a.sessionUpdate }));
    const send = (c, p) => window.__h.sendChunks(ws, c, p);
    const silence = (ms, p) => window.__h.sendSilence(ws, a.silence, ms, p);
    const waitFor = (pred, to, label) => window.__h.waitFor(events, pred, to, label);
    const idle = window.__h.idle;
    const log = (m) => console.log(`[${a.name}] ${m}`);

    log('sending question'); await send(a.question); await silence(1500);
    const completed = await waitFor(
      e => e.type === 'conversation.item.input_audio_transcription.completed',
      60000, 'transcription.completed',
    );
    log(`transcript: ${JSON.stringify(completed.transcript)}`);
    await idle(1000);

    log(`closing ws (sessionId=${state.sessionId})`);
    ws.close(); await idle(500);
    return {
      sessionId: state.sessionId,
      eventCount: events.length,
      transcript: completed.transcript ?? null,
      responseEventCount: events.filter(e => typeof e.type === 'string' && e.type.startsWith('response.')).length,
    };
  }, { wsUrl: WS_URL, question: QUESTION, silence: lib.SILENCE_200MS, name, sessionUpdate });

  console.log(`Result: sessionId=${r.sessionId}, events=${r.eventCount}, responseEvents=${r.responseEventCount}`);
  console.log(`Transcript: ${JSON.stringify(r.transcript)}`);

  const problems = [];
  if (!r.transcript) problems.push('empty transcript');
  if (r.transcript === PLACEHOLDER) problems.push(`transcript is placeholder "${PLACEHOLDER}" (audio-direct leaked into transcription mode)`);
  if (r.responseEventCount > 0) problems.push(`${r.responseEventCount} response.* events (expected 0 in transcription-only mode)`);
  if (problems.length) {
    console.error(`  FAIL: ${problems.join('; ')}`);
    throw new Error(problems.join('; '));
  }
  console.log('  OK');
  return r.sessionId;
}

lib.runAll(SCENARIOS, runOne, 'sessions_transcription.json').then((sessions) => {
  const failed = Object.entries(sessions).filter(([, v]) => v === null);
  if (failed.length) process.exit(1);
});
