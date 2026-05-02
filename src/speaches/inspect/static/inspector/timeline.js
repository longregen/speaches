(function () {
  const D_FALLBACK = {
    LANES: [
      { id: 'error',       name: 'Error',       hint: '' },
      { id: 'audio_level', name: 'Audio',       hint: 'PCM RMS' },
      { id: 'vad',         name: 'VAD',         hint: 'Silero' },
      { id: 'stt',         name: 'STT',         hint: 'whisper' },
      { id: 'turn',        name: 'Turn',        hint: 'boundaries' },
      { id: 'bargein',     name: 'Barge-in',    hint: 'user interrupts' },
      { id: 'llm',         name: 'LLM',         hint: 'model' },
      { id: 'response',    name: 'Response',    hint: 'plan / phrase' },
      { id: 'tool',        name: 'Tool',        hint: 'use / result / summary' },
      { id: 'tts_req',     name: 'TTS phrases', hint: 'tts executor' },
      { id: 'tts_chunk',   name: 'TTS chunks',  hint: '24 kHz PCM' },
      { id: 'wire',        name: 'Wire',        hint: 'protocol' },
    ],
    PALETTES: {
      warm:     { audio_level:'#6E7C7F', vad:'#7A92A8', stt:'#7F9B7F', turn:'#9F7E9B', bargein:'#C06B8A', llm:'#A8906A', response:'#C89B6A', tool:'#9CA88A', tts_req:'#C4A45A', tts_chunk:'#B88B5A', wire:'#9B9590', error:'#B88080' },
      semantic: { audio_level:'#6BBED3', vad:'#6FA8DC', stt:'#6BBE7F', turn:'#C77BBA', bargein:'#F07B90', llm:'#C8A2E8', response:'#E8A96B', tool:'#88C9A1', tts_req:'#E8A96B', tts_chunk:'#E8C76B', wire:'#9B9590', error:'#E87878' },
      mono:     { audio_level:'#5A564F', vad:'#8D7A5A', stt:'#A8906A', turn:'#8E7958', bargein:'#A67A66', llm:'#C8B08E', response:'#DEC49B', tool:'#A8B091', tts_req:'#DEC49B', tts_chunk:'#BC9C6E', wire:'#726B62', error:'#B88080' },
    },
  };
  // Lazy accessor: timeline.js loads before app.js sets INSPECTOR_DATA.
  const D = new Proxy({}, {
    get(_target, prop) {
      const src = window.INSPECTOR_DATA || D_FALLBACK;
      return src[prop];
    },
  });

  const ERROR_KINDS = new Set(['error', 'phrase_error', 'dropped', 'raised', 'bargein_missed']);

  function makeBand(lane, kind, t0, t1, { open, close = null, corr = {}, ongoing = false } = {}) {
    return { lane, kind, t0, t1, open, close, corr, ongoing };
  }
  function makeTurn(turnId, t0, t1) {
    return { turn_id: turnId, t0, t1 };
  }

  function Timeline(opts) {
    this.lanes = D.LANES;
    this.events = [];
    this.bands = [];
    this.turns = [];
    this.ttsPhraseTexts = new Map();
    this.ttsChunkRows = 1;

    this.view = { t0: 0, pxPerMs: 0.25 };
    this.followTail = true;
    this.paused = false;

    this.treatment = 'blocks';
    this.density = 'comfortable';
    this.palette = 'warm';

    this.rulerCanvas = opts.rulerCanvas;
    this.tlCanvas    = opts.tlCanvas;
    this.mmCanvas    = opts.mmCanvas;
    this.tlWrap      = opts.tlWrap;
    this.gutter      = opts.gutter;

    this.hover = null;
    this.selected = null;
    this.cursorMs = null;
    this.playbackRange = null;
    this.playheadMs = null;

    this._dpr = Math.max(1, window.devicePixelRatio || 1);
    this.bindResize();
    this.renderGutter();
  }

  Timeline.prototype.bindResize = function () {
    const resize = () => {
      [this.rulerCanvas, this.tlCanvas, this.mmCanvas].forEach(c => {
        const r = c.getBoundingClientRect();
        c.width = Math.max(1, Math.floor(r.width * this._dpr));
        c.height = Math.max(1, Math.floor(r.height * this._dpr));
      });
      this.draw();
    };
    new ResizeObserver(resize).observe(this.tlWrap);
    new ResizeObserver(resize).observe(this.rulerCanvas);
    new ResizeObserver(resize).observe(this.mmCanvas);
    requestAnimationFrame(resize);
  };

  Timeline.prototype._tlPxWidth  = function () { return this.tlCanvas.getBoundingClientRect().width; };
  Timeline.prototype._tlPxHeight = function () { return this.tlCanvas.getBoundingClientRect().height; };

  Timeline.prototype.setEvents = function (events) {
    this.events = events.slice();
    this.rebuildBands();
    this.rebuildTurns();
    this.draw();
  };
  Timeline.prototype.appendEvent = function (ev) {
    this.events.push(ev);
    if (this._frameScheduled) return;
    this._frameScheduled = true;
    requestAnimationFrame(() => {
      this._frameScheduled = false;
      this.rebuildBands();
      this.rebuildTurns();
      if (this.followTail && !this.paused) this.followTailIntoView();
      this.draw();
    });
  };
  Timeline.prototype.followTailIntoView = function () {
    if (!this.events.length) return;
    const last = this.events[this.events.length - 1].t;
    const spanMs = this._tlPxWidth() / this.view.pxPerMs;
    const pad = 80 / this.view.pxPerMs;
    this.view.t0 = Math.max(0, last + pad - spanMs);
  };

  Timeline.prototype.rebuildBands = function () {
    const s = {
      bands: [],
      vadOpen: new Map(), vadDone: new Map(),
      llmOpen: new Map(),
      respOpen: new Map(),
      ttsOpen: new Map(),
      ttsPlaybackOrigin: new Map(),
      ttsPlaybackCursor: new Map(),
      ttsPhraseTexts: new Map(),
      bargeinOpen: new Map(),
    };
    for (const e of this.events) ingestBandEvent(s, e);
    const lastT = this.events.length ? this.events[this.events.length - 1].t : 0;
    closeOngoingBands(s, lastT);
    this.ttsPhraseTexts = s.ttsPhraseTexts;
    this.bands = s.bands;
    const prevRows = this.ttsChunkRows;
    this.ttsChunkRows = assignTtsChunkRows(this.bands);
    if (this.ttsChunkRows !== prevRows) this.refreshGutterHeights();
  };

  // Greedy interval packing: each tts_chunk goes on the lowest row whose last band ended before t0.
  function assignTtsChunkRows(bands) {
    const chunks = bands.filter(b => b.lane === 'tts_chunk');
    chunks.sort((a, b) => a.t0 - b.t0);
    const rowEnds = [];
    let maxRow = 0;
    for (const band of chunks) {
      let row = -1;
      for (let r = 0; r < rowEnds.length; r++) {
        if (rowEnds[r] <= band.t0) { row = r; break; }
      }
      if (row < 0) { row = rowEnds.length; rowEnds.push(band.t1); }
      else { rowEnds[row] = band.t1; }
      band.row = row;
      if (row > maxRow) maxRow = row;
    }
    return Math.max(1, maxRow + 1);
  }

  function ingestBandEvent(s, e) {
    const kind = e.kind;
    switch (e.lane) {
      case 'vad':       return ingestVad(s, e, kind);
      case 'stt':       return (kind === 'final' || kind === 'audio_direct') ? ingestSttFinal(s, e) : undefined;
      case 'llm':       return ingestLlm(s, e, kind);
      case 'response':  return ingestResponse(s, e, kind);
      case 'tts_req':   return ingestTtsReq(s, e, kind);
      case 'tts_chunk': return (kind === 'chunk' || kind === 'first_chunk') ? ingestTtsChunk(s, e) : undefined;
      case 'bargein':   return ingestBargein(s, e, kind);
      case 'tool':      return ingestTool(s, e, kind);
    }
  }

  // Tool lane: each tool's lifecycle =
  //   use_token (LLM emitted the call)
  //   → result (tool returned)
  //   → start_summary (narrator began computing a summary)
  //   → summary (narrator wrote a summary).
  // Open the band on use_token, extend it through whichever of
  // {result, start_summary, summary} arrives last so the band's right
  // edge tracks the most informative moment. start_summary/summary
  // also stamp ticks within the band. If the band never closes,
  // it stays ongoing (drawn with an open right edge).
  function ingestTool(s, e, kind) {
    const p = e.payload || {};
    const name = p.name;
    if (!name) return;
    if (!s.toolOpen) s.toolOpen = new Map();
    if (!s.toolBand) s.toolBand = new Map();
    if (kind === 'use_token') {
      s.toolOpen.set(name, e);
    } else if (kind === 'result' || kind === 'start_summary' || kind === 'summary') {
      const open = s.toolOpen.get(name);
      if (!open) return;
      const existing = s.toolBand.get(name);
      if (existing) {
        existing.t1 = e.t;
        existing.close = e;
        // Prefer the most informative kind for label rendering:
        //   summary > start_summary > result.
        if (kind === 'summary' || (kind === 'start_summary' && existing.kind === 'result')) {
          existing.kind = kind;
        }
        existing.ongoing = false;
      } else {
        const band = makeBand('tool', kind, open.t, e.t, { open, close: e, corr: e.corr || {} });
        s.toolBand.set(name, band);
        s.bands.push(band);
      }
    }
  }

  function ingestVad(s, e, kind) {
    const c = e.corr || {};
    if (kind === 'confirmed_start') {
      s.vadOpen.set(c.item_id, e);
    } else if (kind === 'stopped') {
      const open = s.vadOpen.get(c.item_id);
      if (!open) return;
      s.bands.push(makeBand('vad', 'speech', open.t, e.t, { open, close: e, corr: c }));
      s.vadDone.set(c.item_id, { t0: open.t, t1: e.t });
      s.vadOpen.delete(c.item_id);
    } else if (kind === 'pending_start') {
      s.bands.push(makeBand('vad', 'pending', e.t, e.t + 200, { open: e, corr: c }));
    }
  }

  function ingestSttFinal(s, e) {
    const p = e.payload || {};
    if (p.audio_start_ms == null || p.audio_end_ms == null) return;
    const c = e.corr || {};
    const vr = s.vadDone.get(c.item_id);
    const t0 = vr ? vr.t0 : p.audio_start_ms;
    const t1 = vr ? vr.t1 : p.audio_end_ms;
    s.bands.push(makeBand('stt', 'utterance', t0, t1, { open: e, corr: c }));
  }

  function ingestLlm(s, e, kind) {
    const c = e.corr || {};
    if (kind === 'request') {
      s.llmOpen.set(c.response_id, e);
    } else if (kind === 'done') {
      const open = s.llmOpen.get(c.response_id);
      if (!open) return;
      s.bands.push(makeBand('llm', 'response', open.t, e.t, { open, close: e, corr: c }));
      s.llmOpen.delete(c.response_id);
    }
  }

  function ingestResponse(s, e, kind) {
    const c = e.corr || {};
    if (kind === 'plan_start') {
      s.respOpen.set(c.response_id, e);
    } else if (kind === 'done') {
      const open = s.respOpen.get(c.response_id);
      if (!open) return;
      s.bands.push(makeBand('response', 'assembly', open.t, e.t, { open, close: e, corr: c }));
      s.respOpen.delete(c.response_id);
    }
  }

  function ingestTtsReq(s, e, kind) {
    const c = e.corr || {};
    if (kind === 'phrase_sent') {
      s.ttsOpen.set(c.phrase_id, e);
      s.ttsPhraseTexts.set(c.phrase_id, e.payload?.text || '');
    } else if (kind === 'phrase_rendered' || kind === 'phrase_done' || kind === 'error') {
      const open = s.ttsOpen.get(c.phrase_id);
      if (!open) return;
      const bandKind = kind === 'error' ? 'phrase_error' : 'phrase';
      s.bands.push(makeBand('tts_req', bandKind, open.t, e.t, { open, close: e, corr: c }));
      s.ttsOpen.delete(c.phrase_id);
    }
  }

  // TTS chunks abut in a per-response playback cursor (not wall-clock time).
  function ingestTtsChunk(s, e) {
    const c = e.corr || {};
    const p = e.payload || {};
    const rid = c.response_id;
    const ms = p.ms_audio || 0;
    if (!s.ttsPlaybackOrigin.has(rid)) {
      s.ttsPlaybackOrigin.set(rid, e.t);
      s.ttsPlaybackCursor.set(rid, 0);
    }
    const cursor = s.ttsPlaybackCursor.get(rid);
    const origin = s.ttsPlaybackOrigin.get(rid);
    s.bands.push(makeBand('tts_chunk', 'chunk', origin + cursor, origin + cursor + ms, { open: e, corr: c }));
    s.ttsPlaybackCursor.set(rid, cursor + ms);
  }

  function ingestBargein(s, e, kind) {
    const c = e.corr || {};
    const key = c.response_id || c.item_id || 'default';
    if (kind === 'bargein_pending') {
      s.bargeinOpen.set(key, e);
    } else if (kind === 'bargein_fired') {
      const open = s.bargeinOpen.get(key);
      if (open) {
        s.bands.push(makeBand('bargein', 'fired', open.t, e.t, { open, close: e, corr: c }));
        s.bargeinOpen.delete(key);
      }
    } else if (kind === 'bargein_cancelled') {
      const open = s.bargeinOpen.get(key);
      if (open) {
        s.bands.push(makeBand('bargein', 'cancelled', open.t, e.t, { open, close: e, corr: c }));
        s.bargeinOpen.delete(key);
      }
    }
  }

  function closeOngoingBands(s, lastT) {
    const push = (lane, kind, open) => s.bands.push(
      makeBand(lane, kind, open.t, lastT, { open, corr: open.corr, ongoing: true })
    );
    s.vadOpen.forEach(o => push('vad', 'speech', o));
    s.llmOpen.forEach(o => push('llm', 'response', o));
    s.respOpen.forEach(o => push('response', 'assembly', o));
    s.ttsOpen.forEach(o => push('tts_req', 'phrase', o));
    s.bargeinOpen.forEach(o => push('bargein', 'pending', o));
  }

  Timeline.prototype.rebuildTurns = function () {
    const turns = [];
    let cur = null;
    for (const e of this.events) {
      if (e.lane !== 'turn') continue;
      if (e.kind === 'turn_start' && e.payload?.role === 'user') {
        cur = makeTurn(e.corr.turn_id, e.t, null);
        turns.push(cur);
      } else if (e.kind === 'turn_end' && cur && cur.turn_id === e.corr.turn_id) {
        cur.t1 = e.t;
        cur = null;
      }
    }
    if (cur) cur.t1 = this.events.length ? this.events[this.events.length - 1].t : 0;
    this.turns = turns;
  };

  Timeline.prototype.laneHeight = function (laneId) {
    const base = this.density === 'compact' ? 32 : 42;
    if (laneId === 'tts_chunk') {
      const rows = Math.max(1, this.ttsChunkRows || 1);
      return base + (rows - 1) * this.subRowHeight();
    }
    return base;
  };
  Timeline.prototype.subRowHeight = function () {
    return this.density === 'compact' ? 16 : 20;
  };
  Timeline.prototype.laneYOffset = function (laneIdx) {
    let y = 0;
    for (let i = 0; i < laneIdx; i++) y += this.laneHeight(this.lanes[i].id);
    return y;
  };
  Timeline.prototype.lanesTotalHeight = function () {
    let y = 0;
    for (const l of this.lanes) y += this.laneHeight(l.id);
    return y;
  };
  Timeline.prototype.bandRowMetrics = function (band, laneY, laneH) {
    if (band.lane === 'tts_chunk') {
      const base = this.density === 'compact' ? 32 : 42;
      const subH = this.subRowHeight();
      const row = band.row || 0;
      if (row === 0) return { top: laneY + 6, rowH: base - 12 };
      return { top: laneY + base + (row - 1) * subH + 2, rowH: subH - 4 };
    }
    return { top: laneY + 6, rowH: laneH - 12 };
  };
  Timeline.prototype.isLaneHidden = function (laneId) {
    return this.hiddenLanes && this.hiddenLanes.has && this.hiddenLanes.has(laneId);
  };
  Timeline.prototype.colorFor = function (laneId, kind) {
    const p = D.PALETTES[this.palette];
    if (laneId === 'error' || ERROR_KINDS.has(kind)) return p.error;
    return p[laneId] || '#999';
  };
  Timeline.prototype.msToPx = function (ms) { return (ms - this.view.t0) * this.view.pxPerMs; };
  Timeline.prototype._pxToMs = function (px) { return this.view.t0 + px / this.view.pxPerMs; };

  Timeline.prototype.draw = function () {
    this.drawRuler();
    this.drawLaneRows();
    this.drawMinimap();
    this.refreshGutterState();
    if (this.onViewChange) {
      const span = this._tlPxWidth() / this.view.pxPerMs;
      this.onViewChange({ t0: this.view.t0, span, pxPerMs: this.view.pxPerMs });
    }
  };

  Timeline.prototype.drawRuler = function () {
    const c = this.rulerCanvas, ctx = c.getContext('2d');
    ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
    const w = c.width / this._dpr, h = c.height / this._dpr;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#151515';
    ctx.fillRect(0, 0, w, h);

    const msPerTick = niceStep(120 / this.view.pxPerMs);
    const t0 = Math.floor(this.view.t0 / msPerTick) * msPerTick;
    const t1 = this.view.t0 + w / this.view.pxPerMs;

    for (const turn of this.turns) {
      const x0 = this.msToPx(turn.t0), x1 = this.msToPx(turn.t1);
      if (x1 < 0 || x0 > w) continue;
      ctx.fillStyle = 'rgba(168,144,106,0.08)';
      ctx.fillRect(x0, 0, Math.max(2, x1 - x0), h);
      ctx.strokeStyle = 'rgba(168,144,106,0.35)';
      ctx.beginPath();
      ctx.moveTo(x0 + 0.5, 0); ctx.lineTo(x0 + 0.5, h); ctx.stroke();
    }

    ctx.font = '11px ui-monospace, "SF Mono", Consolas, monospace';
    ctx.textBaseline = 'middle';
    for (let t = t0; t <= t1 + msPerTick; t += msPerTick) {
      const x = Math.round(this.msToPx(t)) + 0.5;
      ctx.strokeStyle = '#2E2E2E';
      ctx.beginPath(); ctx.moveTo(x, h - 12); ctx.lineTo(x, h); ctx.stroke();
      ctx.fillStyle = '#9B9590';
      ctx.fillText(formatMs(t), x + 4, h - 6);
      const sub = msPerTick / 5;
      for (let k = 1; k < 5; k++) {
        const sx = Math.round(this.msToPx(t + sub * k)) + 0.5;
        ctx.strokeStyle = '#232323';
        ctx.beginPath(); ctx.moveTo(sx, h - 6); ctx.lineTo(sx, h); ctx.stroke();
      }
    }

    ctx.strokeStyle = '#2E2E2E';
    ctx.beginPath(); ctx.moveTo(0, h - 0.5); ctx.lineTo(w, h - 0.5); ctx.stroke();

    if (this.cursorMs != null) {
      const x = Math.round(this.msToPx(this.cursorMs)) + 0.5;
      ctx.strokeStyle = 'rgba(200,176,142,0.6)';
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }
    if (this.selected) {
      const x = Math.round(this.msToPx(this.selected.t)) + 0.5;
      ctx.strokeStyle = '#C8B08E';
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      ctx.lineWidth = 1;
    }
  };

  Timeline.prototype.drawLaneRows = function () {
    const c = this.tlCanvas, ctx = c.getContext('2d');
    ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
    const w = c.width / this._dpr, h = c.height / this._dpr;
    ctx.clearRect(0, 0, w, h);

    const laneY = new Array(this.lanes.length);
    const laneH = new Array(this.lanes.length);
    {
      let y = 0;
      for (let i = 0; i < this.lanes.length; i++) {
        laneY[i] = y;
        laneH[i] = this.laneHeight(this.lanes[i].id);
        y += laneH[i];
      }
    }

    for (let i = 0; i < this.lanes.length; i++) {
      ctx.fillStyle = i % 2 ? '#1A1A1A' : '#181818';
      ctx.fillRect(0, laneY[i], w, laneH[i]);
    }
    ctx.strokeStyle = '#232323';
    for (let i = 1; i <= this.lanes.length; i++) {
      const y = (i < this.lanes.length ? laneY[i] : laneY[i - 1] + laneH[i - 1]) - 0.5;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }

    for (const turn of this.turns) {
      const x0 = this.msToPx(turn.t0), x1 = this.msToPx(turn.t1);
      if (x1 < 0 || x0 > w) continue;
      ctx.fillStyle = 'rgba(168,144,106,0.04)';
      ctx.fillRect(x0, 0, Math.max(2, x1 - x0), h);
      ctx.strokeStyle = 'rgba(168,144,106,0.22)';
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x0 + 0.5, 0); ctx.lineTo(x0 + 0.5, h); ctx.stroke();
      ctx.setLineDash([]);
    }

    if (this.playbackRange) {
      const rx0 = this.msToPx(this.playbackRange.t0);
      const rx1 = this.msToPx(this.playbackRange.t1);
      ctx.fillStyle = 'rgba(200,176,142,0.09)';
      ctx.fillRect(rx0, 0, Math.max(2, rx1 - rx0), h);
      ctx.strokeStyle = 'rgba(200,176,142,0.55)';
      ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(rx0 + 0.5, 0); ctx.lineTo(rx0 + 0.5, h); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(rx1 - 0.5, 0); ctx.lineTo(rx1 - 0.5, h); ctx.stroke();
      ctx.setLineDash([]);
    }

    const msPerTick = niceStep(120 / this.view.pxPerMs);
    const gridT0 = Math.floor(this.view.t0 / msPerTick) * msPerTick;
    const gridT1 = this.view.t0 + w / this.view.pxPerMs;
    ctx.strokeStyle = '#202020';
    for (let t = gridT0; t <= gridT1 + msPerTick; t += msPerTick) {
      const x = Math.round(this.msToPx(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }

    this.drawAudioSparkline(ctx, w);

    for (const band of this.bands) {
      if (this.isLaneHidden(band.lane)) continue;
      const laneIdx = this.lanes.findIndex(l => l.id === band.lane);
      if (laneIdx < 0) continue;
      const x0 = this.msToPx(band.t0);
      const x1 = this.msToPx(band.t1);
      if (x1 < 0 || x0 > w) continue;
      const col = this.colorFor(band.lane, band.kind);
      const { top, rowH } = this.bandRowMetrics(band, laneY[laneIdx], laneH[laneIdx]);
      this.drawBand(ctx, band, x0, x1, top, rowH, col);
    }

    const visibleT0 = this.view.t0;
    const visibleT1 = this.view.t0 + w / this.view.pxPerMs;
    for (const e of this.events) {
      if (e.lane === 'audio_level') continue;
      if (this.isLaneHidden(e.lane)) continue;
      if (e.t < visibleT0 - 10 || e.t > visibleT1 + 10) continue;
      const laneIdx = this.lanes.findIndex(l => l.id === e.lane);
      if (laneIdx < 0) continue;
      if (isBandEndpoint(e)) continue;
      const col = this.colorFor(e.lane, e.kind);
      const x = this.msToPx(e.t);
      this.drawTick(ctx, e, x, laneY[laneIdx], laneH[laneIdx], col);
    }

    this.drawLlmTokenLabels(ctx, w, visibleT0, visibleT1);

    if (this.cursorMs != null) {
      const x = Math.round(this.msToPx(this.cursorMs)) + 0.5;
      ctx.strokeStyle = 'rgba(200,176,142,0.35)';
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      ctx.setLineDash([]);
    }
    if (this.selected) {
      const x = Math.round(this.msToPx(this.selected.t)) + 0.5;
      ctx.strokeStyle = '#C8B08E';
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    }

    if (this.playheadMs != null) {
      const x = Math.round(this.msToPx(this.playheadMs)) + 0.5;
      ctx.strokeStyle = '#F2EDE4';
      ctx.lineWidth = 1.5;
      ctx.shadowColor = 'rgba(242,237,228,0.6)';
      ctx.shadowBlur = 6;
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.lineWidth = 1;
      ctx.fillStyle = '#F2EDE4';
      ctx.beginPath();
      ctx.moveTo(x - 4, 0); ctx.lineTo(x + 4, 0); ctx.lineTo(x, 5);
      ctx.closePath(); ctx.fill();
    }
  };

  Timeline.prototype.drawLlmTokenLabels = function (ctx, w, visibleT0, visibleT1) {
    if (this.isLaneHidden('llm')) return;
    const laneIdx = this.lanes.findIndex(l => l.id === 'llm');
    if (laneIdx < 0) return;
    const laneH = this.laneHeight('llm');
    const y = this.laneYOffset(laneIdx);

    ctx.font = '11px ui-monospace, monospace';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#F2EDE4';

    const top = y + 6;
    const rowH = laneH - 12;
    const labelY = top + rowH / 2;
    const pad = 3;
    let lastEndX = -Infinity;

    for (const e of this.events) {
      if (e.lane !== 'llm' || e.kind !== 'chunk') continue;
      if (e.t < visibleT0 - 10 || e.t > visibleT1 + 10) continue;
      const delta = e.payload && e.payload.delta;
      if (!delta) continue;
      const text = String(delta).replace(/\s+/g, ' ');
      if (!text) continue;
      const x = this.msToPx(e.t);
      if (x < lastEndX + pad) continue;
      ctx.fillText(text, x + 6, labelY);
      lastEndX = x + 6 + ctx.measureText(text).width;
    }
  };

  Timeline.prototype.drawAudioSparkline = function (ctx, w) {
    if (this.isLaneHidden('audio_level')) return;
    const laneIdx = this.lanes.findIndex(l => l.id === 'audio_level');
    if (laneIdx < 0) return;
    const laneH = this.laneHeight('audio_level');
    const y = this.laneYOffset(laneIdx);
    const samples = this.events.filter(e => e.lane === 'audio_level');
    if (!samples.length) return;

    const mic = [], tts = [];
    for (const s of samples) {
      const channel = s.payload && s.payload.channel;
      (channel === 'tts_out' ? tts : mic).push(s);
    }

    const padY = 3;
    const half = (laneH - padY * 2) / 2;
    const scale = (rms) => Math.min(1, rms / 0.18);
    const midY = y + padY + half;

    // dir: -1 = up from baseline (mic), +1 = down (tts).
    const drawSide = (arr, baselineY, dir, color) => {
      if (!arr.length) return;
      ctx.beginPath();
      let started = false;
      for (const s of arr) {
        const x = this.msToPx(s.t);
        if (x < -2 || x > w + 2) continue;
        const rms = (s.payload && s.payload.rms) || 0;
        const yy = baselineY + dir * half * scale(rms);
        if (!started) { ctx.moveTo(x, baselineY); ctx.lineTo(x, yy); started = true; }
        else ctx.lineTo(x, yy);
      }
      if (!started) return;
      const lastX = this.msToPx(arr[arr.length - 1].t);
      ctx.lineTo(lastX, baselineY);
      ctx.closePath();
      ctx.fillStyle = color + '40';
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.stroke();
    };

    const pal = D.PALETTES[this.palette] || {};
    drawSide(mic, midY, -1, pal.vad || '#7A92A8');
    drawSide(tts, midY, +1, pal.tts_chunk || '#B88B5A');

    ctx.strokeStyle = '#232323';
    ctx.beginPath();
    ctx.moveTo(0, Math.round(midY) + 0.5);
    ctx.lineTo(w, Math.round(midY) + 0.5);
    ctx.stroke();
  };

  Timeline.prototype.drawBand = function (ctx, band, x0, x1, top, rowH, col) {
    const isErr = band.kind === 'phrase_error';
    const w = Math.max(2, x1 - x0);

    if (isErr) {
      const grad = ctx.createLinearGradient(0, top, 0, top + rowH);
      grad.addColorStop(0, col);
      grad.addColorStop(1, 'rgba(184,128,128,0.35)');
      ctx.fillStyle = grad; ctx.fillRect(x0, top, w, rowH);
      ctx.strokeStyle = col; ctx.setLineDash([3, 2]);
      ctx.strokeRect(x0 + 0.5, top + 0.5, w - 1, rowH - 1);
      ctx.setLineDash([]);
      this.drawBandLabel(ctx, band, x0, top, w, rowH, col);
      return;
    }
    ctx.fillStyle = col + '40';
    ctx.fillRect(x0, top, w, rowH);
    ctx.fillStyle = col;
    ctx.fillRect(x0, top, 2, rowH);
    if (!band.ongoing) ctx.fillRect(x1 - 2, top, 2, rowH);
    ctx.globalAlpha = 0.85;
    ctx.fillRect(x0, top, w, 1.5);
    ctx.globalAlpha = 1;
    this.drawBandLabel(ctx, band, x0, top, w, rowH, col);
  };

  Timeline.prototype.drawBandLabel = function (ctx, band, x, y, w, h, col) {
    if (w < 48) return;
    const label = this.bandLabel(band);
    if (!label) return;
    ctx.font = '11px ui-monospace, monospace';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#F2EDE4';
    // Sticky labels (STT, LLM, TTS) track the left viewport edge when the band scrolls off.
    const sticky = (band.lane === 'stt' && band.kind === 'utterance')
                || (band.lane === 'llm' && band.kind === 'response')
                || (band.lane === 'tts_chunk');
    const tx = sticky ? Math.max(x + 6, 6) : (x + 6);
    ctx.save();
    ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip();
    ctx.fillText(label, tx, y + h / 2);
    ctx.restore();
  };

  Timeline.prototype.bandLabel = function (band) {
    if (band.lane === 'vad' && band.kind === 'speech') return `speech · ${Math.round(band.t1 - band.t0)}ms`;
    if (band.lane === 'vad' && band.kind === 'pending') return 'pending';
    if (band.lane === 'stt' && band.kind === 'utterance') {
      if (band.open.kind === 'audio_direct') {
        const ms = band.open.payload?.duration_ms || Math.round(band.t1 - band.t0);
        return `[audio direct · ${ms}ms]`;
      }
      const p = band.open.payload || {};
      const t = p.text || '';
      const ns = p.avg_no_speech_prob;
      return ns != null ? `"${t}" · ns ${ns}` : `"${t}"`;
    }
    if (band.lane === 'llm' && band.kind === 'response') {
      const ttft = band.close?.payload?.elapsed_ms ?? (band.t1 - band.t0);
      const tokOut = band.close?.payload?.tok_out;
      return tokOut ? `llm ${tokOut} tok · ${Math.round(ttft)}ms` : `llm ${Math.round(ttft)}ms`;
    }
    if (band.lane === 'response' && band.kind === 'assembly') {
      const p = band.close?.payload;
      if (p?.failed_phrases) return `assembly · ${p.completed_phrases}/${p.phrases} phrases · ${p.failed_phrases} failed`;
      if (p?.phrases) return `assembly · ${p.phrases} phrase${p.phrases > 1 ? 's' : ''}`;
      return 'response assembly';
    }
    if (band.lane === 'tts_req' && band.kind === 'phrase') {
      return band.open.payload?.text || '';
    }
    if (band.lane === 'tts_req' && band.kind === 'phrase_error') return '✕ error · worker closed';
    if (band.lane === 'bargein') {
      const ms = Math.round(band.t1 - band.t0);
      if (band.kind === 'fired') return `barge-in fired · ${ms}ms`;
      if (band.kind === 'cancelled') return `false start · ${ms}ms`;
      return `pending · ${ms}ms`;
    }
    if (band.lane === 'tts_chunk') {
      const p = band.open.payload || {};
      const phraseId = band.corr?.phrase_id;
      const text = phraseId ? (this.ttsPhraseTexts.get(phraseId) || '') : '';
      const ms = p.ms_audio || 0;
      return text ? `${text} · ${ms}ms` : `chunk #${p.chunk_idx || 0} · ${ms}ms`;
    }
    if (band.lane === 'tool') {
      const name = band.open.payload?.name || 'tool';
      // Prefer the closing event's payload, in informativeness order:
      //   summary > start_summary > result > args.
      if (band.close?.kind === 'summary') {
        const summary = band.close.payload?.summary;
        if (summary) return `${name} · ${String(summary).slice(0, 80)}`;
      }
      if (band.close?.kind === 'start_summary') return `${name} · narrating…`;
      if (band.close?.kind === 'result') {
        const result = band.close.payload?.result;
        if (result) return `${name} · ${String(result).slice(0, 60)}`;
      }
      const args = band.open.payload?.args;
      if (args && typeof args === 'object') {
        const compact = JSON.stringify(args);
        return `${name}(${compact.length > 60 ? compact.slice(0, 60) + '…' : compact})`;
      }
      return name;
    }
    return '';
  };

  const IMPORTANT_KINDS = new Set([
    'first_token', 'pending_start',
    'error', 'dropped', 'raised', 'bargein_missed',
    'partial', 'final',
    'user_committed', 'turn_start', 'turn_end',
    'phrase_boundary',
    'use_token', 'result', 'start_summary', 'summary',
  ]);

  Timeline.prototype.drawTick = function (ctx, e, x, y, laneH, col) {
    const important = IMPORTANT_KINDS.has(e.kind);
    if (this.treatment === 'blocks') {
      const h = laneH - 14;
      const rw = e.kind === 'partial' || e.kind === 'chunk' ? 2 : (important ? 4 : 2);
      ctx.fillStyle = col;
      ctx.fillRect(x - rw / 2, y + 7, rw, h);
      if (ERROR_KINDS.has(e.kind)) {
        ctx.fillStyle = '#B88080';
        ctx.fillRect(x - 2, y + 3, 4, 4);
      }
    } else if (this.treatment === 'ticks') {
      ctx.strokeStyle = col;
      ctx.lineWidth = important ? 1.8 : 1;
      ctx.beginPath();
      ctx.moveTo(x + 0.5, y + 8);
      ctx.lineTo(x + 0.5, y + laneH - 6);
      ctx.stroke();
      ctx.lineWidth = 1;
    } else {
      const cy = y + laneH / 2 + 4;
      ctx.strokeStyle = col + 'AA';
      ctx.beginPath();
      ctx.moveTo(x + 0.5, y + laneH - 5);
      ctx.lineTo(x + 0.5, cy);
      ctx.stroke();
      ctx.fillStyle = col;
      const r = important ? 3.2 : 2;
      ctx.beginPath(); ctx.arc(x, cy, r, 0, Math.PI * 2); ctx.fill();
    }

    if (this.selected && this.selected.seq === e.seq) {
      ctx.strokeStyle = '#C8B08E';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(x - 6, y + 3, 12, laneH - 6);
      ctx.lineWidth = 1;
    }
    if (this.hover && this.hover.seq === e.seq) {
      ctx.strokeStyle = 'rgba(242,237,228,0.45)';
      ctx.strokeRect(x - 5, y + 5, 10, laneH - 10);
    }
  };

  Timeline.prototype.drawMinimap = function () {
    const c = this.mmCanvas, ctx = c.getContext('2d');
    ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
    const w = c.width / this._dpr, h = c.height / this._dpr;
    ctx.clearRect(0, 0, w, h);
    if (!this.events.length) return;

    const t0 = 0;
    const t1 = Math.max(500, this.events[this.events.length - 1].t + 200);
    const laneH = h / this.lanes.length;

    for (let i = 0; i < this.lanes.length; i++) {
      ctx.fillStyle = i % 2 ? '#181818' : '#1A1A1A';
      ctx.fillRect(0, i * laneH, w, laneH);
    }
    for (const turn of this.turns) {
      const x0 = (turn.t0 / (t1 - t0)) * w;
      const x1 = (turn.t1 / (t1 - t0)) * w;
      ctx.fillStyle = 'rgba(168,144,106,0.10)';
      ctx.fillRect(x0, 0, Math.max(1, x1 - x0), h);
    }
    for (const e of this.events) {
      const laneIdx = this.lanes.findIndex(l => l.id === e.lane);
      if (laneIdx < 0) continue;
      const x = (e.t - t0) / (t1 - t0) * w;
      ctx.fillStyle = this.colorFor(e.lane, e.kind);
      ctx.fillRect(x, laneIdx * laneH + 1, 1, laneH - 2);
    }
    for (const band of this.bands) {
      const laneIdx = this.lanes.findIndex(l => l.id === band.lane);
      if (laneIdx < 0) continue;
      const x0 = (band.t0 - t0) / (t1 - t0) * w;
      const x1 = (band.t1 - t0) / (t1 - t0) * w;
      ctx.fillStyle = this.colorFor(band.lane, band.kind) + '60';
      ctx.fillRect(x0, laneIdx * laneH + 1, Math.max(1, x1 - x0), laneH - 2);
    }

    const vx0 = (this.view.t0 - t0) / (t1 - t0) * w;
    const vw  = (this._tlPxWidth() / this.view.pxPerMs) / (t1 - t0) * w;
    ctx.strokeStyle = '#C8B08E';
    ctx.lineWidth = 1.5;
    ctx.strokeRect(vx0 + 0.5, 0.5, Math.max(8, vw) - 1, h - 1);
    ctx.fillStyle = 'rgba(200,176,142,0.08)';
    ctx.fillRect(vx0, 0, Math.max(8, vw), h);
    ctx.lineWidth = 1;
  };

  Timeline.prototype.renderGutter = function () {
    this.gutter.innerHTML = '';
    this.lanes.forEach((lane) => {
      const div = document.createElement('div');
      div.className = 'lane-label';
      div.dataset.lane = lane.id;
      div.style.setProperty('--lane-color', D.PALETTES[this.palette][lane.id]);
      div.innerHTML = `
        <span class="swatch"></span>
        <div>
          <div class="name">${lane.name}</div>
          <div class="sub">${lane.hint}</div>
        </div>
        <span class="count" data-count="${lane.id}">0</span>
      `;
      this.gutter.appendChild(div);
    });
    this.refreshGutterHeights();
  };
  Timeline.prototype.refreshGutterHeights = function () {
    this.gutter.querySelectorAll('.lane-label').forEach(el => {
      el.style.height = this.laneHeight(el.dataset.lane) + 'px';
    });
  };
  Timeline.prototype.refreshGutterState = function () {
    const counts = {};
    this.events.forEach(e => counts[e.lane] = (counts[e.lane] || 0) + 1);
    this.gutter.querySelectorAll('[data-count]').forEach(el => {
      el.textContent = counts[el.dataset.count] || 0;
    });
    this.gutter.querySelectorAll('.lane-label').forEach(el => {
      el.style.setProperty('--lane-color', D.PALETTES[this.palette][el.dataset.lane]);
    });
  };

  Timeline.prototype.hitTest = function (px, py) {
    let laneIdx = -1, laneY = 0, laneH = 0;
    for (let i = 0, y = 0; i < this.lanes.length; i++) {
      const hh = this.laneHeight(this.lanes[i].id);
      if (py >= y && py < y + hh) { laneIdx = i; laneY = y; laneH = hh; break; }
      y += hh;
    }
    if (laneIdx < 0) return null;
    const laneId = this.lanes[laneIdx].id;
    if (this.isLaneHidden(laneId)) return null;
    const tMs = this._pxToMs(px);
    const tolMs = 6 / this.view.pxPerMs;

    let chunkRow = null;
    if (laneId === 'tts_chunk') {
      const localY = py - laneY;
      const base = this.density === 'compact' ? 32 : 42;
      if (localY < base) chunkRow = 0;
      else chunkRow = 1 + Math.floor((localY - base) / this.subRowHeight());
    }
    const band = findBandAt(this.bands, laneId, tMs, tolMs, chunkRow);

    // Progressive bands: prefer whichever of {latest progressive event, band.open, band.close} is closest in time.
    if (band && isProgressiveBand(band)) {
      const candidates = [];
      const progressive = progressiveHit(this.events, band, tMs);
      if (progressive) candidates.push(progressive);
      if (band.open)   candidates.push(band.open);
      if (band.close)  candidates.push(band.close);
      if (!candidates.length) return null;
      let best = candidates[0], bestDist = Math.abs(best.t - tMs);
      for (let i = 1; i < candidates.length; i++) {
        const d = Math.abs(candidates[i].t - tMs);
        if (d < bestDist) { best = candidates[i]; bestDist = d; }
      }
      return best;
    }

    let best = null, bestDist = Infinity;
    for (const e of this.events) {
      if (e.lane !== laneId) continue;
      if (e.lane === 'audio_level') continue;
      const d = Math.abs(e.t - tMs);
      if (d < bestDist && d <= tolMs) { best = e; bestDist = d; }
    }
    if (best) return best;
    return band ? pickBandEndpoint(band, tMs) : null;
  };

  function findBandAt(bands, laneId, tMs, tolMs, row = null) {
    let containing = null;
    let nearest = null, nearestDist = Infinity;
    for (const band of bands) {
      if (band.lane !== laneId) continue;
      if (row != null && (band.row || 0) !== row) continue;
      if (tMs >= band.t0 && tMs <= band.t1) { containing = band; break; }
      if (tMs >= band.t0 - tolMs && tMs <= band.t1 + tolMs) {
        const centerDist = Math.abs((band.t0 + band.t1) / 2 - tMs);
        if (centerDist < nearestDist) { nearest = band; nearestDist = centerDist; }
      }
    }
    return containing || nearest;
  }

  function pickBandEndpoint(band, tMs) {
    const oD = Math.abs(band.open.t - tMs);
    const cD = band.close ? Math.abs(band.close.t - tMs) : Infinity;
    return cD < oD ? band.close : band.open;
  }

  function isProgressiveBand(band) {
    return (band.lane === 'llm' && band.kind === 'response')
        || (band.lane === 'stt' && band.kind === 'utterance')
        || (band.lane === 'response' && band.kind === 'assembly')
        || (band.lane === 'tts_chunk');
  }

  function progressiveHit(events, band, tMs) {
    if (band.lane === 'tts_chunk') return band.open;
    let kinds, scopeField;
    if (band.lane === 'llm')           { kinds = new Set(['chunk']);            scopeField = 'response_id'; }
    else if (band.lane === 'stt')      { kinds = new Set(['partial', 'final']); scopeField = 'item_id'; }
    else if (band.lane === 'response') { kinds = new Set(['phrase_boundary']);  scopeField = 'response_id'; }
    else return null;
    const scope = band.corr && band.corr[scopeField];
    let latest = null;
    for (const e of events) {
      if (e.lane !== band.lane) continue;
      if (!kinds.has(e.kind)) continue;
      if (scope && e.corr && e.corr[scopeField] !== scope) continue;
      if (e.t > tMs) break;
      latest = e;
    }
    return latest;
  }

  Timeline.prototype.zoomAtPx = function (px, factor) {
    const t = this._pxToMs(px);
    this.view.pxPerMs = Math.max(0.01, Math.min(6, this.view.pxPerMs * factor));
    this.view.t0 = t - px / this.view.pxPerMs;
    if (this.view.t0 < 0) this.view.t0 = 0;
    this.followTail = false;
    this.draw();
  };
  Timeline.prototype.panBy = function (dx) {
    this.view.t0 -= dx / this.view.pxPerMs;
    if (this.view.t0 < 0) this.view.t0 = 0;
    this.followTail = false;
    this.draw();
  };
  Timeline.prototype.fit = function () {
    if (!this.events.length) return;
    const last = this.events[this.events.length - 1].t;
    const w = this._tlPxWidth();
    this.view.pxPerMs = (w - 40) / Math.max(500, last);
    this.view.t0 = 0;
    this.draw();
  };
  Timeline.prototype.toEnd = function () {
    this.followTail = true;
    this.followTailIntoView();
    this.draw();
  };
  Timeline.prototype.setDensity = function (d) {
    this.density = d;
    document.documentElement.dataset.density = d;
    this.refreshGutterHeights();
    this.draw();
  };
  Timeline.prototype.setTreatment = function (t) { this.treatment = t; this.draw(); };
  Timeline.prototype.setPalette = function (p) { this.palette = p; this.refreshGutterState(); this.draw(); };

  function niceStep(ms) {
    const safe = Math.max(0.001, ms);
    const exp = Math.pow(10, Math.floor(Math.log10(safe)));
    const n = safe / exp;
    if (n < 1.5) return exp;
    if (n < 3)   return 2 * exp;
    if (n < 7)   return 5 * exp;
    return 10 * exp;
  }
  function formatMs(ms) {
    if (ms >= 1000) return (ms / 1000).toFixed(3) + 's';
    if (ms >= 1)    return ms.toFixed(1) + 'ms';
    return (ms * 1000).toFixed(1) + 'us';
  }
  function isBandEndpoint(e) {
    if (e.lane === 'vad'       && (e.kind === 'confirmed_start' || e.kind === 'stopped')) return true;
    if (e.lane === 'llm'       && (e.kind === 'request' || e.kind === 'done')) return true;
    if (e.lane === 'tts_req'   && (e.kind === 'phrase_sent' || e.kind === 'phrase_rendered' || e.kind === 'phrase_done')) return true;
    if (e.lane === 'response'  && (e.kind === 'plan_start' || e.kind === 'done')) return true;
    if (e.lane === 'tts_chunk' && (e.kind === 'chunk' || e.kind === 'first_chunk')) return true;
    if (e.lane === 'stt'       && e.kind === 'audio_direct') return true;
    if (e.lane === 'bargein'   && (e.kind === 'bargein_pending' || e.kind === 'bargein_fired' || e.kind === 'bargein_cancelled')) return true;
    return false;
  }

  window.Timeline = Timeline;
  window.__formatMs = formatMs;
})();
