const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const { CHROMIUM_PATH, CHROMIUM_ARGS } = require('./lib');
const INSPECT_URL = process.env.INSPECT_URL || 'http://127.0.0.1:1327';
const SESSIONS = process.env.SESSIONS || '/tmp/realtime_scenarios/sessions_extra.json';
const OUT_DIR = process.env.OUT_DIR || '/tmp/realtime_scenarios/shots';

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const sessions = JSON.parse(fs.readFileSync(SESSIONS, 'utf8'));
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, args: CHROMIUM_ARGS });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  page.on('console', msg => { if (msg.type() === 'error') console.log(`  [page:error] ${msg.text()}`); });

  // Block the live-sessions poll so it can't hijack our session after the
  // history-stream finishes and closes the WS.
  await page.route('**/v1/inspect/sessions', route => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));

  for (const [name, sid] of Object.entries(sessions)) {
    if (!sid) { console.log(`skip ${name} (no sid)`); continue; }
    const url = `${INSPECT_URL}/v1/inspect/?sid=${sid}`;
    console.log(`\n>>> ${name} ${sid}\n    ${url}`);
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForFunction(
        (targetSid) => {
          const el = document.querySelector('#sessionIdText');
          return el && el.textContent && el.textContent.trim().startsWith(targetSid.slice(0, 10));
        },
        sid, { timeout: 20000 },
      ).catch((e) => console.log(`    header match timeout: ${e.message}`));
      await page.waitForFunction(
        () => {
          const el = document.querySelector('#stEvents');
          return el && parseInt(el.textContent, 10) > 50;
        },
        { timeout: 30000 },
      ).catch((e) => console.log(`    events-count timeout: ${e.message}`));
      await page.waitForTimeout(1500);
      await page.click('#btnPause').catch(() => {});
      const wrap = await page.$('#tlWrap');
      if (wrap) {
        const box = await wrap.boundingBox();
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        for (let i = 0; i < 4; i++) {
          await page.mouse.wheel(0, 500);
          await page.waitForTimeout(30);
        }
      }
      await page.waitForTimeout(400);
      const shotPath = path.join(OUT_DIR, `${name}.png`);
      await page.screenshot({ path: shotPath, fullPage: true });
      console.log(`    saved ${shotPath}`);
    } catch (e) {
      console.log(`    error: ${e.message}`);
    }
  }
  await browser.close();
})();
