# swarmlite — roadmap

Phased like swarmfs: each phase has an exit criterion; a phase is done when
its criterion is met with tests. Keep this file updated (mark items DONE
with a date).

## v0 — read-only VFS (Python)

Goal: `swarmlite.connect("bzz://<ref>/site.db")` returns a working
read-only `apsw.Connection`.

- [x] `SwarmVFS` / `SwarmVFSFile` (DONE 2026-07-23): `xOpen`, `xRead`,
      `xFileSize`; `xAccess` reports journal/WAL absent; write ops raise
      `apsw.ReadOnlyError`; `SQLITE_IOCAP_IMMUTABLE`; duck-typed file
      object (verified apsw accepts it); `temp_store=MEMORY` forced so
      sorts never open temp files.
- [x] URL resolution via fsspec/swarmfs (DONE 2026-07-23): any fsspec URL;
      `bzzf://` resolves at open time. *Remaining nit: record the
      resolved immutable root (not just the URL) on the connection for
      feed opens — needs a swarmfs introspection hook.*
- [x] LRU page cache (DONE 2026-07-23), contiguous-run fetching;
      `storage_options` (incl. `block_size`) passed through to fsspec.
- [x] Offline tests over `memory://` fsspec (DONE 2026-07-23): correctness
      AND read-budget assertions via `con.swarmlite_file` counters.
- [x] Boundary tests (DONE 2026-07-23): empty DB; `page_size` ≠ 4096;
      cache smaller than file; FTS5; truncated file fails loudly;
      big ORDER BY spills to memory not temp files.
- [x] Offline demo (DONE 2026-07-23): `examples/offline_demo.py` — 134 MB
      database, cold point lookup 4 pages (16 KB), warm 0, FTS5 hit
      10 pages; `swarmlite query --stats` prints the same counters.
- [x] Live demo (DONE 2026-07-23, Bee 2.8.1 light node / Swarm Desktop,
      Gnosis mainnet): published the 134.5 MB demo DB via swarmfs (59 s,
      depth-19 batch), then live queries: point lookup **5 pages / 20 KB
      in 0.02 s** (warm: 0 reads), indexed top-N 9 pages, FTS5 12 pages.

**Exit criterion: MET** — point lookup ≤ 6 pages cold, ≤ 2 warm, asserted
offline by `test_point_lookup_read_budget` (4 cold / 0 warm on 134 MB) and
demonstrated live (5 cold / 0 warm).

Operational learnings from the live run:

- Use `block_size=65536` for interactive/point-lookup use (now the CLI
  default). The fsspec default block is large, so every VFS miss pulled
  ~1 MB through `/bytes` — slow, and misleading next to the page counters.
- Right after an upload, a large range read was truncated mid-stream by
  the node (`ContentLengthError`, 512 KiB of ~1 MiB); retry succeeded and
  small blocks avoid it. Worth reproducing and filing against swarmfs
  (retry-on-truncation) and/or Bee.

## v1 — publisher

Goal: `swarmlite publish site.db --feed <topic>` does the whole
checklist and prints the pin + feed URLs.

- [x] `publish()` library function (DONE 2026-07-23): backup-API copy
      (never mutates the source; checkpoints live WAL), pragma checklist
      (`journal_mode=DELETE`, `page_size=4096`), ANALYZE, `VACUUM`,
      `integrity_check`, upload via `fs.transaction`, returns the root.
- [x] Feed publishing (DONE 2026-07-23): `--feed <topic>` +
      `--signer`/`$SWARMLITE_SIGNER`; uploads through `bzzf://` so ONE
      upload both advances the feed and yields the immutable pin; owner
      derived from the signer; prints both URLs.
- [x] Warnings, not failures (DONE 2026-07-23): WAL switched, page size
      rewritten, large tables with no index.
