# Vendored: @noble/secp256k1 2.3.0

Source: the `@noble/secp256k1` npm package, version 2.3.0
(https://github.com/paulmillr/noble-secp256k1), MIT license (see
LICENSE). Single-file, zero-dependency, audited ECDSA implementation.

Vendored subset: `index.js` only (unmodified ESM).

Used for one thing: recovering the signer address of a feed update's
single-owner chunk (`src/verify.js` verifySoc), so `resolveFeed(...,
{verify: true})` catches a forged feed even on an untrusted gateway.

Vendored (rather than npm-installed) for the same reason as wa-sqlite
and js-sha3: pages must be fully self-contained to publish on Swarm.

To upgrade: `npm pack @noble/secp256k1`, extract, copy `index.js` and
LICENSE, update the version here, re-run `node js/test/verify.mjs`.
