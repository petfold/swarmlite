// Offline tests for the pure-JS publisher: the prepare checklist runs
// on wa-sqlite in memory, feed signing is checked BYTE-FOR-BYTE against
// the swarmfs-signed fixture (RFC 6979 is deterministic), and the HTTP
// surface is exercised against a stub gateway. No Bee node:
//
//   node js/test/publish.mjs

import { readFile } from 'node:fs/promises';
import { PrepareError, prepare } from '../src/prepare.js';
import {
  PublishError, autoStamp, buildFeedUpdate, ownerAddress, planBatch,
  publish, suggestDepth, updateFeed,
} from '../src/publisher.js';
import { socAddress, toHex } from '../src/verify.js';

const fixture = JSON.parse(await readFile(
  new URL('fixtures/verified.json', import.meta.url), 'utf8'));
const wasmBinary = await readFile(
  new URL('../vendor/wa-sqlite/dist/wa-sqlite-async.wasm', import.meta.url));
const moduleOptions = { wasmBinary };
const KEY = 'ad'.repeat(32);
const MB = 2 ** 20;

function assert(cond, msg, extra) {
  if (!cond) {
    console.error(`FAIL: ${msg}`, extra ?? '');
    process.exit(1);
  }
  console.log(`ok: ${msg}`);
}

async function expectError(fn, type, pattern, msg) {
  try {
    await fn();
  } catch (e) {
    assert(e instanceof type && pattern.test(e.message), `${msg} (${e.message})`);
    return;
  }
  assert(false, `${msg} — no error was thrown`);
}

// ---- prepare ----------------------------------------------------------------

{
  const db = new Uint8Array(Buffer.from(fixture.db.content, 'base64'));
  const { bytes, warnings } = await prepare(db, { moduleOptions });
  assert(Buffer.from(bytes.subarray(0, 15)).toString() === 'SQLite format 3'
    && bytes[18] === 1 && bytes[19] === 1,
    `prepare emits a legacy-journal SQLite file (${bytes.length} B, `
    + `warnings: ${warnings.length})`);

  await expectError(() => prepare(new Uint8Array(200), { moduleOptions }),
    PrepareError, /bad header magic/, 'garbage input fails loudly');

  const wal = db.slice();
  wal[18] = wal[19] = 2;
  await expectError(() => prepare(wal, { moduleOptions }),
    PrepareError, /journal_mode=DELETE/, 'WAL input is rejected with the fix');
}

// ---- feed signing vs the swarmfs fixture ------------------------------------

{
  const f = fixture.feed;
  assert(ownerAddress(KEY) === f.owner, 'owner address derives like eth_keys');
  const upd = await buildFeedUpdate(KEY, f.topic, f.index, f.reference, 1753344000);
  assert(Buffer.compare(Buffer.from(upd.soc),
    Buffer.from(f.soc, 'base64')) === 0,
    'feed update SOC is byte-identical to the swarmfs-signed fixture');
  assert(toHex(socAddress(upd.identifier, Buffer.from(f.owner, 'hex'))) === f.socAddress,
    'SOC address matches');
}

// ---- stamps -----------------------------------------------------------------

const jsonResp = (body, { ok = true, status = 200, headers } = {}) => ({
  ok, status, headers: headers ?? new Map(),
  json: async () => body, text: async () => JSON.stringify(body),
  body: { async cancel() {} },
});

