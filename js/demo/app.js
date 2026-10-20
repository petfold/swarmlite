// swarmlite demo: lazy SQL against a database published next to this page.
// Everything (page, reader, wasm, database) lives under one immutable
// Swarm root; the database URL is simply relative to the page.

import { open } from './src/index.js';

const $ = (id) => document.getElementById(id);
const fmtKB = (b) => `${(b / 1024).toFixed(0)} KB`;
const fmtMB = (b) => `${(b / 2 ** 20).toFixed(1)} MB`;

let db;
let before = null;

function status(msg) { $('status').innerHTML = msg; }

function reportDelta(label, t0) {
  const s = db.stats();
  const pages = s.pagesFetched - (before?.pagesFetched ?? 0);
  const bytes = s.bytesFetched - (before?.bytesFetched ?? 0);
  const ms = (performance.now() - t0).toFixed(0);
  status(
    `${label}: fetched <b>${pages} pages</b> (${fmtKB(bytes)}) ` +
    `of a ${fmtMB(s.fileSize)} database · ${ms} ms` +
    (db.transport === 'window.swarm' ? ' · via window.swarm' : ''),
  );
  before = s;
}

function render(rows) {
  $('results').innerHTML = rows.map((r) => `
    <div class="post">
      <div class="t">${escapeHtml(r.title)}</div>
      <div class="m">${r.author} · ${new Date(r.ts * 1000).toISOString().slice(0, 10)}</div>
    </div>`).join('') || '<p class="hint">no results</p>';
}

const escapeHtml = (s) =>
  s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

async function latest() {
  const t0 = performance.now();
  const rows = await db.query(
    'SELECT title, author, ts FROM posts ORDER BY ts DESC LIMIT 10');
  render(rows);
  reportDelta('latest 10 posts', t0);
}

async function search(word) {
  const t0 = performance.now();
  const rows = await db.query(
    `SELECT p.title, p.author, p.ts
       FROM kw JOIN posts p ON p.id = kw.id
      WHERE kw.word = ?
      ORDER BY kw.ts DESC LIMIT 10`,
    [word.toLowerCase()],
  );
  render(rows);
  reportDelta(`search “${escapeHtml(word)}”`, t0);
}

// One query at a time, newest input wins: through window.swarm a search
// takes seconds, and terms typed past while it runs aren't worth their
// reads (Freedom budgets them per origin). Failures show, not vanish.
let timer;
let busy = false;
let next = null; // newest term still to run; '' = latest posts

async function drain() {
  busy = true;
  while (next !== null) {
    const q = next;
    next = null;
    try {
      await (q ? search(q) : latest());
      $('err').textContent = '';
    } catch (e) {
      $('err').textContent = `query failed: ${e?.message ?? e}`;
    }
  }
  busy = false;
}

$('search').addEventListener('input', (e) => {
  clearTimeout(timer);
  const q = e.target.value.trim();
  timer = setTimeout(() => {
    next = q;
    if (!busy) drain();
  }, 250);
});

try {
  const url = new URL('demo.db', location.href).href;
  db = await open(url);
  const s = db.stats();
  $('dbsize').textContent = fmtMB(s.fileSize);
  before = s;
  await latest();
} catch (e) {
  $('err').textContent =
    `failed to open the database: ${e}\n` +
    (globalThis.swarm?.isFreedomBrowser
      ? '(read through window.swarm: is the Swarm node running?)'
      : '(this page needs to be served by a gateway with HTTP Range support)');
  throw e;
}
