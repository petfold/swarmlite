// Minimal static file server with HTTP Range support, for running the
// smoke test with no Bee node (docs/roadmap.md: offline determinism):
//
//   node js/test/serve.mjs <dir> [port]        # then
//   node js/test/smoke.mjs http://localhost:8977/demo.db
//
// Only what the VFS needs: GET with a single `bytes=from-to` range,
// 206 + Content-Range on hits, 200 full body otherwise.

import { createServer } from 'node:http';
import { stat, open } from 'node:fs/promises';
import { join, normalize } from 'node:path';

const dir = process.argv[2] ?? '.';
const port = Number(process.argv[3] ?? 8977);

const server = createServer(async (req, res) => {
  const path = normalize(join(dir, decodeURIComponent(new URL(req.url, 'http://x').pathname)));
  if (!path.startsWith(normalize(dir))) { res.writeHead(403).end(); return; }
  let size;
  try {
    size = (await stat(path)).size;
  } catch {
    res.writeHead(404).end();
    return;
  }
  const m = /^bytes=(\d+)-(\d+)?$/.exec(req.headers.range ?? '');
  const from = m ? Number(m[1]) : 0;
  const to = m ? Math.min(m[2] ? Number(m[2]) : size - 1, size - 1) : size - 1;
  if (from >= size) {
    res.writeHead(416, { 'Content-Range': `bytes */${size}` }).end();
    return;
  }
  res.writeHead(m ? 206 : 200, {
    'Content-Length': to - from + 1,
    'Content-Range': `bytes ${from}-${to}/${size}`,
    'Accept-Ranges': 'bytes',
  });
  const fh = await open(path);
  fh.createReadStream({ start: from, end: to, autoClose: true }).pipe(res);
});

server.listen(port, () => {
  console.log(`serving ${dir} on http://localhost:${port}/ (Range enabled)`);
});
