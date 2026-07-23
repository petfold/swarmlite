# swarmlite — User Guide

Everything needed to go from an empty machine to running lazy, verifiable
`SELECT` against a SQLite database published on Ethereum Swarm.

All examples in this guide were run against a real Bee 2.8.1 light node on
Gnosis mainnet (via Swarm Desktop) on 2026-07-23; the numbers shown are
from that session.

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
git clone https://github.com/petfold/swarmlite
cd swarmlite
python3 -m venv .venv && source .venv/bin/activate

# until swarmfs is on PyPI, install it from a local checkout first:
git clone https://github.com/petfold/swarmfs ../swarmfs
pip install -e ../swarmfs -e ".[test]"
```

Verify:

```bash
pytest                          # 18 tests, no node needed
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

### Buying a batch

```bash
curl -s -X POST "http://localhost:1633/stamps/<amount>/<depth>"
# → {"batchID": "...", "txHash": "0x..."}
```

Then poll `curl -s http://localhost:1633/stamps/<batchID>` until
`"usable": true` (took ~40 s on Gnosis in our session).

**Choosing `depth`** (capacity): theoretical capacity is
`2^depth × 4 KB`, but immutable batches fail when any *bucket* overflows,
so leave headroom:

| upload size | depth | theoretical capacity |
|-------------|-------|----------------------|
| ≤ 50 MB     | 18    | 1 GB                 |
| ≤ 300 MB    | 19    | 2 GB                 |
| ≤ 1 GB      | 20    | 4 GB                 |

**Choosing `amount`** (lifetime): validity in seconds is roughly
`amount / currentPrice × 5` (Gnosis block time), with `currentPrice` from
`curl -s http://localhost:1633/chainstate`. Cost in xBZZ is
`amount × 2^depth / 10^16`.

Worked example from our session: `amount=2000000000`, `depth=19` →
**≈ 0.105 xBZZ**, TTL ≈ 41 hours. For a durable publication you'd pick a
much larger amount (weeks/months) or top up later:

```bash
curl -s -X PATCH "http://localhost:1633/stamps/topup/<batchID>/<addedAmount>"
```

**The root you publish dies with its stamp.** For a demo, hours are fine;
for anything real, size the amount accordingly and monitor `batchTTL`.

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
takes a few minutes to become resolvable on mainnet (§8); the pin URL
works immediately. Republishing to the same feed updates what readers
see; every previous pin keeps working (free snapshots).

Same thing from Python: `swarmlite.publish("site.db", feed="mysite",
signer=key, stamp="<batchID>") -> root`.

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

then `swarmlite publish demo.db --name demo.db --stamp <batchID>`.

## 6. Querying a published database

### CLI

```bash
swarmlite query "bzz://<root>/demo.db" \
    "SELECT title FROM posts WHERE id = 73123" --stats
# Post 73123: on sqlite
# fetched 5 pages (20 KB) in 5 reads, of a 134.5 MB file
```

Options: `--api-url` (else `$BEE_API_URL`, else localhost), `--stats`
(page-economy line on stderr), `--block-size` (transport readahead,
default 64 KiB — see below).

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

URL forms:

- `bzz://<root>/site.db` — an immutable, pinned version. Reproducible
  forever (while stamped).
- `bzzf://<owner>/<topic>/<name>` — a feed, resolved to the latest
  published version at open time (verified live: a feed-published test DB
  answered a point lookup in 3 page fetches). Freshly published feeds
  take minutes to become resolvable (§8); pin roots when you need
  reproducibility.
- `file://…`, `memory://…` — local/testing, same code path.

### Tuning: `block_size` and the page cache

Two caches cooperate: swarmlite's LRU of 4 KB pages (`cache_pages`,
default 1024 ≈ 4 MB) and the transport's readahead block
(`block_size`). Each VFS cache miss pulls one block:

- **Point lookups / OLTP-ish reads:** small blocks — the 64 KiB default.
- **Scans / analytics:** larger blocks (e.g. `block_size=2**20`) fetch
  the file in fewer round trips.

The `--stats` counters report what the VFS requested (pages); the network
may transfer more (whole blocks). If the numbers matter, measure both.

## 7. Patterns for applications

- **Search:** build an FTS5 index at publish time; it's just more B-tree
  pages, so `MATCH` queries stay lazy (12 pages in the demo).
- **Pin vs. follow:** applications wanting reproducibility record the
  root they read (`con.swarmlite_url`); dashboards wanting freshness
  follow a feed.
- **Per-tenant data:** one database file per user/feed — isolation and
  sharding for free; a client opens only its own.
- **Multi-author:** one feed per author; the publisher merges and
  republishes one combined DB (recordstore's `reconcile` is the natural
  merge engine when it is the system of record).
- **What to avoid:** unindexed scans and `count(*)` over big tables
  (whole-file reads), gigantic `ORDER BY` without an index (sorts spill
  to RAM — `temp_store=MEMORY` is forced), and expecting writes: this is
  a read replica by design.

## 8. Troubleshooting

- **`StampError: no usable postage stamp ... full (utilization at 100%)`**
  — the batch is exhausted (immutable batches can't be reused once any
  bucket fills). Buy a new one (§3).
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

## 9. Limitations (current, honest)

- Read-only by design; the browser/WASM reader (v2) is not built yet —
  see `docs/roadmap.md`.
- Fresh feeds take minutes to become resolvable (§8);
  `con.swarmlite_url` records the URL, not yet the resolved root, for
  feed opens.
- Concurrent `connect()` calls to the *same* URL are unsupported
  (different URLs are fine).
- Latency: each cold miss is a round trip; interactive after warm-up,
  never OLTP. Gateway reads should keep verification on (swarmfs
  `verify=True`) — a local light node verifies natively.