- [x] CLI (DONE 2026-07-23): `swarmlite publish` wired; `query` existed.
- [x] Round-trip tests (DONE 2026-07-23): offline orchestration tests
      with a fake filesystem + opt-in live tests
      (`SWARMLITE_TEST_BEE/STAMP/SIGNER`). Live pin round-trip passed;
      live feed round-trip passed — **feed reads confirmed live** (first
      time), with the caveat that a freshly published feed takes minutes
      to become resolvable on mainnet (SOC push-sync), so the test polls
      up to `SWARMLITE_TEST_FEED_WAIT` (default 300 s).

**Exit criterion: MET** — with a funded node, `swarmlite publish demo.db`
then `swarmlite query "bzz://<root>/demo.db" ... --stats` round-trips
using only the README quick start.

## v2 — browser (JS/WASM)

Goal: the same lazy-page trick inside SQLite-WASM in a browser.

- [x] `js/` package (DONE 2026-07-24): `SwarmVFS` over wa-sqlite's
      Asyncify build (vendored, 1.0.0 — no COOP/COEP needed), gateway
      Range reads, LRU page cache, contiguous-run fetching capped at
      64 KiB per request, retry-with-backoff on truncated ranges, same
      read counters as the Python VFS. `open(url)` returns
      `{query, stats, close}`. *Decision: chunk-level BMT verification
      deferred — against a local light node the node verifies retrieved
      chunks; client-side verification (for untrusted gateways) moved to
      Later, keccak256 is already vendored for it.*
- [x] Feed resolution in JS (DONE 2026-07-24): `resolveFeed(api, owner,
      topic)` in `js/src/feeds.js` — GET `/feeds/{owner}/{topic}`; Bee
      (2.8.1, `Swarm-Feed-Resolved-Version: v1`) returns the reference
      in the **Etag** header (body streams the resolved content —
      cancelled unread); fallback parses a raw bee-js update payload.
      Topics hash exactly like swarmfs `topic_bytes` (keccak256 via
      vendored js-sha3; raw 64-hex passes through). Offline unit tests
      (`js/test/feed.mjs`, stubbed fetch) + verified live: resolves to
      the same root swarmfs published. Resolution happens once, before
      `open()` — a feed can move mid-session, a database must not.
- [x] Demo static site (DONE 2026-07-24): `js/demo/` — HTML + reader +
      wasm + 41.9 MB `demo.db` (30 k posts) all under ONE immutable
      root, published by `js/demo/publish_site.py`. Keyword search via a
      covering inverted index `kw(word, ts, id)` (the vendored wa-sqlite
      has no FTS5; `ts` in the key matters — without it, top-N-by-time
      forced a point lookup per match: ~2 300 pages instead of ~15).
- [x] Smoke test (DONE 2026-07-24): `js/test/smoke.mjs` asserts
      correctness AND the read budget, offline (`js/test/serve.mjs`, a
      Range-capable static server — no Bee node) and live. Live against
      Bee 2.8.1: cold point lookup **5 reads / 20 KB**, warm 0; whole
      4-query session 23 pages / 92 KB of a 41.9 MB database.

**Exit criterion: MET** (measured via the Node smoke test against the
live gateway; all site assets serve with correct MIME types incl.
`application/wasm` — final in-browser click-through still to be
eyeballed). Demo root (2026-07-24):
`5ac6a781227c7481bbc8da9bc0e9abb87179f8c542c764acdcaa72806d8634da`.

## Later / opportunistic

- Client-side verification in JS for untrusted gateways: BMT/chunk
  inclusion per page, SOC signature check in `resolveFeed` (mirror
  swarmfs `verify_soc`). keccak256 already vendored (js-sha3).
- Readahead tuning; bundling hot top-level pages into one prefetch.
- Cookbook doc: DuckDB-WASM + Parquet over swarmfs (works today, zero new
  code) — the analytics flavour.
- recordstore → `site.db` materialization example (datacat pattern).
- Multi-author example: per-author feeds, publisher merges via
  recordstore `reconcile`.
- Optional: page-level proof serving for gateway readers (BMT inclusion
  per page) if swarmfs doesn't already cover the need.
