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

**Exit criterion: MET** — measured via the Node smoke test against the
live gateway, and confirmed in a real browser (Brave, 2026-07-24):
posts load, keyword search works, the status line reports per-query
page counts. Live demo root (republished 2026-07-25 on a 28-day
batch, now shipping the verified reader):
`d1d54686def7f40714d093f2d8c4a2739d6d905d16b834b41f04b7128be8690b`.

## v2.1 — client-side verification (JS)

Goal: trustless reads through an UNTRUSTED gateway — every byte checked
against the 32-byte root in the URL.

- [x] BMT + verified joiner + Mantaray lookup (DONE 2026-07-24):
      `js/src/verify.js` (chunk addressing, verifying chunk store over
      `/chunks`, range-descending tree reader) and `js/src/mantaray.js`
      (manifest path resolution) — read-only ports of swarmfs
      `bmt.py`/`join.py`/`mantaray/`, held byte-compatible by fixtures
      that swarmfs GENERATES (`js/test/make_fixtures.py` →
      `js/test/fixtures/verified.json`).
- [x] `open(url, {verify: true})` (DONE 2026-07-24): resolves the
      manifest client-side and reads pages chunk-by-chunk, each
      BMT-verified. Live: point lookup 5 pages via 13 verified chunks
      (45 KB) of 41.9 MB; a 4-query session 19 pages / 31 chunks /
      113 KB. Cost model: one request per 4 KB chunk + tree/manifest
      overhead (~2× the fast path's requests) — use against public
      gateways; a local light node verifies natively, keep the default
      Range path there.
- [x] `resolveFeed(..., {verify: true})` (DONE 2026-07-24): takes only
      the update *index* from the gateway, derives the SOC address
      itself, fetches the chunk and verifies address derivation +
      owner-signature recovery (vendored @noble/secp256k1 2.3.0).
      Latestness remains unprovable client-side (as in swarmfs).
      Offline forged-signature/wrong-owner tests + live round-trip.
- [x] Erasure-coding discovery (2026-07-24): redundancy-uploaded files
      have intermediate chunks with FEWER than 128 data refs (seen
      live: 107 data + 21 parity). The JS walker infers the per-child
      unit from the first child's own span instead of 128^k position
      math. swarmfs `join.py` had the same 128-fanout assumption —
      fix ported back (swarmfs commit `dd7def6`, DONE 2026-07-24,
      verified live against the redundancy-uploaded demo file).

## v2.2 — JS-first packaging (npm + pure-JS publisher)

Goal: a full-stack JS developer never needs Python (see
`docs/ecosystem-strategy-qa.md` for the strategy behind this).

- [x] Pure-JS publisher (DONE 2026-07-24): `js/src/prepare.js` runs the
      whole checklist in memory on wa-sqlite via a writable MemVFS
      (WAL inputs rejected with the one-line local fix, since the
      in-memory VFS deliberately has no shared-memory support);
      `js/src/publisher.js` does stamps (auto-select/plan/buy — same
      money rules), POST /bzz upload, and signed feed updates whose
      SOCs are **byte-identical** to swarmfs's (RFC 6979 determinism,
      asserted against Python-generated fixtures). Signing uses noble's
      signAsync over built-in WebCrypto — zero new dependencies.
- [x] npm package `swarmlite` + CLI (DONE 2026-07-24): package.json
      with exports/bin/files (25 files, ~479 kB tarball, all vendored
      deps with LICENSE+PROVENANCE), `js/README.md` as the npm front
      page, and `npx swarmlite publish|query` mirroring the Python CLI
      (stdout=root, stderr=human lines, placeholder detection,
      --verify). Live round-trip: JS publish (auto stamp) → JS verified
      query → Python query of the same root. *Publishing to the
      registry itself is a human step: `cd js && npm publish` (name is
      free; needs npm login).*

