// A tiny writable in-memory VFS for wa-sqlite, used by the publisher's
// prepare step (js/src/prepare.js): the database file and its rollback
// journal live in RAM, the checklist runs (VACUUM etc.), and the
// finished bytes are read back out. Fully synchronous — no Asyncify
// round trips — and never touches disk or network.

import { FacadeVFS } from '../vendor/wa-sqlite/src/FacadeVFS.js';
import * as VFS from '../vendor/wa-sqlite/src/VFS.js';

class MemFile {
  data = new Uint8Array(0);
  size = 0;

  ensure(capacity) {
    if (capacity <= this.data.length) return;
    const grown = new Uint8Array(Math.max(capacity, this.data.length * 2, 4096));
    grown.set(this.data);
    this.data = grown;
  }
}

export class MemVFS extends FacadeVFS {
  mxPathname = 512;

  /** @type {Map<string, MemFile>} filename -> contents */
  files = new Map();
  /** @type {Map<number, {file: MemFile, name: string, deleteOnClose: boolean}>} */
  #open = new Map();

  /** Preload a file (e.g. the database to prepare). */
  put(name, bytes) {
    const f = new MemFile();
    f.data = new Uint8Array(bytes); // private copy
    f.size = bytes.byteLength;
    this.files.set(name, f);
  }

  /** Read a file's current contents (e.g. the prepared database). */
  get(name) {
    const f = this.files.get(name);
    return f ? f.data.slice(0, f.size) : null;
  }

  jOpen(name, fileId, flags, pOutFlags) {
    name = name ?? `temp-${fileId}`; // anonymous temp files
    let file = this.files.get(name);
    if (!file) {
      if (!(flags & VFS.SQLITE_OPEN_CREATE)) return VFS.SQLITE_CANTOPEN;
      file = new MemFile();
      this.files.set(name, file);
    }
    this.#open.set(fileId, {
      file, name,
      deleteOnClose: Boolean(flags & VFS.SQLITE_OPEN_DELETEONCLOSE),
    });
    pOutFlags.setInt32(0, flags, true);
    return VFS.SQLITE_OK;
  }

  jClose(fileId) {
    const entry = this.#open.get(fileId);
    this.#open.delete(fileId);
    if (entry?.deleteOnClose) this.files.delete(entry.name);
    return VFS.SQLITE_OK;
  }

  jRead(fileId, pData, iOffset) {
    const { file } = this.#open.get(fileId);
    const available = Math.max(file.size - iOffset, 0);
    const n = Math.min(pData.byteLength, available);
    if (n > 0) pData.set(file.data.subarray(iOffset, iOffset + n), 0);
    if (n < pData.byteLength) {
      pData.fill(0, n);
      return VFS.SQLITE_IOERR_SHORT_READ;
    }
    return VFS.SQLITE_OK;
  }

  jWrite(fileId, pData, iOffset) {
    const { file } = this.#open.get(fileId);
    file.ensure(iOffset + pData.byteLength);
    // pData is wa-sqlite's buffer proxy, not a Uint8Array: set() would
    // treat it as an array-like with no elements and copy zeros, so take
    // a real view with subarray()
    file.data.set(pData.subarray(), iOffset);
    file.size = Math.max(file.size, iOffset + pData.byteLength);
    return VFS.SQLITE_OK;
  }

  jTruncate(fileId, iSize) {
    const { file } = this.#open.get(fileId);
    file.size = Math.min(file.size, iSize);
    return VFS.SQLITE_OK;
  }

  jFileSize(fileId, pSize64) {
    const { file } = this.#open.get(fileId);
    pSize64.setBigInt64(0, BigInt(file.size), true);
    return VFS.SQLITE_OK;
  }

  jSync() {
    return VFS.SQLITE_OK; // RAM is always "synced"
  }

  jDelete(name) {
    this.files.delete(name);
    return VFS.SQLITE_OK;
  }

  jAccess(name, flags, pResOut) {
    pResOut.setInt32(0, this.files.has(name) ? 1 : 0, true);
    return VFS.SQLITE_OK;
  }
}
