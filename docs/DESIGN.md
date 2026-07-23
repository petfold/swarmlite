# swarmlite — design

Distilled from the "Databases on Swarm" talk (Balaton, July 2026), Part II
and the six "thin adaptor" slides, plus the source design conversations.
This is the target state; the roadmap tracks what exists.

## 1. Problem and shape of the solution

Conventional developers expect a SQL endpoint. Building a SQL *engine* over
Swarm-native structures is a multi-year tarpit (Dolt spent years building
MySQL semantics over prolly trees); but "can only talk SQL" almost never
means SQL as the storage engine's native protocol — it means the tools,
ORMs and habits expect SQL *somewhere*. So serve SQL at the **projection
layer**:

1. **Snapshot artifacts** — publish the dataset as SQLite/Parquet at each
   commit. Works today via swarmfs (DuckDB over `bzz://` Parquet).
2. **Lazy SQLite over Swarm** — *this project*: a read-only VFS fetching
   pages on demand.
3. **Live connectors** (virtual tables / FDW) — possible, chatty, ranked
   third; out of scope here.

Writes follow the pattern The Graph proved: the decentralised system is the
system of record; an indexer/publisher materializes; apps query SQL
locally; writes go through the application's API. The developer's Postgres
is not replaced but **demoted** — from system of record to disposable cache.

## 2. The read path (v0)

```
application          plain SELECT
SQLite engine        unmodified (apsw)
bzz-VFS shim         xOpen / xRead / xFileSize
swarmfs file object  seek(off); read(n)  [block cache, readahead]
Bee                  /bytes, HTTP range reads
```

- **VFS surface.** apsw lets a pure-Python class register as a VFS.
  Read-only needs: `xOpen` (resolve URL → swarmfs file object + size),
  `xRead(amount, offset)`, `xFileSize`. `xAccess` reports journal/WAL
  files absent. `xLock`/`xUnlock` are no-ops (file is immutable).
  Write methods raise `apsw.ReadOnlyError`.
- **Page = chunk.** SQLite default page is 4096 bytes; Swarm chunks are
  4 KB. With `page_size=4096` and a VACUUMed file, one page read maps onto
  one chunk-aligned range. Misaligned files still work — they just fetch
  slightly more.
- **Caching.** Two layers: swarmfs's block cache (readahead, `block_size`
  tunable) and an LRU page cache in the VFS keyed by `(root, offset)`.
  The B-tree's top levels are shared by all queries; after warm-up, cold
  point reads cost 1–2 round trips.
- **Verification.** Via a local light node, chunk verification is native.
  Via a public gateway, swarmfs verifies fetched chunks client-side
  against BMT addresses (`allow_gateway=True` + `verify=True`). swarmlite
  exposes/propagates these options; it does not implement verification
  itself.
- **URL forms.** `bzz://<ref>/site.db` pins an immutable version;
  `bzzf://<owner>/<topic>` resolves a feed to the latest published root
  (readers pin for reproducibility, follow for freshness). Resolution is
  swarmfs's job.

### Cost model (why lazy SELECT is cheap)

| query on a multi-GB file      | pages touched          |
|-------------------------------|------------------------|
| point lookup by primary key   | ~3–4 (16 KB)           |
| top-10 by an indexed column   | ~10–15                 |
| full-text hit (FTS5 index)    | tens                   |
| unindexed `count(*)`          | every page — don't     |

FTS5 works over the lazy VFS unmodified: the index is just more B-tree
pages.

## 3. The write path: the publisher (v1)

The publisher works on a *local* SQLite file — full SQL, real transactions
— then ships an immutable snapshot:

```
PRAGMA page_size=4096;         -- align pages to chunks
PRAGMA journal_mode=DELETE;    -- no WAL sidecar in the artifact
-- caller creates indexes, runs ANALYZE
VACUUM;                        -- contiguous layout -> readahead works
PRAGMA integrity_check;
```

then `fs.transaction:` upload via swarmfs → `root = fs.latest(...)` →
optionally `bzzf://` feed update (`ffs.pipe_file(feed_url, root)`).

Every publish is a permanent snapshot (old roots stay valid). If
recordstore is the system of record, `site.db` is materialized from each
commit — the datacat pattern: authoritative store, disposable SQL
projection. The publish step is where that materialization plugs in, but
swarmlite itself is agnostic about where the local DB came from.

## 4. The browser phase (v2)

SQLite compiles to WASM (sql.js, official builds). The same VFS idea in JS
fetches pages via a gateway's range reads — or natively in a Swarm-aware
browser. Precedent: phiresky's `sql.js-httpvfs` (HTTP ranges against
static hosting) — transplants almost perfectly; the additions are chunk
verification and feed resolution. Resulting shape: HTML + JS served from
an immutable Swarm root, data queried client-side from a published
database — a multi-GB dataset behind a static site, no origin server.

Kept in `js/` as a separate build when started; the Python package does
not depend on it.

## 5. Web-app patterns this enables

- **Read path (the 95%)**: posts/catalog/docs in `site.db`, rendered
  client-side; search via FTS5.
- **Write path**: forms post to the application's endpoint (or signed
  message / contract event) → publisher applies locally → republishes →
  feed advances. Never a SQL `UPDATE` across the network.
- **Multi-author**: one feed per author; publisher merges (recordstore
  `reconcile`) and materializes one site DB.
- **Per-tenant data**: one DB file per user/feed — isolation and
  "sharding" for free.
- **WordPress, translated**: theme → static bundle; wp_posts → `site.db`;
  admin panel → publisher tool; comments → per-author feeds folded in at
  republish.

## 6. Costs and caveats (be honest in docs and errors)

- **Latency**: each cold page is a round trip. Mitigations: page cache,
  readahead over VACUUMed layout, bundling hot top levels (later).
  Interactive after warm-up; never OLTP.
- **Consistency**: feeds are eventually consistent, last-write-wins
  (~15 s TTL cache in swarmfs). Pin a root for reproducibility.
- **Trust**: gateway reads must be verified client-side; local light node
  is the recommended setup.
- **Writes**: by design not transparent; the publisher pattern is the
  write API.

## 7. Relation to the rest of the stack

swarmfs (files) → **swarmlite** (SQL projection) sits beside recordstore
(records/versions) and POT (authenticated indexes). swarmlite needs none
of recordstore/POT to be useful; it becomes more valuable when they feed
it (materialized projections of canonical roots). The DuckDB/Parquet
flavour of the same trick needs nothing new at all and should be
documented as a cookbook page, not code.