**Exit criterion: MET** — a JS developer can `npm i swarmlite` (once
published) and read, verify, and publish without Python installed.

## v2.3 — stamp lifecycle UX (2026-07-29)

Driven by a real need: the searchable-blog demo had 24 days of postage left and no
way to extend it except raw `curl`. A published root lives exactly as long as its
batch, and an expired batch cannot be revived, so renewal belongs in the publisher.

- [x] `swarmlite stamps` — every batch with the two numbers that decide a
      publication's fate: life left (`batchTTL`, in days) and fullest-bucket
      occupancy (what actually bounds the next upload). Sorted by urgency.
- [x] `swarmlite stamps --check [--min-ttl 7d]` — same table, non-zero exit and an
      actionable line per batch below the threshold. Cron-friendly; prevention beats
      the alternative, because nothing recovers an expired batch.
- [x] `swarmlite stamps topup ID --for 4w | --to 60d | --budget 0.5` — the three
      questions publishers actually ask. Prints cost/effect, surfaces the
      dilute-first warning, confirms before spending (`--yes` for non-interactive,
      refused outright when stdin isn't a tty), and waits out the ~40–50 s the node
      takes to apply it.
- [x] `swarmlite stamps dilute ID --depth N` — for capacity rather than time, priced
      in the currency it costs (remaining TTL, roughly halved per depth step).
- [x] `plan_batch` forwards `redundancy`/`encrypted`/`risk`/`depth`, so sizing can be
      exact (`depth_for_addresses(split(data)[1])`, 0% overflow risk) instead of an
      estimate from bytes. Defaults match how swarmlite uploads (erasure level 2).
- [x] Requires **swarmfs >= 0.4.0** (floor raised from 0.1.0, which would have
      resolved to a swarmfs without any of these).

Validated live against Bee 2.8.1: `stamps` and `--check` against the real demo batch
(40.2 d, 4/8 buckets), `topup --for 1h` planning correctly (0.0026 xBZZ) and refusing
to spend without a confirmation, and a real +1 h renewal driven through this repo's
sync bridge — which matters because swarmfs's own tests only ever drive it with
`asyncio.run`, never fsspec's shared background loop.

**Still open**: `publish()` records no batch↔root mapping, so `stamps` can list
batches but not name the publication each keeps alive. Bee has no reverse index, so
only swarmlite can supply this; where the record lives (local sidecar vs. published
manifest) is undecided.

## Later / opportunistic

- [x] Cookbook: Postgres/MySQL read replica (DONE 2026-07-25,
      `docs/cookbook-read-replica.md`) — DB-API materializer verified
      end-to-end: 50 k-row source → 3.0 MB replica → published →
      queried at 10 pages / 40 KB.
- [x] Cookbook: DuckDB + Parquet analytics (DONE 2026-07-25,
      `docs/cookbook-duckdb-parquet.md`) — 1 M rows published live
      (5.0 MB, 20 row groups): count(*) = 16 KB, pruned week filter =
      214 KB, full columnar aggregate = 3.9 MB.
- [x] WordPress exporter demo (DONE 2026-07-25,
      `examples/wordpress/`): standard WXR export → `blog.db` (posts +
      body-word covering index) + a static theme where the index,
      search, and every post page are SELECTs against the one published
      file. Sample-blog generator + offline tests (pages/drafts
      skipped, read budgets); published live — 48 posts, whole
      browse-and-read session 16 pages / 64 KB. `--feed` keeps one URL
      across re-exports. Follow-up out of scope: mirroring media into
      the manifest.
- Mongo-flavored query facade over recordstore, if demand appears.
- Readahead tuning; bundling hot top-level pages into one prefetch.
- recordstore → `site.db` materialization example (datacat pattern).
- Multi-author example: per-author feeds, publisher merges via
  recordstore `reconcile`.
- Optional: page-level proof serving for gateway readers (BMT inclusion
  per page) if swarmfs doesn't already cover the need.
