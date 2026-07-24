// The publisher's checklist, in JS: mirror of swarmlite/publish.py
// prepare() — PRAGMA page_size=4096, ANALYZE, VACUUM, integrity_check,
// and the same missing-index warnings (PRAGMA table_list: shadow tables
// skipped, WITHOUT ROWID counts as indexed). Runs entirely in memory on
// wa-sqlite via MemVFS; the input bytes are never modified.
//
// One divergence from the Python publisher: WAL-mode inputs are
// REJECTED rather than converted. Switching out of WAL requires
// opening the database, and WAL needs shared-memory VFS support that
// the in-memory VFS (deliberately) does not have. The fix is a
// one-liner for the caller, stated in the error.

import SQLiteAsyncESMFactory from '../vendor/wa-sqlite/dist/wa-sqlite-async.mjs';
import * as SQLite from '../vendor/wa-sqlite/src/sqlite-api.js';
import { MemVFS } from './memvfs.js';

export const PAGE_SIZE = 4096; // one SQLite page = one Swarm chunk
const LARGE_TABLE_ROWS = 5000;

export class PrepareError extends Error {
  name = 'PrepareError';
}

let modulePromise = null;
let vfsCounter = 0;

/** Produce a publish-ready copy of a SQLite database.
 * @param {Uint8Array} bytes - the database file's contents
 * @returns {Promise<{bytes: Uint8Array, warnings: string[]}>} */
export async function prepare(bytes, { moduleOptions } = {}) {
  if (bytes.byteLength < 100 ||
      new TextDecoder().decode(bytes.subarray(0, 15)) !== 'SQLite format 3') {
    throw new PrepareError('not a SQLite database (bad header magic)');
  }
  if (bytes[18] === 2 || bytes[19] === 2) {
    throw new PrepareError(
      'database is in WAL mode, which cannot ship to Swarm (WAL needs ' +
      'sidecar files). Switch it locally first — ' +
      `sqlite3 your.db "PRAGMA journal_mode=DELETE" — and re-run`,
    );
  }

  modulePromise ??= SQLiteAsyncESMFactory(moduleOptions);
  const sqlite3 = SQLite.Factory(await modulePromise);
  const vfs = new MemVFS();
  vfs.name = `swarmlite-mem-${++vfsCounter}`;
  vfs.put('work.db', bytes);
  sqlite3.vfs_register(vfs, false);
  const db = await sqlite3.open_v2(
    'work.db', SQLite.SQLITE_OPEN_READWRITE | SQLite.SQLITE_OPEN_CREATE, vfs.name);

  async function rows(sql) {
    const out = [];
    for await (const stmt of sqlite3.statements(db, sql)) {
      while ((await sqlite3.step(stmt)) === SQLite.SQLITE_ROW) {
        out.push(sqlite3.row(stmt));
      }
    }
    return out;
  }

  const warnings = [];
  try {
    const [[pageSize]] = await rows('PRAGMA page_size');
    if (pageSize !== PAGE_SIZE) {
      await rows(`PRAGMA page_size=${PAGE_SIZE}`);
      warnings.push(
        `page_size was ${pageSize}; rewriting to ${PAGE_SIZE} ` +
        '(one page = one Swarm chunk)',
      );
    }

    // ordinary tables only: 'shadow' skips virtual-table internals, and
    // wr=1 (WITHOUT ROWID) means the PRIMARY KEY is the table's B-tree
    for (const [schema, table, type, _ncol, wr] of await rows('PRAGMA table_list')) {
      if (schema !== 'main' || type !== 'table' || wr) continue;
      if (table.startsWith('sqlite_')) continue;
      const [[n]] = await rows(`SELECT count(*) FROM "${table}"`);
      const [[idx]] = await rows(
        `SELECT count(*) FROM sqlite_master WHERE type='index' ` +
        `AND tbl_name='${table.replaceAll("'", "''")}'`);
      if (n >= LARGE_TABLE_ROWS && idx === 0) {
        warnings.push(
          `table '${table}' has ${n} rows and no index — remote queries ` +
          'filtering on non-rowid columns will scan the whole file ' +
          '(INTEGER PRIMARY KEY lookups are still fine)',
        );
      }
    }

    await rows('ANALYZE');
    await rows('VACUUM'); // applies the page size, defragments

    const check = (await rows('PRAGMA integrity_check')).map((r) => r[0]);
    if (check.length !== 1 || check[0] !== 'ok') {
      throw new PrepareError(
        `integrity_check failed: ${check.slice(0, 5).join('; ')}`);
    }
  } finally {
    await sqlite3.close(db);
  }

  return { bytes: vfs.get('work.db'), warnings };
}
