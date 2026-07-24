// swarmlite browser VFS: lazy, read-only SQLite pages over Swarm gateway
// range reads. The JS twin of swarmlite/vfs.py — same design, same
// counters (docs/DESIGN.md section 4).
//
// Works with wa-sqlite's Asyncify build: VFS methods may return
// handleAsync(async () => ...), so page fetches are ordinary async
// fetch() calls — no SharedArrayBuffer, no COOP/COEP headers.
//
// The "filename" SQLite opens is a URL: anything a gateway serves with
// HTTP Range support, typically
//     http://localhost:1633/bzz/<root>/site.db        (pin, immutable)
//     https://gateway.example/bzz/<root>/site.db
// Feed URLs must be resolved to a root by the caller before opening
// (a feed can move mid-session; a database must not).
//
// Read-only by design: journal/WAL are reported absent, writes fail.
// Chunk-level BMT verification is a documented follow-up (roadmap):
// against a local light node, retrieved chunks are verified by the node.

import * as VFS from '../vendor/wa-sqlite/src/VFS.js';

export const PAGE_SIZE = 4096; // SQLite default page == Swarm chunk
const DEFAULT_CACHE_PAGES = 1024; // ~4 MiB
const MAX_RUN_PAGES = 16; // cap one Range request at 64 KiB — big ranges
                          // are the ones gateways abort on fresh content
const FETCH_TIMEOUT_MS = 30_000;

export class SwarmVFS extends VFS.Base {
  name = 'swarmlite';
  mxPathName = 1024; // filenames are URLs

  /** @type {Map<number, object>} sqlite3_file id -> open file state */
  #files = new Map();
  /** @type {Map<string, string>} short alias -> URL */
  #urls = new Map();
  /** @type {Map<string, object>} short alias -> {size, read} source */
  #sources = new Map();
  #cachePages;
  #fetch;

  constructor({ cachePages = DEFAULT_CACHE_PAGES, fetchFn } = {}) {
    super();
    this.#cachePages = Math.max(1, cachePages);
    this.#fetch = fetchFn ?? ((...args) => globalThis.fetch(...args));
  }

  /** Map a short alias to a database URL. SQLite opens the alias; the
   * VFS fetches the URL. Necessary because VFS filename length is
   * limited (mxPathName can be as small as 64 in some builds), and it
   * keeps URLs out of SQLite's filename handling entirely. */
  registerUrl(alias, url) {
    this.#urls.set(alias, url);
  }

  /** Map an alias to a custom page source instead of a URL:
   * `{ size, read(from, toInclusive) -> Promise<Uint8Array> }`. Used
   * for verified reads, where pages come from a chunk-tree walk rather
   * than gateway Range requests. */
  registerSource(alias, source) {
    this.#sources.set(alias, source);
  }

