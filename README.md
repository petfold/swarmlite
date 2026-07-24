# swarmlite

**Verifiable serverless SQLite hosting on Ethereum Swarm.**

Publish an ordinary SQLite file to [Swarm](https://docs.ethswarm.org/) and
run `SELECT` against it — fetching only the B-tree pages the query plan
touches, each one verifiable against the file's 32-byte content address.
No database server anywhere.

Measured live (Bee 2.8.1 light node, Gnosis mainnet, 134.5 MB database):

```bash
$ swarmlite query "bzz://<root>/demo.db" \
      "SELECT title FROM posts WHERE id = 73123" --stats
Post 73123: on sqlite
fetched 5 pages (20 KB) in 5 reads, of a 134.5 MB file    # 0.02 s
```

(`<root>` stands for the 64-hex reference `swarmlite publish` prints;
the CLI will remind you if a placeholder slips through.)

Warm repeats fetch nothing; an FTS5 full-text search fetched 12 pages.

**Status: v2.** The Python read path (`swarmlite.connect`, `swarmlite
query`), the publisher (`swarmlite publish`, with feed support), and the
**browser reader** (`js/`, SQLite-WASM — no install, no wallet, no
extension) are implemented, tested (offline suites + opt-in live
round-trips), and demonstrated live, including `bzzf://` feed reads and
a self-contained demo site. **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)
has the complete setup and worked examples**; design in
[docs/DESIGN.md](docs/DESIGN.md), plan in
[docs/roadmap.md](docs/roadmap.md).

## How it works

SQLite never touches storage directly — all I/O goes through its **VFS**
interface, and a read-only VFS needs three methods. SQLite's default page
(4 KB) equals a Swarm chunk; a query walks a B-tree, so even on a
multi-GB file a point lookup touches ~4 pages. swarmlite maps
`xRead(offset, n)` onto [swarmfs](https://github.com/petfold/swarmfs)
range reads, with an LRU page cache in front:

```
application          plain SELECT
SQLite engine        unmodified (apsw)
bzz-VFS shim         xRead(off, n) -> ranged fetch     <- this package
swarmfs              f.seek(off); f.read(n)
Bee                  /bytes, HTTP range reads
```

Writes stay where SQL writes belong: the **publisher** works on a local
file — full SQL, real transactions — then ships an immutable snapshot and
optionally advances a feed. Every publish is a permanent snapshot; the
old roots keep working. (Precedent for the read path: phiresky's
`sql.js-httpvfs` over plain HTTP ranges; Swarm adds verification and
permanence.)

## Quick start

```bash
git clone https://github.com/petfold/swarmlite
cd swarmlite
python3 -m venv .venv && source .venv/bin/activate
git clone https://github.com/petfold/swarmfs ../swarmfs   # until it's on PyPI
pip install -e ../swarmfs -e ".[test]"

pytest                           # 45 tests, no node needed
python examples/offline_demo.py  # the demo, offline — no node, no funds
```

The offline demo runs the identical code path through an in-memory
filesystem: a cold point lookup on a 134 MB database fetches 4 pages.

### Going live

With a Bee node (e.g. [Swarm Desktop](https://desktop.ethswarm.org/)) and
a usable postage stamp — see the
[User Guide](docs/USER_GUIDE.md) for stamp buying and sizing. The demo
above ran in RAM, so first materialize a database file to publish
(or use your own SQLite file):

```bash
python -c "import sys; sys.path.insert(0, 'examples');
from offline_demo import build
open('demo.db', 'wb').write(build(rows=30000))"

swarmlite publish demo.db
# pin:  bzz://<root>/demo.db      <- the 64-hex root hash is minted HERE
```

That printed root is the database's content address — save it; it is
also re-derivable by publishing the identical file again. Then:

```bash
swarmlite query "bzz://<root>/demo.db" \
    "SELECT title FROM posts WHERE id = 12345" --stats
```

No stamp yet? `swarmlite publish demo.db --buy` prices a batch sized
for the file, shows the xBZZ cost, and buys it from the node's wallet.

If the data will ever change, publish into a feed from the first
version — the same single upload advances the signed feed AND yields
the pin, and readers get a stable URL from day one:

```bash
swarmlite publish demo.db --feed mysite --signer <private key hex>
# pin:  bzz://<root>/demo.db
# feed: bzzf://<owner>/mysite/demo.db
```

Every publish is a permanent snapshot; `swarmlite snapshots
"bzzf://<owner>/mysite/demo.db"` lists the whole version history, each
line a pinned `bzz://` URL you can still query.

```python
import swarmlite

con = swarmlite.connect(f"bzz://{root}/demo.db")
rows = list(con.execute("SELECT ... "))
print(con.swarmlite_file.stats())   # pages/bytes fetched vs. file size
```

URL forms: `bzz://<root>/site.db` pins an immutable version;
`bzzf://<owner>/<topic>` follows a feed to the latest. `file://` and
`memory://` work too (tests, local use). Connections are strictly
read-only — DML raises `apsw.ReadOnlyError`.

### In the browser

The same lazy-page trick runs inside SQLite-WASM (`js/`, vendored
wa-sqlite): a static page, the reader, the wasm engine and the database
all publish under **one immutable Swarm root** — a multi-GB dataset
behind a static site, no server, nothing to install for readers.

```bash
python js/demo/publish_site.py --stamp <batchID>
# site: http://localhost:1633/bzz/<root>/index.html
```

Measured live: a cold point lookup fetched 5 pages (20 KB) of a 41.9 MB
database; a whole 4-query session 23 pages (92 KB). Feeds resolve in the
browser too (`resolveFeed`), so the page can always show the latest
published snapshot. Against an untrusted gateway, `verify: true` checks
every byte client-side against the 32-byte root (BMT per chunk,
signature recovery per feed update) — a tampering gateway is caught on
the first bad chunk. User Guide §7 has the details.

## What this is not

- Not a SQL engine over Swarm-native indexes — that is
  [recordstore](https://github.com/petfold/recordstore)/POT territory.
- Not OLTP. Feeds are eventually consistent; pin a root when you need
  reproducibility.
- Not a write path through the VFS. The publisher pattern *is* the write
  API.

## Relatives

- [swarmfs](https://github.com/petfold/swarmfs) — the fsspec backend this
  builds on (range reads, transactional writes, feeds).
- [recordstore](https://github.com/petfold/recordstore) — versioned
  key→record store; the natural system of record that a published
  `site.db` is materialized from.
- DuckDB-WASM + Parquet over range requests — the analytics flavour of
  the same trick; works today via swarmfs with no new code.

## License

BSD 3-Clause. Author: Peter Foldiak.
