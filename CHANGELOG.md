# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

Back-filled 2026-09-11 from the git history and from the GitHub Release notes
that had served as the only record until then (0.2.1 keeps its original
wording). 0.1.0 and 0.2.0 had no notes beyond a compare link, so their entries
are summarised from their commits.

## [Unreleased]

### Added

- **npm package: Freedom Browser support.** In Freedom, `open()` reads
  chunk by chunk through the injected `window.swarm` provider
  (`swarm_readChunk`), the only route a page there has to the node, and
  still BMT-verifies every chunk client-side. It accepts the page's own
  `bzz://<root>/<path>` URLs, `swarm: null` opts out, and the new
  `transport` field reports which path was taken.
- **npm package: parallel verified reads.** The chunks a read needs are
  fetched concurrently (at most 8 in flight, with duplicate requests
  merged), and the tree walk remembers each subtree's child span instead
  of fetching a first child to learn it. Together these cut the demo's
  `window.swarm` session from 34 to 30 chunk reads. The new `readahead`
  option (default 0) widens a page miss into its neighbours, trading
  extra reads for fewer round trips.

### Fixed

- **npm package: overlapping queries.** A query started while another was
  waiting on a page read (a search box firing on each keystroke, say) made
  SQLite read the wrong pages: "database disk image is malformed", or a
  crash inside wasm. wa-sqlite's async build can only pause one call at a
  time, across every open database, so `open()`, `query()` and `close()`
  now queue. It showed up in Freedom, where a search through
  `window.swarm` takes seconds; over HTTP the window was just narrower.
  The demo now runs only the newest search term and shows query errors.

### Changed

- **npm package:** the browser reader and the JS publisher now run SQLite
  3.53.0 (vendored wa-sqlite 1.1.2, from 1.0.0 / SQLite 3.44.0). wa-sqlite
  1.0.0 is still the newest release on npm, so the files now come from the
  GitHub tag. Breaking for code that builds a `SwarmVFS` itself: the
  constructor is now `new SwarmVFS(name, module, options)`, following
  wa-sqlite's new `FacadeVFS` base class. `open()` callers are unaffected.

## [0.3.1] — 2026-08-04

### Added

- **Encrypted publishing:** `--encrypt` / `publish(encrypt=True)`.

### Changed

- The swarmfs floor moves to 0.7.1, and the docs point at the pinned
  references.

### Documentation

- `REFERENCE.md` — definition-first, and pinned to the code by tests.

## [0.3.0] — 2026-07-29

### Added

- **Stamp lifecycle UX:** `swarmlite stamps` (list / check / topup / dilute).
- PyPI keywords.

## [0.2.1] — 2026-07-28

Metadata only, no code changes.

Completes `[project.urls]` — Repository and Issues alongside Homepage — so the
PyPI sidebar links back to the repository and issue tracker.

### Changed

- Packaging and CI normalised across the stack: publishing on a `v*` tag push,
  gated on the test suite, running in the `pypi` environment, with
  `release.yml` renamed to `publish.yml`. CI, PyPI and license badges added to
  the README.

## [0.2.0] — 2026-07-25

### Added

- **Browser reader** (v2): a wa-sqlite VFS, feed resolution and a demo site —
  click-through confirmed in Brave.
- **Client-side verification for untrusted gateways** (v2.1).
- **npm package `swarmlite`** (v2.2): a pure-JS publisher plus CLI, verified
  end-to-end from the registry.
- `--buy` purchases a postage batch sized for the file.
- **Snapshots:** list a feed's version history, with feed-first guidance and
  real depth tiers.
- Cookbooks: Postgres/MySQL read replica, and DuckDB + Parquet analytics.
- A WordPress exporter demo: WXR to a searchable blog under one Swarm root.

### Changed

- **Stamp mechanics moved down to swarmfs**; swarmlite keeps only TTL parsing
  and the UX around it.
- Token-less PyPI publishing via Trusted Publishers; CI covers the Python suite
  (with a swarmfs sibling checkout) and the Node suites.
- Pin-only publishes print a one-line tip pointing at `--feed`.
- CLI errors are one-liners rather than tracebacks, and copy-pasted
  placeholders are caught.

### Fixed

- False missing-index warnings on publish; `--name` defaults to the source
  file name.
- The fresh-clone demo path: a runnable quick start with actionable errors.

## [0.1.0] — 2026-07-23

First release: a read-only SQLite VFS over Swarm plus a publish helper.

- **v0:** read-only VFS with an LRU page cache and read counters.
- **v1 publisher:** checklist, transactional upload, signed feed publishing.
- Default 64 KiB transport blocks for the CLI; live-node demo completed.
- User Guide covering the full path — node, stamps, publish, query.