{
  const stamps = [
    { batchID: 'aa'.repeat(32), usable: true, batchTTL: 100, utilizationRatio: 0.2 },
    { batchID: 'bb'.repeat(32), usable: true, batchTTL: 99999, utilizationRatio: 0.2 },
    { batchID: 'cc'.repeat(32), usable: true, batchTTL: 99999, utilizationRatio: 1.0 },
    { batchID: 'dd'.repeat(32), usable: false, batchTTL: 99999 },
  ];
  const picked = await autoStamp('http://gw', { fetchFn: async () => jsonResp({ stamps }) });
  assert(picked === 'bb'.repeat(32), 'auto picks longest-TTL usable batch');

  await expectError(
    () => autoStamp('http://gw', { fetchFn: async () => jsonResp({ stamps: [] }) }),
    PublishError, /buy one first/, 'no stamps is actionable');

  assert(suggestDepth(1 * MB) === 18 && suggestDepth(16 * MB) === 19
    && suggestDepth(1024 * MB) === 20 && suggestDepth(2048 * MB) === 21,
    'depth tiers match swarmfs');

  const plan = await planBatch('http://gw', 10 * MB, 3600, {
    fetchFn: async () => jsonResp({ currentPrice: '1000', minimumValidityBlocks: 17280 }),
  });
  assert(plan.depth === 18 && plan.amount === (17280 + 720) * 1000,
    'plan pads the chain minimum like swarmfs');
}

// ---- upload + feed over a stub gateway --------------------------------------

{
  const calls = [];
  const gw = async (url, opts = {}) => {
    calls.push({ url, opts });
    if (url.includes('/bzz?'))   return jsonResp({ reference: '11'.repeat(32) });
    if (url.includes('/feeds/')) return jsonResp({}, { ok: false, status: 404 });
    if (url.includes('/soc/'))   return jsonResp({ reference: '22'.repeat(32) });
    if (url.includes('/stamps')) return jsonResp({ stamps: [
      { batchID: 'ab'.repeat(32), usable: true, batchTTL: 9999, utilizationRatio: 0 }] });
    return jsonResp({}, { ok: false, status: 404 });
  };

  const db = new Uint8Array(Buffer.from(fixture.db.content, 'base64'));
  const lines = [];
  const { root, feed } = await publish(db, {
    apiUrl: 'http://gw', name: 'tiny.db', feed: 'mysite', signer: KEY,
    fetchFn: gw, moduleOptions, log: (l) => lines.push(l),
  });
  assert(root === '11'.repeat(32), 'publish returns the upload reference');
  assert(feed.index === 0, 'fresh feed gets index 0');

  const upload = calls.find((c) => c.url.includes('/bzz?'));
  assert(upload.opts.headers['Swarm-Postage-Batch-Id'] === 'ab'.repeat(32)
    && upload.url.includes('name=tiny.db'), 'upload carries stamp and name');
  const soc = calls.find((c) => c.url.includes('/soc/'));
  assert(soc.url.includes(`/soc/${ownerAddress(KEY)}/`) && /sig=[0-9a-f]{130}/.test(soc.url),
    'SOC POST addresses the owner and carries the signature');
  assert(lines.some((l) => l.startsWith('pin:'))
    && lines.some((l) => l.startsWith('feed:')), 'human lines printed');

  // pin-only publish points at feeds instead
  const quiet = [];
  await publish(db, { apiUrl: 'http://gw', name: 'tiny.db',
    fetchFn: gw, moduleOptions, log: (l) => quiet.push(l) });
  assert(quiet.some((l) => l.startsWith('tip:')), 'pin-only publish prints the feed tip');

  await expectError(
    () => publish(db, { apiUrl: 'http://gw', feed: 'mysite', fetchFn: gw, moduleOptions }),
    PublishError, /signer/, 'feed without signer fails loudly');
}

// next-index derivation from an existing head
{
  const gw = async (url, opts = {}) => {
    if (url.includes('/feeds/')) {
      return jsonResp({}, { headers: new Map([['swarm-feed-index', '0000000000000004']]) });
    }
    return jsonResp({ reference: '33'.repeat(32) });
  };
  const { index } = await updateFeed('http://gw', KEY, 'mysite', 'ee'.repeat(32),
    'ab'.repeat(32), { fetchFn: gw, timestamp: 1753344000 });
  assert(index === 5, 'existing feed head advances to the next index');
}

console.log('publish test passed');
