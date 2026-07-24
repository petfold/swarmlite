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

/** Open a read-only lazy connection to a database URL (gateway URL with
 * Range support). Returns { query, stats, close, sqlite3, db, vfs }.
 *
 * With `verify: true` every byte is checked against the 32-byte root in
 * the URL: the manifest is resolved client-side and pages are read
 * chunk-by-chunk via /chunks, each BMT-verified — safe against a
 * tampering gateway, at the cost of one request per 4 KB chunk (a local
 * light node verifies natively, so the default fast path is fine
 * there). Requires a '<api>/bzz/<64-hex root>/<path>' URL. */
export async function open(url, { cachePages, moduleOptions, verify = false, fetchFn } = {}) {
  modulePromise ??= SQLiteAsyncESMFactory(moduleOptions);
  const module = await modulePromise;
  const sqlite3 = SQLite.Factory(module);

  const vfs = new SwarmVFS({ cachePages, fetchFn });
  // register a unique VFS name per open (each VFS instance carries its
  // own cache/stats), and open via a short alias — SQLite VFS filename
  // buffers can be as small as 64 bytes, too short for gateway URLs
  vfs.name = `swarmlite-${++aliasCounter}`;
  const alias = `db-${aliasCounter}`;
  let chunkStore = null;
  if (verify) {
    const m = /^(.+?)\/bzz\/([0-9a-fA-F]{64})\/(.+)$/.exec(url);
    if (!m) {
      throw new Error(
        `verify: true needs a '<api>/bzz/<64-hex root>/<path>' URL to ` +
        `know what to verify against, got ${url}`,
      );
    }
    const [, api, root, path] = m;
    chunkStore = new VerifiedChunkStore(api, { fetchFn });
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
  sqlite3.vfs_register(vfs, false);
  const db = await sqlite3.open_v2(alias, SQLite.SQLITE_OPEN_READONLY, vfs.name);

  /** Run SQL; returns [{col: value, ...}, ...]. */
  async function query(sql, bindings) {
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
  }

  return {
    query,
    // verified mode adds chunk-level counters (network = chunk bytes,
    // including intermediate tree/manifest chunks)
    stats: () => ({ ...vfs.statsFor(alias), ...(chunkStore?.stats ?? {}) }),
    close: () => sqlite3.close(db),
    sqlite3,
    db,
    vfs,
  };
}
