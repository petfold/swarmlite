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
    `of a ${fmtMB(s.fileSize)} database · ${ms} ms`,
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

let timer;
$('search').addEventListener('input', (e) => {
  clearTimeout(timer);
  const q = e.target.value.trim();
  timer = setTimeout(() => (q ? search(q) : latest()), 250);
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
    `(this page needs to be served by a gateway with HTTP Range support)`;
  throw e;
}
