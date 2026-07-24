// Node smoke test for the JS lazy VFS, against a real gateway URL.
//
//   node js/test/smoke.mjs http://localhost:1633/bzz/<root>/demo.db
//
// Expects the demo database from examples/offline_demo.py (published with
// `swarmlite publish`). Asserts correctness AND the read budget — the
// same claims the Python suite makes.

import { readFile } from 'node:fs/promises';
import { open } from '../src/index.js';

const url = process.argv[2];
if (!url) {
  console.error('usage: node js/test/smoke.mjs <gateway-url-of-demo.db>');
  process.exit(2);
}

function assert(cond, msg, extra) {
  if (!cond) {
    console.error(`FAIL: ${msg}`, extra ?? '');
    process.exit(1);
  }
  console.log(`ok: ${msg}`);
}

const wasmBinary = await readFile(
  new URL('../vendor/wa-sqlite/dist/wa-sqlite-async.wasm', import.meta.url),
);
const db = await open(url, { moduleOptions: { wasmBinary } });

// cold point lookup (id 12345 exists in every demo DB size we publish)
let rows = await db.query('SELECT title FROM posts WHERE id = 12345');
assert(rows.length === 1 && /^Post 12345: on /.test(rows[0].title),
  'point lookup returns the right row', rows);
const cold = db.stats();
assert(cold.readCount <= 6,
  `cold lookup within budget (${cold.readCount} reads, ${cold.bytesFetched} B ` +
  `of a ${(cold.fileSize / 2 ** 20).toFixed(1)} MB file)`, cold);

// warm repeat: zero new fetches
rows = await db.query('SELECT title FROM posts WHERE id = 12345');
const warm = db.stats();
assert(warm.readCount === cold.readCount, 'warm repeat fetches nothing');

// indexed top-N
rows = await db.query('SELECT title FROM posts ORDER BY ts DESC LIMIT 5');
assert(rows.length === 5, 'indexed top-N returns 5 rows');

// keyword search over the inverted-index table (the vendored wa-sqlite
// build has no FTS5; kw point lookups are the lazy-friendly equivalent)
rows = await db.query(
  "SELECT p.title FROM kw JOIN posts p ON p.id = kw.id " +
  "WHERE kw.word = 'verifiable' ORDER BY kw.ts DESC LIMIT 10");
assert(rows.length === 10, 'keyword search returns 10 hits');

const final = db.stats();
console.log(
  `total: ${final.pagesFetched} pages (${(final.bytesFetched / 1024).toFixed(0)} KB) ` +
  `in ${final.readCount} reads, of a ${(final.fileSize / 2 ** 20).toFixed(1)} MB file`);
assert(final.bytesFetched < final.fileSize / 100,
  'network transfer proportional to pages touched, not DB size');

await db.close();
console.log('smoke test passed');
