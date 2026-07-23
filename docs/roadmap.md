# swarmlite — roadmap

Phased like swarmfs: each phase has an exit criterion; a phase is done when
its criterion is met with tests. Keep this file updated (mark items DONE
with a date).

## v0 — read-only VFS (Python)

Goal: `swarmlite.connect("bzz://<ref>/site.db")` returns a working
read-only `apsw.Connection`.

- [ ] `SwarmVFS` / `SwarmVFSFile` (apsw subclasses): `xOpen`, `xRead`,
      `xFileSize`; `xAccess` reports journal/WAL absent; write ops raise
      `apsw.ReadOnlyError`.
- [ ] URL resolution via fsspec/swarmfs: `bzz://` (pin) and `bzzf://`
      (feed → latest root at open time; record which root was opened on
      the connection object).
- [ ] LRU page cache keyed by `(root, offset)`; swarmfs `block_size`
      passthrough.
- [ ] Offline tests over `memory://` fsspec: correctness of queries AND a
      read-budget assertion (spy counts fetches; point lookup ≤ N reads).
- [ ] Boundary tests: empty DB; `page_size` ≠ 4096; file bigger than
      cache; FTS5 query; truncated file fails loudly.
- [ ] Demo: publish a sample DB by hand (swarmfs), query it via
      `swarmlite.connect` against a live Bee node (opt-in env vars).

**Exit criterion:** on a ≥1 GB synthetic DB served through the VFS, a
primary-key point lookup completes while fetching ≤ 6 pages after a cold
start, ≤ 2 after warm-up — asserted by the read-budget test offline and
demonstrated once against a live node.

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
