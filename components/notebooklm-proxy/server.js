/**
 * server.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Express + Puppeteer backend.  Keeps a persistent headless Chrome session
 * open to NotebookLM and exposes a /ask endpoint for the frontend.
 *
 * Keep alive with:  pm2 start ecosystem.config.js
 *
 * Environment variables (from .env or pm2 env):
 *   NOTEBOOK_URL    Full URL to your NotebookLM notebook  [REQUIRED]
 *   PORT            HTTP port                              [default: 3001]
 *   ALLOWED_ORIGIN  CORS whitelist                        [default: *]
 * ─────────────────────────────────────────────────────────────────────────────
 */

require('dotenv').config();
const express   = require('express');
const cors      = require('cors');
const helmet    = require('helmet');
const rateLimit = require('express-rate-limit');
const puppeteer = require('puppeteer');
const path      = require('path');

// ── Config ────────────────────────────────────────────────────────────────────
const PORT           = process.env.PORT           || 3001;
const NOTEBOOK_URL   = process.env.NOTEBOOK_URL;
const ALLOWED_ORIGIN = process.env.ALLOWED_ORIGIN || '*';
const SESSION_DIR    = path.join(__dirname, 'session-data');

if (!NOTEBOOK_URL) {
  console.error('❌  NOTEBOOK_URL is not set. Add it to your .env file.');
  process.exit(1);
}

// ── Selectors (update if Google changes their UI) ─────────────────────────────
const SEL = {
  input:    'textarea[placeholder*="Ask"], textarea[aria-label*="Ask"], textarea[aria-label*="query"]',
  send:     'button[aria-label*="Send"], button[aria-label*="send"], button[jsname="c6xFrd"]',
  response: '.response-text, .model-response-text, message-content, .chat-turn-message',
  loading:  '.loading-indicator, .progress-spinner, mat-progress-spinner, .thinking-indicator',
};

// ── Puppeteer state ───────────────────────────────────────────────────────────
let browser = null;
let page    = null;
let ready   = false;
const queue = [];          // serialise concurrent requests
let processing = false;

async function initBrowser() {
  console.log('🚀  Launching headless Chrome …');
  browser = await puppeteer.launch({
    headless: 'new',
    userDataDir: SESSION_DIR,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  });

  page = await browser.newPage();
  await page.setUserAgent(
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  );

  console.log(`🔗  Navigating to: ${NOTEBOOK_URL}`);
  await page.goto(NOTEBOOK_URL, { waitUntil: 'networkidle2', timeout: 60_000 });

  // Check if we landed on a Google login page (session expired)
  const isLogin = await page.$('input[type="email"]');
  if (isLogin) {
    console.error('❌  Not logged in — session missing or expired.');
    console.error('    Run: node loginOnce.js   then restart the server.');
    process.exit(1);
  }

  // Wait for the chat input to appear
  try {
    await page.waitForSelector(SEL.input, { timeout: 20_000 });
  } catch {
    console.warn('⚠️   Chat input not found — selectors may need updating.');
    console.warn('    Open DevTools on NotebookLM and check SEL.input in server.js');
  }

  ready = true;
  console.log('✅  NotebookLM session ready — server accepting requests.\n');
}

async function askNotebookLM(question) {
  // Focus the input
  await page.waitForSelector(SEL.input, { timeout: 10_000 });
  await page.click(SEL.input);

  // Clear any existing text
  await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (el) { el.value = ''; el.dispatchEvent(new Event('input', { bubbles: true })); }
  }, SEL.input);

  // Type the question
  await page.type(SEL.input, question, { delay: 25 });

  // Count existing response blocks before submitting
  const beforeCount = await page.$$eval(SEL.response, (els) => els.length).catch(() => 0);

  // Submit — try Enter key, fall back to clicking the send button
  await page.keyboard.press('Enter');

  // Wait for a NEW response block to appear
  try {
    await page.waitForFunction(
      ({ sel, count }) => document.querySelectorAll(sel).length > count,
      { timeout: 60_000 },
      { sel: SEL.response, count: beforeCount }
    );
  } catch {
    throw new Error('Timed out waiting for a new response from NotebookLM');
  }

  // Wait for the loading spinner to disappear (generation finished)
  try {
    await page.waitForFunction(
      (sel) => !document.querySelector(sel),
      { timeout: 90_000 },
      SEL.loading
    );
  } catch {
    // spinner selector may not match — continue anyway
  }

  // Extra safety buffer for final text render
  await new Promise((r) => setTimeout(r, 800));

  // Grab the last (newest) response block text
  const allTexts = await page.$$eval(SEL.response, (els) =>
    els.map((el) => el.innerText.trim()).filter(Boolean)
  );

  if (!allTexts.length) {
    throw new Error('Could not extract response text — selector may need updating');
  }

  return allTexts[allTexts.length - 1];
}

// Serialise requests so we never type two questions at once
function enqueue(question) {
  return new Promise((resolve, reject) => {
    queue.push({ question, resolve, reject });
    if (!processing) processQueue();
  });
}

async function processQueue() {
  if (!queue.length) { processing = false; return; }
  processing = true;
  const { question, resolve, reject } = queue.shift();
  try {
    const answer = await askNotebookLM(question);
    resolve(answer);
  } catch (err) {
    reject(err);
  }
  processQueue();
}

// ── Express app ───────────────────────────────────────────────────────────────
const app = express();

app.use(helmet({ contentSecurityPolicy: false }));
app.use(cors({ origin: ALLOWED_ORIGIN }));
app.use(express.json());

// Serve the frontend from the same process (optional)
app.use(express.static(path.join(__dirname, 'public')));

// Rate limiter — 30 requests per minute per IP
app.use('/ask', rateLimit({
  windowMs: 60_000,
  max: 30,
  message: { error: 'Too many requests — slow down.' },
}));

// ── Health check ──────────────────────────────────────────────────────────────
app.get('/health', (_req, res) => {
  res.json({ status: ready ? 'ready' : 'initialising', timestamp: new Date().toISOString() });
});

// ── Main endpoint ─────────────────────────────────────────────────────────────
app.post('/ask', async (req, res) => {
  const { question } = req.body || {};

  if (!question || typeof question !== 'string' || !question.trim()) {
    return res.status(400).json({ error: 'Missing or empty question field.' });
  }

  if (!ready) {
    return res.status(503).json({ error: 'Server is still initialising — try again shortly.' });
  }

  try {
    const answer = await enqueue(question.trim());
    res.json({ answer });
  } catch (err) {
    console.error('Error answering question:', err.message);
    res.status(500).json({ error: 'Failed to get answer.', detail: err.message });
  }
});

// ── Boot ──────────────────────────────────────────────────────────────────────
initBrowser()
  .then(() => {
    app.listen(PORT, () => {
      console.log(`🌐  API listening on http://localhost:${PORT}`);
      console.log(`    POST /ask   { "question": "..." }`);
      console.log(`    GET  /health\n`);
    });
  })
  .catch((err) => {
    console.error('Fatal error during startup:', err);
    process.exit(1);
  });

// ── Graceful shutdown ─────────────────────────────────────────────────────────
async function shutdown(signal) {
  console.log(`\n${signal} received — shutting down …`);
  if (browser) await browser.close().catch(() => {});
  process.exit(0);
}
process.on('SIGINT',  () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
