// swarmlite/js: open a SQLite database published on Swarm, lazily.
//
//   import { open } from './src/index.js';
//   const db = await open('http://localhost:1633/bzz/<root>/site.db');
//   const rows = await db.query('SELECT title FROM posts LIMIT 5');
//   console.log(db.stats());   // pages/bytes fetched vs. file size
//
// Fully self-contained: wa-sqlite is vendored, everything loads from
// relative URLs, so the whole reader can itself be published on Swarm.

import SQLiteAsyncESMFactory from '../vendor/wa-sqlite/dist/wa-sqlite-async.mjs';
import * as SQLite from '../vendor/wa-sqlite/src/sqlite-api.js';
import { SwarmVFS } from './SwarmVFS.js';
import { NodeStore, lookupPath } from './mantaray.js';
import { VerifiedChunkStore, verifiedRead, verifiedSize } from './verify.js';

export { SwarmVFS, PAGE_SIZE } from './SwarmVFS.js';
export { resolveFeed, topicHex } from './feeds.js';
export { VerificationError } from './verify.js';

let modulePromise = null;
let aliasCounter = 0;

// wa-sqlite's Asyncify build can suspend only one call at a time, and
// every open() shares the one module: a query that starts while another
// waits on a page fetch reads garbage ("database disk image is
// malformed") or traps the wasm. So every call into SQLite queues here,
// which is what a search box firing on each keystroke needs.
let sqliteQueue = Promise.resolve();
function serialized(fn) {
  const run = sqliteQueue.then(fn);
  sqliteQueue = run.catch(() => {});
  return run;
}

/** Freedom Browser injects window.swarm: the only route a page has to
 * the node (its local HTTP API is blocked to web content). */
const freedomProvider = () =>
  globalThis.swarm?.isFreedomBrowser === true ? globalThis.swarm : null;

/** Open a read-only lazy connection to a database URL (gateway URL with
 * Range support). Returns { query, stats, close, transport, sqlite3, db, vfs }.
 *
 * With `verify: true` every byte is checked against the 32-byte root in
 * the URL: the manifest is resolved client-side and pages are read
 * chunk-by-chunk via /chunks, each BMT-verified — safe against a
 * tampering gateway, at the cost of one request per 4 KB chunk (a local
 * light node verifies natively, so the default fast path is fine
 * there). Requires a '<api>/bzz/<64-hex root>/<path>' URL.
 *
 * In Freedom Browser (`window.swarm` present) chunks are read the same
 * verified way but through `swarm.readChunk` instead of HTTP, so the
 * URL may also be 'bzz://<64-hex root>/<path>' — what a page's own
 * relative URLs resolve to there. Pass another window.swarm-style
 * provider as `swarm`, or `swarm: null` to use HTTP anyway.
 * `transport` on the result says which path was taken.
 *
 * query() and close() calls queue and run one at a time, across all open
 * databases; the raw `sqlite3`/`db` handles bypass that queue. */
export async function open(url, {
  cachePages, readahead, moduleOptions, verify = false, fetchFn, swarm = freedomProvider(),
} = {}) {
  modulePromise ??= SQLiteAsyncESMFactory(moduleOptions);
  const module = await modulePromise;
  const sqlite3 = SQLite.Factory(module);

  // register a unique VFS name per open (each VFS instance carries its
  // own cache/stats), and open via a short alias — SQLite VFS filename
  // buffers can be as small as 64 bytes, too short for gateway URLs
  const vfs = new SwarmVFS(`swarmlite-${++aliasCounter}`, module, { cachePages, readahead, fetchFn });
  const alias = `db-${aliasCounter}`;
  let chunkStore = null;
  if (verify || swarm) {
    const m = /^(?:(.+?)\/bzz\/|bzz:\/\/)([0-9a-fA-F]{64})\/(.+)$/.exec(url);
    if (!m || (!swarm && !m[1])) {
      throw new Error(
        `${swarm ? 'reading through window.swarm' : 'verify: true'} needs a ` +
        `'<api>/bzz/<64-hex root>/<path>' URL` +
        `${swarm ? " or 'bzz://<64-hex root>/<path>'" : ''} to know what to ` +
        `verify against, got ${url}`,
      );
    }
    const [, api, root, path] = m;
    chunkStore = new VerifiedChunkStore(swarm ?? api, { fetchFn });
    const nodes = new NodeStore((ref) => verifiedRead(chunkStore, ref));
    const entry = await lookupPath(nodes, root.toLowerCase(), path);
    if (!entry) {
      throw new Error(`verify: '${path}' is not a file in manifest ${root}`);
    }
    const size = await verifiedSize(chunkStore, entry.reference);
    vfs.registerSource(alias, {
      size,
      read: (from, toInclusive) =>
        verifiedRead(chunkStore, entry.reference, from, toInclusive + 1),
    });
  } else {
    vfs.registerUrl(alias, url);
  }
  const db = await serialized(() => {
    sqlite3.vfs_register(vfs, false);
    return sqlite3.open_v2(alias, SQLite.SQLITE_OPEN_READONLY, vfs.name);
  });

  /** Run SQL; returns [{col: value, ...}, ...]. */
  function query(sql, bindings) {
    return serialized(async () => {
      const rows = [];
      for await (const stmt of sqlite3.statements(db, sql)) {
        if (bindings) sqlite3.bind_collection(stmt, bindings);
        const cols = sqlite3.column_names(stmt);
        while ((await sqlite3.step(stmt)) === SQLite.SQLITE_ROW) {
          const row = {};
          cols.forEach((c, i) => { row[c] = sqlite3.column(stmt, i); });
          rows.push(row);
        }
      }
      return rows;
    });
  }

  return {
    query,
    transport: swarm ? 'window.swarm' : verify ? 'verified /chunks' : 'range',
    // verified mode adds chunk-level counters (network = chunk bytes,
    // including intermediate tree/manifest chunks)
    stats: () => ({ ...vfs.statsFor(alias), ...(chunkStore?.stats ?? {}) }),
    close: () => serialized(() => sqlite3.close(db)),
    sqlite3,
    db,
    vfs,
  };
}
