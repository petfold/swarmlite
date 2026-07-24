// The pure-JS publisher: stamps, upload, and signed feed updates over
// a Bee node's HTTP API — so a JS developer never needs the Python
// package. Mirrors swarmlite/publish.py + swarmfs (stamps.py, feeds.py)
// behavior for behavior; the signing math is checked byte-for-byte
// against swarmfs-generated fixtures (RFC 6979 is deterministic).
//
// Money rules match the Python side exactly: `autoStamp` selects, and
// `planBatch`/`buyBatch` exist as capabilities — nothing here spends
// xBZZ unless the caller explicitly asks (the CLI asks the human).

// signing uses built-in WebCrypto (noble's signAsync); Node 18 has it
// under node:crypto rather than on globalThis — browsers skip this
if (!globalThis.crypto?.subtle) {
  const { webcrypto } = await import('node:crypto');
  globalThis.crypto = webcrypto;
}

import { getPublicKey, signAsync } from '../vendor/noble-secp256k1/index.js';
import { keccak256Bytes } from '../vendor/js-sha3/index.js';
import { chunkAddress, feedIdentifier, fromHex, socAddress, toHex } from './verify.js';
import { topicHex } from './feeds.js';

const BLOCK_SECS = 5; // Gnosis block time
const PLUR_PER_BZZ = 10 ** 16;
const MIN_TTL_SECS = 60;

// upload size -> batch depth; see swarmfs stamps.py — an immutable
// batch dies when any single bucket fills (measured live: one 42 MB
// upload filled a depth-18 batch), so keep overflow risk under ~5%
const DEPTH_TIERS = [[15 * 2 ** 20, 18], [150 * 2 ** 20, 19], [2 ** 30, 20]];

export class PublishError extends Error {
  name = 'PublishError';
}

const doFetch = (fetchFn) => fetchFn ?? ((...args) => globalThis.fetch(...args));

async function checkOk(resp, what) {
  if (resp.ok) return resp;
  const detail = (await resp.text().catch(() => '')).slice(0, 200);
  throw new PublishError(`Bee API ${resp.status} for ${what}: ${detail}`);
}

// ---- stamps ----------------------------------------------------------------

/** Pick the usable batch with the longest TTL (swarmfs auto-selection). */
export async function autoStamp(apiUrl, { fetchFn } = {}) {
  const resp = await checkOk(await doFetch(fetchFn)(`${apiUrl}/stamps`), '/stamps');
  const stamps = (await resp.json()).stamps ?? [];
  const usable = stamps.filter((s) =>
    s.usable && !(s.utilizationRatio >= 1.0) &&
    !(s.batchTTL >= 0 && s.batchTTL <= MIN_TTL_SECS));
  if (!usable.length) {
    throw new PublishError(
      stamps.length
        ? `no usable postage stamp on ${apiUrl}: ` + stamps.map((s) =>
            `${s.batchID.slice(0, 8)}…(${s.usable ? 'full or expiring' : 'not usable'})`).join('; ')
        : `no postage stamps on ${apiUrl} — buy one first (planBatch/buyBatch, ` +
          'or `swarmlite publish --buy` from the Python CLI)',
    );
  }
  return usable.reduce((a, b) =>
    (b.batchTTL ?? -1) > (a.batchTTL ?? -1) ? b : a).batchID;
}

export function suggestDepth(sizeBytes) {
  for (const [limit, depth] of DEPTH_TIERS) {
    if (sizeBytes <= limit) return depth;
  }
  return 20 + Math.ceil(Math.log2(sizeBytes / 2 ** 30));
}

/** Price a batch for sizeBytes lasting ttlSecs (floor padded an hour
 * above the chain's strict 24h minimum — mirrors swarmfs). */
export async function planBatch(apiUrl, sizeBytes, ttlSecs, { fetchFn } = {}) {
  const resp = await checkOk(await doFetch(fetchFn)(`${apiUrl}/chainstate`), '/chainstate');
  const chain = await resp.json();
  const price = Number(chain.currentPrice);
  const floor = Number(chain.minimumValidityBlocks ?? 0) + 3600 / BLOCK_SECS;
  const blocks = Math.max(Math.ceil(ttlSecs / BLOCK_SECS), floor);
  const depth = suggestDepth(sizeBytes);
  const amount = blocks * price;
  return { depth, amount, ttlSecs: blocks * BLOCK_SECS,
           costBzz: amount * 2 ** depth / PLUR_PER_BZZ };
}

/** Buy a batch and poll until usable. Spends the node wallet's xBZZ —
 * callers decide, this only executes. Every failure path after the
 * purchase carries the batch id. */
