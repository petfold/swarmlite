# swarmlite — User Guide

*(This is the tutorial — it teaches by doing, and every output shown was
produced for real. To look something up — a signature, a flag, a URL
form, a table — use the compact [`REFERENCE.md`](REFERENCE.md), which is
pinned against the code by the test suite.)*

Everything needed to go from an empty machine to running lazy, verifiable
`SELECT` against a SQLite database published on Ethereum Swarm.

All examples in this guide were run against a real Bee 2.8.1 light node on
Gnosis mainnet (via Swarm Desktop) on 2026-07-23/24; the numbers shown are
from those sessions.

---

## 0. What you get, in numbers

A published **134.5 MB** database; queries from a cold start:

| query                          | fetched              | time    |
|--------------------------------|----------------------|---------|
| point lookup by primary key    | 5 pages (20 KB)      | 0.02 s  |
| same query again (warm)        | 0 pages              | ~0 ms   |
| latest-5 by indexed timestamp  | 9 pages (36 KB)      |         |
| FTS5 full-text search          | 12 pages (48 KB)     |         |

The query walks SQLite's B-tree; depth grows logarithmically, so cost is a
handful of 4 KB pages regardless of file size. SQLite's default page size
equals a Swarm chunk, and the file is content-addressed, so what you read
is verifiable against a 32-byte root. There is no database server anywhere.

The same trick runs **in a browser** with no install at all: the demo site
(§7) queried a 41.9 MB database live at 5 Range requests / 20 KB for a
cold point lookup — 23 pages (92 KB) for a whole 4-query session.

Writes are the publisher's job: work on a local file with full SQL, then
ship an immutable snapshot (and optionally advance a feed). Never a SQL
`UPDATE` across the network — see §5.

## 1. Prerequisites

- **Python ≥ 3.11.**
- **A Bee node** — needed for publishing and for live reads. Options:
  - **Swarm Desktop** (easiest): install from
    <https://desktop.ethswarm.org/>, let it start its light node. The API
    is at `http://localhost:1633`.
  - **bee light node**: <https://docs.ethswarm.org/> — run with a funded
    Gnosis wallet.
  - **`bee dev`**: a throwaway local node for experiments; stamps are
    free/fake there.
- **Funds, for publishing only.** Uploads need a postage stamp bought with
  xBZZ from the node's wallet (plus a little xDAI for gas). *Reading a
  published database needs no stamp and no funds at all.*
- No node? The offline demo (§4) exercises the identical code path
  through an in-memory filesystem.

Check the node is up:

```bash
curl -s http://localhost:1633/health
# {"status":"ok","version":"2.8.1-...","apiVersion":"8.1.0"}
```

swarmlite finds the node the same way swarmfs does: an explicit
`api_url=...` / `--api-url` beats `$BEE_API_URL`, which beats the default
`http://localhost:1633`.

## 2. Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install swarmlite            # pulls swarmfs from PyPI automatically
```

or, for development, from a clone:

```bash
git clone https://github.com/petfold/swarmlite
cd swarmlite
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
```

Verify:

```bash
pytest                          # 43 tests, no node needed
python examples/offline_demo.py # the demo of §4, offline
swarmlite --help
```

(Why the venv: modern distros mark the system Python "externally managed"
— PEP 668 — so `pip install` outside a venv is refused.)

## 3. Postage stamps (publishers only)

A stamp is a prepaid batch that funds storage of your chunks for a limited
time. Check what the node has:

```bash
curl -s http://localhost:1633/stamps
```

Look for `"usable": true` **and** `"utilizationRatio"` well below 1 — an
immutable batch at ratio 1.0 is full and further uploads with it fail
(swarmlite/swarmfs will tell you: `full (utilization at 100%)`).

### Letting swarmlite buy one

No usable stamp? `publish` can buy a batch sized for your file from the
node's wallet — it shows the exact cost and asks first:

```bash
swarmlite publish site.db --buy
# batch for 41.9 MB: depth 18, amount 1225476000, lasting ~25 h
#   -> 0.0321 xBZZ from the node's wallet
# buy it? [y/N] y
# buying (waits for on-chain confirmation) ...
# bought batch 645b8538...
# pin:  bzz://<root>/site.db
```

`--ttl` takes `36h`/`7d`/`4w` (default `1d`; the chain enforces a 24 h
minimum, so short TTLs are rounded up). `--yes` skips the prompt for
scripts. The purchase waits (~40 s live) for on-chain confirmation
before uploading. Requires xBZZ (and a little xDAI for gas) in the
node's wallet — check `curl -s http://localhost:1633/wallet`; if it's
empty, fund it or get a stamp from a service such as Beeport and pass
it with `--stamp`.

