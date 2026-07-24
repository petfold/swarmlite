// swarmlite feeds (JS): resolve a Swarm feed (owner, topic) to the
// immutable root it currently points at, via the gateway's /feeds
// endpoint. The gateway does the SOC lookup server-side; the reference
// travels in the response's Etag header (verified live against Bee
// 2.8.1, `Swarm-Feed-Resolved-Version: v1` — the body streams the
// resolved content, which we cancel unread). For gateways that return
// the raw update payload as the body instead, parsing that is the
// fallback.
//
// Resolution happens once, BEFORE open(): a feed can move mid-session,
// a database must not (see SwarmVFS.js). Typical use:
//
//   const { reference } = await resolveFeed(api, owner, topic);
//   const db = await open(`${api}/bzz/${reference}/site.db`);
//
// Client-side SOC signature verification (so an untrusted gateway
// cannot forge an update) is a documented follow-up, together with
// chunk/BMT verification — both need only keccak256, already vendored.

import { keccak256 } from '../vendor/js-sha3/index.js';

const HEX64 = /^[0-9a-fA-F]{64}$/;

/** 32-byte feed topic as hex: a raw 64-hex string passes through,
 * anything else is keccak256 of the utf-8 string — mirrors swarmfs
 * topic_bytes(), so topics printed by `swarmlite publish` resolve. */
export function topicHex(topic) {
  return HEX64.test(topic) ? topic.toLowerCase() : keccak256(topic);
}

/** Resolve bzzf://<owner>/<topic> to { reference, index } using a Bee
 * API/gateway URL. Throws with an actionable message on any failure —
 * never returns a guess. */
export async function resolveFeed(apiUrl, owner, topic, { fetchFn } = {}) {
  const doFetch = fetchFn ?? ((...args) => globalThis.fetch(...args));
  owner = owner.toLowerCase().replace(/^0x/, '');
  if (!/^[0-9a-f]{40}$/.test(owner)) {
    throw new Error(`feed owner must be a 20-byte hex address, got "${owner}"`);
  }
  const url =
    `${apiUrl.replace(/\/+$/, '')}/feeds/${owner}/${topicHex(topic)}?type=sequence`;
  const resp = await doFetch(url);
  if (!resp.ok) {
    throw new Error(
      `feed lookup failed: HTTP ${resp.status} for ${url}` +
      (resp.status === 404 ? ' — feed has no updates yet, or still syncing' : ''),
    );
  }
  const index = parseInt(resp.headers.get('swarm-feed-index') ?? '', 16);
  const done = (reference) =>
    ({ reference, index: Number.isNaN(index) ? null : index });

  // Bee resolves the update server-side; Etag carries the reference and
  // the body streams the content it points at — skip the download.
  const etag = (resp.headers.get('etag') ?? '').replaceAll('"', '');
  if (HEX64.test(etag)) {
    await resp.body?.cancel?.();
    return done(etag.toLowerCase());
  }

  // fallback: the body is the raw update payload (bee-js format)
  const payload = new Uint8Array(await resp.arrayBuffer());
  if (payload.byteLength === 40 || payload.byteLength === 72) {
    return done(toHex(payload.subarray(8))); // timestamp(8) + ref(32|64)
  }
  if (payload.byteLength === 32 || payload.byteLength === 64) {
    return done(toHex(payload)); // bare reference, no timestamp
  }
  throw new Error(
    `unexpected /feeds response from ${url}: no reference Etag, and the ` +
    `body (${payload.byteLength} bytes) is not a bee-js feed update ` +
    '(timestamp + reference) — was the feed published with swarmlite/swarmfs?',
  );
}

const toHex = (bytes) =>
  Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