export async function buyBatch(apiUrl, amount, depth, { fetchFn, waitSecs = 300 } = {}) {
  const f = doFetch(fetchFn);
  const resp = await checkOk(
    await f(`${apiUrl}/stamps/${amount}/${depth}`, { method: 'POST' }),
    'POST /stamps');
  const batchId = (await resp.json()).batchID;
  const deadline = Date.now() + waitSecs * 1000;
  while (Date.now() < deadline) {
    const poll = await f(`${apiUrl}/stamps/${batchId}`);
    if (poll.ok && (await poll.json()).usable) return batchId;
    if (!poll.ok && poll.status !== 400 && poll.status !== 404) {
      throw new PublishError(
        `batch ${batchId} was bought (tx submitted) but polling failed ` +
        `(HTTP ${poll.status}) — check ${apiUrl}/stamps/${batchId} and ` +
        'use it once usable');
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  throw new PublishError(
    `batch ${batchId} was bought but is still not usable after ${waitSecs}s ` +
    `— check ${apiUrl}/stamps/${batchId} and use it once usable`);
}

// ---- upload ----------------------------------------------------------------

/** Upload one file via POST /bzz (Bee builds the single-file manifest).
 * Returns the manifest root reference. */
export async function uploadFile(apiUrl, name, bytes, stamp, { fetchFn } = {}) {
  const resp = await checkOk(await doFetch(fetchFn)(
    `${apiUrl}/bzz?name=${encodeURIComponent(name)}`,
    {
      method: 'POST',
      body: bytes,
      headers: {
        'Content-Type': 'application/octet-stream',
        'Swarm-Postage-Batch-Id': stamp,
      },
    },
  ), 'POST /bzz');
  return (await resp.json()).reference;
}

// ---- signed feed updates ---------------------------------------------------

/** The feed owner's 20-byte address for a private key, as hex. */
export function ownerAddress(privateKeyHex) {
  const pub = getPublicKey(privateKeyHex.replace(/^0x/, ''), false); // uncompressed
  return toHex(keccak256Bytes(pub.subarray(1)).subarray(12));
}

async function personalSign(digest32, privateKeyHex) {
  const prefixed = keccak256Bytes(concat(
    new TextEncoder().encode('\x19Ethereum Signed Message:\n32'), digest32));
  const sig = await signAsync(prefixed, privateKeyHex.replace(/^0x/, ''));
  const out = new Uint8Array(65);
  out.set(sig.toBytes(), 0); // r ‖ s (compact)
  out[64] = sig.recovery + 27;
  return out;
}

function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let off = 0;
  for (const p of parts) { out.set(p, off); off += p.length; }
  return out;
}

const cacData = (payload) => {
  const span = new Uint8Array(8);
  new DataView(span.buffer).setBigUint64(0, BigInt(payload.length), true);
  return concat(span, payload);
};

/** Build one signed feed-update chunk (bee-js format: timestamp + ref).
 * Pure function of its inputs — testable against swarmfs fixtures. */
export async function buildFeedUpdate(privateKeyHex, topic, index, referenceHex, timestamp) {
  const identifier = feedIdentifier(fromHex(topicHex(topic)), index);
  const ts = new Uint8Array(8);
  new DataView(ts.buffer).setBigUint64(0, BigInt(timestamp), false);
  const data = cacData(concat(ts, fromHex(referenceHex)));
  const digest = keccak256Bytes(concat(identifier, chunkAddress(data)));
  const signature = await personalSign(digest, privateKeyHex);
  return { identifier, signature, data,
           soc: concat(identifier, signature, data) };
}

/** Publish `referenceHex` as the feed's next update. */
export async function updateFeed(apiUrl, privateKeyHex, topic, referenceHex, stamp,
                                 { fetchFn, timestamp } = {}) {
  const f = doFetch(fetchFn);
  const owner = ownerAddress(privateKeyHex);
  // next index: current head + 1, or 0 for a fresh feed
  const head = await f(
    `${apiUrl}/feeds/${owner}/${topicHex(topic)}?type=sequence`,
    { headers: { 'Swarm-Only-Root-Chunk': 'true' } });
  await head.body?.cancel?.();
  const headIndex = head.ok ? head.headers.get('swarm-feed-index') : null;
  const index = headIndex ? parseInt(headIndex, 16) + 1 : 0;

  const { identifier, signature, data } = await buildFeedUpdate(
    privateKeyHex, topic, index, referenceHex,
    timestamp ?? Math.floor(Date.now() / 1000));
  await checkOk(await f(
    `${apiUrl}/soc/${owner}/${toHex(identifier)}?sig=${toHex(signature)}`,
    {
      method: 'POST',
      body: data,
      headers: { 'Swarm-Postage-Batch-Id': stamp },
    },
  ), 'POST /soc');
  return { owner, index };
}

// ---- orchestration ---------------------------------------------------------

/** The whole publish flow: prepare -> upload -> optionally advance a
 * feed. Returns { root, warnings, feed? }. `log` receives the human
 * lines (pin/feed/warnings); pass a no-op for quiet. */
export async function publish(bytes, {
  apiUrl = 'http://localhost:1633',
  name = 'site.db',
  stamp = 'auto',
  feed = null,
  signer = null,
  fetchFn,
  moduleOptions,
  log = () => {},
} = {}) {
  const { prepare } = await import('./prepare.js');
  const { bytes: prepared, warnings } = await prepare(bytes, { moduleOptions });
  for (const w of warnings) log(`warning: ${w}`);

  if (!stamp || stamp === 'auto') stamp = await autoStamp(apiUrl, { fetchFn });
  const root = await uploadFile(apiUrl, name, prepared, stamp, { fetchFn });
  log(`pin:  bzz://${root}/${name}`);

  let feedInfo = null;
  if (feed) {
    if (!signer) {
      throw new PublishError(
        'feed publishing needs a signer key: pass signer (the feed ' +
        "owner's private key hex)");
    }
    feedInfo = await updateFeed(apiUrl, signer, feed, root, stamp, { fetchFn });
    log(`feed: bzzf://${feedInfo.owner}/${feed}/${name}`);
  } else {
    log('tip:  this URL names exactly this version. If the data will ' +
        'change, republish with a feed so readers get one stable URL');
  }
  return { root, warnings, feed: feedInfo };
}
