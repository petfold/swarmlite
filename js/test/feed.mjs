// Offline unit test for feed resolution: topic hashing must match
// swarmfs's topic_bytes() exactly (vectors below computed with it),
// and payload parsing must handle every bee-js update shape. Runs with
// no Bee node:
//
//   node js/test/feed.mjs

import { resolveFeed, topicHex } from '../src/feeds.js';
import { keccak256 } from '../vendor/js-sha3/index.js';

function assert(cond, msg, extra) {
  if (!cond) {
    console.error(`FAIL: ${msg}`, extra ?? '');
    process.exit(1);
  }
  console.log(`ok: ${msg}`);
}

// keccak sanity: the well-known Ethereum vector
assert(
  keccak256('hello') ===
    '1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8',
  'vendored keccak256 matches the known vector',
);

// topic hashing mirrors swarmfs topic_bytes()
assert(
  topicHex('swarmlite-demo') ===
    'a9ed6d8826e7bda6cb1f0ac6a2860a9f29cea54bac995a4e7bf3135228a67bf5',
  'human-readable topic hashes like swarmfs',
);
assert(
  topicHex('AbCd'.repeat(16)) === 'abcd'.repeat(16),
  'raw 64-hex topic passes through (lowercased)',
);

// resolveFeed parses the bee-js payload shapes, via a stubbed gateway
const OWNER = 'ab'.repeat(20);
const REF = new Uint8Array(32).map((_, i) => i);
const REF_HEX = Array.from(REF, (b) => b.toString(16).padStart(2, '0')).join('');

const stub = (payload, { status = 200, index = '0000000000000002', etag } = {}) =>
  async (url) => ({
    ok: status === 200,
    status,
    url,
    headers: new Map([['swarm-feed-index', index], ['etag', etag]]),
    body: { cancelled: false, async cancel() { this.cancelled = true; } },
    arrayBuffer: async () => payload.buffer.slice(0),
  });

// primary path: Bee resolves server-side, reference travels in Etag
let r = await resolveFeed('http://gw/', OWNER, 'swarmlite-demo', {
  fetchFn: stub(new Uint8Array(999), { etag: `"${REF_HEX.toUpperCase()}"` }),
});
assert(r.reference === REF_HEX && r.index === 2,
  'Etag reference wins, body ignored', r);

let ts8 = new Uint8Array(40);
ts8.set(REF, 8);
r = await resolveFeed('http://gw/', OWNER, 'swarmlite-demo', {
  fetchFn: stub(ts8),
});
assert(r.reference === REF_HEX && r.index === 2,
  'timestamp+reference payload parses', r);

r = await resolveFeed('http://gw', `0x${OWNER}`, 'ab'.repeat(32), {
  fetchFn: stub(new Uint8Array(REF)),
});
assert(r.reference === REF_HEX, 'bare-reference payload and 0x owner parse');

for (const [payload, what] of [
  [new Uint8Array(17), 'garbage-length payload'],
]) {
  let threw = null;
  try {
    await resolveFeed('http://gw', OWNER, 't', { fetchFn: stub(payload) });
  } catch (e) {
    threw = e;
  }
  assert(threw && /unexpected \/feeds response/.test(threw.message),
    `${what} fails loudly`, threw?.message);
}

let threw = null;
try {
  await resolveFeed('http://gw', OWNER, 't', {
    fetchFn: stub(new Uint8Array(0), { status: 404 }),
  });
} catch (e) {
  threw = e;
}
assert(threw && /404/.test(threw.message), 'HTTP 404 fails loudly with status');

console.log('feed test passed');
