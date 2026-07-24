# Vendored: js-sha3 0.9.3

Source: the `js-sha3` npm package, version 0.9.3
(https://github.com/emn178/js-sha3), MIT license (see LICENSE).

Vendored subset: `src/sha3.js` only (unmodified), plus our `index.js`
ESM facade. Used for keccak256: hashing human-readable feed topics
(`src/feeds.js`), and later chunk/BMT verification.

Vendored (rather than npm-installed) for the same reason as wa-sqlite:
demo pages must be fully self-contained to be published on Swarm —
no CDN, no registry, only relative URLs under one immutable root.

Sanity check: `keccak256('hello')` =
`1c8aff950685c2ed4bc3174f3472287b56d9517b9c948127319a09a7a36deac8`
(the well-known Ethereum keccak vector; asserted in `test/feed.mjs`).

To upgrade: `npm pack js-sha3`, extract, copy `src/sha3.js` and
LICENSE, update the version here, re-run `node js/test/feed.mjs`.
