# swarmlite

**Verifiable serverless SQLite on [Ethereum Swarm](https://www.ethswarm.org/).**

Publish an ordinary SQLite file to Swarm and run `SELECT` against it —
from a browser or Node — fetching only the 4 KB B-tree pages the query
plan touches. Measured live: a cold point lookup on a published
41.9 MB database fetched **5 pages / 20 KB**; a whole 4-query session
23 pages. There is no database server anywhere, and with
`verify: true` every byte is checked client-side against the 32-byte
content address — a tampering gateway is caught on the first bad chunk.

Everything is vendored (wa-sqlite, js-sha3, noble-secp256k1): zero
runtime dependencies, and the whole reader can itself be published on
Swarm next to your data — one immutable root serves the page, the
engine, and the database.

## Read (browser or Node)

```js
import { open, resolveFeed } from 'swarmlite';

const db = await open('http://localhost:1633/bzz/<root>/site.db');
const rows = await db.query('SELECT title FROM posts WHERE id = ?', [12345]);
console.log(db.stats()); // { readCount, pagesFetched, bytesFetched, fileSize }
```

Any URL served with HTTP Range support works. Follow a feed for
always-latest (resolve once, at load — a database must not move
mid-session), and pass `{ verify: true }` on untrusted gateways:

```js
const { reference } = await resolveFeed(api, owner, 'mysite', { verify: true });
const db = await open(`${api}/bzz/${reference}/site.db`, { verify: true });
```

In Node, pass the wasm explicitly:
`open(url, { moduleOptions: { wasmBinary: await readFile(...) } })`.

## Publish (Node)

```js
import { publish } from 'swarmlite/publisher';

const { root } = await publish(await readFile('site.db'), {
  feed: 'mysite',            // optional: stable URL + version history
  signer: process.env.SWARMLITE_SIGNER,
  log: console.error,
});
```

or from the shell (no Python required):

```bash
npx swarmlite publish site.db --feed mysite --signer <key hex>
npx swarmlite query "bzz://<root>/site.db" "SELECT count(*) FROM posts" --stats
```

The publisher runs the same checklist as the Python CLI — page size
4096 (one page = one Swarm chunk), ANALYZE, VACUUM, integrity check,
missing-index warnings — entirely in memory via wa-sqlite, then uploads
and (optionally) signs a feed update. Feed signatures are byte-for-byte
identical to the Python implementation's (checked in CI against
fixtures the Python side generates). `--buy` prices and purchases a
postage batch from the node's wallet, with the cost shown first.

Writes are the publisher's job by design: work on a local SQLite file
with full SQL, then ship an immutable snapshot. Reader connections are
strictly read-only — the URL *is* the content hash.

## More

The [project repository](https://github.com/petfold/swarmlite) has the
full User Guide (stamps, feeds, snapshots, verification, patterns), the
Python reader/publisher, and a self-contained demo site. BSD-3-Clause.
