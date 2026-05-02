(function () {
  const DEV = !!window.INSPECT_DEV_MODE;
  const LANES_DEFAULT = [
    { id: 'error',       name: 'Error',       hint: 'mirrored from any lane' },
    { id: 'audio_level', name: 'Audio',       hint: 'PCM RMS · 40ms/window' },
    { id: 'vad',         name: 'VAD',         hint: 'Silero' },
    { id: 'stt',         name: 'STT',         hint: 'whisper' },
    { id: 'turn',        name: 'Turn',        hint: 'turn boundaries · user_committed' },
    { id: 'bargein',     name: 'Barge-in',    hint: 'pending / fired / cancelled / missed' },
    { id: 'llm',         name: 'LLM',         hint: 'model' },
    { id: 'response',    name: 'Response',    hint: 'plan · phrase split' },
    { id: 'tool',        name: 'Tool',        hint: 'use_token · result · summary' },
    { id: 'tts_req',     name: 'TTS phrases', hint: 'tts executor' },
    { id: 'tts_chunk',   name: 'TTS chunks',  hint: '24 kHz PCM' },
    { id: 'wire',        name: 'Wire',        hint: 'protocol control' },
  ];
  const PALETTES_DEFAULT = {
    warm: {
      audio_level: '#6E7C7F', vad: '#7A92A8', stt: '#7F9B7F',
      turn: '#9F7E9B', llm: '#A8906A',
      response: '#C89B6A', tool: '#9CA88A', tts_req: '#C4A45A', tts_chunk: '#B88B5A',
      wire: '#9B9590', error: '#B88080',
    },
    semantic: {
      audio_level: '#6BBED3', vad: '#6FA8DC', stt: '#6BBE7F',
      turn: '#C77BBA', llm: '#C8A2E8',
      response: '#E8A96B', tool: '#88C9A1', tts_req: '#E8A96B', tts_chunk: '#E8C76B',
      wire: '#9B9590', error: '#E87878',
    },
    mono: {
      audio_level: '#5A564F', vad: '#8D7A5A', stt: '#A8906A',
      turn: '#8E7958', llm: '#C8B08E',
      response: '#DEC49B', tool: '#A8B091', tts_req: '#DEC49B', tts_chunk: '#BC9C6E',
      wire: '#726B62', error: '#B88080',
    },
  };
  const D = window.INSPECTOR_DATA || { LANES: LANES_DEFAULT, PALETTES: PALETTES_DEFAULT };
  const $ = (s) => document.querySelector(s);

  const TWEAKS = Object.assign({}, window.TWEAK_DEFAULTS);
  function applyTheme(v) { document.documentElement.setAttribute('data-theme', v); }
  applyTheme(TWEAKS.theme);

  const tl = new window.Timeline({
    rulerCanvas: $('#rulerCanvas'),
    tlCanvas: $('#tlCanvas'),
    mmCanvas: $('#mmCanvas'),
    tlWrap: $('#tlWrap'),
    gutter: $('#laneGutter'),
  });
  tl.treatment = TWEAKS.treatment;
  tl.density = TWEAKS.density;
  tl.palette = TWEAKS.palette;
  document.documentElement.dataset.density = TWEAKS.density;

  let currentSid = null;
  let currentWs = null;
  let sessionStartWallMs = 0;

  function stripScheme(u) { return u.replace(/^https?:\/\//, ''); }
  const wsScheme = location.protocol === 'https:' ? 'wss' : 'ws';

  const idAliases = { turn: new Map(), item: new Map(), response: new Map(), phrase: new Map() };
  function resetAliases() {
    idAliases.turn.clear(); idAliases.item.clear();
    idAliases.response.clear(); idAliases.phrase.clear();
  }
  function registerAliases(ev) {
    const c = ev.corr || {};
    const m = { turn: c.turn_id, item: c.item_id, response: c.response_id, phrase: c.phrase_id };
    for (const k of Object.keys(m)) {
      const id = m[k];
      if (!id) continue;
      const reg = idAliases[k];
      if (!reg.has(id)) reg.set(id, reg.size + 1);
    }
  }
  function aliasFor(kind, id) {
    if (!id) return id;
    const reg = idAliases[kind];
    if (!reg) return id;
    const n = reg.get(id);
    return n == null ? id : `${kind} ${n}`;
  }

  function normalizeEvent(raw) {
    if (sessionStartWallMs === 0 && raw.ts_wall) sessionStartWallMs = raw.ts_wall * 1000;
    const t_ms = raw.ts_wall ? (raw.ts_wall * 1000 - sessionStartWallMs) : 0;
    const ev = Object.assign({}, raw, { t: t_ms });
    registerAliases(ev);
    return ev;
  }

  async function fetchSessions() {
    const [live, hist] = await Promise.all([
      fetch('/v1/inspect/sessions').then(r => r.ok ? r.json() : []).catch(err => {
        console.error('[inspector] fetch /v1/inspect/sessions failed:', err);
        return [];
      }),
      fetch('/v1/inspect/sessions/history').then(r => r.ok ? r.json() : []).catch(err => {
        console.error('[inspector] fetch /v1/inspect/sessions/history failed:', err);
        return [];
      }),
    ]);
    return { live, hist };
  }

  async function openSession(sid) {
    if (currentWs) { try { currentWs.close(); } catch (_) {} currentWs = null; }
    currentSid = sid;
    sessionStartWallMs = 0;
    resetAliases();
    tl.setEvents([]);
    tl.fit();
    $('#sessionIdText').textContent = sid;
    setState('live');
    const url = `${wsScheme}://${stripScheme(location.origin)}/v1/inspect/${encodeURIComponent(sid)}/stream`;
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    currentWs = ws;
    ws.onmessage = (ev) => {
      let text;
      if (typeof ev.data === 'string') text = ev.data;
      else if (ev.data instanceof ArrayBuffer) text = new TextDecoder().decode(ev.data);
      else return;
      for (const line of text.split('\n')) {
        if (!line.trim()) continue;
        try {
          const raw = JSON.parse(line);
          tl.appendEvent(normalizeEvent(raw));
        } catch (err) {
          console.error('[inspector] failed to parse event line:', err, line);
        }
      }
      scheduleStatus();
    };
    ws.onclose = () => {
      if (currentWs === ws) setState('replay');
    };
    ws.onerror = (ev) => {
      console.error('[inspector] event-stream WebSocket error:', ev);
      if (currentWs === ws) setState('replay');
    };
  }

  async function loadDevScenario(which) {
    if (!D.generateClean) return;
    const events = which === 'problem' ? D.generateProblem() : D.generateClean();
    resetAliases();
    for (const ev of events) registerAliases(ev);
    tl.setEvents(events);
    tl.fit();
    setState('replay');
    $('#sessionIdText').textContent = `dev:${which}`;
    updateStatus();
  }

  function setState(s) {
    const pill = $('#statePill');
    const txt = $('#stateText');
    pill.classList.remove('state-live', 'state-paused', 'state-replay');
    if (s === 'live')    { pill.classList.add('state-live');   txt.textContent = 'Live'; }
    if (s === 'paused')  { pill.classList.add('state-paused'); txt.textContent = 'Paused'; }
    if (s === 'replay')  { pill.classList.add('state-replay'); txt.textContent = 'Replay'; }
    refreshLiveButton();
  }

  // Hard errors -> error lane + top-bar badge. `cancelled`/`rejected_*` are warnings, not errors.
  const ERR_KINDS = new Set(['error','raised','dropped','failed','phrase_error','bargein_missed']);
  let _statusScheduled = false;
  function scheduleStatus() {
    if (_statusScheduled) return;
    _statusScheduled = true;
    requestAnimationFrame(() => { _statusScheduled = false; updateStatus(); });
  }
  function updateStatus() {
    $('#stEvents').textContent = tl.events.length;
    $('#stSeq').textContent = tl.events.length ? tl.events[tl.events.length - 1].seq : '—';
    const errors = tl.events.filter(e => e.lane === 'error' || ERR_KINDS.has(e.kind));
    $('#stDropped').textContent = errors.length;
    let badge = document.getElementById('errorBadge');
    if (errors.length) {
      if (!badge) {
        badge = document.createElement('button');
        badge.id = 'errorBadge';
        badge.className = 'btn';
        badge.style.cssText = 'color:#B88080;background:rgba(184,128,128,0.12);border:1px solid rgba(184,128,128,0.3);border-radius:999px;padding:4px 12px;cursor:pointer;font-weight:600';
        badge.addEventListener('click', () => {
          const cur = tl.selected;
          const list = errors;
          const curIdx = cur ? list.findIndex(e => e.seq === cur.seq) : -1;
          const next = list[(curIdx + 1) % list.length];
          if (next) { selectEvent(next); centerOn(next.t); }
        });
        const topbar = document.querySelector('.topbar');
        if (topbar) topbar.appendChild(badge);
      }
      badge.innerHTML = `● ${errors.length} ${errors.length === 1 ? 'error' : 'errors'}`;
      badge.style.display = '';
    } else if (badge) {
      badge.style.display = 'none';
    }
    const ends = turnEnds();
    if (ends.length) {
      const center = tl.view.t0 + (tl._tlPxWidth() / tl.view.pxPerMs) / 2;
      let idx = ends.findIndex(t => t > center);
      if (idx < 0) idx = ends.length;
      const tips = document.getElementById('stTips');
      if (tips) {
        tips.textContent = `turn ${Math.max(1, idx)}/${ends.length}  ·  wheel zoom · drag pan · [ ] zoom · / find · space pause · r replay · ⌃, ⌃. turns`;
      }
    }
  }
  tl.onViewChange = updateStatus;

  const wrap = $('#tlWrap');
  wrap.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (e.ctrlKey || e.metaKey || Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
      const factor = Math.pow(1.0015, -e.deltaY);
      tl.zoomAtPx(px, factor);
    } else {
      tl.panBy(-e.deltaX);
    }
    updateStatus();
  }, { passive: false });

  let dragging = false; let dragLast = 0;
  wrap.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (e.shiftKey) return; // shift-drag selection handled separately
    const rect = wrap.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const hit = tl.hitTest(px, py);
    if (hit) {
      if (tl.playbackRange && !playback.active) {
        tl.playbackRange = null;
        tl.draw();
      }
      selectEvent(hit);
      return;
    }
    if (tl.playbackRange && !playback.active) {
      tl.playbackRange = null;
      tl.draw();
    }
    dragging = true; dragLast = e.clientX;
  });
  window.addEventListener('mousemove', (e) => {
    if (dragging) {
      tl.panBy(e.clientX - dragLast);
      dragLast = e.clientX;
      updateStatus();
      return;
    }
    const rect = wrap.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (px < 0 || py < 0 || px > rect.width || py > rect.height) {
      tl.hover = null; tl.cursorMs = null;
      hideTooltip();
      tl.draw();
      $('#stCursor').textContent = 'cursor —';
      return;
    }
    tl.cursorMs = tl._pxToMs(px);
    const hit = tl.hitTest(px, py);
    if (hit !== tl.hover) {
      tl.hover = hit;
      tl.draw();
      if (hit) showTooltip(hit, e.clientX, e.clientY);
      else hideTooltip();
    } else if (hit) {
      moveTooltip(e.clientX, e.clientY);
    }
    $('#stCursor').textContent = 'cursor ' + window.__formatMs(tl.cursorMs);
  });
  window.addEventListener('mouseup', () => { dragging = false; });

  function turnEnds() {
    return tl.events.filter(e => e.lane === 'turn' && e.kind === 'turn_end').map(e => e.t).sort((a, b) => a - b);
  }
  function centerOn(tMs) {
    const span = tl._tlPxWidth() / tl.view.pxPerMs;
    tl.view.t0 = Math.max(0, tMs - span / 2);
    tl.followTail = false;
    tl.draw();
    updateStatus();
  }
  function jumpTurn(dir) {
    const ends = turnEnds();
    if (!ends.length) return;
    const center = tl.view.t0 + (tl._tlPxWidth() / tl.view.pxPerMs) / 2;
    let target = null;
    if (dir > 0) target = ends.find(t => t > center + 10);
    else { for (const t of ends) { if (t < center - 10) target = t; else break; } }
    if (target == null) target = dir > 0 ? ends[ends.length - 1] : ends[0];
    centerOn(target);
  }

  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    // Ctrl+,/. turn navigation (accepts both key layouts).
    if (e.ctrlKey && (e.key === ',' || e.key === '<')) { jumpTurn(-1); e.preventDefault(); return; }
    if (e.ctrlKey && (e.key === '.' || e.key === '>')) { jumpTurn(1);  e.preventDefault(); return; }

    if (e.key === ' ') {
      handleSpace();
      e.preventDefault();
    }
    else if (e.key === 'f') toggleFollow();
    else if (e.key === ',') tl.panBy(60);
    else if (e.key === '.') tl.panBy(-60);
    else return;
    updateStatus();
  });

  function togglePause() {
    tl.paused = !tl.paused;
    $('#btnPause').setAttribute('aria-pressed', tl.paused ? 'true' : 'false');
    setState(tl.paused ? 'paused' : (currentWs && currentWs.readyState === 1 ? 'live' : 'replay'));
  }
  function isSessionAlive() {
    return !!(currentWs && currentWs.readyState === 1);
  }
  function toggleFollow() {
    // Turning Follow on requires a live session; turning off always works.
    if (!tl.followTail && !isSessionAlive()) return;
    const live = !tl.followTail;
    tl.followTail = live;
    $('#btnFollow').setAttribute('aria-pressed', live ? 'true' : 'false');
    if (live) {
      if (playback.active) stopPlayback();
      tl.toEnd();
    }
    updateStatus();
  }
  function refreshLiveButton() {
    const btn = $('#btnFollow');
    if (!btn) return;
    const alive = isSessionAlive();
    btn.disabled = !alive && !tl.followTail;
    btn.style.opacity = btn.disabled ? '0.5' : '';
    btn.style.cursor = btn.disabled ? 'not-allowed' : '';
  }
  // Space: Live -> Replay (stop playback); Replay+playing -> stop; Replay+idle -> play from selected/cursor.
  function handleSpace() {
    if (tl.followTail) {
      tl.followTail = false;
      $('#btnFollow').setAttribute('aria-pressed', 'false');
      if (playback.active) stopPlayback();
      updateStatus();
      return;
    }
    if (playback.active) {
      stopPlayback();
      return;
    }
    if (!tl.events.length) return;
    const anchor = tl.selected ? tl.selected.t : tl.cursorMs;
    if (anchor == null) return;
    const lastT = tl.events[tl.events.length - 1].t;
    startPlayback('live', { t0: Math.max(0, anchor), t1: lastT });
  }
  $('#btnPause').addEventListener('click', togglePause);
  $('#btnFollow').addEventListener('click', toggleFollow);

  const mm = $('#mmCanvas');
  let mmDragging = false;
  function mmUpdateFromPx(px) {
    if (!tl.events.length) return;
    const r = mm.getBoundingClientRect();
    const t1 = Math.max(500, tl.events[tl.events.length - 1].t + 200);
    const tClick = (px / r.width) * t1;
    const spanMs = tl._tlPxWidth() / tl.view.pxPerMs;
    tl.view.t0 = Math.max(0, tClick - spanMs / 2);
    tl.followTail = false;
    tl.draw();
    updateStatus();
  }
  mm.addEventListener('mousedown', (e) => {
    mmDragging = true;
    const r = mm.getBoundingClientRect();
    mmUpdateFromPx(e.clientX - r.left);
  });
  window.addEventListener('mousemove', (e) => {
    if (!mmDragging) return;
    const r = mm.getBoundingClientRect();
    mmUpdateFromPx(Math.max(0, Math.min(r.width, e.clientX - r.left)));
  });
  window.addEventListener('mouseup', () => { mmDragging = false; });

  const HIDDEN_KEY = 'inspect.hiddenLanes';
  function loadHidden() {
    try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]')); } catch (_) { return new Set(); }
  }
  function saveHidden(set) {
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...set])); } catch (_) {}
  }
  tl.hiddenLanes = loadHidden();
  function applyHidden() {
    document.querySelectorAll('#laneGutter .lane-label').forEach(el => {
      const id = el.dataset.lane;
      if (tl.hiddenLanes.has(id)) {
        el.style.opacity = '0.35';
        el.style.textDecoration = 'line-through';
      } else {
        el.style.opacity = '';
        el.style.textDecoration = '';
      }
    });
    tl.draw();
  }
  setTimeout(applyHidden, 50);

  $('#laneGutter').addEventListener('click', (e) => {
    const lbl = e.target.closest('.lane-label');
    if (!lbl) return;
    const laneId = lbl.dataset.lane;
    if (tl.hiddenLanes.has(laneId)) tl.hiddenLanes.delete(laneId);
    else tl.hiddenLanes.add(laneId);
    saveHidden(tl.hiddenLanes);
    applyHidden();
  });
  $('#laneGutter').addEventListener('dblclick', (e) => {
    const lbl = e.target.closest('.lane-label');
    if (!lbl) return;
    const laneId = lbl.dataset.lane;
    tl.hiddenLanes.delete(laneId);
    saveHidden(tl.hiddenLanes);
    applyHidden();
    const centerMs = tl.view.t0 + (tl._tlPxWidth() / tl.view.pxPerMs) / 2;
    const next = tl.events.find(x => x.lane === laneId && x.t > centerMs) || tl.events.find(x => x.lane === laneId);
    if (next) selectEvent(next);
  });

  const LANE_META = {};
  D.LANES.forEach(L => LANE_META[L.id] = { name: L.name });
  LANE_META.error = { name: 'Error' };

  function selectEvent(e) {
    tl.selected = e;
    tl.draw();
    renderInspector(e);
  }

  function corrRefs(e) {
    const c = e.corr || {};
    const out = [];
    if (c.turn_id) out.push(['turn', aliasFor('turn', c.turn_id)]);
    if (c.item_id) out.push(['item', aliasFor('item', c.item_id)]);
    if (c.response_id) out.push(['response', aliasFor('response', c.response_id)]);
    if (c.phrase_id) out.push(['phrase', aliasFor('phrase', c.phrase_id)]);
    if (e.lane === 'stt' && c.item_id) {
      const vadStart = tl.events.find(x => x.lane === 'vad' && x.kind === 'confirmed_start' && x.corr?.item_id === c.item_id);
      const vadStop  = tl.events.find(x => x.lane === 'vad' && x.kind === 'stopped'         && x.corr?.item_id === c.item_id);
      if (vadStart) {
        const dur = vadStop ? Math.round(vadStop.t - vadStart.t) : null;
        out.push(['vad', dur != null ? `speech · ${dur}ms` : 'speech']);
      }
    }
    return out;
  }

  function renderInspector(e) {
    if (!e) return;
    $('#iEyebrow').textContent = `Seq ${e.seq} · ${window.__formatMs(e.t)} from session start`;
    const laneMeta = LANE_META[e.lane] || { name: e.lane };
    $('#iTitle').textContent = `${laneMeta.name} · ${e.kind}`;
    const laneColor = D.PALETTES[tl.palette][e.lane] || '#999';
    const isErr = e.lane === 'error' || e.kind === 'error' || e.kind === 'dropped' || e.kind === 'raised' || e.kind === 'bargein_missed';
    const tagColor = isErr ? D.PALETTES[tl.palette].error : laneColor;
    const tags = [
      `<span class="tag lane" style="--tag-color:${tagColor}">${e.lane}</span>`,
      `<span class="tag lane" style="--tag-color:${tagColor}">${e.kind}</span>`,
    ];
    corrRefs(e).forEach(([k,v]) => {
      tags.push(`<span class="tag"><span style="color:var(--fg-dim);margin-right:4px">${k}</span>${v}</span>`);
    });
    if (e.span_id) tags.push(`<span class="tag">span ${e.span_id.slice(0,10)}</span>`);
    $('#iSubline').innerHTML = tags.join('');

    renderBody(e);
    const tabLabel = $('.insp-tab[data-tab="related"]');
    tabLabel.innerHTML = `Related <span style="color:var(--fg-faint);margin-left:4px">${countRelated(e)}</span>`;
  }

  function related(e) {
    const c = e.corr || {};
    if (!c.turn_id && !c.item_id && !c.response_id && !c.phrase_id) return [];
    return tl.events.filter(x => {
      if (x.seq === e.seq) return false;
      const xc = x.corr || {};
      return (c.phrase_id && xc.phrase_id === c.phrase_id)
          || (c.response_id && xc.response_id === c.response_id)
          || (c.item_id && xc.item_id === c.item_id)
          || (c.turn_id && xc.turn_id === c.turn_id && !c.response_id && !c.item_id);
    });
  }
  function countRelated(e) { return related(e).length; }

  let activeTab = 'pretty';
  function renderBody(e) {
    const body = $('#inspBody');
    body.classList.toggle('pretty', activeTab === 'pretty');
    body.classList.toggle('raw',    activeTab === 'raw');

    if (activeTab === 'pretty') {
      body.innerHTML = prettyHTML(e);
    } else if (activeTab === 'raw') {
      body.innerHTML = '<pre>' + syntaxHighlight(JSON.stringify(stripHelpers(e), null, 2)) + '</pre>';
    } else {
      body.innerHTML = relatedHTML(e);
    }
  }

  function stripHelpers(e) { const { t, ...rest } = e; return rest; }

  function prettyHTML(e) {
    const meta = [
      ['lane',     e.lane],
      ['kind',     e.kind],
      ['seq',      e.seq],
      ['t (mono)', window.__formatMs(e.t)],
      ['wall',     new Date(e.ts_wall * 1000).toISOString().replace('T', ' ').replace('Z', '')],
      ['span_id',  e.span_id],
    ];
    const refs = corrRefs(e);
    const payload = Object.entries(e.payload || {});
    return `
      <div class="sec">
        <h4>Event</h4>
        ${meta.map(([k,v]) => row(k, v, k === 't (mono)' ? 'num' : null)).join('')}
      </div>
      <div class="sec">
        <h4>Correlation</h4>
        ${refs.length
          ? refs.map(([k,v]) => corrRow(e, k, v)).join('')
          : `<div class="row"><span class="k">—</span><span class="v nul">no references</span></div>`}
      </div>
      <div class="sec">
        <h4>Payload</h4>
        ${payload.length ? payload.map(([k,v]) => k === 'messages' && Array.isArray(v) ? messagesRow(k, v) : row(k, v, typeofClass(v))).join('') : `<div class="row"><span class="k">—</span><span class="v nul">no payload</span></div>`}
      </div>
      <div class="sec">
        <h4>Cross-reference</h4>
        ${row('session.id', e.session_id)}
        ${row('OTEL', e.span_id ? 'open in Tempo ↗' : '—', 'str')}
      </div>
    `;
  }

  function firstEventForCorr(kind, rawId) {
    if (kind === 'vad') {
      return tl.events.find(x => x.lane === 'vad' && x.kind === 'confirmed_start' && x.corr?.item_id === rawId);
    }
    const field = kind + '_id';
    return tl.events.find(x => x.corr && x.corr[field] === rawId);
  }

  function corrRow(e, k, label) {
    const c = e.corr || {};
    const rawId = k === 'vad' ? c.item_id : c[k + '_id'];
    const target = rawId ? firstEventForCorr(k, rawId) : null;
    if (!target) return row(k, label, 'str');
    return `<div class="row" data-seq="${target.seq}" style="cursor:pointer" title="Jump to ${escapeHTML(String(label))}"><span class="k">${k}</span><span class="v str">${escapeHTML(String(label))}</span></div>`;
  }

  function sanitizeContentForDisplay(c) {
    if (typeof c === 'string') return c;
    if (!Array.isArray(c)) return JSON.stringify(c);
    return JSON.stringify(c.map(part => {
      if (part && part.type === 'audio_url' && part.audio_url && typeof part.audio_url.url === 'string' && part.audio_url.url.startsWith('data:')) {
        const kb = Math.round(part.audio_url.url.length * 0.75 / 1024);
        return { type: 'audio_url', audio_url: { url: `[WAV ${kb} KB]` } };
      }
      return part;
    }), null, 2);
  }

  function messagesRow(k, msgs) {
    const lines = msgs.map(m => {
      const role = m.role || '?';
      const text = sanitizeContentForDisplay(m.content);
      return `<div><span class="v str">${escapeHTML(role)}</span>: ${escapeHTML(text)}</div>`;
    }).join('');
    return `<div class="row"><span class="k">${k}</span><span class="v" style="white-space:pre-wrap;word-break:break-word">${lines}</span></div>`;
  }

  function row(k, v, cls) {
    let val;
    if (v == null) val = '<span class="v nul">null</span>';
    else if (typeof v === 'number') val = `<span class="v num">${v}</span>`;
    else if (typeof v === 'string') val = `<span class="v ${cls||'str'}">${escapeHTML(v)}</span>`;
    else if (typeof v === 'object') val = `<pre class="v obj" style="white-space:pre-wrap;margin:0;font:inherit">${syntaxHighlight(JSON.stringify(v, null, 2))}</pre>`;
    else val = `<span class="v">${escapeHTML(String(v))}</span>`;
    return `<div class="row"><span class="k">${k}</span>${val}</div>`;
  }
  function typeofClass(v) {
    if (typeof v === 'number') return 'num';
    if (typeof v === 'string') return 'str';
    if (v == null) return 'nul';
    return '';
  }
  function escapeHTML(s) {
    return String(s).replace(/[&<>]/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;' }[c]));
  }
  function syntaxHighlight(json) {
    return json
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/("[^"]+")(\s*:)/g, '<span class="tok-k">$1</span>$2')
      .replace(/:\s*("[^"]*")/g, ': <span class="tok-s">$1</span>')
      .replace(/:\s*(-?\d+\.?\d*)/g, ': <span class="tok-n">$1</span>')
      .replace(/:\s*(true|false)/g, ': <span class="tok-b">$1</span>')
      .replace(/:\s*(null)/g, ': <span class="tok-p">$1</span>');
  }
  function relatedHTML(e) {
    const rel = related(e);
    if (!rel.length) {
      return '<div style="color:var(--fg-dim);font-family:var(--font-serif);font-style:italic">No correlated events.</div>';
    }
    const refs = corrRefs(e);
    return `
      <div style="font-family:var(--font-sans);font-size:10px;letter-spacing:0.1em;text-transform:uppercase;color:var(--fg-dim);margin-bottom:10px">
        ${rel.length} events · ${refs.map(([k,v]) => `${k}=${v}`).join(' · ')}
      </div>
      ${rel.map(x => `
        <div class="row" style="cursor:pointer;padding:6px 0;border-bottom:1px dashed var(--hair)" data-seq="${x.seq}">
          <span class="k" style="font-variant-numeric:tabular-nums">${window.__formatMs(x.t)}</span>
          <span class="v"><span class="tag lane" style="--tag-color:${D.PALETTES[tl.palette][x.lane]||'#999'}">${x.lane}</span> ${x.kind}</span>
        </div>
      `).join('')}
    `;
  }

  document.querySelectorAll('.insp-tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.insp-tab').forEach(x => x.setAttribute('aria-selected', 'false'));
      t.setAttribute('aria-selected', 'true');
      activeTab = t.dataset.tab;
      if (tl.selected) renderBody(tl.selected);
    });
  });
  $('#inspBody').addEventListener('click', (ev) => {
    const row = ev.target.closest('[data-seq]');
    if (!row) return;
    const seq = parseInt(row.dataset.seq, 10);
    const e = tl.events.find(x => x.seq === seq);
    if (e) { selectEvent(e); centerOn(e.t); }
  });

  $('#btnCopy').addEventListener('click', () => {
    if (!tl.selected) return;
    navigator.clipboard.writeText(JSON.stringify(stripHelpers(tl.selected), null, 2));
    const btn = $('#btnCopy');
    const prev = btn.textContent;
    btn.textContent = 'copied ✓';
    setTimeout(() => btn.textContent = prev, 900);
  });

  async function downloadBlob(url, filename) {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 0);
  }
  $('#btnExport').addEventListener('click', async () => {
    const btn = $('#btnExport');
    const prev = btn.textContent;
    btn.textContent = '⤓ …';
    try {
      if (currentSid) {
        const res = await fetch(`/v1/inspect/sessions/history/${encodeURIComponent(currentSid)}`, { cache: 'no-store' });
        if (res.ok) {
          const blob = await res.blob();
          downloadBlob(URL.createObjectURL(blob), `${currentSid}.ndjson`);
          btn.textContent = '⤓ done';
          setTimeout(() => btn.textContent = prev, 900);
          return;
        }
      }
      const body = tl.events.map(e => JSON.stringify(stripHelpers(e))).join('\n') + '\n';
      const blob = new Blob([body], { type: 'application/x-ndjson' });
      const name = currentSid ? `${currentSid}.ndjson` : `inspector-${Date.now()}.ndjson`;
      downloadBlob(URL.createObjectURL(blob), name);
      btn.textContent = '⤓ done';
    } catch (err) {
      btn.textContent = '⤓ fail';
    } finally {
      setTimeout(() => btn.textContent = prev, 900);
    }
  });
  $('#btnExportAudio').addEventListener('click', async () => {
    if (!currentSid) return;
    const btn = $('#btnExportAudio');
    const prev = btn.textContent;
    btn.textContent = '⤓ …';
    try {
      for (const ch of ['mic_in', 'tts_out']) {
        const res = await fetch(`/v1/inspect/sessions/${encodeURIComponent(currentSid)}/audio?channel=${ch}&from_ms=0&to_ms=0`, { cache: 'no-store' });
        if (!res.ok) continue;
        const blob = await res.blob();
        if (blob.size <= 44) continue;
        downloadBlob(URL.createObjectURL(blob), `${currentSid}.${ch}.wav`);
      }
      btn.textContent = '⤓ done';
    } catch (err) {
      btn.textContent = '⤓ fail';
    } finally {
      setTimeout(() => btn.textContent = prev, 1200);
    }
  });

  document.querySelectorAll('.insp-foot .nav button').forEach((b, i) => {
    b.addEventListener('click', () => {
      if (!tl.selected) return;
      const lane = tl.selected.lane;
      const laneEvs = tl.events.filter(x => x.lane === lane);
      const idx = laneEvs.findIndex(x => x.seq === tl.selected.seq);
      const next = i === 0 ? laneEvs[Math.max(0, idx - 1)] : laneEvs[Math.min(laneEvs.length - 1, idx + 1)];
      if (next) selectEvent(next);
    });
  });

  const tt = $('#tooltip');
  function showTooltip(e, mx, my) {
    const laneColor = D.PALETTES[tl.palette][e.lane] || '#999';
    const p = e.payload || {};
    const rows = [];
    if (p.text) rows.push(['text', shorten(p.text, 600)]);
    if (p.delta) rows.push(['delta', shorten(String(p.delta), 600)]);
    if (e.lane === 'llm' && (e.kind === 'chunk' || e.kind === 'done' || e.kind === 'cancelled')) {
      const rid = e.corr?.response_id;
      let cumm = '';
      for (const x of tl.events) {
        if (x.lane !== 'llm' || x.kind !== 'chunk') continue;
        if (rid && x.corr?.response_id !== rid) continue;
        if (x.seq > e.seq) break;
        if (x.payload?.delta) cumm += x.payload.delta;
      }
      if (cumm) rows.push([e.kind === 'chunk' ? 'cumm' : 'text', shorten(cumm, 600)]);
    }
    if (e.lane === 'response' && (e.kind === 'done' || e.kind === 'cancelled')) {
      const rid = e.corr?.response_id;
      const phrases = [];
      for (const x of tl.events) {
        if (x.lane !== 'tts_req' || x.kind !== 'phrase_sent') continue;
        if (rid && x.corr?.response_id !== rid) continue;
        if (x.seq > e.seq) break;
        const t = x.payload?.text;
        if (t) phrases.push(t);
      }
      if (phrases.length) rows.push(['phrases', shorten(phrases.map((t,i) => `${i+1}. ${t}`).join('\n'), 600)]);
    }
    if (e.lane === 'stt' && e.kind === 'backfill') {
      rows.push(['backfill', p.text || '']);
      rows.push(['item', p.item_id || '']);
    }
    if (e.lane === 'turn' && e.kind === 'bargein_context') {
      if (p.heard) rows.push(['heard', shorten(p.heard, 300)]);
      if (p.unheard) rows.push(['unheard', shorten(p.unheard, 300)]);
    }
    if (e.lane === 'bargein') {
      if (p.delay_ms != null) rows.push(['delay', p.delay_ms + 'ms']);
      if (p.reason) rows.push(['reason', p.reason]);
    }
    if (p.model) rows.push(['model', p.model]);
    if (p.bytes != null) rows.push(['bytes', p.bytes]);
    if (p.event_type) rows.push(['event', p.event_type]);
    if (p.elapsed_ms != null) rows.push(['elapsed', p.elapsed_ms + 'ms']);
    if (p.ttft_ms != null) rows.push(['ttft', p.ttft_ms + 'ms']);
    if (p.tok_out != null) rows.push(['tok_out', p.tok_out]);
    if (p.prob != null) rows.push(['prob', p.prob]);
    if (p.rms != null) rows.push(['rms', p.rms]);
    if (p.ms_audio != null) rows.push(['ms_audio', p.ms_audio]);
    if (p.reason) rows.push(['reason', p.reason]);
    if (p.error) rows.push(['error', shorten(p.error, 600)]);
    if (p.avg_no_speech_prob != null) {
      const parts = [`avg ${p.avg_no_speech_prob}`];
      if (p.min_no_speech_prob != null) parts.push(`min ${p.min_no_speech_prob}`);
      if (p.max_no_speech_prob != null) parts.push(`max ${p.max_no_speech_prob}`);
      if (p.no_speech_prob_threshold != null) parts.push(`thr ${p.no_speech_prob_threshold}`);
      else if (p.threshold != null) parts.push(`thr ${p.threshold}`);
      rows.push(['no_speech', parts.join(' · ')]);
    }

    const c = e.corr || {};
    if (c.phrase_id) rows.push(['phrase', aliasFor('phrase', c.phrase_id)]);
    else if (c.response_id) rows.push(['response', aliasFor('response', c.response_id)]);
    else if (c.item_id) rows.push(['item', aliasFor('item', c.item_id)]);
    else if (c.turn_id) rows.push(['turn', aliasFor('turn', c.turn_id)]);

    tt.innerHTML = `
      <div class="t-head">
        <span class="t-lane" style="background:${laneColor}40;color:${laneColor}">${e.lane}</span>
        <span>${e.kind}</span>
        <span class="t-dim" style="margin-left:auto">${window.__formatMs(e.t)}</span>
      </div>
      ${rows.map(([k,v]) => `<div class="t-row"><span class="t-dim">${k}</span><span>${escapeHTML(v)}</span></div>`).join('')}
      <div class="t-row" style="margin-top:6px"><span class="t-dim">seq</span><span>${e.seq}</span></div>
    `;
    moveTooltip(mx, my);
    tt.style.display = 'block';
  }
  function moveTooltip(mx, my) {
    const r = tt.getBoundingClientRect();
    let x = mx + 14, y = my + 14;
    if (x + r.width > window.innerWidth - 8) x = mx - r.width - 14;
    if (y + r.height > window.innerHeight - 8) y = my - r.height - 14;
    tt.style.left = x + 'px';
    tt.style.top  = y + 'px';
  }
  function hideTooltip() { tt.style.display = 'none'; }
  function shorten(s, n) { s = String(s); return s.length > n ? s.slice(0, n) + '…' : s; }

  const tweakPanel = $('#tweakPanel');
  const fab = $('#tweakFab');
  $('#tweakClose').addEventListener('click', () => tweakPanel.classList.remove('open'));
  fab.addEventListener('click', () => tweakPanel.classList.toggle('open'));

  document.querySelectorAll('.seg').forEach(seg => {
    const name = seg.dataset.tweak;
    seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === TWEAKS[name] ? 'true' : 'false'));
    seg.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', 'false'));
      b.setAttribute('aria-pressed', 'true');
      const v = b.dataset.v;
      TWEAKS[name] = v;
      applyTweak(name, v);
      persistTweaks();
    });
  });

  function applyTweak(name, v) {
    if (name === 'treatment') tl.setTreatment(v);
    if (name === 'density')   tl.setDensity(v);
    if (name === 'palette')   { tl.setPalette(v); if (tl.selected) renderInspector(tl.selected); }
    if (name === 'theme')     applyTheme(v);
  }

  function persistTweaks() {
    try { localStorage.setItem('inspect.tweaks', JSON.stringify(TWEAKS)); } catch (_) {}
  }

  const sessionPill = $('#sessionPill');
  sessionPill.addEventListener('click', async () => {
    const pop = document.createElement('div');
    pop.style.cssText = 'position:absolute;z-index:40;background:var(--bg-panel);border:1px solid var(--hair-2);border-radius:8px;padding:6px;min-width:320px;font-family:var(--font-mono);font-size:12px;max-height:60vh;overflow:auto;box-shadow:var(--shadow-raised)';
    const r = sessionPill.getBoundingClientRect();
    pop.style.left = r.left + 'px';
    pop.style.top = (r.bottom + 4) + 'px';
    document.body.appendChild(pop);
    function close() { pop.remove(); document.removeEventListener('mousedown', onDoc); }
    function onDoc(e) { if (!pop.contains(e.target)) close(); }
    setTimeout(() => document.addEventListener('mousedown', onDoc), 0);
    pop.innerHTML = '<div style="padding:8px;color:var(--fg-dim)">loading…</div>';
    const { live, hist } = await fetchSessions();
    const lines = [];
    lines.push('<div style="padding:4px 8px;color:var(--fg-faint);text-transform:uppercase;letter-spacing:0.1em;font-size:10px">Live</div>');
    if (!live.length) lines.push('<div style="padding:6px 10px;color:var(--fg-dim)">—</div>');
    for (const s of live) {
      lines.push(`<div class="__sess" data-id="${s.id}" data-live="1" style="padding:6px 10px;cursor:pointer;border-radius:4px">${s.id} <span style="color:var(--fg-dim)">· ${s.model||''}</span></div>`);
    }
    lines.push('<div style="padding:4px 8px;color:var(--fg-faint);text-transform:uppercase;letter-spacing:0.1em;font-size:10px;margin-top:6px">History</div>');
    if (!hist.length) lines.push('<div style="padding:6px 10px;color:var(--fg-dim)">—</div>');
    for (const s of hist.slice(0, 50)) {
      const dt = new Date(s.mtime*1000).toISOString().replace('T',' ').slice(0,19);
      const kb = (s.size_bytes/1024).toFixed(0);
      lines.push(`<div class="__sess" data-id="${s.id}" data-live="0" style="padding:6px 10px;cursor:pointer;border-radius:4px">${s.id} <span style="color:var(--fg-dim)">· ${dt} · ${kb} KB</span></div>`);
    }
    if (DEV) {
      lines.push('<div style="padding:4px 8px;color:var(--fg-faint);text-transform:uppercase;letter-spacing:0.1em;font-size:10px;margin-top:6px">Dev scenarios</div>');
      lines.push('<div class="__sess" data-dev="clean" style="padding:6px 10px;cursor:pointer">dev: clean turn</div>');
      lines.push('<div class="__sess" data-dev="problem" style="padding:6px 10px;cursor:pointer">dev: problem turn</div>');
    }
    pop.innerHTML = lines.join('');
    pop.querySelectorAll('.__sess').forEach(el => {
      el.addEventListener('mouseover', () => el.style.background = 'rgba(255,255,255,0.04)');
      el.addEventListener('mouseout', () => el.style.background = '');
      el.addEventListener('click', () => {
        const devv = el.dataset.dev;
        if (devv) { loadDevScenario(devv); close(); return; }
        const id = el.dataset.id;
        history.replaceState(null, '', `?sid=${encodeURIComponent(id)}`);
        openSession(id);
        close();
      });
    });
  });

  // Replay: windowed (`r`) sweeps selected.t - pre to selected.t + post; live (Shift+R) sweeps to end.
  // Both drive tl.playheadMs via rAF and kick off audio playback.
  const playback = {
    active: false,
    mode: null,
    startWall: 0,
    startMs: 0,
    endMs: 0,
    speed: 1,
    scrubbing: false,
    channels: { mic: true, tts: true },
    rafId: null,
  };

  function currentSpeed() { return parseFloat(TWEAKS.replaySpeed || '1') || 1; }
  function currentPre()   { return parseInt(TWEAKS.replayPre  || '500', 10); }
  function currentPost()  { return parseInt(TWEAKS.replayPost || '1000', 10); }

  function startPlayback(mode, opts) {
    stopPlayback(false);
    if (!tl.events.length) return;
    const lastT = tl.events[tl.events.length - 1].t;

    let t0, t1;
    if (opts && opts.t0 != null && opts.t1 != null) {
      t0 = Math.max(0, opts.t0);
      t1 = Math.min(lastT, opts.t1);
    } else if (mode === 'window') {
      const anchor = tl.selected ? tl.selected.t : (tl.cursorMs ?? lastT / 2);
      t0 = Math.max(0, anchor - currentPre());
      t1 = Math.min(lastT, anchor + currentPost());
    } else {
      const spanMs = tl._tlPxWidth() / tl.view.pxPerMs;
      t0 = tl.playheadMs ?? (tl.view.t0 + spanMs / 2);
      t1 = lastT;
    }
    if (t1 <= t0) return;

    playback.active = true;
    playback.mode = mode;
    playback.startWall = performance.now();
    playback.startMs = t0;
    playback.endMs = t1;
    playback.speed = currentSpeed();
    tl.playbackRange = { t0, t1 };
    tl.playheadMs = t0;
    tl.paused = false;
    setState('replay');

    $('#btnReplayStop').style.display = '';
    $('#audioChannelsWrap').style.display = '';

    const _end = mode === 'live' ? 0 : Math.max(t0 + 100, t1);
    const anchor = (tl.selected && tl.selected.t >= t0 && tl.selected.t <= t1) ? tl.selected : null;
    startAudioSources(t0 | 0, _end | 0, anchor).catch(err => console.error('[inspector] startAudioSources failed:', err));

    if (mode === 'window') {
      const spanMs = tl._tlPxWidth() / tl.view.pxPerMs;
      const windowMs = t1 - t0;
      if (windowMs > spanMs * 0.9) {
        tl.view.pxPerMs = (tl._tlPxWidth() - 80) / windowMs;
      }
      tl.view.t0 = Math.max(0, t0 - 60 / tl.view.pxPerMs);
      tl.followTail = false;
    }

    const tick = () => {
      if (!playback.active) return;
      if (!playback.scrubbing) {
        const elapsed = (performance.now() - playback.startWall) * playback.speed;
        tl.playheadMs = playback.startMs + elapsed;
      }
      const ph = tl.playheadMs;
      if (playback.mode === 'live' && !playback.scrubbing) {
        const spanMs = tl._tlPxWidth() / tl.view.pxPerMs;
        tl.view.t0 = Math.max(0, ph - spanMs * 0.4);
      }
      updateAudioIndicator(ph);
      tl.draw();
      updateStatus();
      if (!playback.scrubbing && ph >= playback.endMs) { stopPlayback(true); return; }
      playback.rafId = requestAnimationFrame(tick);
    };
    playback.rafId = requestAnimationFrame(tick);
  }

  function stopPlayback() {
    if (playback.rafId) cancelAnimationFrame(playback.rafId);
    playback.rafId = null;
    const wasActive = playback.active;
    playback.active = false;
    playback.mode = null;
    tl.playheadMs = null;
    tl.playbackRange = null;
    stopAudioSources();
    $('#btnReplayStop').style.display = 'none';
    $('#audioChannelsWrap').style.display = 'none';
    $('#stAudio').textContent = 'audio ○ idle';
    $('#stAudio').style.color = 'var(--fg-faint)';
    if (wasActive) { setState(currentWs && currentWs.readyState === 1 ? 'live' : 'replay'); tl.draw(); }
  }

  let _actx = null;
  function getActx() {
    if (!_actx) {
      try { _actx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (err) { console.error('[inspector] AudioContext creation failed (replay disabled):', err); _actx = null; }
    }
    return _actx;
  }
  const _audio = {
    mic: { src: null, gain: null },
    tts: { src: null, gain: null },
    active: false,
    fromMs: 0,
    // Aborted by the next startAudioSources so in-flight fetches don't schedule stale sources.
    abort: null,
    // All scheduled sources; stopAudioSources walks this so nothing leaks if only one side is referenced.
    sources: [],
  };

  async function fetchAudioBuffer(channel, fromMs, toMs, signal) {
    if (!currentSid) return null;
    const url = `/v1/inspect/sessions/${encodeURIComponent(currentSid)}/audio?channel=${channel}&from_ms=${fromMs|0}&to_ms=${toMs|0}`;
    try {
      const r = await fetch(url, { signal });
      if (!r.ok) return null;
      const buf = await r.arrayBuffer();
      if (buf.byteLength <= 44) return null;
      const actx = getActx();
      if (!actx) return null;
      return await actx.decodeAudioData(buf);
    } catch (err) {
      if (err && err.name === 'AbortError') return null;
      console.error('[inspector] fetchAudioBuffer failed:', channel, fromMs, toMs, err);
      return null;
    }
  }

  function stopAudioSources() {
    if (_audio.abort) { _audio.abort.abort(); _audio.abort = null; }
    for (const src of _audio.sources) {
      try { src.stop(); } catch (_) {}
    }
    _audio.sources = [];
    for (const side of ['mic', 'tts']) {
      _audio[side].src = null;
    }
    _audio.active = false;
  }

  // Anchor lane hints which channel is relevant (user-speech lanes -> mic only, tts lanes -> tts only).
  function effectiveChannels(anchor) {
    const prefer = { mic: true, tts: true };
    if (anchor && anchor.lane) {
      if (['vad','stt','turn','bargein'].includes(anchor.lane)) prefer.tts = false;
      else if (['tts_req','tts_chunk'].includes(anchor.lane)) prefer.mic = false;
      else if (anchor.lane === 'audio_level' && anchor.payload && anchor.payload.channel === 'tts_out') prefer.mic = false;
      else if (anchor.lane === 'audio_level' && anchor.payload && anchor.payload.channel === 'mic_in') prefer.tts = false;
      else if (anchor.lane === 'response' && anchor.kind === 'phrase_boundary') prefer.mic = false;
    }
    return {
      mic: playback.channels.mic && prefer.mic,
      tts: playback.channels.tts && prefer.tts,
    };
  }

  async function startAudioSources(fromMs, toMs, anchor) {
    stopAudioSources();
    const ctl = new AbortController();
    _audio.abort = ctl;
    const signal = ctl.signal;
    const actx = getActx();
    if (!actx) return;
    if (actx.state === 'suspended') {
      try { await actx.resume(); }
      catch (err) { console.error('[inspector] replay AudioContext resume failed (audio will not play):', err); }
    }
    if (signal.aborted) return;
    const eff = effectiveChannels(anchor);
    const [micBuf, ttsBuf] = await Promise.all([
      eff.mic ? fetchAudioBuffer('mic_in', fromMs, toMs, signal) : Promise.resolve(null),
      eff.tts ? fetchAudioBuffer('tts_out', fromMs, toMs, signal) : Promise.resolve(null),
    ]);
    if (signal.aborted) return;
    const speed = parseFloat(TWEAKS.replaySpeed || '1') || 1;
    const t0 = actx.currentTime + 0.02;
    function schedule(side, buf) {
      if (!buf) return;
      const src = actx.createBufferSource();
      src.buffer = buf;
      src.playbackRate.value = speed;
      const g = actx.createGain();
      g.gain.value = 1;
      src.connect(g).connect(actx.destination);
      try { src.start(t0); }
      catch (err) { console.error('[inspector] BufferSource.start failed:', side, t0, err); }
      _audio[side].src = src;
      _audio[side].gain = g;
      _audio.sources.push(src);
    }
    schedule('mic', micBuf);
    schedule('tts', ttsBuf);
    _audio.active = true;
    _audio.fromMs = fromMs;
  }

  function applyChannelGain(side, on) {
    const g = _audio[side].gain;
    if (g) g.gain.value = on ? 1 : 0;
  }

  function updateAudioIndicator(ph) {
    const el = $('#stAudio');
    if (playback.active) {
      el.textContent = `audio ● playing @ ${window.__formatMs(ph)}`;
      el.style.color = '';
    } else {
      el.textContent = 'audio ○ idle';
      el.style.color = 'var(--fg-faint)';
    }
  }

  $('#btnReplayStop').addEventListener('click', stopPlayback);
  $('#btnChMic').addEventListener('click', () => {
    playback.channels.mic = !playback.channels.mic;
    $('#btnChMic').setAttribute('aria-pressed', playback.channels.mic ? 'true' : 'false');
    applyChannelGain('mic', playback.channels.mic);
    if (playback.active) updateAudioIndicator(tl.playheadMs ?? 0);
  });
  $('#btnChTts').addEventListener('click', () => {
    playback.channels.tts = !playback.channels.tts;
    $('#btnChTts').setAttribute('aria-pressed', playback.channels.tts ? 'true' : 'false');
    applyChannelGain('tts', playback.channels.tts);
    if (playback.active) updateAudioIndicator(tl.playheadMs ?? 0);
  });

  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    if (e.key === 'Escape' && playback.active) { stopPlayback(); e.preventDefault(); }
  });

  // Shift-drag selects replay range; dragging the playhead during replay scrubs.
  const tlWrap = wrap;
  let selDrag = null;
  let playheadDrag = false;
  tlWrap.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    const rect = tlWrap.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const tMs = tl._pxToMs(px);
    if (playback.active && tl.playheadMs != null) {
      const phPx = tl.msToPx(tl.playheadMs);
      if (Math.abs(phPx - px) < 8) {
        playheadDrag = true;
        playback.scrubbing = true;
        e.stopPropagation();
        return;
      }
    }
    if (e.shiftKey) {
      selDrag = { startMs: tMs };
      e.stopPropagation();
    }
  }, true);
  window.addEventListener('mousemove', (e) => {
    const rect = tlWrap.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (playheadDrag) {
      const ph = Math.max(0, tl._pxToMs(px));
      tl.playheadMs = ph;
      playback.startWall = performance.now();
      playback.startMs = ph;
      updateAudioIndicator(ph);
      tl.draw();
      return;
    }
    if (selDrag) {
      const tMs = tl._pxToMs(px);
      tl.playbackRange = { t0: Math.min(selDrag.startMs, tMs), t1: Math.max(selDrag.startMs, tMs) };
      tl.draw();
    }
  });
  window.addEventListener('mouseup', () => {
    if (playheadDrag) {
      playheadDrag = false;
      playback.scrubbing = false;
      if (playback.active && tl.playheadMs != null) {
        const from = tl.playheadMs | 0;
        const to = playback.mode === 'window' ? (playback.endMs | 0) : 0;
        startAudioSources(from, to).catch(err => console.error('[inspector] startAudioSources (re-seek) failed:', err));
      }
    }
    if (selDrag && tl.playbackRange) {
      const r = tl.playbackRange;
      if (r.t1 - r.t0 > 40) startPlayback('window', { t0: r.t0, t1: r.t1 });
      else { tl.playbackRange = null; tl.draw(); }
    }
    selDrag = null;
  });

  async function boot() {
    const params = new URLSearchParams(location.search);
    const sid = params.get('sid');
    if (sid) {
      openSession(sid);
      startLivePoll();
      return;
    }
    if (DEV && params.get('scenario')) {
      loadDevScenario(params.get('scenario'));
      return;
    }
    const { live, hist } = await fetchSessions();
    const pick = (live && live[0]) || (hist && hist[0]);
    if (pick) {
      history.replaceState(null, '', `?sid=${encodeURIComponent(pick.id)}`);
      openSession(pick.id);
    } else if (DEV && D.generateClean) {
      loadDevScenario('clean');
    } else {
      setState('replay');
      $('#sessionIdText').textContent = 'click to pick a session';
      $('#iTitle').textContent = 'No active session';
      $('#iSubline').innerHTML = '<span class="tag">click the session pill ▾ to open one; a new live session will auto-load when it starts</span>';
    }
    startLivePoll();
  }

  // Poll live sessions; auto-load when idle, else flash pill if a different session appears.
  let _lastLiveIds = new Set();
  function startLivePoll() {
    setInterval(async () => {
      try {
        const r = await fetch('/v1/inspect/sessions', { cache: 'no-store' });
        if (!r.ok) return;
        const arr = await r.json();
        const ids = new Set(arr.map(s => s.id));
        const fresh = [...ids].filter(i => !_lastLiveIds.has(i));
        _lastLiveIds = ids;
        if (!fresh.length) return;
        const onDead = !currentWs || currentWs.readyState !== 1;
        if (onDead) {
          const pickId = fresh[0];
          history.replaceState(null, '', `?sid=${encodeURIComponent(pickId)}`);
          openSession(pickId);
          flashPill();
        } else if (!ids.has(currentSid)) {
          flashPill();
        }
      } catch (err) {
        console.error('[inspector] live-session poll failed:', err);
      }
    }, 2000);
  }
  function flashPill() {
    const p = $('#sessionPill');
    if (!p) return;
    p.style.transition = 'box-shadow 0.4s';
    p.style.boxShadow = '0 0 0 2px var(--accent)';
    setTimeout(() => { p.style.boxShadow = ''; }, 900);
  }
  window.addEventListener('inspector:openSession', (e) => {
    if (e.detail && e.detail.sid) {
      history.replaceState(null, '', `?sid=${encodeURIComponent(e.detail.sid)}`);
      openSession(e.detail.sid);
    }
  });

  boot();

  setTimeout(() => {
    const interesting = tl.events.find(x => x.kind === 'first_token')
      || tl.events.find(x => x.kind === 'final')
      || tl.events.find(x => x.lane !== 'audio_level');
    if (interesting) selectEvent(interesting);
  }, 600);

  const settingsBtn = $('#btnSettings');
  const settingsPanel = $('#settingsPanel');
  const settingsClose = $('#settingsClose');
  const ssetFooter = $('#ssetFooter');
  const ssetAudio = $('#ssetAudio');

  const _catalog = { stt: [], tts: [], voices: {} };

  async function fetchModelCatalog() {
    try {
      const r = await fetch('/v1/inspect/sessions/models');
      if (!r.ok) return;
      const data = await r.json();
      for (const m of data.data || []) {
        if (m.task === 'automatic-speech-recognition') _catalog.stt.push(m.id);
        if (m.task === 'text-to-speech') {
          _catalog.tts.push(m.id);
          const voices = (m.voices || []).map(v => typeof v === 'object' ? v.name : String(v));
          if (voices.length) _catalog.voices[m.id] = voices;
        }
      }
      populateSelect('#modelStt', _catalog.stt);
      populateSelect('#modelTts', _catalog.tts);
      $('#modelTts').addEventListener('change', () => updateVoiceOptions());
      updateVoiceOptions();
    } catch (_) {}
  }

  function populateSelect(sel, items, current) {
    const el = typeof sel === 'string' ? $(sel) : sel;
    el.innerHTML = '';
    for (const id of items) {
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = id;
      if (id === current) opt.selected = true;
      el.appendChild(opt);
    }
  }

  function updateVoiceOptions(currentVoice) {
    const ttsModel = $('#modelTts').value;
    const voices = _catalog.voices[ttsModel] || [];
    const allVoices = voices.length ? voices : ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'];
    populateSelect('#modelVoice', allVoices, currentVoice);
  }

  fetchModelCatalog();

  function toggleSettings() {
    const open = document.body.classList.toggle('settings-open');
    settingsBtn.setAttribute('aria-pressed', open ? 'true' : 'false');
    tl.resize();
  }
  settingsBtn.addEventListener('click', toggleSettings);
  settingsClose.addEventListener('click', () => {
    document.body.classList.remove('settings-open');
    settingsBtn.setAttribute('aria-pressed', 'false');
    tl.resize();
  });

  function flashFooter(msg) {
    const ft = ssetFooter;
    ft.classList.add('sent');
    $('#ssetFooterText').textContent = msg || 'session.update sent';
    clearTimeout(ft._timer);
    ft._timer = setTimeout(() => ft.classList.remove('sent'), 1200);
  }

  function sendSessionUpdate(partial) {
    if (!window.__inspectRealtimeWs) return;
    const ws = window.__inspectRealtimeWs;
    if (ws.readyState !== 1) return;
    ws.send(JSON.stringify({ type: 'session.update', session: partial }));
    flashFooter();
  }

  function populateSettings(session) {
    if (!session) return;
    const td = session.turn_detection || {};
    const iat = session.input_audio_transcription || {};

    $('#vadThreshold').value = td.threshold ?? 0.8;
    $('#vadThresholdVal').textContent = (td.threshold ?? 0.8).toFixed(2);
    $('#vadMinSpeech').value = td.min_speech_duration_ms ?? 120;
    $('#vadSilence').value = td.silence_duration_ms ?? 350;
    $('#vadBargeIn').value = td.barge_in_delay_ms ?? 400;
    $('#modelLlm').value = session.model || '';

    setSelectValue('#modelStt', iat.model);
    setSelectValue('#modelTts', session.speech_model);
    updateVoiceOptions(session.voice);
    setSelectValue('#modelVoice', session.voice);

    $('#instructions').value = session.instructions || '';

    const mode = session.audio_direct_to_llm ? 'audio_direct' : 'stt';
    document.querySelector(`input[name="audio_mode"][value="${mode}"]`).checked = true;
    ssetAudio.dataset.mode = mode;
    if (session.audio_direct_model) {
      $('#audioDirectModel').value = session.audio_direct_model;
    }
    if (session.audio_direct_prompt) {
      $('#audioDirectPrompt').value = session.audio_direct_prompt;
    }
  }

  function setSelectValue(sel, val) {
    const el = typeof sel === 'string' ? $(sel) : sel;
    if (!val) return;
    if (![...el.options].some(o => o.value === val)) {
      const opt = document.createElement('option');
      opt.value = val; opt.textContent = val;
      el.insertBefore(opt, el.firstChild);
    }
    el.value = val;
  }

  window.__inspectPopulateSettings = populateSettings;

  document.querySelectorAll('input[name="audio_mode"]').forEach(r => {
    r.addEventListener('change', () => {
      const mode = r.value;
      ssetAudio.dataset.mode = mode;
      sendSessionUpdate({ audio_direct_to_llm: mode === 'audio_direct' });
    });
  });

  let _adModelTimer = null;
  $('#audioDirectModel').addEventListener('input', (e) => {
    clearTimeout(_adModelTimer);
    _adModelTimer = setTimeout(() => {
      sendSessionUpdate({ audio_direct_model: e.target.value });
    }, 600);
  });

  let _promptTimer = null;
  $('#audioDirectPrompt').addEventListener('input', (e) => {
    clearTimeout(_promptTimer);
    _promptTimer = setTimeout(() => {
      sendSessionUpdate({ audio_direct_prompt: e.target.value });
    }, 600);
  });

  $('#vadThreshold').addEventListener('input', (e) => {
    const v = parseFloat(e.target.value);
    $('#vadThresholdVal').textContent = v.toFixed(2);
    sendSessionUpdate({ turn_detection: { threshold: v } });
  });

  function vadNumberInput(id, field) {
    let t = null;
    $(id).addEventListener('input', (e) => {
      clearTimeout(t);
      t = setTimeout(() => {
        sendSessionUpdate({ turn_detection: { [field]: parseInt(e.target.value, 10) || 0 } });
      }, 400);
    });
  }
  vadNumberInput('#vadMinSpeech', 'min_speech_duration_ms');
  vadNumberInput('#vadSilence', 'silence_duration_ms');
  vadNumberInput('#vadBargeIn', 'barge_in_delay_ms');

  $('#modelStt').addEventListener('change', (e) => sendSessionUpdate({ input_audio_transcription: { model: e.target.value } }));
  $('#modelTts').addEventListener('change', (e) => {
    sendSessionUpdate({ speech_model: e.target.value });
    updateVoiceOptions();
  });
  $('#modelVoice').addEventListener('change', (e) => sendSessionUpdate({ voice: e.target.value }));

  let _llmTimer = null;
  $('#modelLlm').addEventListener('input', (e) => {
    clearTimeout(_llmTimer);
    _llmTimer = setTimeout(() => sendSessionUpdate({ model: e.target.value }), 600);
  });

  let _instrTimer = null;
  $('#instructions').addEventListener('input', (e) => {
    clearTimeout(_instrTimer);
    _instrTimer = setTimeout(() => sendSessionUpdate({ instructions: e.target.value }), 600);
  });

  async function enumerateDevices() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      console.log('[devices]', devices.map(d => `${d.kind}: "${d.label}" (${d.deviceId.slice(0,8)})`));
      const micSel = document.getElementById('deviceMic');
      const spkSel = document.getElementById('deviceSpk');
      micSel.innerHTML = '';
      spkSel.innerHTML = '';
      for (const d of devices) {
        const opt = document.createElement('option');
        opt.value = d.deviceId;
        if (d.kind === 'audioinput') {
          opt.textContent = d.label || `Microphone ${micSel.options.length + 1}`;
          micSel.appendChild(opt);
        } else if (d.kind === 'audiooutput') {
          opt.textContent = d.label || `Speaker ${spkSel.options.length + 1}`;
          spkSel.appendChild(opt);
        }
      }
      const savedMic = localStorage.getItem('inspect.deviceMic');
      const savedSpk = localStorage.getItem('inspect.deviceSpk');
      if (savedMic && [...micSel.options].some(o => o.value === savedMic)) micSel.value = savedMic;
      if (savedSpk && [...spkSel.options].some(o => o.value === savedSpk)) spkSel.value = savedSpk;
    } catch (err) { console.warn('[devices] enumerate failed:', err); }
  }

  document.getElementById('deviceMic').addEventListener('change', (e) => {
    localStorage.setItem('inspect.deviceMic', e.target.value);
    window.__inspectSelectedMic = e.target.value;
  });
  document.getElementById('deviceSpk').addEventListener('change', (e) => {
    localStorage.setItem('inspect.deviceSpk', e.target.value);
    window.__inspectSelectedSpk = e.target.value;
  });

  async function ensureDevicePermission() {
    console.log('[settings] requesting mic permission for device enumeration...');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach(t => t.stop());
      console.log('[settings] mic permission granted');
    } catch (err) {
      console.warn('[settings] mic permission denied:', err);
    }
    await enumerateDevices();
  }

  window.__inspectEnumerateDevices = enumerateDevices;
  enumerateDevices();
  ensureDevicePermission();
  navigator.mediaDevices.addEventListener('devicechange', enumerateDevices);

})();
