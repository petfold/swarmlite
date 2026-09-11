# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

Back-filled 2026-09-11 from the git history and from the GitHub Release notes
that had served as the only record until then (0.2.1 keeps its original
wording). 0.1.0 and 0.2.0 had no notes beyond a compare link, so their entries
are summarised from their commits.

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
