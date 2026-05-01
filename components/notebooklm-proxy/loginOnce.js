/**
 * loginOnce.js
 * Run this ONCE on a machine with a display (local / VNC / RDP).
 * Logs in to Google interactively and saves the session to ./session-data/
 * so server.js can reuse it headlessly forever after.
 *
 * Usage:  node loginOnce.js
 */
require('dotenv').config();
const puppeteer = require('puppeteer');
const path = require('path');

const SESSION_DIR  = path.join(__dirname, 'session-data');
const NOTEBOOK_URL = process.env.NOTEBOOK_URL || 'https://notebooklm.google.com';

(async () => {
  console.log('\n╔═══════════════════════════════════════════════════╗');
  console.log(  '║   NotebookLM Proxy — One-Time Login Setup         ║');
  console.log(  '╚═══════════════════════════════════════════════════╝\n');
  console.log('Steps:');
  console.log('  1. A Chrome window will open.');
  console.log('  2. Sign in to Google in that window.');
  console.log('  3. Navigate to your NotebookLM notebook.');
  console.log('  4. Once the chat UI is visible, press Ctrl+C here.\n');
  console.log('Session dir :', SESSION_DIR);
  console.log('Notebook URL:', NOTEBOOK_URL, '\n');

  let browser;
  try {
    browser = await puppeteer.launch({
      headless: false,
      userDataDir: SESSION_DIR,
      defaultViewport: null,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--start-maximized'],
    });
  } catch (err) {
    console.error('ERROR: Could not launch browser:', err.message);
    console.error('Make sure a display is available (run locally or via VNC).');
    process.exit(1);
  }

  const page = await browser.newPage();
  await page.goto(NOTEBOOK_URL, { waitUntil: 'networkidle2', timeout: 60000 });
  console.log('✅  Browser open — complete login, then press Ctrl+C.');

  process.on('SIGINT', async () => {
    console.log('\n✅  Session saved to ./session-data/');
    console.log('    Start the server:  pm2 start ecosystem.config.js\n');
    await browser.close().catch(() => {});
    process.exit(0);
  });

  browser.on('disconnected', () => {
    console.log('\n⚠️   Browser closed. Session data may be saved — try node server.js');
    process.exit(0);
  });
})();
