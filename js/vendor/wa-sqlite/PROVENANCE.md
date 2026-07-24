# Vendored: wa-sqlite 1.0.0

Source: the `wa-sqlite` npm package, version 1.0.0
(https://github.com/rhashimoto/wa-sqlite), MIT license (see LICENSE).

Vendored subset only:

- `dist/wa-sqlite-async.mjs`, `dist/wa-sqlite-async.wasm` — the Asyncify
  build (async VFS methods; no COOP/COEP or SharedArrayBuffer needed).
- `src/VFS.js`, `src/sqlite-api.js`, `src/sqlite-constants.js` — the JS
  API and VFS base class.

Vendored (rather than npm-installed) because swarmlite's demo pages must
be fully self-contained to be published on Swarm: a strict page has no
CDN, no registry, only relative URLs under one immutable root.

To upgrade: `npm pack wa-sqlite`, extract, copy the same files, update
the version here, and re-run `node js/test/smoke.mjs`.