  xOpen(name, fileId, flags, pOutFlags) {
    return this.handleAsync(async () => {
      if (!(flags & VFS.SQLITE_OPEN_MAIN_DB)) {
        return VFS.SQLITE_CANTOPEN; // no journals, no temp files
      }
      const source = this.#sources.get(name);
      const url = this.#urls.get(name) ?? name;
      try {
        let size;
        if (source) {
          size = source.size;
        } else {
          // one 1-byte range read: existence check + total size
          const resp = await this.#fetch(url, {
            headers: { Range: 'bytes=0-0' },
          });
          size = contentRangeTotal(resp);
          if (size === null) {
            console.error(
              `swarmlite: ${url} -> HTTP ${resp.status}, ` +
              `Content-Range ${resp.headers.get('content-range')} — ` +
              `need a gateway URL with Range support`,
            );
            return VFS.SQLITE_CANTOPEN;
          }
        }
        this.#files.set(fileId, {
          alias: name,
          url,
          source,
          size,
          cache: new Map(), // page index -> Uint8Array (Map keeps LRU order)
          stats: { readCount: 0, pagesFetched: 0, bytesFetched: 0, fileSize: size },
        });
        pOutFlags.setInt32(0, VFS.SQLITE_OPEN_READONLY, true);
        return VFS.SQLITE_OK;
      } catch (e) {
        console.error(`swarmlite: open ${url} failed:`, e);
        return VFS.SQLITE_CANTOPEN;
      }
    });
  }

  xClose(fileId) {
    this.#files.delete(fileId);
    return VFS.SQLITE_OK;
  }

  xRead(fileId, pData, iOffset) {
    return this.handleAsync(async () => {
      const file = this.#files.get(fileId);
      if (!file) return VFS.SQLITE_IOERR;
      try {
        const wanted = Math.min(iOffset + pData.byteLength, file.size) - iOffset;
        if (wanted > 0) {
          const data = await this.#readRange(file, iOffset, wanted);
          pData.set(data, 0);
        }
        if (wanted < pData.byteLength) {
          // short read past EOF: zero-fill, per SQLite contract
          pData.fill(0, Math.max(wanted, 0));
          return VFS.SQLITE_IOERR_SHORT_READ;
        }
        return VFS.SQLITE_OK;
      } catch (e) {
        console.error(`swarmlite: read @${iOffset} failed:`, e);
        return VFS.SQLITE_IOERR;
      }
    });
  }

  xFileSize(fileId, pSize64) {
    const file = this.#files.get(fileId);
    if (!file) return VFS.SQLITE_IOERR;
    pSize64.setBigInt64(0, BigInt(file.size), true);
    return VFS.SQLITE_OK;
  }

  xAccess(name, flags, pResOut) {
    pResOut.setInt32(0, 0, true); // journals/WAL never exist
    return VFS.SQLITE_OK;
  }

  xSectorSize(fileId) {
    return PAGE_SIZE;
  }

  xDeviceCharacteristics(fileId) {
    return VFS.SQLITE_IOCAP_IMMUTABLE; // content-addressed: never changes
  }

  xWrite() {
    return VFS.SQLITE_READONLY;
  }

  xTruncate() {
    return VFS.SQLITE_READONLY;
  }

  xDelete() {
    return VFS.SQLITE_READONLY;
  }

  /** Read counters for an open database URL or alias (last opened wins). */
  statsFor(urlOrAlias) {
    for (const file of this.#files.values()) {
      if (file.url === urlOrAlias || file.alias === urlOrAlias) {
        return { ...file.stats };
      }
    }
    return null;
  }

  // ---- transport ----------------------------------------------------------

  /** Ranged fetch with retry: gateways occasionally close a range read
   * mid-stream on freshly uploaded content (seen live against Bee 2.8.1);
   * a short backoff and retry recovers. */
  async #fetchRange(url, from, to, attempts = 5) {
    let lastErr;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      try {
        const resp = await this.#fetch(url, {
          headers: { Range: `bytes=${from}-${to}` },
          signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status} for bytes=${from}-${to}`);
        const blob = new Uint8Array(await resp.arrayBuffer());
        if (blob.byteLength !== to - from + 1) {
          throw new Error(
            `truncated range read: got ${blob.byteLength} of ${to - from + 1}`);
        }
        return blob;
      } catch (e) {
        lastErr = e;
        if (attempt < attempts) {
          // seen live: freshly uploaded content can be unretrievable for
          // seconds while the node syncs — back off up to ~7.5 s total
          console.warn(
            `swarmlite: retry ${attempt} for bytes=${from}-${to} (${e})`);
          await new Promise((r) => setTimeout(r, 500 * 2 ** (attempt - 1)));
        }
      }
    }
    throw lastErr;
  }

  // ---- paging ------------------------------------------------------------

  /** Serve [offset, offset+length) from the page cache, fetching each
   * contiguous run of missing pages with a single Range request. */
  async #readRange(file, offset, length) {
    const first = Math.floor(offset / PAGE_SIZE);
    const last = Math.floor((offset + length - 1) / PAGE_SIZE);

    const pages = new Map();
    for (let i = first; i <= last; i++) {
      const hit = file.cache.get(i);
      if (hit !== undefined) {
        file.cache.delete(i); // refresh LRU position
        file.cache.set(i, hit);
        pages.set(i, hit);
      }
    }

    let i = first;
    while (i <= last) {
      if (pages.has(i)) { i++; continue; }
      let j = i;
      while (j <= last && !pages.has(j) && j - i < MAX_RUN_PAGES) j++;
      const from = i * PAGE_SIZE;
      const to = Math.min(j * PAGE_SIZE, file.size) - 1;
      const blob = file.source
        ? await file.source.read(from, to)
        : await this.#fetchRange(file.url, from, to);
      file.stats.readCount += 1;
      file.stats.bytesFetched += blob.byteLength;
      for (let k = i; k < j; k++) {
        const page = blob.subarray((k - i) * PAGE_SIZE, (k - i + 1) * PAGE_SIZE);
        pages.set(k, page);
        file.cache.set(k, page);
        file.stats.pagesFetched += 1;
      }
      i = j;
    }

    while (file.cache.size > this.#cachePages) {
      file.cache.delete(file.cache.keys().next().value); // evict LRU
    }

    const out = new Uint8Array(length);
    let outPos = 0;
    let cur = offset;
    while (outPos < length) {
      const k = Math.floor(cur / PAGE_SIZE);
      const page = pages.get(k);
      const pageStart = cur - k * PAGE_SIZE;
      const n = Math.min(PAGE_SIZE - pageStart, length - outPos);
      out.set(page.subarray(pageStart, pageStart + n), outPos);
      outPos += n;
      cur += n;
    }
    return out;
  }
}

/** Parse the total size out of a 206 response's Content-Range header. */
function contentRangeTotal(resp) {
  if (resp.status !== 206) return null;
  const m = /\/(\d+)\s*$/.exec(resp.headers.get('content-range') ?? '');
  return m ? Number(m[1]) : null;
}
