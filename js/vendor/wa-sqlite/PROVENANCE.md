# Vendored: wa-sqlite 1.1.2 (SQLite 3.53.0)

Source: the `v1.1.2` tag of https://github.com/rhashimoto/wa-sqlite,
MIT license (see LICENSE). The npm package `wa-sqlite` stopped at 1.0.0
(2024-01, SQLite 3.44.0) while the repository moved on, so the files
come from the tag, where the built `dist/` is committed. The tag's
package.json still says 1.1.1; the wasm is from upstream's "Update
SQLite to 3.53." commit (2026-04-10), and `SELECT sqlite_version()`
reports 3.53.0.

Vendored subset only:

- `dist/wa-sqlite-async.mjs`, `dist/wa-sqlite-async.wasm` — the Asyncify
  build (async VFS methods; no COOP/COEP or SharedArrayBuffer needed).
- `src/VFS.js`, `src/FacadeVFS.js`, `src/sqlite-api.js`,
  `src/sqlite-constants.js` — the JS API, the VFS base class, and the
  FacadeVFS convenience layer our VFSes extend (`async` jXxx methods
  are what makes a method asynchronous).

All six files were checked byte-for-byte against the tag's git blob
hashes (`git hash-object` vs. the GitHub contents API).

Vendored (rather than npm-installed) because swarmlite's demo pages must
be fully self-contained to be published on Swarm: a strict page has no
CDN, no registry, only relative URLs under one immutable root.

To upgrade: fetch the same files from the new tag (raw.githubusercontent
.com/rhashimoto/wa-sqlite/<tag>/<path>), check them against the tag's
blob hashes, update the versions here, and re-run `npm test` in `js/`
plus `node js/test/smoke.mjs` (offline via `js/test/serve.mjs`).