### Buying a batch by hand

```bash
curl -s -X POST "http://localhost:1633/stamps/<amount>/<depth>"
# → {"batchID": "...", "txHash": "0x..."}
```

Then poll `curl -s http://localhost:1633/stamps/<batchID>` until
`"usable": true` (took ~40 s on Gnosis in our session).

**Choosing `depth`** (capacity): theoretical capacity is
`2^depth × 4 KB`, but an upload fails when any *bucket* overflows, so
leave headroom:

The size→depth table lives in [REFERENCE.md §6](REFERENCE.md#6-stamps-swarmlitestamps).

(The gap between upload size and theoretical capacity there is the
balls-into-buckets effect: chunks
hash into 65 536 buckets by their address, each holding
`2^(depth − 16)`. A chunk landing in a full bucket is refused on an
immutable batch — HTTP 402 `batch is overissued` — so the upload fails,
though the batch and everything already stamped survive; diluting one
depth doubles every bucket and the retry succeeds with the same root.
Measured live: one **42 MB** upload filled a depth-18 batch, which the
model puts at ~6% for plain chunks and ~13% once swarmfs's default
erasure parity is counted.)

Those sizes are what swarmfs's `suggest_depth` accepts at its default
1% overflow risk **for the way swarmlite uploads** (erasure level 2,
unencrypted) — it counts parity and manifest chunks, not just payload
bytes, so a more redundant upload of the same file needs a deeper batch.
`--buy` uses exactly this. Two ways to do better than an estimate:

```python
import swarmfs
from swarmlite.stamps import plan_batch, depth_for_addresses

root, chunks = swarmfs.split(open("site.db", "rb").read())  # needs swarmfs[feeds]
plan = plan_batch(size, ttl, depth=depth_for_addresses(chunks))  # exact, 0% risk
```

and, for a batch you already own, `swarmlite stamps` (below) reports the
true fullest-bucket occupancy rather than a guess.

**Choosing `amount`** (lifetime): the arithmetic is in
[REFERENCE.md §6](REFERENCE.md#6-stamps-swarmlitestamps); the short
version is that validity scales with `amount` and the current chain
price, so quote it with `swarmlite stamps` rather than trusting a
purchase-time estimate.

Worked example from our session: `amount=2000000000`, `depth=19` →
**≈ 0.105 xBZZ**, TTL ≈ 41 hours. For a durable publication you'd pick a
much larger amount (weeks/months) or top up later:

```bash
curl -s -X PATCH "http://localhost:1633/stamps/topup/<batchID>/<addedAmount>"
```

Topping up is additive — it adds to the remaining TTL rather than
restarting it. You rarely need that `curl`, though: see
[Watching and renewing a batch](#watching-and-renewing-a-batch) below.
`docs/runbook-stamp-topup.md` keeps the by-hand procedure, the pricing
arithmetic and the post-topup indexing delay for when you want them.

### Watching and renewing a batch

A published root lives exactly as long as its batch, so this is the
maintenance the whole thing rests on.

```bash
swarmlite stamps                        # what you own, and how long it has
swarmlite stamps --check --min-ttl 7d   # exit 1 when something needs renewing
```

```
batch      depth  life left  fullest bucket status
c931c8a5…     19     40.2 d       4/8 (50%) ok
```

Two numbers decide a publication's fate. **life left** is the node's
`batchTTL`; when it hits zero the chunks stop being paid for. **fullest
bucket** is what bounds the *next* upload — not total capacity — because a
chunk hashing into a full bucket is refused.

`--check` is built for cron: it prints the same table, then exits non-zero
with an actionable line per batch below the threshold. Since an expired
batch cannot be revived, a weekly check is worth more than any amount of
care at purchase time.

```bash
swarmlite stamps topup <batchID> --for 4w      # extend BY four weeks
swarmlite stamps topup <batchID> --to 60d      # extend TO 60 days left
swarmlite stamps topup <batchID> --budget 0.5  # spend at most 0.5 xBZZ
```

Each prints the cost, what it buys, and the resulting life, then asks
before spending (`--yes` to skip, required when stdin isn't a terminal).
It waits out the ~40–50 s the node takes to apply the change, so when it
returns the new life is real. The quoted duration is priced at the
*current* `currentPrice`, which drifts, so re-check rather than trusting
one calculation.

If the fullest bucket is nearly full you need **capacity**, not time:

```bash
swarmlite stamps dilute <batchID> --depth 20   # doubles every bucket
```

Dilution costs only gas, but the same balance now covers twice the chunks,
so it roughly halves the remaining life per depth step — dilute *first*,
then top up, or you pay for time the dilution throws away. `topup` warns
you when a batch is in that state.

**The root you publish outlives its stamp only by luck.** At expiry the
chunks stop being paid for and become the first candidates for eviction —
they aren't deleted at that instant, but the window is unpredictable and
an expired batch cannot be topped up. Treat expiry as loss: for a demo,
hours are fine; for anything real, size the amount accordingly and
monitor `batchTTL`.

## 4. The offline demo (no node, no funds)

```bash
python examples/offline_demo.py
```

Expected output (abridged):

```
building a demo database (100,000 posts) ...
published 134.5 MB to memory://demo.db

point lookup -> 'Post 73123: on sqlite'
  cold: fetched 4 pages (16 KB) in 4 reads of a 134.5 MB file   [0.3 ms]
  warm (same query): fetched 0 pages (0 KB) in 0 reads
latest 5 posts -> indexed top-N: fetched 7 pages (28 KB)
full-text search 'verifiable' -> FTS5: fetched 10 pages (40 KB)

Same code path as bzz:// — swap the URL to go live.
```

That last line is literal: the VFS is transport-agnostic (any fsspec URL),
so `memory://` in tests and `bzz://` in production exercise the same code.

## 5. Publishing a database

One command:

```bash
swarmlite publish site.db --stamp <batchID>
# warning: journal_mode was WAL; switched to DELETE for the artifact
# warning: table 'log' has 80000 rows and no index — remote queries ...
# pin:  bzz://<root>/site.db
```

It works on a **copy** (your file is never touched; a live WAL database
is checkpointed correctly via the backup API) and runs the publisher's
checklist: `journal_mode=DELETE`, `page_size=4096`, `ANALYZE`, `VACUUM`,
`integrity_check`, then uploads inside a swarmfs transaction. Fixable
issues become warnings; a failed integrity check aborts.

For a **stable, updatable URL**, publish into a signed feed — a single
upload advances the feed *and* yields the immutable pin:

```bash
swarmlite publish site.db --feed mysite --signer <private key hex>
# pin:  bzz://<root>/site.db
# feed: bzzf://<owner>/mysite/site.db
```

The signer key can also come from `$SWARMLITE_SIGNER`; the owner address
is derived from it. Requires the feeds extra
(`pip install -e "../swarmfs[feeds]"`). Note: a *freshly published* feed
takes a few minutes to become resolvable on mainnet (§9); the pin URL
works immediately. Republishing to the same feed updates what readers
see; every previous pin keeps working (free snapshots).

Same thing from Python: `swarmlite.publish("site.db", feed="mysite",
signer=key, stamp="<batchID>") -> root`.

**Rule of thumb: if the database will ever change, publish into a feed
from the very first version.** A feed costs nothing extra (the same
single upload advances the feed *and* yields the pin), and the
`bzzf://` URL you hand out is stable from day one. Retrofitting a feed
later means redistributing a new URL to every reader — exactly the
problem feeds exist to solve. Skip the feed only for truly one-shot
datasets, or when readers learn the root some other way (app config, a
registry, a contract).

### Updating a published database (yes, UPDATE works — locally)

The write cycle is: edit the local file with ordinary SQL, republish.
Run live while writing this section:

```bash
# 1. write with any tool — it's just a local SQLite file
sqlite3 demo.db "UPDATE posts SET title = 'Post 12345: revised' WHERE id = 12345"

# 2. republish: the changed content gets a NEW immutable root
swarmlite publish demo.db
# pin:  bzz://00c929ed.../demo.db     (the old root keeps working!)

# 3. both versions now coexist, permanently addressable:
swarmlite query "bzz://8597aa78.../demo.db" "SELECT title FROM posts WHERE id = 12345"
# Post 12345: on page                  <- the old snapshot, untouched
swarmlite query "bzz://00c929ed.../demo.db" "SELECT title FROM posts WHERE id = 12345"
# Post 12345: revised                  <- the new one
```

An `UPDATE` can never produce the *same* `bzz://` URL — the root is the
hash of the content, so changed bytes mean a changed address. That is
what feeds are for: publish with `--feed mysite` each time, and the
stable `bzzf://<owner>/mysite/demo.db` URL always resolves to the
newest root while every older pin remains valid (version history for
free). Re-publishing an *unchanged* file re-derives the identical root
— publishing is idempotent.

Deleting is the same story: you can stop referencing a root, and it
expires with its stamp, but you cannot recall bytes others may have
pinned. Publish accordingly.

### Listing snapshots

With a feed, the version history is not something you have to keep —
the feed *is* the history: every update is a signed (timestamp, root)
pair, permanently addressable. `swarmlite snapshots` walks it (run
live, a feed published twice):

```bash
swarmlite snapshots "bzzf://<owner>/snapshot-test/note.txt" --verify
# 0  2026-07-24 15:26:18 UTC  bzz://c250c588.../note.txt
# 1  2026-07-24 15:26:21 UTC  bzz://a53a9062.../note.txt   <- latest
```

Each line is a fully usable pin — `swarmlite query
"bzz://c250c588.../db"` reads yesterday's data as easily as today's
(while its stamp lives). `--verify` signature-checks every update, so
even an untrusted endpoint cannot slip a forged version into the list;
an update that is not currently retrievable shows as such rather than
breaking the walk. Same thing from Python:
`swarmlite.snapshots("bzzf://<owner>/<topic>") -> [Snapshot(index,
root, timestamp), ...]`, oldest first.

### What the checklist does, and why (manual version)

If you prefer to prepare the file yourself:

```python
import sqlite3

con = sqlite3.connect("site.db")
con.execute("PRAGMA page_size=4096")       # align pages to Swarm chunks
con.execute("PRAGMA journal_mode=DELETE")  # no WAL sidecar in the artifact
# ... create tables, indexes for the queries readers will run, insert data
con.execute("ANALYZE")
con.commit()
con.execute("VACUUM")                      # contiguous layout -> readahead works
con.close()
```

Why it matters: `page_size=4096` makes one page = one chunk; indexes are
what keep remote queries to a handful of pages (an unindexed scan reads
the whole file — over the network); `VACUUM` defragments so readahead is
effective. `swarmlite publish` does all of this for you on a copy.

Timing reference: our 134.5 MB demo file uploaded in **59 s** on a light
node. The root is permanent and immutable — republish after changes and
you get a *new* root; old ones keep working until their stamp expires.

Try the full flow with the demo data: `examples/offline_demo.py` exposes
`build()`, so

```python
import sys; sys.path.insert(0, "examples")
from offline_demo import build
open("demo.db", "wb").write(build())
```

then `swarmlite publish demo.db --stamp <batchID>`.

## 6. Querying a published database

### CLI

```bash
swarmlite query "bzz://<root>/demo.db" \
    "SELECT title FROM posts WHERE id = 73123" --stats
# Post 73123: on sqlite
# fetched 5 pages (20 KB) in 5 reads, of a 134.5 MB file
```

`<root>` is the 64-hex reference printed by `swarmlite publish` (§5) —
substitute your own; the ids above are from the 100 k-row demo build.

(Flags for every command are tabulated in
[REFERENCE.md §7](REFERENCE.md#7-cli); the ones used here are `--stats`
for the page-economy line and `--api-url` when the node isn't on
localhost.)

### Python

```python
import swarmlite

con = swarmlite.connect("bzz://<root>/demo.db")

for title, in con.execute(
        "SELECT title FROM posts ORDER BY ts DESC LIMIT 5"):
    print(title)

print(con.swarmlite_file.stats())
# {'url': ..., 'file_size': 141033472, 'read_count': 9,
#  'pages_fetched': 9, 'bytes_fetched': 36864, 'cached_pages': 9}
```

`connect()` returns an [apsw](https://rogerbinns.github.io/apsw/)
`Connection` — cursors, `executemany`, row tracing all work as usual. It
is strictly read-only: any DML/DDL raises `apsw.ReadOnlyError`. Keyword
arguments beyond `cache_pages` are passed to the fsspec filesystem, e.g.
`swarmlite.connect(url, api_url="http://bee:1633", block_size=65536)`.

The same URL works pinned (`bzz://<root>/…` — reproducible forever) or
through a feed (`bzzf://<owner>/<topic>/…` — always the latest version;
verified live: a feed-published test DB answered a point lookup in 3
page fetches, though freshly published feeds take minutes to become
resolvable, §9). The full URL-form and tuning tables — `cache_pages`,
`block_size`, and the two-cache interplay — moved to
[REFERENCE.md §4](REFERENCE.md#4-reading); the one rule worth
remembering: small blocks for point lookups (the default), big blocks
(`block_size=2**20`) for scans.

## 7. The browser demo (a database behind a static page)

The `js/` package is the browser twin of the Python VFS: a
[wa-sqlite](https://github.com/rhashimoto/wa-sqlite) (SQLite-WASM) VFS
that fetches 4 KB pages with HTTP Range requests straight from a
gateway. **Readers need nothing installed** — no wallet, no node
software, no browser extension: any modern browser opens the page.
(Somebody's Bee node/gateway has to *serve* the content, e.g. your
Swarm Desktop node at `localhost:1633` or a public gateway.)

Everything is vendored (wa-sqlite 1.0.0 Asyncify build, js-sha3 for
topic hashing), so the page, the reader, the wasm engine and the
database publish together under **one immutable root** — the site
carries its own database, all URLs relative.

### Publish the demo site

With a node and a usable stamp (§3):

```bash
python js/demo/publish_site.py --stamp <batchID>
# building a demo database (30,000 posts) ...
# uploading 43.0 MB site ...
# root: 5ac6a781227c7481bbc8da9bc0e9abb87179f8c542c764acdcaa72806d8634da
# site: http://localhost:1633/bzz/<root>/index.html
# db:   http://localhost:1633/bzz/<root>/demo.db
```

Open the `site:` URL. The latest posts load, the search box does
keyword search, and the status line shows the page economy per query.
Measured live (Bee 2.8.1): cold point lookup **5 Range requests /
20 KB**; a whole 4-query session **23 pages (92 KB)** of a 41.9 MB
database. `--rows` scales the demo data; `--api-url` targets another
node.

### The npm package (reader + publisher, no Python)

`js/` is published on npm as [`swarmlite`](https://www.npmjs.com/package/swarmlite)
— the browser/Node reader *plus* a pure-JS publisher, zero runtime
dependencies:

```bash
npm install swarmlite
npx swarmlite publish site.db --feed mysite --signer <key hex>
npx swarmlite query "bzz://<root>/site.db" "SELECT ..." --stats --verify
```

The JS publisher runs the same checklist in memory via wa-sqlite (WAL
inputs are rejected with the one-line local fix), follows the same
money rules (`--buy` shows cost and asks), and produces feed-update
signatures **byte-identical** to the Python implementation's — the
test suite asserts this against fixtures the Python side generates, so
the two publishers are interchangeable mid-feed.

### Use the reader in your own page

Copy `js/src/` and `js/vendor/` next to your page (that's the whole
dependency tree — or `npm install swarmlite` in a bundled app), then:

```js
import { open, resolveFeed } from './src/index.js';

const db = await open('http://localhost:1633/bzz/<root>/site.db');
const rows = await db.query('SELECT title FROM posts WHERE id = ?', [12345]);
console.log(db.stats()); // { readCount, pagesFetched, bytesFetched, fileSize }
```

Any URL served with HTTP Range support works — for the single-root
pattern just use a relative URL (`new URL('site.db', location.href)`,
as `js/demo/app.js` does). For a stable, updatable site, resolve a feed
first — once, at page load (a feed can move mid-session; a database
must not):

```js
const { reference } = await resolveFeed(
  'http://localhost:1633', '<owner-hex>', 'mysite');
const db = await open(`http://localhost:1633/bzz/${reference}/site.db`);
```

Topics are hashed exactly like the Python side, so whatever
`swarmlite publish --feed <topic>` printed resolves as-is.

### Browser-specific notes

- **Search without FTS5:** the vendored wa-sqlite build has no FTS5.
  The demo uses a covering inverted index instead —
  `kw(word, ts, id, PRIMARY KEY(word, ts, id)) WITHOUT ROWID` — so
  "newest N posts matching *word*" resolves inside one index range
  (~15 pages). The ORDER BY column must be **in the key**: without
  `ts` there, the same query cost a point lookup per match (~2,300
  pages, measured). Publisher-side indexing is the whole game.
- **CORS:** page and database under one root on the same gateway is
  same-origin and just works. A page hosted elsewhere needs the
  gateway to allow it (`cors-allowed-origins` in the Bee config).
- **Developer tests** (Node ≥ 18; readers never need Node): offline —

  ```bash
  node js/test/feed.mjs           # feed resolution, against a stubbed gateway
  python - <<'EOF'                # build the demo DB locally
  import sys
  sys.path.insert(0, 'examples')
  from offline_demo import build
  open('/tmp/demo.db', 'wb').write(build(rows=30000))
  EOF
  node js/test/serve.mjs /tmp &   # bundled Range-capable static server
  node js/test/smoke.mjs http://localhost:8977/demo.db
  ```

  The smoke test asserts correctness AND the read budget; point it at
  the live `db:` URL from `publish_site.py` for the gateway version.
- **Verification:** against your local light node, the node itself
  verifies every retrieved chunk — the default fast path is already
  trustworthy there. Reading through an **untrusted gateway** (someone
  else's node, a public gateway), pass `verify: true`:

  ```js
  const db = await open(`${gw}/bzz/${root}/site.db`, { verify: true });
  const { reference } = await resolveFeed(gw, owner, topic, { verify: true });
  ```

  Every byte is then checked against the 32-byte root in the URL: the
  manifest is resolved client-side, pages are fetched chunk-by-chunk
  and BMT-verified, and feed updates get full signature verification —
  a tampering gateway is caught on the first bad chunk, a forged feed
  on the signature. Cost: one request per 4 KB chunk plus verified
  tree/manifest chunks (measured live: point lookup 5 pages via 13
  chunks / 45 KB; roughly 2× the requests of the fast path). What it
  cannot prove: that a feed update is the *latest* one.

## 8. Patterns for applications

- **Search:** build an FTS5 index at publish time; it's just more B-tree
  pages, so `MATCH` queries stay lazy (12 pages in the demo).
- **Pin vs. follow:** applications wanting reproducibility record the
  root they read (`con.swarmlite_url`); dashboards wanting freshness
  follow a feed. `swarmlite snapshots` (§5) lists a feed's whole
  version history when you need to go back.
- **Per-tenant data:** one database file per user/feed — isolation and
  sharding for free; a client opens only its own.
- **Multi-author:** one feed per author; the publisher merges and
  republishes one combined DB (recordstore's `reconcile` is the natural
  merge engine when it is the system of record).
- **What to avoid:** unindexed scans and `count(*)` over big tables
  (whole-file reads), gigantic `ORDER BY` without an index (sorts spill
  to RAM — `temp_store=MEMORY` is forced), and expecting writes: this is
  a read replica by design.

## 9. Troubleshooting

- **`StampError: no usable postage stamp ... full (utilization at 100%)`**
  or **`402 batch is overissued`** — a bucket is full, so the batch can't
  stamp further chunks that hash into it. Nothing already stored is lost.
  Prefer diluting it by one depth
  (`PATCH /stamps/dilute/<batchID>/<depth+1>`), which doubles every
  bucket's capacity and lets the same upload through with the same root;
  it costs gas and halves the remaining TTL, so top up afterwards. Buying
  a new batch (§3) also works but abandons the paid-for life on the old
  one.
- **`ContentLengthError` / `Response payload is not completed` shortly
  after publishing** — seen once in live testing: a large range read was
  truncated by the node right after upload. Retry, and/or use the default
  64 KiB `block_size` (large blocks were the trigger). If reproducible,
  please file an issue.
- **Feed URL 404s (`FileNotFoundError`) right after publishing** — the
  signed feed update takes minutes to push-sync on mainnet before
  lookups find it; the pin URL printed next to it works immediately.
  Seen live: resolvable after ~2–3 minutes. Republishing to an
  *existing* feed doesn't have this gap for readers already following it.
- **Root worked yesterday, 404/timeouts today** — the stamp likely
  expired (`batchTTL` in `/stamps`). Republish, or top up before expiry.
- **`ValueError: swarmlite.connect() needs a URL`** — plain paths are
  rejected on purpose; use `file:///abs/path.db` for local files.
- **`... was not opened through swarmlite.connect()`** — the VFS refuses
  files SQLite tries to open behind your back; always go through
  `connect()`.
- **Database in WAL mode** — published artifacts must be
  `journal_mode=DELETE` (the checklist, §5); WAL needs sidecar files that
  don't exist on Swarm.
- **`pip` refuses to install** — PEP 668; use the venv (§2).

## 10. Limitations (current, honest)

- *Reader connections* are read-only by design (in the browser too):
  a published snapshot is content-addressed, so it cannot change under
  its own root. Writes are not missing — they are the publisher's side
  (§5): full SQL against your local file, then publish a new snapshot
  and advance the feed. Never a SQL `UPDATE` across the network.
- The vendored wa-sqlite build has no FTS5 — use the covering-index
  pattern (§7) for browser search; Python readers get FTS5.
- Fresh feeds take minutes to become resolvable (§9);
  `con.swarmlite_url` records the URL, not yet the resolved root, for
  feed opens.
- Concurrent `connect()` calls to the *same* URL are unsupported
  (different URLs are fine).
- Latency: each cold miss is a round trip; interactive after warm-up,
  never OLTP. Gateway reads should keep verification on (swarmfs
  `verify=True`) — a local light node verifies natively.
