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
import {
  VerificationError, feedIdentifier, fromHex, socAddress, toHex, verifySoc,
} from './verify.js';

const HEX64 = /^[0-9a-fA-F]{64}$/;

/** 32-byte feed topic as hex: a raw 64-hex string passes through,
 * anything else is keccak256 of the utf-8 string — mirrors swarmfs
 * topic_bytes(), so topics printed by `swarmlite publish` resolve. */
export function topicHex(topic) {
  return HEX64.test(topic) ? topic.toLowerCase() : keccak256(topic);
}

/** Resolve bzzf://<owner>/<topic> to { reference, index } using a Bee
 * API/gateway URL. Throws with an actionable message on any failure —
 * never returns a guess.
 *
 * With `verify: true` the update's single-owner chunk is fetched and
 * checked client-side (address derivation + owner-signature recovery),
 * so an untrusted gateway cannot forge a feed update. What cannot be
 * proven client-side is *latestness* — a malicious gateway could serve
 * an older (but genuine) update; that limitation is shared with every
 * feed reader, including swarmfs. */
export async function resolveFeed(apiUrl, owner, topic, { fetchFn, verify = false } = {}) {
  const doFetch = fetchFn ?? ((...args) => globalThis.fetch(...args));
  owner = owner.toLowerCase().replace(/^0x/, '');
  if (!/^[0-9a-f]{40}$/.test(owner)) {
    throw new Error(`feed owner must be a 20-byte hex address, got "${owner}"`);
  }
  const api = apiUrl.replace(/\/+$/, '');
  const url = `${api}/feeds/${owner}/${topicHex(topic)}?type=sequence`;
  if (verify) {
    return resolveVerified(doFetch, api, url, owner, topicHex(topic));
  }
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

/** Verified resolution: take only the update INDEX from the gateway's
 * headers (server-side sequence lookup), then fetch the single-owner
 * chunk at the address we derive ourselves and verify it fully —
 * mirrors swarmfs FeedOps.latest(verify=True). */
async function resolveVerified(doFetch, api, url, owner, topicHexStr) {
  const head = await doFetch(url, {
    headers: { 'Swarm-Only-Root-Chunk': 'true' }, // headers only, skip content
  });
  if (!head.ok) {
    throw new Error(
      `feed lookup failed: HTTP ${head.status} for ${url}` +
      (head.status === 404 ? ' — feed has no updates yet, or still syncing' : ''),
    );
  }
  await head.body?.cancel?.();
  const indexHex = head.headers.get('swarm-feed-index');
  if (!/^[0-9a-fA-F]{16}$/.test(indexHex ?? '')) {
    throw new VerificationError(
      `cannot verify: no Swarm-Feed-Index header on ${url} (got ${indexHex})`,
    );
  }
  const index = parseInt(indexHex, 16);
  const ownerBytes = fromHex(owner);
  const addr = socAddress(feedIdentifier(fromHex(topicHexStr), index), ownerBytes);
  const resp = await doFetch(`${api}/chunks/${toHex(addr)}`);
  if (!resp.ok) {
    throw new Error(
      `feed update chunk ${toHex(addr).slice(0, 16)}… not retrievable ` +
      `(HTTP ${resp.status}) — the gateway reported index ${index} but ` +
      'does not serve the corresponding single-owner chunk',
    );
  }
  const soc = new Uint8Array(await resp.arrayBuffer());
  const payload = verifySoc(soc, ownerBytes, addr); // throws VerificationError on forgery
  let ref;
  if (payload.byteLength === 40 || payload.byteLength === 72) {
    ref = payload.subarray(8); // timestamp(8) + reference(32|64)
  } else if (payload.byteLength === 32 || payload.byteLength === 64) {
    ref = payload;
  } else {
    throw new VerificationError(
      `verified feed update carries an unexpected ${payload.byteLength}-byte ` +
      'payload — not a bee-js update (timestamp + reference)',
    );
  }
  return { reference: toHex(ref), index };
}
