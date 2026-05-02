const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const CHROMIUM_PATH = process.env.CHROMIUM_PATH || undefined;
const BASE_URL = process.env.SPEACHES_URL || 'ws://127.0.0.1:1327';
const OUT_DIR = process.env.OUT_DIR || '/tmp/realtime_scenarios';
const AUDIO_DIR = path.join(__dirname, 'audio');

// Chromium 147+ refuses ws://localhost from about:blank without these flags.
const CHROMIUM_ARGS = [
  '--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessRespectPreflightResults,BlockInsecurePrivateNetworkRequests',
];

function pcmToB64Chunks(name, chunkBytes = 9600) {
  const buf = fs.readFileSync(path.join(AUDIO_DIR, name));
  const out = [];
  for (let i = 0; i < buf.length; i += chunkBytes) {
    out.push(buf.subarray(i, i + chunkBytes).toString('base64'));
  }
  return out;
}

const SILENCE_200MS = Buffer.alloc(9600).toString('base64');

const HARNESS_JS = `
  window.__h = {
    async openWs(wsUrl) {
      const ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      const events = [];
      const state = { sessionId: null };
      ws.onmessage = (e) => {
        let msg; try { msg = JSON.parse(e.data); } catch { return; }
        events.push({ t: Date.now(), ...msg });
        if (msg.type === 'session.created') state.sessionId = msg.session.id;
      };
      ws.onclose = (e) => console.log('ws onclose, code=' + e.code);
      await new Promise((resolve, reject) => {
        ws.onopen = () => resolve();
        setTimeout(() => reject(new Error('ws open timeout')), 15000);
      });
      while (!state.sessionId) await new Promise(r => setTimeout(r, 50));
      return { ws, events, state };
    },
    sendChunks(ws, chunks, paceMs = 100) {
      return new Promise(async (resolve) => {
        for (const c of chunks) {
          if (ws.readyState !== 1) break;
          ws.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: c }));
          await new Promise(r => setTimeout(r, paceMs));
        }
        resolve();
      });
    },
    sendSilence(ws, silence, ms, paceMs = 100) {
      const n = Math.max(1, Math.round(ms / 200));
      return window.__h.sendChunks(ws, Array(n).fill(silence), paceMs);
    },
    idle(ms) { return new Promise(r => setTimeout(r, ms)); },
    waitFor(events, pred, to = 30000, label = '?') {
      return new Promise((resolve, reject) => {
        for (let i = 0; i < events.length; i++) if (pred(events[i])) return resolve(events[i]);
        let from = events.length;
        const iv = setInterval(() => {
          while (from < events.length) {
            if (pred(events[from])) { clearInterval(iv); clearTimeout(t); return resolve(events[from]); }
            from++;
          }
        }, 50);
        const t = setTimeout(() => { clearInterval(iv); reject(new Error('timeout waiting for ' + label)); }, to);
      });
    },
  };
`;

async function runPage(flow, args) {
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, args: CHROMIUM_ARGS });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  page.on('console', m => console.log(`  [browser:${m.type()}] ${m.text()}`));
  await page.goto('about:blank');
  try {
    await page.addScriptTag({ content: HARNESS_JS });
    return await page.evaluate(flow, args);
  } finally {
    await browser.close();
  }
}

async function runAll(scenarioIds, runOne, outFile) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const picked = process.argv[2];
  const want = picked ? scenarioIds.filter(id => id === picked) : scenarioIds;
  if (picked && !want.length) {
    console.error(`unknown scenario: ${picked}`);
    console.error(`available: ${scenarioIds.join(', ')}`);
    process.exit(2);
  }
  const sessions = {};
  for (const id of want) {
    try { sessions[id] = await runOne(id); }
    catch (e) { console.error(`scenario ${id} crashed:`, e.message); sessions[id] = null; }
  }
  console.log('\n=== SESSION IDS ===');
  console.log(JSON.stringify(sessions, null, 2));
  const p = path.join(OUT_DIR, outFile);
  fs.writeFileSync(p, JSON.stringify(sessions, null, 2));
  console.log(`Wrote ${p}`);
  return sessions;
}

module.exports = {
  CHROMIUM_PATH, BASE_URL, OUT_DIR, AUDIO_DIR, CHROMIUM_ARGS,
  pcmToB64Chunks, SILENCE_200MS, HARNESS_JS, runPage, runAll,
};
