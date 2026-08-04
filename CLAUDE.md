# swarmlite — CLAUDE.md

Persistent brief for Claude Code. Read this first, every session. Keep it
updated as decisions change; treat it as the source of truth over any single
conversation.

## What this is

`swarmlite` is a read-only SQLite **VFS** over Ethereum Swarm, plus a
**publish** helper. It lets anyone run `SELECT` against a SQLite file
published on Swarm, fetching only the B-tree pages the query touches
(4 KB page = 4 KB Swarm chunk), each fetch verifiable against the file's
content address. Writes happen locally at the publisher, which uploads an
immutable snapshot and advances a feed. Pitch: *"verifiable serverless
SQLite hosting."*

Full design: `docs/DESIGN.md`. Build order and exit criteria:
`docs/roadmap.md`. The design was worked out in the "Databases on Swarm"
talk (Balaton, July 2026) and its source conversations.

## Primary audience (drives priorities)

Web developers who "can only talk SQL": they get a SQL endpoint without a
server. The read path (Python first, browser later) matters most; the
publisher CLI second; everything else after.

## Names and identifiers (decided)

- Package / import name: `swarmlite`. CLI entry point: `swarmlite`.
- Main entry: `swarmlite.connect(url) -> apsw.Connection` (read-only).
- VFS classes: `SwarmVFS`, `SwarmVFSFile` (in `swarmlite/vfs.py`).
- Publisher: `swarmlite.publish()` (in `swarmlite/publish.py`) and
  `swarmlite publish` CLI.
- URLs: `bzz://<ref>/path.db` (immutable pin) and
  `bzzf://<owner>/<topic>` (feed = latest). Resolution and fetching are
  delegated to swarmfs — do not reimplement Bee HTTP here.

## Hard constraints (do not violate)

1. **Read-only VFS.** Never implement `xWrite`/`xTruncate`/locking as if
   writes could work over the network. Opening for write raises. The
   publisher pattern is the only write path.
2. **Delegate transport to swarmfs.** All Bee access goes through
   `fsspec.filesystem("bzz"/"bzzf")` file objects (`seek`/`read`,
   `block_size`, caching). If swarmfs lacks a needed capability, extend
   swarmfs (separate repo) rather than duplicating transport logic here.
   swarmfs's supported API is its test-pinned `docs/REFERENCE.md` — the
   `swarmfs.feeds` helpers this repo consumes (snapshots.py, publish.py)
   are documented there as of 2026-08-04, §9 Feeds, with swarmlite named
   as the consumer. The floor is `swarmfs>=0.7.1`: publish() runs inside
   `with fs.transaction:`, and 0.7.1 carries the fix for the
   transaction-exit crash on fsspec < 2024.3.0 (distro-fsspec vintages).
3. **apsw, not stdlib sqlite3.** stdlib `sqlite3` cannot register a Python
   VFS; apsw can (`apsw.VFS` / `apsw.VFSFile` subclasses).
4. **Journal/WAL files do not exist remotely.** The VFS must report them
   absent (`xAccess` false, open of `-journal`/`-wal` behaves as SQLite
   expects for a read-only, unchanging database). Published files must not
   be in WAL mode — the publisher enforces `journal_mode=DELETE` (or
   `TRUNCATE`) before upload.
5. **Determinism for tests.** Offline tests must run with no Bee node,
   using fsspec's `memory://` filesystem (or a local-file fsspec instance)
   behind the same VFS code path. Live-node tests are opt-in via env vars
   (`SWARMLITE_TEST_BEE`, `SWARMLITE_TEST_STAMP`), mirroring swarmfs
   conventions.

## The core design facts (why this works)

- SQLite I/O is pluggable: a read-only VFS needs `xOpen`, `xRead`,
  `xFileSize` (plus trivial `xAccess`/`xLock` no-ops).
- A query walks a B-tree: point lookup on a multi-GB file touches ~3–4
  pages; top-N on an indexed column ~10–15; FTS5 hits tens. Unindexed
  scans touch everything — that's a publisher problem (add indexes), not
  ours.
- Page size 4096 aligns pages to Swarm chunks; the publisher enforces
  `PRAGMA page_size=4096` + `VACUUM` (contiguous layout → readahead works).
- The top B-tree levels are shared by every query — cache pages (LRU keyed
  by (root, offset)), and after warm-up cold reads drop to 1–2 round trips.

## Publisher checklist (encode in `publish()`, keep in sync with docs)

