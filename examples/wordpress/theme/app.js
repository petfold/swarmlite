// A WordPress blog as one queryable file: the list, the search, and
// every post page are SELECTs against blog.db, published next to this
// page under the same immutable Swarm root. Hash routing: #/ is the
// index, #/post/<slug> is a post.

import { open } from './src/index.js';

const $ = (id) => document.getElementById(id);
const fmtKB = (b) => `${(b / 1024).toFixed(0)} KB`;
const esc = (s) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const when = (ts) => new Date(ts * 1000).toISOString().slice(0, 10);

let db;
let before = null;

function report(label) {
  const s = db.stats();
  const pages = s.pagesFetched - (before?.pagesFetched ?? 0);
  const bytes = s.bytesFetched - (before?.bytesFetched ?? 0);
  $('status').innerHTML =
    `${label}: fetched <b>${pages} pages</b> (${fmtKB(bytes)}) of a ` +
    `${(s.fileSize / 2 ** 20).toFixed(1)} MB blog`;
  before = s;
}

function renderList(rows, label) {
  $('main').innerHTML = rows.map((r) => `
    <div class="item">
      <a href="#/post/${encodeURIComponent(r.slug)}">${esc(r.title)}</a>
      <div class="m">${esc(r.author)} · ${when(r.ts)} · ${esc(r.tags)}</div>
    </div>`).join('') || '<p class="m">no posts found</p>';
  report(label);
}

async function showIndex(query) {
  if (query) {
    const rows = await db.query(
      `SELECT p.slug, p.title, p.author, p.ts, p.tags
         FROM kw JOIN posts p ON p.id = kw.id
        WHERE kw.word = ? ORDER BY kw.ts DESC LIMIT 20`,
      [query.toLowerCase()]);
    renderList(rows, `search “${esc(query)}”`);
  } else {
    const rows = await db.query(
      'SELECT slug, title, author, ts, tags FROM posts ORDER BY ts DESC LIMIT 20');
    renderList(rows, 'latest posts');
  }
}

async function showPost(slug) {
  const rows = await db.query(
    'SELECT title, author, ts, html FROM posts WHERE slug = ?', [slug]);
  if (!rows.length) { $('main').innerHTML = '<p>post not found</p>'; return; }
  const p = rows[0];
  // the html is the publisher's own exported content
  $('main').innerHTML = `
    <p class="back"><a href="#/">&larr; all posts</a></p>
    <article>
      <div class="m">${esc(p.author)} · ${when(p.ts)}</div>
      ${p.html}
    </article>`;
  report(`post “${esc(p.title)}”`);
  window.scrollTo(0, 0);
}

function route() {
  const h = decodeURIComponent(location.hash);
  const m = /^#\/post\/(.+)$/.exec(h);
  if (m) showPost(m[1]);
  else showIndex($('search').value.trim());
}

let timer;
$('search').addEventListener('input', () => {
  clearTimeout(timer);
  timer = setTimeout(() => { location.hash = '#/'; route(); }, 250);
});
window.addEventListener('hashchange', route);

try {
  db = await open(new URL('blog.db', location.href).href);
  const [{ value: title }] = await db.query("SELECT value FROM meta WHERE key='title'");
  document.title = title;
  $('blogtitle').textContent = title;
  before = db.stats();
  route();
} catch (e) {
  $('status').textContent = `failed to open the blog database: ${e}`;
  throw e;
}
