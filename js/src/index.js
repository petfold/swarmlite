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

export { SwarmVFS, PAGE_SIZE } from './SwarmVFS.js';
export { resolveFeed, topicHex } from './feeds.js';

let modulePromise = null;
let aliasCounter = 0;

/** Open a read-only lazy connection to a database URL (gateway URL with
 * Range support). Returns { query, stats, close, sqlite3, db, vfs }. */
export async function open(url, { cachePages, moduleOptions } = {}) {
  modulePromise ??= SQLiteAsyncESMFactory(moduleOptions);
  const module = await modulePromise;
  const sqlite3 = SQLite.Factory(module);

  const vfs = new SwarmVFS({ cachePages });
  // register a unique VFS name per open (each VFS instance carries its
  // own cache/stats), and open via a short alias — SQLite VFS filename
  // buffers can be as small as 64 bytes, too short for gateway URLs
  vfs.name = `swarmlite-${++aliasCounter}`;
  const alias = `db-${aliasCounter}`;
  vfs.registerUrl(alias, url);
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
    stats: () => vfs.statsFor(url),
    close: () => sqlite3.close(db),
    sqlite3,
    db,
    vfs,
  };
}
