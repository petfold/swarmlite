// Mantaray manifest lookup: resolve a path inside a manifest trie to
// the file's data reference, fetching nodes on demand. A read-only port
// of swarmfs's mantaray/node.py (unmarshal) and mantaray/walk.py
// (NodeStore/locate) — see there for the wire-format documentation.
// Only lookup is ported; listing and building stay publisher-side.
//
// Transport-agnostic: the store's `load` function (ref hex -> node
// bytes) is the only I/O dependency. Load through verifiedRead and the
// whole manifest resolution is trustless.

export class MantarayFormatError extends Error {
  name = 'MantarayFormatError';
}

const OBFUSCATION_KEY_SIZE = 32;
const VERSION_HASH_SIZE = 31;
const HEADER_SIZE = OBFUSCATION_KEY_SIZE + VERSION_HASH_SIZE + 1; // 64
const FORK_HEADER_SIZE = 2;
const FORK_PRE_REFERENCE_SIZE = 32;
const PREFIX_MAX_SIZE = FORK_PRE_REFERENCE_SIZE - FORK_HEADER_SIZE; // 30
const METADATA_SIZE_SIZE = 2;

// keccak256("mantaray:0.1") / keccak256("mantaray:0.2") truncated to 31
// bytes, as pre-computed in bee's pkg/manifest/mantaray/marshal.go
const VERSION_01_HASH = '025184789d63635766d78c41900196b57d7400875ebe4d9b5d1e76bd9652a9';
const VERSION_02_HASH = '5768b3b6a7db56d21d1abff40d41cebfc83448fed8d7e9b06ec0d3b073f28f';

const NT_VALUE = 0x02;
const NT_EDGE = 0x04;

const hex = (bytes) =>
  Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');

/** Decode one serialized Mantaray node. Returns
 * { entry: hex|null, forks: Map<byte, fork> } with fork =
 * { nodeType, prefix: Uint8Array, ref: hex, metadata: object|null }. */
export function unmarshal(data) {
  if (data.length < HEADER_SIZE) {
    throw new MantarayFormatError(`serialized node too short: ${data.length} bytes`);
  }
  const key = data.subarray(0, OBFUSCATION_KEY_SIZE);
  const body = data.slice(OBFUSCATION_KEY_SIZE); // copy — XORed in place
  if (key.some((b) => b !== 0)) {
    for (let i = 0; i < body.length; i++) body[i] ^= key[i % OBFUSCATION_KEY_SIZE];
  }

  const versionHash = hex(body.subarray(0, VERSION_HASH_SIZE));
  let version;
  if (versionHash === VERSION_01_HASH) version = 1;
  else if (versionHash === VERSION_02_HASH) version = 2;
  else throw new MantarayFormatError(`unknown version hash: ${versionHash}`);

  const rbs = body[VERSION_HASH_SIZE];
  let off = VERSION_HASH_SIZE + 1;
  if (body.length < off + rbs + 32) {
    throw new MantarayFormatError('serialized node too short for entry + fork bitmap');
  }
  const entryBytes = body.subarray(off, off + rbs);
  off += rbs;
  const bitmap = body.subarray(off, off + 32);
  off += 32;

  const node = {
    entry: entryBytes.some((b) => b !== 0) ? hex(entryBytes) : null,
    forks: new Map(),
  };

  for (let b = 0; b < 256; b++) {
    if (!((bitmap[b >> 3] >> (b & 7)) & 1)) continue;
    if (version === 2 && rbs === 0) continue; // bee skips fork parsing here
    const base = FORK_PRE_REFERENCE_SIZE + rbs;
    if (body.length < off + base) {
      throw new MantarayFormatError(`truncated fork record for byte ${b}`);
    }
    const nodeType = body[off];
    const prefixLen = body[off + 1];
    if (prefixLen === 0 || prefixLen > PREFIX_MAX_SIZE) {
      throw new MantarayFormatError(`invalid fork prefix length: ${prefixLen}`);
    }
    const prefix = body.subarray(off + FORK_HEADER_SIZE, off + FORK_HEADER_SIZE + prefixLen);
    const ref = hex(body.subarray(off + FORK_PRE_REFERENCE_SIZE, off + base));

    let metadata = null;
    let size = base;
    if (version === 2 && nodeType & 0x10 /* NT_WITH_METADATA */) {
      const msize = (body[off + base] << 8) | body[off + base + 1];
      size = base + METADATA_SIZE_SIZE + msize;
      if (body.length < off + size) {
        throw new MantarayFormatError(`truncated fork metadata for byte ${b}`);
      }
      const raw = body.subarray(off + base + METADATA_SIZE_SIZE, off + size);
      try {
        metadata = JSON.parse(new TextDecoder().decode(raw)); // '\n' padding is valid JSON whitespace
      } catch (e) {
        throw new MantarayFormatError(`bad fork metadata JSON: ${e}`);
      }
    }
    node.forks.set(b, { nodeType, prefix, ref, metadata });
    off += size;
  }
  return node;
}

/** Fetch-and-parse cache for manifest nodes, keyed by reference (hex).
 * Content-addressed, so cached nodes never go stale. */
export class NodeStore {
  #load;
  #cache = new Map();
  #cacheSize;

  constructor(load, { cacheSize = 4096 } = {}) {
    this.#load = load;
    this.#cacheSize = cacheSize;
  }

  async get(refHex) {
    const hit = this.#cache.get(refHex);
    if (hit !== undefined) {
      this.#cache.delete(refHex);
      this.#cache.set(refHex, hit);
      return hit;
    }
    const node = unmarshal(await this.#load(refHex));
    this.#cache.set(refHex, node);
    while (this.#cache.size > this.#cacheSize) {
      this.#cache.delete(this.#cache.keys().next().value);
    }
    return node;
  }
}

const startsWith = (bytes, prefix) =>
  prefix.length <= bytes.length && prefix.every((b, i) => bytes[i] === b);

/** Resolve `path` inside the manifest at `rootRefHex` to the file's
 * data reference: { reference: hex, metadata } — or null if the path
 * does not name a file in the manifest. */
export async function lookupPath(store, rootRefHex, path) {
  let needle = new TextEncoder().encode(path);
  let node = await store.get(rootRefHex);
  while (needle.length) {
    const fork = node.forks.get(needle[0]);
    if (fork === undefined || !startsWith(needle, fork.prefix)) return null;
    needle = needle.subarray(fork.prefix.length);
    if (!needle.length) {
      // path consumed exactly at this fork: a file iff it's a value
      // fork whose child carries an entry
      if (!(fork.nodeType & NT_VALUE)) return null;
      const child = await store.get(fork.ref);
      return child.entry ? { reference: child.entry, metadata: fork.metadata } : null;
    }
    if (!(fork.nodeType & NT_EDGE)) return null;
    node = await store.get(fork.ref);
  }
  // empty path = the manifest root itself: a directory, not a file
  return null;
}
