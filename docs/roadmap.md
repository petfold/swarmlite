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

Goal: `swarmlite publish site.db --feed site/root` does the whole
checklist and prints the pin + feed URLs.

- [ ] `publish()` library function: pragma checklist
      (`page_size=4096`, `journal_mode=DELETE`), `VACUUM`,
      `integrity_check`, upload via `fs.transaction`, return root.
- [ ] Optional feed advance (`--feed`, `signer` from env/keystore);
      prints immutable root AND feed URL.
- [ ] Warnings, not failures, for: missing indexes on large tables,
      non-4096 page size it had to rewrite, WAL mode it had to switch.
- [ ] CLI (`swarmlite publish`, `swarmlite query <url> <sql>` for smoke
      tests).
- [ ] Round-trip integration test (opt-in): publish → connect → query →
      republish → feed follows.

**Exit criterion:** a fresh machine with a funded Bee node can
`pip install`, publish the example DB, and query it back with only the
README quick start.

## v2 — browser (JS/WASM)

Goal: the same lazy-page trick inside SQLite-WASM in a browser.

- [ ] `js/` package: page-fetching VFS for SQLite-WASM over gateway range
      reads; chunk verification included.
- [ ] Feed resolution in JS (or via a tiny resolver endpoint — decide).
- [ ] Demo static site: HTML+JS+`site.db` all under one immutable root,
      FTS5 search box. ("A multi-GB dataset behind a static site.")

**Exit criterion:** the demo site loads from a gateway and serves search
queries with network transfer proportional to pages touched, not DB size.

## Later / opportunistic

- Readahead tuning; bundling hot top-level pages into one prefetch.
- Cookbook doc: DuckDB-WASM + Parquet over swarmfs (works today, zero new
  code) — the analytics flavour.
- recordstore → `site.db` materialization example (datacat pattern).
- Multi-author example: per-author feeds, publisher merges via
  recordstore `reconcile`.
- Optional: page-level proof serving for gateway readers (BMT inclusion
  per page) if swarmfs doesn't already cover the need.
