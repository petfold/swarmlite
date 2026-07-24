// Offline tests for client-side verification, against fixtures that
// swarmfs (the reference implementation) generated — regenerate with
// `python js/test/make_fixtures.py`. Runs with no Bee node:
//
//   node js/test/verify.mjs

import { readFile } from 'node:fs/promises';
import { NodeStore, lookupPath } from '../src/mantaray.js';
import { open, resolveFeed } from '../src/index.js';
import {
  VerificationError, VerifiedChunkStore, feedIdentifier, fromHex,
  socAddress, toHex, verifiedRead, verifiedSize,
} from '../src/verify.js';
import { topicHex } from '../src/feeds.js';

const fixture = JSON.parse(await readFile(
  new URL('fixtures/verified.json', import.meta.url), 'utf8'));

const b64 = (s) => {
  const buf = Buffer.from(s, 'base64');
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
};

function assert(cond, msg, extra) {
  if (!cond) {
    console.error(`FAIL: ${msg}`, extra ?? '');
    process.exit(1);
  }
  console.log(`ok: ${msg}`);
}

async function expectVerificationError(fn, msg) {
  try {
    await fn();
  } catch (e) {
    assert(e instanceof VerificationError, `${msg} (${e.message})`);
    return;
  }
  assert(false, `${msg} — no error was thrown`);
}

/** A gateway stub serving /chunks/{ref} and /feeds/... from a chunk
 * map; `tamper` lets a test corrupt what the "gateway" returns. */
function gateway(chunks, { feedIndex = '0000000000000000' } = {}) {
  return async (url) => {
    const resp = (ok, status, bytes) => ({
      ok, status,
      headers: new Map([['swarm-feed-index', feedIndex]]),
      body: { async cancel() {} },
      arrayBuffer: async () => bytes.buffer.slice(
        bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    });
    let m = /\/chunks\/([0-9a-f]{64})$/.exec(url);
    if (m) {
      const data = chunks.get(m[1]);
      return data ? resp(true, 200, data) : resp(false, 404, new Uint8Array(0));
    }
    if (/\/feeds\//.test(url)) return resp(true, 200, new Uint8Array(0));
    return resp(false, 404, new Uint8Array(0));
  };
}

const honest = new Map(
  Object.entries(fixture.chunks).map(([ref, data]) => [ref, b64(data)]));
honest.set(fixture.feed.socAddress, b64(fixture.feed.soc));
const content = b64(fixture.db.content);

// ---- verified tree reads ---------------------------------------------------

{
  const store = new VerifiedChunkStore('http://gw', { fetchFn: gateway(honest) });
  assert(await verifiedSize(store, fixture.db.ref) === fixture.db.size,
    'verified size matches');
  const all = await verifiedRead(store, fixture.db.ref, 0, fixture.db.size);
  assert(Buffer.compare(Buffer.from(all), Buffer.from(content)) === 0,
    'verified full read reproduces the exact content');
  const slice = await verifiedRead(store, fixture.db.ref, 4090, 8200);
  assert(Buffer.compare(Buffer.from(slice),
    Buffer.from(content.subarray(4090, 8200))) === 0,
    'verified range read across chunk boundaries matches');
}

// a tampering gateway: flip one byte in one DB leaf chunk
{
  const tampered = new Map(honest);
  const leafRef = [...tampered.keys()].find((ref) => {
    const c = tampered.get(ref);
    return c.length === 8 + 4096 && ref !== fixture.db.ref; // a full leaf
  });
  const bad = tampered.get(leafRef).slice();
  bad[100] ^= 0xff;
  tampered.set(leafRef, bad);
  const store = new VerifiedChunkStore('http://gw', { fetchFn: gateway(tampered) });
  await expectVerificationError(
    () => verifiedRead(store, fixture.db.ref, 0, fixture.db.size),
    'tampered chunk content is caught');
}

// ---- manifest lookup -------------------------------------------------------

{
  const store = new VerifiedChunkStore('http://gw', { fetchFn: gateway(honest) });
  const nodes = new NodeStore((ref) => verifiedRead(store, ref));
  const entry = await lookupPath(nodes, fixture.manifest.root, fixture.manifest.path);
  assert(entry?.reference === fixture.db.ref,
    'manifest lookup resolves the path to the file reference');
  assert(await lookupPath(nodes, fixture.manifest.root, 'nope.db') === null,
    'missing path resolves to null');
}

// ---- full verified database open ------------------------------------------

{
  const wasmBinary = await readFile(
    new URL('../vendor/wa-sqlite/dist/wa-sqlite-async.wasm', import.meta.url));
  const db = await open(
    `http://gw/bzz/${fixture.manifest.root}/${fixture.manifest.path}`,
    { verify: true, fetchFn: gateway(honest), moduleOptions: { wasmBinary } });
  const rows = await db.query('SELECT title FROM posts WHERE id = 42');
  assert(rows.length === 1 && rows[0].title === 'Post 42: deterministic fixture row',
    'verified SQL query returns the right row');
  const s = db.stats();
  assert(s.chunksFetched > 0 && s.pagesFetched > 0,
    `verified stats report chunk fetches (${s.chunksFetched} chunks, ` +
    `${s.pagesFetched} pages)`);
  await db.close();

  let threw = null;
  try {
    await open('http://gw/plain.db', { verify: true, fetchFn: gateway(honest) });
  } catch (e) { threw = e; }
  assert(threw && /bzz\/<64-hex root>/.test(threw.message),
    'verify without a bzz root URL fails loudly');
}

// ---- verified feed resolution ----------------------------------------------

{
  const f = fixture.feed;
  const r = await resolveFeed('http://gw', f.owner, f.topic,
    { verify: true, fetchFn: gateway(honest) });
  assert(r.reference === f.reference && r.index === f.index,
    'verified feed resolution recovers the signed reference');

  // forged signature: flip a byte inside the SOC's signature field
  const forged = new Map(honest);
  const badSoc = b64(f.soc).slice();
  badSoc[40] ^= 0x01;
  forged.set(f.socAddress, badSoc);
  await expectVerificationError(
    () => resolveFeed('http://gw', f.owner, f.topic,
      { verify: true, fetchFn: gateway(forged) }),
    'forged feed update signature is caught');

  // the genuine update served under a DIFFERENT owner's feed address:
  // the address check passes (we plant it there), signature recovery
  // must expose that the signer is not that owner
  const fakeOwner = 'ab'.repeat(20);
  const fakeAddr = toHex(socAddress(
    feedIdentifier(fromHex(topicHex(f.topic)), 0), fromHex(fakeOwner)));
  const reowned = new Map(honest);
  reowned.set(fakeAddr, b64(f.soc));
  await expectVerificationError(
    () => resolveFeed('http://gw', fakeOwner, f.topic,
      { verify: true, fetchFn: gateway(reowned) }),
    'update signed by a different key than the feed owner is caught');
}

console.log('verify test passed');
