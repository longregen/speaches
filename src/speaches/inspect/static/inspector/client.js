// ---------------------------------------------------------------------------
// client.js — built-in realtime test client for the inspector.
// One button: "New session" → connects, captures mic, plays TTS, shows status.
// ---------------------------------------------------------------------------

(function () {
  const SAMPLE_RATE = 24000;
  const BUFFER_SIZE = 4096;

  const state = {
    ws: null,
    sessionId: null,
    micStream: null,
    micCtx: null,
    micProcessor: null,
    playCtx: null,
    playScheduledEnd: 0,
    status: 'idle', // idle | connecting | live | speaking | processing | error
    lastTranscript: '',
    lastResponse: '',
    responseText: '',
  };

  // --- Audio helpers --------------------------------------------------------

  function resample(samples, fromRate, toRate) {
    if (fromRate === toRate) return samples;
    const ratio = fromRate / toRate;
    const len = Math.floor(samples.length / ratio);
    const out = new Float32Array(len);
    for (let i = 0; i < len; i++) {
      const idx = i * ratio;
      const lo = Math.floor(idx);
      const hi = Math.min(lo + 1, samples.length - 1);
      const f = idx - lo;
      out[i] = samples[lo] * (1 - f) + samples[hi] * f;
    }
    return out;
  }

  function float32ToInt16(f32) {
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      i16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return i16;
  }

  function int16ToBase64(i16) {
    const bytes = new Uint8Array(i16.buffer);
    let bin = '';
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  function base64ToFloat32(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const i16 = new Int16Array(bytes.buffer);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
    return f32;
  }

  // --- Connection -----------------------------------------------------------

  async function connect() {
    if (state.ws) return;
    setStatus('connecting');

    // Acquire mic FIRST — must happen in user-gesture context (click handler).
    try {
      const micId = window.__inspectSelectedMic || localStorage.getItem('inspect.deviceMic') || undefined;
      const audioConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };
      if (micId) audioConstraints.deviceId = { exact: micId };
      const stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
      state.micStream = stream;
      console.log('[client] mic acquired, sample rate will be determined after AudioContext creation');
      // Re-enumerate now that permission is granted (labels + output devices become available)
      if (typeof window.__inspectEnumerateDevices === 'function') window.__inspectEnumerateDevices();
    } catch (err) {
      console.error('[client] mic permission denied or unavailable:', err);
      setStatus('error');
      return;
    }

    // Create AudioContext in user-gesture context too (prevents "suspended" state).
    try {
      state.micCtx = new AudioContext();
      if (state.micCtx.state === 'suspended') await state.micCtx.resume();
      console.log('[client] AudioContext created, sampleRate:', state.micCtx.sampleRate);
    } catch (err) {
      console.error('[client] AudioContext creation failed:', err);
      setStatus('error');
      return;
    }

    // Wire up the mic processing pipeline.
    const source = state.micCtx.createMediaStreamSource(state.micStream);
    const processor = state.micCtx.createScriptProcessor(BUFFER_SIZE, 1, 1);
    const nativeRate = state.micCtx.sampleRate;
    let chunkCount = 0;

    processor.onaudioprocess = (e) => {
      if (!state.ws || state.ws.readyState !== 1) return;
      const raw = e.inputBuffer.getChannelData(0);
      const resampled = resample(raw, nativeRate, SAMPLE_RATE);
      const b64 = int16ToBase64(float32ToInt16(resampled));
      state.ws.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: b64 }));
      chunkCount++;
      if (chunkCount <= 3) console.log('[client] sent audio chunk #' + chunkCount, 'samples:', resampled.length);
    };

    source.connect(processor);
    // Muted output so we don't hear our own mic through speakers.
    const muter = state.micCtx.createGain();
    muter.gain.value = 0;
    processor.connect(muter);
    muter.connect(state.micCtx.destination);
    state.micProcessor = processor;
    console.log('[client] mic pipeline wired, opening WebSocket...');

    // Now connect the WebSocket.
    const model = btn.dataset.model || 'llm-default';
    const sttModel = btn.dataset.stt || 'deepdml/faster-whisper-large-v3-turbo-ct2';
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${scheme}://${location.host}/v1/realtime?model=${encodeURIComponent(model)}&transcription_model=${encodeURIComponent(sttModel)}`;
    console.log('[client] connecting to', url);
    const ws = new WebSocket(url);
    state.ws = ws;
    ws.onopen = () => {
      console.log('[client] WebSocket open');
      setStatus('live');
    };
    ws.onmessage = onMessage;
    ws.onclose = (ev) => {
      console.log('[client] WebSocket closed, code:', ev.code, 'reason:', ev.reason);
      disconnect();
    };
    ws.onerror = (err) => {
      console.error('[client] WebSocket error:', err);
      setStatus('error');
      disconnect();
    };
  }

  function disconnect() {
    stopMic();
    stopPlayback();
    if (state.ws) { try { state.ws.close(); } catch (_) {} state.ws = null; }
    window.__inspectRealtimeWs = null;
    state.sessionId = null;
    state.responseText = '';
    setStatus('idle');
  }

  // --- Server events --------------------------------------------------------

  function onMessage(ev) {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    console.log('[client] <<', msg.type);
    switch (msg.type) {
      case 'session.created':
        state.sessionId = msg.session.id;
        // Expose WS for settings sidebar
        window.__inspectRealtimeWs = state.ws;
        // Populate settings panel from session defaults
        if (typeof window.__inspectPopulateSettings === 'function') {
          window.__inspectPopulateSettings(msg.session);
        }
        // Update voice (mic is already streaming).
        state.ws.send(JSON.stringify({
          type: 'session.update',
          session: {
            voice: btn.dataset.voice || 'af_heart',
            instructions: 'Provide very short, concise answers. Keep responses to one or two sentences whenever possible.',
          },
        }));
        // Auto-open this session in the inspector timeline.
        window.dispatchEvent(
          new CustomEvent('inspector:openSession', { detail: { sid: msg.session.id } })
        );
        break;

      case 'input_audio_buffer.speech_started':
        setStatus('speaking');
        stopPlayback(); // barge-in
        state.responseText = '';
        break;

      case 'input_audio_buffer.speech_stopped':
        setStatus('processing');
        break;

      case 'conversation.item.input_audio_transcription.completed':
        if (msg.transcript && msg.transcript.trim()) {
          state.lastTranscript = msg.transcript.trim();
          renderBadge();
        }
        break;

      case 'response.output_text.delta':
        state.responseText += (msg.delta || '');
        break;

      case 'response.output_audio.delta':
        if (msg.delta) playAudioChunk(msg.delta);
        break;

      case 'response.done':
        if (state.responseText.trim()) {
          state.lastResponse = state.responseText.trim();
          state.responseText = '';
        }
        setStatus('live');
        break;

      case 'error':
        console.error('[client] server error:', msg.error);
        setStatus('error');
        break;
    }
  }

  // --- Microphone -----------------------------------------------------------

  function stopMic() {
    if (state.micProcessor) { state.micProcessor.disconnect(); state.micProcessor = null; }
    if (state.micCtx) { try { state.micCtx.close(); } catch (_) {} state.micCtx = null; }
    if (state.micStream) { state.micStream.getTracks().forEach(t => t.stop()); state.micStream = null; }
  }

  // --- Playback -------------------------------------------------------------

  function playAudioChunk(b64) {
    if (!state.playCtx) {
      state.playCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      state.playScheduledEnd = 0;
    }
    const ctx = state.playCtx;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    const samples = base64ToFloat32(b64);
    const buf = ctx.createBuffer(1, samples.length, SAMPLE_RATE);
    buf.getChannelData(0).set(samples);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    const now = ctx.currentTime;
    const at = Math.max(now + 0.005, state.playScheduledEnd);
    src.start(at);
    state.playScheduledEnd = at + buf.duration;
  }

  function stopPlayback() {
    if (state.playCtx) { try { state.playCtx.close(); } catch (_) {} state.playCtx = null; }
    state.playScheduledEnd = 0;
  }

  // --- UI -------------------------------------------------------------------

  const btn = document.getElementById('btnNewSession');
  if (!btn) return;

  const STATUS_MAP = {
    idle:       { label: 'New session',   cls: '',             dot: false },
    connecting: { label: 'connecting...', cls: 'st-active',    dot: true  },
    live:       { label: 'live',          cls: 'st-live',      dot: true  },
    speaking:   { label: 'listening...',  cls: 'st-speaking',  dot: true  },
    processing: { label: 'processing...', cls: 'st-active',    dot: true  },
    error:      { label: 'error',         cls: 'st-error',     dot: false },
  };

  function setStatus(s) {
    state.status = s;
    renderBadge();
  }

  function renderBadge() {
    const info = STATUS_MAP[state.status] || STATUS_MAP.idle;
    btn.className = 'btn client-badge ' + info.cls;

    let html = '';
    if (info.dot) html += '<span class="client-dot"></span>';

    if (state.status === 'idle') {
      html += 'New session';
    } else {
      html += info.label;
      if (state.lastTranscript && (state.status === 'processing' || state.status === 'live')) {
        html += ' <span class="client-transcript">' + escapeHTML(shorten(state.lastTranscript, 30)) + '</span>';
      }
    }
    btn.innerHTML = html;
  }

  function escapeHTML(s) {
    return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
  function shorten(s, n) { return s.length > n ? s.slice(0, n) + '\u2026' : s; }

  btn.addEventListener('click', () => {
    if (state.status === 'idle' || state.status === 'error') {
      connect();
    } else {
      disconnect();
    }
  });

  // Allow configuring model/voice via URL params.
  const params = new URLSearchParams(location.search);
  btn.dataset.model = params.get('client_model') || 'llm-default';
  btn.dataset.voice = params.get('client_voice') || 'af_heart';
  btn.dataset.stt   = params.get('client_stt')   || 'deepdml/faster-whisper-large-v3-turbo-ct2';

  renderBadge();
})();
