#!/usr/bin/env node
// swarmlite CLI (pure JS — no Python needed):
//
//   swarmlite publish <db> [--name F] [--feed TOPIC] [--signer HEX]
//                          [--stamp ID] [--buy] [--ttl 1d] [--yes]
//                          [--api-url URL] [--quiet]
//   swarmlite query <url> <sql> [--verify] [--api-url URL]
//
// Same behavior as the Python CLI: bare root on stdout, human lines on
// stderr, one-line errors, exit 1 on failure. SWARMLITE_DEBUG=1 shows
// tracebacks; the signer key falls back to $SWARMLITE_SIGNER.

import { readFile } from 'node:fs/promises';
import { basename } from 'node:path';
import { parseArgs } from 'node:util';
import { createInterface } from 'node:readline/promises';

const wasmBinary = await readFile(
  new URL('../vendor/wa-sqlite/dist/wa-sqlite-async.wasm', import.meta.url));
const moduleOptions = {
  wasmBinary,
  // Emscripten's random-device probe is noisy but harmless in Node
  printErr: (t) => { if (!/initRandomDevice/.test(String(t))) console.error(t); },
};

const UNITS = { s: 1, m: 60, h: 3600, d: 86400, w: 7 * 86400 };

function parseTtl(text) {
  const unit = UNITS[text.slice(-1)];
  const value = Number(unit ? text.slice(0, -1) : text);
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`cannot parse TTL '${text}' — use e.g. 36h, 7d, 4w, or seconds`);
  }
  return value * (unit ?? 1);
}

function rejectPlaceholders(values) {
  for (const [name, value] of Object.entries(values)) {
    const m = value && /<[^<> ]+>/.exec(value);
    if (m) {
      throw new Error(
        `${name} contains the placeholder ${m[0]} — replace it with the ` +
        'real value (the root/reference is printed by publish)');
    }
  }
}

async function cmdPublish(args) {
  const { values, positionals } = parseArgs({
    args,
    allowPositionals: true,
    options: {
      name: { type: 'string' },
      feed: { type: 'string' },
      signer: { type: 'string' },
      stamp: { type: 'string', default: 'auto' },
      buy: { type: 'boolean', default: false },
      ttl: { type: 'string', default: '1d' },
      yes: { type: 'boolean', default: false },
      'api-url': { type: 'string' },
      quiet: { type: 'boolean', default: false },
    },
  });
  const [dbPath] = positionals;
  if (!dbPath) throw new Error('usage: swarmlite publish <db> [options]');
  rejectPlaceholders({
    db_path: dbPath, name: values.name, feed: values.feed,
    stamp: values.stamp, 'api-url': values['api-url'],
  });
  const apiUrl = values['api-url'] ?? process.env.BEE_API_URL ?? 'http://localhost:1633';
  const log = values.quiet ? () => {} : (l) => console.error(l);

  const bytes = new Uint8Array(await readFile(dbPath).catch(() => {
    throw new Error(
      `no such database file: '${dbPath}' — publish uploads an existing ` +
      'local SQLite file');
  }));

  let stamp = values.stamp;
  if (values.buy) {
    if (stamp !== 'auto') throw new Error('--buy and --stamp are mutually exclusive');
    const { planBatch, buyBatch } = await import('../src/publisher.js');
    const plan = await planBatch(apiUrl, bytes.byteLength, parseTtl(values.ttl));
    console.error(
      `batch for ${(bytes.byteLength / 2 ** 20).toFixed(1)} MB: depth ${plan.depth}, ` +
      `amount ${plan.amount}, lasting ~${Math.round(plan.ttlSecs / 3600)} h ` +
      `-> ${plan.costBzz.toFixed(4)} xBZZ from the node's wallet`);
    if (!values.yes) {
      if (!process.stdin.isTTY) {
        throw new Error('--buy needs a terminal to confirm; pass --yes to skip');
      }
      const rl = createInterface({ input: process.stdin, output: process.stderr });
      const answer = (await rl.question('buy it? [y/N] ')).trim().toLowerCase();
      rl.close();
      if (answer !== 'y' && answer !== 'yes') {
        throw new Error('purchase declined; nothing was bought');
      }
    }
    console.error('buying (waits for on-chain confirmation) ...');
    stamp = await buyBatch(apiUrl, plan.amount, plan.depth);
    console.error(`bought batch ${stamp}`);
  }

  const { publish } = await import('../src/publisher.js');
  const { root } = await publish(bytes, {
    apiUrl,
    name: values.name ?? basename(dbPath),
    stamp,
    feed: values.feed ?? null,
    signer: values.signer ?? process.env.SWARMLITE_SIGNER ?? null,
    moduleOptions,
    log,
  });
  console.log(root);
}

async function cmdQuery(args) {
  const { values, positionals } = parseArgs({
    args,
    allowPositionals: true,
    options: {
      verify: { type: 'boolean', default: false },
      stats: { type: 'boolean', default: false },
      'api-url': { type: 'string' },
    },
  });
  const [url, sql] = positionals;
  if (!url || !sql) throw new Error('usage: swarmlite query <url> <sql> [--verify]');
  rejectPlaceholders({ url });
  // accept bzz://<root>/<path> as shorthand for a gateway URL
  const apiUrl = values['api-url'] ?? process.env.BEE_API_URL ?? 'http://localhost:1633';
  const resolved = url.startsWith('bzz://')
    ? `${apiUrl}/bzz/${url.slice('bzz://'.length)}`
    : url;

  const { open } = await import('../src/index.js');
  const db = await open(resolved, { verify: values.verify, moduleOptions });
  try {
    for (const row of await db.query(sql)) {
      console.log(Object.values(row).join('\t'));
    }
    if (values.stats) {
      const s = db.stats();
      console.error(
        `fetched ${s.pagesFetched} pages (${(s.bytesFetched / 1024).toFixed(0)} KB) ` +
        `in ${s.readCount} reads, of a ${(s.fileSize / 2 ** 20).toFixed(1)} MB file` +
        (s.chunksFetched ? ` — ${s.chunksFetched} verified chunks` : ''));
    }
  } finally {
    await db.close();
  }
}

const [command, ...rest] = process.argv.slice(2);
try {
  if (command === 'publish') await cmdPublish(rest);
  else if (command === 'query') await cmdQuery(rest);
  else {
    console.error(
      'usage: swarmlite publish <db> [options] | swarmlite query <url> <sql> [--verify]');
    process.exit(2);
  }
} catch (e) {
  if (process.env.SWARMLITE_DEBUG) throw e;
  console.error(`swarmlite: ${e.message}`);
  process.exit(1);
}