`PRAGMA page_size=4096` → `PRAGMA journal_mode=DELETE` → create indexes /
`ANALYZE` (caller's job, but warn if obviously missing) → `VACUUM` →
integrity check → upload via `fs.transaction` → optionally update
`bzzf://` feed with the new root. Print both the immutable root (pin) and
the feed URL.

## Phases (details in docs/roadmap.md)

- **v0 (DONE 2026-07-23)** — Python read-only VFS over swarmfs;
  `connect()`; offline tests; demonstrated against a real published DB.
- **v1 (DONE 2026-07-23)** — `swarmlite publish` CLI + library function;
  feed publishing via bzzf:// (one upload = feed advance + pin);
  round-trip integration tests passed live.
- **v2 (DONE 2026-07-24)** — JS page-fetching VFS for SQLite-WASM
  (browser) under `js/` (vendored wa-sqlite Asyncify build); `resolveFeed`
  via the gateway's `/feeds` Etag; self-contained demo site (page +
  reader + wasm + DB under one immutable root) published and measured
  live.
- **v2.1 (DONE 2026-07-24)** — client-side verification for untrusted
  gateways: `open(url, {verify: true})` (Mantaray + BMT tree walk over
  `/chunks`, ports of swarmfs held byte-compatible by swarmfs-generated
  fixtures) and `resolveFeed(..., {verify: true})` (SOC signature
  recovery, vendored noble-secp256k1). Known follow-up: port the
  erasure-coding fanout fix back to swarmfs `join.py`.
- **v2.3 (DONE 2026-07-29)** — stamp lifecycle UX: `swarmlite stamps`
  (life left + fullest-bucket headroom), `--check --min-ttl` for cron,
  and `stamps topup ID --for/--to/--budget` / `stamps dilute ID --depth`.
  Mechanics stay in swarmfs (**requires >= 0.4.0**); this repo owns the
  policy — cost shown and confirmed before spending, the dilute-first
  warning surfaced, TTL in days rather than raw seconds. `plan_batch`
  now forwards `redundancy`/`encrypted`/`risk`/`depth`, so a publisher
  can size a batch exactly (`depth_for_addresses(split(data)[1])`)
  instead of estimating from bytes. Rationale: a published root lives
  exactly as long as its batch and an expired batch cannot be revived,
  so renewal is a publishing concern, not an ops afterthought.
  **Still open**: the batch↔publication mapping — `publish()` does not
  record which batch stamped which root, so `stamps` can list batches
  but cannot say which demo each one keeps alive. Deciding where that
  record lives (local sidecar vs. in the manifest) is the next step.
- Later — readahead tuning, hot-page bundling, DuckDB cookbook doc,
  FTS5 demo site.

## Testing strategy

- Unit: build a small DB locally, serve it through `memory://` fsspec,
  open through the VFS, assert queries return correct rows AND assert the
  number/size of reads stays within budget (spy on the file object) — the
  page-economy claim is the product; test it.
- Boundary: empty DB, page_size ≠ 4096 (must still work, just misaligned),
  DB larger than cache, FTS5 queries, malformed/truncated file (must fail
  loudly, not hang).
- Integration (opt-in): publish → read back via live Bee node; feed follow.

## Packaging & release (decided)

- **Two version strings must move together**: `pyproject.toml` and
  `swarmlite/__init__.py`'s `__version__`. They drifted once — `__init__`
  still said `0.1.0` while PyPI served `0.2.1`, so `swarmlite.__version__`
  lied for two releases. Check both on every bump.
- **The npm package versions independently** (`js/package.json`). A
  Python-only change does not bump it, and vice versa; publishing an
  identical JS package just to match numbers is worse than a gap.
- **Release = bump both → commit → `git tag vX.Y.Z && git push origin
  vX.Y.Z`.** `.github/workflows/publish.yml` triggers on the `v*` tag and
  publishes via PyPI trusted publishing (no stored token) from the `pypi`
  environment. Renaming that file or changing the environment breaks
  publishing until the trusted-publisher entry is updated to match.
- **`swarmfs` floor is a real dependency decision**, not boilerplate: raise
  it in the same commit that starts using new swarmfs API, or a fresh
  install resolves an older swarmfs and fails at import (currently
  `>= 0.4.0` for the stamp-lifecycle surface).

## Style

- Python ≥ 3.11, hatchling build, flat package dir (`swarmlite/`), BSD-3.
- Match swarmfs's tone: small modules, explicit errors with actionable
  messages, docstrings that state contracts, no speculative abstractions.
